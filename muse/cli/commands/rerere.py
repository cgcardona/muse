"""muse rerere — reuse recorded resolutions for merge conflicts.

Records how you resolve a merge conflict and replays that resolution
automatically the next time the same conflict appears.  The conflict is
identified by a content fingerprint (SHA-256 of the sorted ours/theirs
object ID pair) so the same resolution applies regardless of surrounding
context changes.

Subcommands
-----------

``muse rerere``          Apply cached resolutions to current conflicts.
``muse rerere record``   Record preimages for current conflicts (no replay).
``muse rerere status``   List cached resolutions and match against current conflicts.
``muse rerere forget``   Remove the cached resolution for specific conflict paths.
``muse rerere clear``    Remove all cached resolutions.
``muse rerere gc``       Garbage-collect preimage-only entries older than 60 days.

Automatic integration
---------------------

``muse merge`` calls rerere automatically:

- **Before writing conflict markers**: tries to auto-apply any cached
  resolutions, resolving matching conflicts without user intervention.
- **Records preimages** for any remaining conflicts so that the user's
  resolution can be saved when they run ``muse commit``.

``muse commit`` (during a conflicted merge) calls rerere to save the
user's chosen resolution for replay on future identical conflicts.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.merge_engine import read_merge_state
from muse.core.repo import require_repo
from muse.core.rerere import (
    RerereRecord,
    apply_cached,
    clear_all,
    compute_fingerprint,
    forget_record,
    gc_stale,
    list_records,
    record_preimage,
    rr_cache_dir,
)
from muse.core.store import get_head_commit_id, read_commit, read_current_branch, read_snapshot
from muse.core.validation import sanitize_display
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("text", "json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_conflict_manifests(
    root: pathlib.Path,
) -> tuple[dict[str, str], dict[str, str]] | None:
    """Return (ours_manifest, theirs_manifest) from the active MERGE_STATE.

    Returns ``None`` when MERGE_STATE is absent or incomplete.
    """
    state = read_merge_state(root)
    if state is None or not state.ours_commit or not state.theirs_commit:
        return None

    def _manifest(commit_id: str) -> dict[str, str]:
        commit = read_commit(root, commit_id)
        if commit is None:
            return {}
        snap = read_snapshot(root, commit.snapshot_id)
        return snap.manifest if snap else {}

    return _manifest(state.ours_commit), _manifest(state.theirs_commit)


def _fmt_record(rec: RerereRecord, *, color: bool) -> str:
    status = "✅ resolved" if rec.has_resolution else "⏳ preimage only"
    ts = rec.recorded_at.strftime("%Y-%m-%d %H:%M")
    return f"{rec.fingerprint[:12]}  {status}  {ts}  {sanitize_display(rec.path)}"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the rerere subcommand."""
    parser = subparsers.add_parser(
        "rerere",
        help="Reuse recorded resolutions for merge conflicts.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Show what would be auto-resolved without writing files.")
    parser.add_argument("--format", "-f", default="text", dest="fmt",
                        help="Output format: text or json.")
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")

    record_p = subs.add_parser("record", help="Record preimages for current conflicts without applying cached resolutions.")
    record_p.add_argument("--format", "-f", default="text", dest="fmt",
                          help="Output format: text or json.")
    record_p.set_defaults(func=run_record)

    status_p = subs.add_parser("status", help="Show cached rerere resolutions and their match against current conflicts.")
    status_p.add_argument("--format", "-f", default="text", dest="fmt",
                          help="Output format: text or json.")
    status_p.set_defaults(func=run_status)

    forget_p = subs.add_parser("forget", help="Remove the cached rerere resolution for one or more conflict paths.")
    forget_p.add_argument("paths", nargs="+",
                          help="Workspace-relative paths whose rerere resolution should be removed.")
    forget_p.add_argument("--format", "-f", default="text", dest="fmt",
                          help="Output format: text or json.")
    forget_p.set_defaults(func=run_forget)

    clear_p = subs.add_parser("clear", help="Remove all cached rerere resolutions.")
    clear_p.add_argument("--yes", "-y", action="store_true",
                         help="Skip confirmation prompt.")
    clear_p.add_argument("--format", "-f", default="text", dest="fmt",
                         help="Output format: text or json.")
    clear_p.set_defaults(func=run_clear)

    gc_p = subs.add_parser("gc", help="Remove preimage-only rerere entries older than 60 days.")
    gc_p.add_argument("--format", "-f", default="text", dest="fmt",
                      help="Output format: text or json.")
    gc_p.set_defaults(func=run_gc)

    parser.set_defaults(func=run)


