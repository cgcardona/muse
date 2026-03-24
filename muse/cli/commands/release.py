"""muse release — create and manage versioned releases on MuseHub.

A Muse release is richer than a Git tag:

- Semver parsed into queryable components (major/minor/patch/pre/build).
- Named distribution channel (stable | beta | alpha | nightly) rather than
  a boolean ``is_prerelease`` flag.
- Changelog auto-generated from typed ``sem_ver_bump`` and
  ``breaking_changes`` fields on commits since the previous release — no
  conventional-commit parsing required.
- ``snapshot_id`` makes the release reproducible from the content-addressed
  object store forever.
- ``agent_id`` / ``model_id`` surface AI provenance from the tip commit.

Usage::

    muse release add <tag>                       — create a local release at HEAD
    muse release list                            — list local releases
    muse release show <tag>                      — inspect one release
    muse release push <tag>                      — push a release to a remote
    muse release delete <tag>                    — delete a local release record
    muse release delete <tag> --remote <remote>  — retract a release from a remote

Deletion semantics::

    Deleting a release removes the named label only. The underlying commit and
    snapshot remain in the content-addressed object store forever — they are
    still reachable by their SHA-256 and are fully reproducible. Only the
    named pointer is removed, not the content it referenced.

Examples::

    muse release add v1.2.0 --title "Summer drop" --body "Bug fixes"
    muse release add v1.3.0-beta.1 --channel beta --draft
    muse release push v1.2.0 --remote origin
    muse release list --channel stable
    muse release show v1.2.0 --format json
    muse release delete v1.2.0-beta.1
    muse release delete v1.2.0 --remote origin
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import uuid

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    ReleaseChannel,
    ReleaseRecord,
    build_changelog,
    delete_release,
    get_release_for_tag,
    list_releases,
    parse_semver,
    read_current_branch,
    resolve_commit_ref,
    semver_channel,
    semver_to_str,
    write_release,
)
from muse.core.validation import sanitize_display

logger = logging.getLogger(__name__)

_CHANNELS: frozenset[str] = frozenset({"stable", "beta", "alpha", "nightly"})
_CHANNEL_MAP: dict[str, ReleaseChannel] = {
    "stable": "stable",
    "beta": "beta",
    "alpha": "alpha",
    "nightly": "nightly",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _resolve_remote_url(root: pathlib.Path, remote: str) -> str:
    from muse.cli.config import get_remote

    url = get_remote(remote, root)
    if not url:
        print(f"❌ Remote '{sanitize_display(remote)}' is not configured.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    return url


def _format_release(release: ReleaseRecord, fmt: str) -> None:
    """Print a release in text or JSON format."""
    if fmt == "json":
        d = release.to_dict()
        print(json.dumps(d, default=str))
        return
    draft_label = " [DRAFT]" if release.is_draft else ""
    print(f"Release {release.tag}{draft_label}")
    print(f"  Channel:  {release.channel}")
    print(f"  Semver:   {semver_to_str(release.semver)}")
    print(f"  Commit:   {release.commit_id[:8]}")
    print(f"  Created:  {release.created_at.isoformat()}")
    if release.title:
        print(f"  Title:    {sanitize_display(release.title)}")
    if release.body:
        print(f"  Body:\n{release.body}")
    if release.changelog:
        print(f"  Changelog ({len(release.changelog)} commits):")
        for entry in release.changelog[:20]:
            bump = entry["sem_ver_bump"]
            bump_label = {"major": "💥", "minor": "✨", "patch": "🔧"}.get(bump, "  ")
            print(f"    {bump_label} {entry['commit_id'][:8]}  {sanitize_display(entry['message'][:72])}")
        if len(release.changelog) > 20:
            print(f"    … and {len(release.changelog) - 20} more")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def run_add(args: argparse.Namespace) -> None:
    """Create a local release at HEAD.

    Parses ``<tag>`` as semver, auto-generates changelog from typed commit
    metadata since the previous release, and writes the record to
    ``.muse/releases/``.
    """
    tag: str = args.tag
    title: str = args.title or ""
    body: str = args.body or ""
    channel_arg: str = args.channel or ""
    is_draft: bool = args.draft
    ref: str | None = args.ref
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    try:
        semver = parse_semver(tag)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    if channel_arg and channel_arg not in _CHANNELS:
        print(
            f"❌ Unknown channel '{sanitize_display(channel_arg)}'. "
            f"Choose: {', '.join(sorted(_CHANNELS))}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    channel: ReleaseChannel = _CHANNEL_MAP.get(channel_arg, semver_channel(semver))

    root = require_repo()
    repo_id = _repo_id(root)
    branch = _branch(root)

    # Resolve the commit to release.
    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        ref_label = ref or "HEAD"
        print(f"❌ Ref '{sanitize_display(ref_label)}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    # Guard: tag must not already exist.
    if get_release_for_tag(root, repo_id, tag) is not None:
        print(f"❌ Release '{sanitize_display(tag)}' already exists. Delete it first.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    # Find the previous release to bound changelog generation.
    existing = list_releases(root, repo_id, include_drafts=False)
    prev_commit_id: str | None = None
    if existing:
        prev_commit_id = existing[0].commit_id

    changelog = build_changelog(root, prev_commit_id, commit.commit_id)

    release = ReleaseRecord(
        release_id=str(uuid.uuid4()),
        repo_id=repo_id,
        tag=tag,
        semver=semver,
        channel=channel,
        commit_id=commit.commit_id,
        snapshot_id=commit.snapshot_id,
        title=title,
        body=body,
        changelog=changelog,
        agent_id=commit.agent_id,
        model_id=commit.model_id,
        is_draft=is_draft,
    )
    write_release(root, release)

    if fmt == "json":
        print(json.dumps(release.to_dict(), default=str))
    else:
        draft_label = " (draft)" if is_draft else ""
        print(
            f"✅ Release {tag}{draft_label} — {len(changelog)} commits "
            f"on branch {sanitize_display(branch)}"
        )


def run_list(args: argparse.Namespace) -> None:
    """List releases (local or remote)."""
    channel_arg: str = args.channel or ""
    include_drafts: bool = args.include_drafts
    remote: str = args.remote or ""
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    channel_filter: ReleaseChannel | None = _CHANNEL_MAP.get(channel_arg) if channel_arg else None

    root = require_repo()

    if remote:
        # Fetch from remote.
        from muse.cli.config import get_auth_token
        from muse.core.store import ReleaseRecord as RR
        from muse.core.transport import TransportError, make_transport

        url = _resolve_remote_url(root, remote)
        token = get_auth_token(root, url)
        transport = make_transport(url)
        try:
            raw_releases = transport.list_releases_remote(
                url, token,
                channel=channel_filter,
                include_drafts=include_drafts,
            )
        except TransportError as exc:
            print(f"❌ Could not fetch releases from remote: {exc}", file=sys.stderr)
            raise SystemExit(ExitCode.REMOTE_ERROR)

        releases = [RR.from_dict(d) for d in raw_releases]
    else:
        repo_id = _repo_id(root)
        releases = list_releases(root, repo_id, channel=channel_filter, include_drafts=include_drafts)

    if fmt == "json":
        print(json.dumps([r.to_dict() for r in releases], default=str))
        return

    if not releases:
        print("No releases found.")
        return

    for r in releases:
        draft_label = " [DRAFT]" if r.is_draft else ""
        print(f"{r.tag:<20} {r.channel:<8} {r.commit_id[:8]}  {sanitize_display(r.title)[:40]}{draft_label}")


def run_show(args: argparse.Namespace) -> None:
    """Show details of a single release."""
    tag: str = args.tag
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    repo_id = _repo_id(root)

    release = get_release_for_tag(root, repo_id, tag)
    if release is None:
        print(f"❌ Release '{sanitize_display(tag)}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.NOT_FOUND)

    _format_release(release, fmt)


def run_push(args: argparse.Namespace) -> None:
    """Push a local release to a remote.

    Transmits the lightweight ``ReleaseRecord`` payload to MuseHub, which then
    runs the full semantic analysis (language breakdown, symbol inventory, API
    surface diff, file hotspots, refactoring events, provenance) as a server-
    side background task.  The push completes immediately; the enriched release
    detail page populates within seconds.
    """
    tag: str = args.tag
    remote: str = args.remote

    from muse.cli.config import get_auth_token
    from muse.core.transport import TransportError, make_transport

    root = require_repo()
    repo_id = _repo_id(root)

    release = get_release_for_tag(root, repo_id, tag)
    if release is None:
        print(f"❌ Release '{sanitize_display(tag)}' not found locally. Run 'muse release add' first.", file=sys.stderr)
        raise SystemExit(ExitCode.NOT_FOUND)

    url = _resolve_remote_url(root, remote)
    token = get_auth_token(root, url)
    transport = make_transport(url)

    try:
        release_id = transport.create_release(url, token, release.to_dict())
    except TransportError as exc:
        print(f"❌ Push failed: {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.REMOTE_ERROR)

    print(f"✅ Release {tag} pushed to {sanitize_display(remote)} (id={release_id[:8]})")


def run_delete(args: argparse.Namespace) -> None:
    """Delete a release label locally and optionally retract it from a remote.

    Deletion removes only the named pointer — the underlying commit and
    snapshot remain in the content-addressed object store forever.  They
    are still fully reproducible by their SHA-256; only the label is gone.

    Published releases require explicit confirmation (type the tag name) so
    accidental retractions of stable releases are hard to do silently.
    """
    tag: str = args.tag
    yes: bool = args.yes
    remote: str = args.remote or ""

    root = require_repo()
    repo_id = _repo_id(root)

    release = get_release_for_tag(root, repo_id, tag)
    if release is None:
        print(f"❌ Release '{sanitize_display(tag)}' not found locally.", file=sys.stderr)
        raise SystemExit(ExitCode.NOT_FOUND)

    # Published releases need a stronger confirmation — type the tag name.
    if not release.is_draft and not yes:
        print(
            f"⚠️  '{sanitize_display(tag)}' is a published release. "
            "Deleting it removes the label; the underlying commit is unaffected."
        )
        answer = input(f"Type the tag name to confirm deletion: ").strip()
        if answer != tag:
            print("Aborted.")
            return
    elif release.is_draft and not yes:
        answer = input(f"Delete draft release '{sanitize_display(tag)}'? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return

    # Retract from the remote first so a local-only failure doesn't leave
    # the local record orphaned relative to the remote.
    if remote:
        from muse.cli.config import get_auth_token
        from muse.core.transport import TransportError, make_transport

        url = _resolve_remote_url(root, remote)
        token = get_auth_token(root, url)
        transport = make_transport(url)
        try:
            transport.delete_release_remote(url, token, tag)
        except TransportError as exc:
            print(f"❌ Remote retraction failed: {exc}", file=sys.stderr)
            raise SystemExit(ExitCode.REMOTE_ERROR)
        print(f"✅ Release {tag} retracted from {sanitize_display(remote)}.")

    deleted = delete_release(root, repo_id, release.release_id)
    if deleted:
        print(f"✅ Release {tag} deleted locally.")
    else:
        print(f"❌ Local release '{sanitize_display(tag)}' could not be deleted.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the release subcommand."""
    parser = subparsers.add_parser(
        "release",
        help="Create and manage versioned releases.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subs.required = True

    # --- add ---
    add_p = subs.add_parser("add", help="Create a local release at HEAD.")
    add_p.add_argument("tag", help="Version tag (semver, e.g. v1.2.0 or v1.3.0-beta.1).")
    add_p.add_argument("--title", default="", help="Release title.")
    add_p.add_argument("--body", default="", help="Release body / description.")
    add_p.add_argument(
        "--channel",
        default="",
        choices=sorted(_CHANNELS),
        help="Distribution channel (default: inferred from semver pre-release label).",
    )
    add_p.add_argument("--draft", action="store_true", help="Mark release as a draft.")
    add_p.add_argument("--ref", default=None, help="Commit ID or branch (default: HEAD).")
    add_p.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    add_p.set_defaults(func=run_add)

    # --- list ---
    list_p = subs.add_parser("list", help="List releases.")
    list_p.add_argument(
        "--channel",
        default="",
        choices=list(sorted(_CHANNELS)) + [""],
        help="Filter by channel.",
    )
    list_p.add_argument("--include-drafts", action="store_true", help="Show draft releases.")
    list_p.add_argument("--remote", default="", help="Fetch from this remote (e.g. origin).")
    list_p.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    list_p.set_defaults(func=run_list)

    # --- show ---
    show_p = subs.add_parser("show", help="Inspect a single release.")
    show_p.add_argument("tag", help="Version tag (e.g. v1.2.0).")
    show_p.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    show_p.set_defaults(func=run_show)

    # --- push ---
    push_p = subs.add_parser("push", help="Push a release to a remote.")
    push_p.add_argument("tag", help="Version tag to push (e.g. v1.2.0).")
    push_p.add_argument("--remote", default="origin", help="Remote name (default: origin).")
    push_p.set_defaults(func=run_push)

    # --- delete ---
    del_p = subs.add_parser(
        "delete",
        help="Delete a release label locally and optionally retract it from a remote.",
    )
    del_p.add_argument("tag", help="Version tag to delete (e.g. v1.2.0).")
    del_p.add_argument("--remote", default="", help="Also retract from this remote (e.g. origin).")
    del_p.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation (drafts) or tag-name confirmation (published).",
    )
    del_p.set_defaults(func=run_delete)
