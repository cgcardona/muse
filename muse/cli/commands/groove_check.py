"""muse groove-check — analyze rhythmic groove across commit history.

Detects which commit disrupted rhythmic consistency by measuring note-onset
deviation from the quantization grid across a commit range.
"""
from __future__ import annotations

import json
import logging
import pathlib
from typing import Optional

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.plugins.music.services.muse_groove_check import (
    GrooveCheckResult,
    analyze_groove_range,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return json.loads((root / ".muse" / "repo.json").read_text())["repo_id"]


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def groove_check(
    ctx: typer.Context,
    commit_range: Optional[str] = typer.Argument(None, help="Commit range (e.g. HEAD~5..HEAD)."),
    track: Optional[str] = typer.Option(None, "--track", help="Scope to a specific instrument track."),
    section: Optional[str] = typer.Option(None, "--section", help="Scope to a musical section."),
    threshold: float = typer.Option(0.1, "--threshold", help="Drift threshold in beats."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Analyze rhythmic groove drift across commit history."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    try:
        results = analyze_groove_range(
            root=root,
            repo_id=repo_id,
            branch=branch,
            commit_range=commit_range,
            track=track,
            section=section,
            threshold=threshold,
        )
    except Exception as exc:
        typer.echo(f"❌ groove-check failed: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if json_out:
        import json as json_mod
        typer.echo(json_mod.dumps([r.__dict__ for r in results], indent=2))
        return

    flagged = [r for r in results if r.status != "OK"]
    typer.echo(f"Groove-check — {len(results)} commits, threshold {threshold} beats\n")
    typer.echo(f"{'Commit':<10} {'Groove Score':<14} {'Drift Δ':<10} Status")
    typer.echo("-" * 46)
    for r in results:
        typer.echo(f"{r.commit_id[:8]:<10} {r.groove_score:<14.4f} {r.drift_delta:<10.4f} {r.status}")

    if flagged:
        worst = max(flagged, key=lambda r: r.drift_delta)
        typer.echo(f"\nFlagged: {len(flagged)} / {len(results)} commits (worst: {worst.commit_id[:8]})")
    else:
        typer.echo(f"\nAll {len(results)} commits within threshold.")
