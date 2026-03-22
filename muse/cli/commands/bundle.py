"""``muse bundle`` — pack and unpack commits for single-file transport.

A bundle is a self-contained JSON file carrying commits, snapshots, and
objects.  It is the porcelain equivalent of ``muse plumbing pack-objects`` /
``unpack-objects``, with friendlier names and the key value-add of
auto-updating local branch refs after ``unbundle``.

Use bundles to transfer a repository slice between machines without a network
connection — copy the file over SSH, USB, or email.

Bundle format: identical to the plumbing ``PackBundle`` JSON (same schema).
The format is stable and human-inspectable.

Subcommands::

    muse bundle create   <file> [<ref>...] [--have <id>...]
    muse bundle unbundle <file>
    muse bundle verify   <file> [-q]
    muse bundle list-heads <file>

Exit codes::

    0 — success
    1 — bundle not found, integrity failure, bad arguments
    3 — I/O error
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.object_store import has_object, write_object
from muse.core.pack import PackBundle, apply_pack, build_pack
from muse.core.repo import require_repo
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_head_commit_id,
    read_current_branch,
    resolve_commit_ref,
    write_commit,
    write_snapshot,
)
from muse.core.validation import sanitize_display, validate_branch_name

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text(encoding="utf-8"))["repo_id"])


def _resolve_refs(
    root: pathlib.Path,
    repo_id: str,
    branch: str,
    refs: list[str],
) -> list[str]:
    """Resolve a list of ref strings to commit IDs. Expands HEAD."""
    ids: list[str] = []
    for ref in refs:
        if ref.upper() == "HEAD":
            cid = get_head_commit_id(root, branch)
            if cid:
                ids.append(cid)
        else:
            rec = resolve_commit_ref(root, repo_id, branch, ref)
            if rec:
                ids.append(rec.commit_id)
            else:
                print(f"❌ Ref '{sanitize_display(ref)}' not found.", file=sys.stderr)
                raise SystemExit(ExitCode.USER_ERROR)
    return ids


def _load_bundle(file_path: pathlib.Path) -> PackBundle:
    try:
        raw = file_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except FileNotFoundError:
        print(f"❌ Bundle file not found: {file_path}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    except json.JSONDecodeError as exc:
        print(f"❌ Bundle is not valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    if not isinstance(parsed, dict):
        print("❌ Bundle has unexpected structure.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    bundle: PackBundle = {}
    if "commits" in parsed and isinstance(parsed["commits"], list):
        bundle["commits"] = parsed["commits"]
    if "snapshots" in parsed and isinstance(parsed["snapshots"], list):
        bundle["snapshots"] = parsed["snapshots"]
    if "objects" in parsed and isinstance(parsed["objects"], list):
        bundle["objects"] = parsed["objects"]
    if "branch_heads" in parsed and isinstance(parsed["branch_heads"], dict):
        bundle["branch_heads"] = {
            k: v for k, v in parsed["branch_heads"].items()
            if isinstance(k, str) and isinstance(v, str)
        }
    return bundle


def _iter_branches(root: pathlib.Path) -> list[tuple[str, str]]:
    heads_dir = root / ".muse" / "refs" / "heads"
    if not heads_dir.exists():
        return []
    result: list[tuple[str, str]] = []
    for ref_file in sorted(heads_dir.rglob("*")):
        if ref_file.is_file():
            branch_name = str(ref_file.relative_to(heads_dir).as_posix())
            cid = ref_file.read_text(encoding="utf-8").strip()
            if cid:
                result.append((branch_name, cid))
    return result


def _reachable_from(root: pathlib.Path, tip_ids: list[str]) -> set[str]:
    from collections import deque
    from muse.core.store import read_commit as _rc
    seen: set[str] = set()
    q: deque[str] = deque(tip_ids)
    while q:
        cid = q.popleft()
        if cid in seen:
            continue
        seen.add(cid)
        c = _rc(root, cid)
        if c:
            if c.parent_commit_id:
                q.append(c.parent_commit_id)
            if c.parent2_commit_id:
                q.append(c.parent2_commit_id)
    return seen


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the bundle subcommand."""
    parser = subparsers.add_parser(
        "bundle",
        help="Pack and unpack commits into a single portable bundle file.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subs.required = True

    # create
    create_p = subs.add_parser("create", help="Create a bundle file containing commits reachable from <refs>.")
    create_p.add_argument("file", help="Output bundle file path.")
    create_p.add_argument("refs", nargs="*", default=None, help="Refs to include (default: HEAD).")
    create_p.add_argument(
        "--have", "-H", nargs="*", default=None, dest="have",
        help="Commits the receiver already has (exclude from bundle).",
    )
    create_p.set_defaults(func=run_create)

    # unbundle
    unbundle_p = subs.add_parser("unbundle", help="Apply a bundle to the local store and optionally advance branch refs.")
    unbundle_p.add_argument("file", help="Bundle file to apply.")
    unbundle_p.add_argument(
        "--no-update-refs", action="store_false", dest="update_refs",
        help="Do not update local branch refs from the bundle's branch_heads.",
    )
    unbundle_p.set_defaults(func=run_unbundle, update_refs=True)

    # verify
    verify_p = subs.add_parser("verify", help="Verify the integrity of a bundle file.")
    verify_p.add_argument("file", help="Bundle file to verify.")
    verify_p.add_argument(
        "--quiet", "-q", action="store_true", dest="quiet",
        help="No output — exit 0 if clean, 1 on failure.",
    )
    verify_p.add_argument(
        "--format", "-f", default="text", dest="fmt",
        help="Output format: text or json.",
    )
    verify_p.set_defaults(func=run_verify)

    # list-heads
    list_heads_p = subs.add_parser("list-heads", help="List the branch heads recorded in a bundle file.")
    list_heads_p.add_argument("file", help="Bundle file to inspect.")
    list_heads_p.add_argument(
        "--format", "-f", default="text", dest="fmt",
        help="Output format: text or json.",
    )
    list_heads_p.set_defaults(func=run_list_heads)


def run_create(args: argparse.Namespace) -> None:
    """Create a bundle file containing commits reachable from <refs>.

    ``--have`` prunes commits the receiver already has, reducing bundle size.
    The output file is self-contained JSON — safe to copy, email, or sneak-net.

    Examples::

        muse bundle create repo.bundle             # HEAD → bundle
        muse bundle create out.bundle feat/audio   # specific branch
        muse bundle create out.bundle HEAD --have old-sha
    """
    file: str = args.file
    refs: list[str] | None = args.refs
    have: list[str] | None = args.have

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = read_current_branch(root)

    want_refs: list[str] = refs or ["HEAD"]
    commit_ids = _resolve_refs(root, repo_id, branch, want_refs)

    if not commit_ids:
        print("❌ No commits to bundle.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    have_ids: list[str] = have or []

    bundle = build_pack(root, commit_ids, have=have_ids)

    # Add branch_heads for the resolved refs.
    heads: dict[str, str] = {}
    for br_name, cid in _iter_branches(root):
        if cid in commit_ids or cid in _reachable_from(root, commit_ids):
            heads[br_name] = cid
    if heads:
        bundle["branch_heads"] = heads

    out_path = pathlib.Path(file)
    out_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

    n_commits = len(bundle.get("commits", []))
    n_objects = len(bundle.get("objects", []))
    size_kb = out_path.stat().st_size / 1024
    print(
        f"✅ Bundle: {out_path}  ({n_commits} commits, {n_objects} objects, {size_kb:.1f} KiB)"
    )


def run_unbundle(args: argparse.Namespace) -> None:
    """Apply a bundle to the local store and optionally advance branch refs.

    This is the key porcelain value-add over ``muse plumbing unpack-objects``:
    after unpacking, branch refs are updated from ``branch_heads`` in the bundle
    so the local repo reflects the sender's branch state.

    Examples::

        muse bundle unbundle repo.bundle
        muse bundle unbundle repo.bundle --no-update-refs
    """
    file: str = args.file
    update_refs: bool = args.update_refs

    root = require_repo()
    bundle = _load_bundle(pathlib.Path(file))

    result = apply_pack(root, bundle)

    print(
        f"Unpacked {result['commits_written']} commit(s), "
        f"{result['snapshots_written']} snapshot(s), "
        f"{result['objects_written']} object(s)  "
        f"({result['objects_skipped']} skipped)"
    )

    if update_refs:
        branch_heads: dict[str, str] = bundle.get("branch_heads") or {}
        updated: list[str] = []
        for br, cid in branch_heads.items():
            try:
                validate_branch_name(br)
            except ValueError:
                logger.warning("⚠️ bundle: skipping invalid branch name %r", br)
                continue
            if len(cid) != 64 or not all(c in "0123456789abcdef" for c in cid):
                logger.warning("⚠️ bundle: skipping invalid commit ID for %r", br)
                continue
            ref_path = root / ".muse" / "refs" / "heads" / br
            ref_path.parent.mkdir(parents=True, exist_ok=True)
            ref_path.write_text(cid, encoding="utf-8")
            updated.append(br)

        if updated:
            print(f"Updated refs: {', '.join(sanitize_display(b) for b in updated)}")

    print("✅ Bundle applied.")


def run_verify(args: argparse.Namespace) -> None:
    """Verify the integrity of a bundle file.

    Checks that every object's SHA-256 matches its declared ``object_id``
    (hash mismatch → corruption).  Also checks that every snapshot's objects
    are present in the bundle.

    Examples::

        muse bundle verify repo.bundle
        muse bundle verify repo.bundle --quiet && echo "clean"
    """
    file: str = args.file
    quiet: bool = args.quiet
    fmt: str = args.fmt

    if fmt not in {"text", "json"}:
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    bundle = _load_bundle(pathlib.Path(file))

    failures: list[str] = []
    objects_checked = 0

    # Build set of object IDs in the bundle.
    bundle_obj_ids: set[str] = set()
    for obj in bundle.get("objects", []):
        obj_id = obj["object_id"]
        content_b64 = obj["content_b64"]
        if not obj_id or not content_b64:
            failures.append("objects list: entry has empty object_id or content_b64")
            continue
        try:
            raw = base64.b64decode(content_b64)
        except Exception:
            failures.append(f"object {obj_id[:12]}: base64 decode error")
            objects_checked += 1
            continue
        actual = hashlib.sha256(raw).hexdigest()
        if actual != obj_id:
            failures.append(f"object {obj_id[:12]}: hash mismatch (corruption)")
        else:
            bundle_obj_ids.add(obj_id)
        objects_checked += 1

    # Check snapshots reference objects in the bundle.
    for snap_dict in bundle.get("snapshots", []):
        snap_id = snap_dict.get("snapshot_id", "")
        manifest = snap_dict.get("manifest", {})
        for rel_path, obj_id in manifest.items():
            if obj_id not in bundle_obj_ids:
                failures.append(
                    f"snapshot {snap_id[:12]}: missing object {obj_id[:12]} for {rel_path}"
                )

    all_ok = len(failures) == 0

    if quiet:
        raise SystemExit(0 if all_ok else ExitCode.USER_ERROR)

    if fmt == "json":
        print(json.dumps({
            "objects_checked": objects_checked,
            "all_ok": all_ok,
            "failures": failures,
        }, indent=2))
    else:
        print(f"Objects checked: {objects_checked}")
        if all_ok:
            print("✅ Bundle is clean.")
        else:
            print(f"❌ {len(failures)} failure(s):")
            for f in failures:
                print(f"  {f}")

    raise SystemExit(0 if all_ok else ExitCode.USER_ERROR)


def run_list_heads(args: argparse.Namespace) -> None:
    """List the branch heads recorded in a bundle file.

    Examples::

        muse bundle list-heads repo.bundle
        muse bundle list-heads repo.bundle --format json
    """
    file: str = args.file
    fmt: str = args.fmt

    if fmt not in {"text", "json"}:
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    bundle = _load_bundle(pathlib.Path(file))
    heads: dict[str, str] = bundle.get("branch_heads") or {}

    if fmt == "json":
        print(json.dumps(heads, indent=2))
    else:
        if not heads:
            print("No branch heads in bundle.")
            return
        for branch, cid in sorted(heads.items()):
            print(f"{cid[:12]}  {sanitize_display(branch)}")
