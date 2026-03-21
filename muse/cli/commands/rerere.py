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

import json
import logging
import pathlib

import typer

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
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()

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
    if color:
        status = (
            typer.style(status, fg=typer.colors.GREEN, bold=True)
            if rec.has_resolution
            else typer.style(status, fg=typer.colors.YELLOW)
        )
    ts = rec.recorded_at.strftime("%Y-%m-%d %H:%M")
    return f"{rec.fingerprint[:12]}  {status}  {ts}  {rec.path}"


# ---------------------------------------------------------------------------
# Default action: apply cached resolutions
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def rerere(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be auto-resolved without writing files."
    ),
    fmt: str = typer.Option(
        "text", "--format", "-f", help="Output format: text or json."
    ),
) -> None:
    """Apply cached conflict resolutions to the current working tree.

    Reads ``.muse/MERGE_STATE.json`` to discover which paths are in conflict,
    looks up each path's fingerprint in ``.muse/rr-cache/``, and restores any
    cached resolution blobs to the working tree.  Paths with no cached
    resolution are left unchanged and reported for manual attention.

    Run after ``muse merge`` exits with conflicts, or let ``muse merge`` invoke
    this automatically via the ``--rerere-autoupdate`` flag.
    """
    if ctx.invoked_subcommand is not None:
        return

    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    state = read_merge_state(root)
    if state is None or not state.conflict_paths:
        typer.echo("No merge in progress — nothing for rerere to do.")
        return

    manifests = _load_conflict_manifests(root)
    if manifests is None:
        typer.echo("❌ MERGE_STATE.json is incomplete — cannot load conflict manifests.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
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
        typer.echo(
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
        typer.echo(f"  {prefix}: {p}")
    for p in remaining:
        typer.echo(f"  ⏳ needs manual resolution: {p}")

    if not auto_resolved and not remaining:
        typer.echo("No conflicting paths found.")


# ---------------------------------------------------------------------------
# record — write preimages without replaying
# ---------------------------------------------------------------------------


@app.command("record")
def rerere_record(
    fmt: str = typer.Option(
        "text", "--format", "-f", help="Output format: text or json."
    ),
) -> None:
    """Record preimages for current conflicts without applying cached resolutions.

    Writes a preimage entry to ``.muse/rr-cache/`` for every conflicting path
    in MERGE_STATE.  The preimage is the fingerprint of the two conflicting
    blob IDs; the resolution is saved later when the user commits.

    Idempotent: re-recording an already-known conflict is a no-op.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    state = read_merge_state(root)
    if state is None or not state.conflict_paths:
        typer.echo("No merge in progress — nothing to record.")
        return

    manifests = _load_conflict_manifests(root)
    if manifests is None:
        typer.echo("❌ MERGE_STATE.json is incomplete — cannot load conflict manifests.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
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
        typer.echo(json.dumps({"recorded": recorded, "skipped": skipped}, indent=2))
        return

    for p in recorded:
        typer.echo(f"  📝 preimage recorded: {p}")
    for p in skipped:
        typer.echo(f"  ⚠️  skipped (one side deleted): {p}")


# ---------------------------------------------------------------------------
# status — show cached resolutions vs current conflicts
# ---------------------------------------------------------------------------


@app.command("status")
def rerere_status(
    fmt: str = typer.Option(
        "text", "--format", "-f", help="Output format: text or json."
    ),
) -> None:
    """Show cached rerere resolutions and their match against current conflicts.

    Lists every entry in ``.muse/rr-cache/``, indicating whether a resolution
    has been recorded.  When a merge is in progress, also marks which cached
    resolutions match the current conflict set.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

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

    is_tty = typer.get_app_dir  # presence check — colour only in a real terminal
    color = False
    try:
        import sys
        color = sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        color = False

    if fmt == "json":
        typer.echo(
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
        typer.echo("No rerere records found.")
        return

    typer.echo(f"{'fingerprint':14}  {'status':16}  {'recorded':16}  path")
    typer.echo("-" * 72)
    for rec in records:
        marker = " ◀ current" if rec.fingerprint in current_fps else ""
        typer.echo(_fmt_record(rec, color=color) + marker)


# ---------------------------------------------------------------------------
# forget — remove cached resolution for specific paths
# ---------------------------------------------------------------------------


@app.command("forget")
def rerere_forget(
    paths: list[str] = typer.Argument(
        ..., help="Workspace-relative paths whose rerere resolution should be removed."
    ),
    fmt: str = typer.Option(
        "text", "--format", "-f", help="Output format: text or json."
    ),
) -> None:
    """Remove the cached rerere resolution for one or more conflict paths.

    Computes the fingerprint for each PATH against the current MERGE_STATE and
    removes its rr-cache entry.  Run this when a recorded resolution is wrong
    and you want to force manual resolution next time.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    state = read_merge_state(root)
    if state is None or not state.conflict_paths:
        typer.echo("❌ No merge in progress — cannot determine conflict fingerprints.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifests = _load_conflict_manifests(root)
    if manifests is None:
        typer.echo("❌ MERGE_STATE.json is incomplete.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
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
        typer.echo(json.dumps({"forgotten": forgotten, "not_found": not_found}, indent=2))
        return

    for p in forgotten:
        typer.echo(f"  🗑  forgot: {p}")
    for p in not_found:
        typer.echo(f"  ⚠️  no record found: {p}")


# ---------------------------------------------------------------------------
# clear — remove all cached resolutions
# ---------------------------------------------------------------------------


@app.command("clear")
def rerere_clear(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt."
    ),
    fmt: str = typer.Option(
        "text", "--format", "-f", help="Output format: text or json."
    ),
) -> None:
    """Remove all cached rerere resolutions.

    Deletes the entire ``.muse/rr-cache/`` directory contents.  This is
    irreversible — all recorded resolutions will be lost.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    if not yes:
        cache = rr_cache_dir(root)
        count = sum(1 for e in cache.iterdir() if e.is_dir()) if cache.exists() else 0
        if count == 0:
            typer.echo("rr-cache is already empty.")
            return
        confirmed = typer.confirm(
            f"This will permanently delete {count} rerere record(s). Continue?"
        )
        if not confirmed:
            typer.echo("Aborted.")
            return

    removed = clear_all(root)

    if fmt == "json":
        typer.echo(json.dumps({"removed": removed}))
        return

    typer.echo(f"✅ Cleared {removed} rerere record(s).")


# ---------------------------------------------------------------------------
# gc — garbage-collect stale preimage-only entries
# ---------------------------------------------------------------------------


@app.command("gc")
def rerere_gc(
    fmt: str = typer.Option(
        "text", "--format", "-f", help="Output format: text or json."
    ),
) -> None:
    """Remove preimage-only rerere entries older than 60 days.

    Keeps all entries that have a resolution saved (regardless of age).
    Removes entries where the user never committed a resolution — these are
    conflicts that were abandoned or resolved in another way.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    removed = gc_stale(root)

    if fmt == "json":
        typer.echo(json.dumps({"removed": removed}))
        return

    if removed:
        typer.echo(f"✅ gc: removed {removed} stale preimage-only entry(s).")
    else:
        typer.echo("gc: nothing to remove.")