# ---------------------------------------------------------------------------
# Default action: apply cached resolutions
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> None:
    """Apply cached conflict resolutions to the current working tree.

    Reads ``.muse/MERGE_STATE.json`` to discover which paths are in conflict,
    looks up each path's fingerprint in ``.muse/rr-cache/``, and restores any
    cached resolution blobs to the working tree.  Paths with no cached
    resolution are left unchanged and reported for manual attention.

    Run after ``muse merge`` exits with conflicts, or let ``muse merge`` invoke
    this automatically via the ``--rerere-autoupdate`` flag.
    """
    dry_run: bool = args.dry_run
    fmt: str = args.fmt

    if fmt not in _FORMAT_CHOICES:
        print(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    state = read_merge_state(root)
    if state is None or not state.conflict_paths:
        print("No merge in progress — nothing for rerere to do.")
        return

    manifests = _load_conflict_manifests(root)
    if manifests is None:
        print("❌ MERGE_STATE.json is incomplete — cannot load conflict manifests.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    ours_manifest, theirs_manifest = manifests

    domain = read_domain(root)
    plugin = resolve_plugin(root)

    auto_resolved: list[str] = []
    remaining: list[str] = []

    for path in sorted(state.conflict_paths):
        ours_id = ours_manifest.get(path, "")
        theirs_id = theirs_manifest.get(path, "")
        if not ours_id or not theirs_id:
            remaining.append(path)
            continue

        fp = compute_fingerprint(path, ours_id, theirs_id, plugin, root)

        if not (root / ".muse" / "rr-cache" / fp / "resolution").exists():
            remaining.append(path)
            continue

        if dry_run:
            auto_resolved.append(path)
            continue

        dest = root / path
        if apply_cached(root, fp, dest):
            auto_resolved.append(path)
        else:
            remaining.append(path)

    if fmt == "json":
        print(
            json.dumps(
                {
                    "dry_run": dry_run,
                    "auto_resolved": auto_resolved,
                    "remaining": remaining,
                },
                indent=2,
            )
        )
        return

    prefix = "[dry-run] would resolve" if dry_run else "✅ rerere auto-resolved"
    for p in auto_resolved:
        print(f"  {prefix}: {sanitize_display(p)}")
    for p in remaining:
        print(f"  ⏳ needs manual resolution: {sanitize_display(p)}")

    if not auto_resolved and not remaining:
        print("No conflicting paths found.")


# ---------------------------------------------------------------------------
# record — write preimages without replaying
# ---------------------------------------------------------------------------


def run_record(args: argparse.Namespace) -> None:
    """Record preimages for current conflicts without applying cached resolutions.

    Writes a preimage entry to ``.muse/rr-cache/`` for every conflicting path
    in MERGE_STATE.  The preimage is the fingerprint of the two conflicting
    blob IDs; the resolution is saved later when the user commits.

    Idempotent: re-recording an already-known conflict is a no-op.
    """
    fmt: str = args.fmt

    if fmt not in _FORMAT_CHOICES:
        print(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    state = read_merge_state(root)
    if state is None or not state.conflict_paths:
        print("No merge in progress — nothing to record.")
        return

    manifests = _load_conflict_manifests(root)
    if manifests is None:
        print("❌ MERGE_STATE.json is incomplete — cannot load conflict manifests.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    ours_manifest, theirs_manifest = manifests

    domain = read_domain(root)
    plugin = resolve_plugin(root)

    recorded: list[str] = []
    skipped: list[str] = []

    for path in sorted(state.conflict_paths):
        ours_id = ours_manifest.get(path, "")
        theirs_id = theirs_manifest.get(path, "")
        if not ours_id or not theirs_id:
            skipped.append(path)
            continue
        record_preimage(root, path, ours_id, theirs_id, domain, plugin)
        recorded.append(path)

    if fmt == "json":
        print(json.dumps({"recorded": recorded, "skipped": skipped}, indent=2))
        return

    for p in recorded:
        print(f"  📝 preimage recorded: {sanitize_display(p)}")
    for p in skipped:
        print(f"  ⚠️  skipped (one side deleted): {sanitize_display(p)}")


# ---------------------------------------------------------------------------
# status — show cached resolutions vs current conflicts
# ---------------------------------------------------------------------------


def run_status(args: argparse.Namespace) -> None:
    """Show cached rerere resolutions and their match against current conflicts.

    Lists every entry in ``.muse/rr-cache/``, indicating whether a resolution
    has been recorded.  When a merge is in progress, also marks which cached
    resolutions match the current conflict set.
    """
    fmt: str = args.fmt

    if fmt not in _FORMAT_CHOICES:
        print(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    records = list_records(root)
    state = read_merge_state(root)

    # Build the set of fingerprints that match current conflicts.
    current_fps: set[str] = set()
    if state and state.conflict_paths:
        manifests = _load_conflict_manifests(root)
        if manifests is not None:
            ours_manifest, theirs_manifest = manifests
            plugin = resolve_plugin(root)
            for path in state.conflict_paths:
                ours_id = ours_manifest.get(path, "")
                theirs_id = theirs_manifest.get(path, "")
                if ours_id and theirs_id:
                    fp = compute_fingerprint(path, ours_id, theirs_id, plugin, root)
                    current_fps.add(fp)

    color = sys.stdout.isatty()

    if fmt == "json":
        print(
            json.dumps(
                {
                    "total": len(records),
                    "records": [
                        {
                            "fingerprint": r.fingerprint,
                            "path": r.path,
                            "domain": r.domain,
                            "has_resolution": r.has_resolution,
                            "resolution_id": r.resolution_id,
                            "recorded_at": r.recorded_at.isoformat(),
                            "matches_current_conflict": r.fingerprint in current_fps,
                        }
                        for r in records
                    ],
                },
                indent=2,
            )
        )
        return

    if not records:
        print("No rerere records found.")
        return

    print(f"{'fingerprint':14}  {'status':16}  {'recorded':16}  path")
    print("-" * 72)
    for rec in records:
        marker = " ◀ current" if rec.fingerprint in current_fps else ""
        print(_fmt_record(rec, color=color) + marker)


# ---------------------------------------------------------------------------
# forget — remove cached resolution for specific paths
# ---------------------------------------------------------------------------


def run_forget(args: argparse.Namespace) -> None:
    """Remove the cached rerere resolution for one or more conflict paths.

    Computes the fingerprint for each PATH against the current MERGE_STATE and
    removes its rr-cache entry.  Run this when a recorded resolution is wrong
    and you want to force manual resolution next time.
    """
    paths: list[str] = args.paths
    fmt: str = args.fmt

    if fmt not in _FORMAT_CHOICES:
        print(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    state = read_merge_state(root)
    if state is None or not state.conflict_paths:
        print("❌ No merge in progress — cannot determine conflict fingerprints.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    manifests = _load_conflict_manifests(root)
    if manifests is None:
        print("❌ MERGE_STATE.json is incomplete.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    ours_manifest, theirs_manifest = manifests
    plugin = resolve_plugin(root)

    forgotten: list[str] = []
    not_found: list[str] = []

    for path in paths:
        ours_id = ours_manifest.get(path, "")
        theirs_id = theirs_manifest.get(path, "")
        if not ours_id or not theirs_id:
            not_found.append(path)
            continue
        fp = compute_fingerprint(path, ours_id, theirs_id, plugin, root)
        if forget_record(root, fp):
            forgotten.append(path)
        else:
            not_found.append(path)

    if fmt == "json":
        print(json.dumps({"forgotten": forgotten, "not_found": not_found}, indent=2))
        return

    for p in forgotten:
        print(f"  🗑  forgot: {sanitize_display(p)}")
    for p in not_found:
        print(f"  ⚠️  no record found: {sanitize_display(p)}")


# ---------------------------------------------------------------------------
# clear — remove all cached resolutions
# ---------------------------------------------------------------------------


def run_clear(args: argparse.Namespace) -> None:
    """Remove all cached rerere resolutions.

    Deletes the entire ``.muse/rr-cache/`` directory contents.  This is
    irreversible — all recorded resolutions will be lost.
    """
    yes: bool = args.yes
    fmt: str = args.fmt

    if fmt not in _FORMAT_CHOICES:
        print(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    if not yes:
        cache = rr_cache_dir(root)
        count = sum(1 for e in cache.iterdir() if e.is_dir()) if cache.exists() else 0
        if count == 0:
            print("rr-cache is already empty.")
            return
        confirmed = input(
            f"This will permanently delete {count} rerere record(s). Continue? [y/N]: "
        ).strip().lower() in ("y", "yes")
        if not confirmed:
            print("Aborted.")
            return

    removed = clear_all(root)

    if fmt == "json":
        print(json.dumps({"removed": removed}))
        return

    print(f"✅ Cleared {removed} rerere record(s).")


# ---------------------------------------------------------------------------
# gc — garbage-collect stale preimage-only entries
# ---------------------------------------------------------------------------


def run_gc(args: argparse.Namespace) -> None:
    """Remove preimage-only rerere entries older than 60 days.

    Keeps all entries that have a resolution saved (regardless of age).
    Removes entries where the user never committed a resolution — these are
    conflicts that were abandoned or resolved in another way.
    """
    fmt: str = args.fmt

    if fmt not in _FORMAT_CHOICES:
        print(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    removed = gc_stale(root)

    if fmt == "json":
        print(json.dumps({"removed": removed}))
        return

    if removed:
        print(f"✅ gc: removed {removed} stale preimage-only entry(s).")
    else:
        print("gc: nothing to remove.")
