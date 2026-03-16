"""muse emotion-diff — compare emotion vectors between two commits."""
from __future__ import annotations

import json
import logging
import pathlib
from typing import Optional

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.plugins.music.services.muse_emotion_diff import compute_emotion_diff

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return json.loads((root / ".muse" / "repo.json").read_text())["repo_id"]


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def emotion_diff(
    ctx: typer.Context,
    commit_a: Optional[str] = typer.Argument(None, help="Base commit (default: HEAD~1)."),
    commit_b: Optional[str] = typer.Argument(None, help="Target commit (default: HEAD)."),
    track: Optional[str] = typer.Option(None, "--track", help="Scope to instrument track."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Compare emotion vectors between two commits."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    try:
        result = compute_emotion_diff(
            root=root,
            repo_id=repo_id,
            branch=branch,
            commit_a=commit_a,
            commit_b=commit_b,
            track=track,
        )
    except Exception as exc:
        typer.echo(f"❌ emotion-diff failed: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if json_out:
        import json as json_mod
        typer.echo(json_mod.dumps(result, indent=2, default=str))
        return

    a_id = result.get("commit_a_id", "")[:8]
    b_id = result.get("commit_b_id", "")[:8]
    typer.echo(f"Emotion diff — {a_id} → {b_id}")
    typer.echo(f"Source: {result.get('source', 'unknown')}\n")

    a_label = result.get("commit_a_emotion", "—")
    b_label = result.get("commit_b_emotion", "—")
    typer.echo(f"Commit A ({a_id}): {a_label}")
    typer.echo(f"Commit B ({b_id}): {b_label}\n")

    dimensions = result.get("dimensions", {})
    if dimensions:
        typer.echo(f"{'Dimension':<12} {'Commit A':<10} {'Commit B':<10} Delta")
        typer.echo("-" * 44)
        for dim, values in sorted(dimensions.items()):
            a_val = values.get("a", 0.0)
            b_val = values.get("b", 0.0)
            delta = b_val - a_val
            sign = "+" if delta >= 0 else ""
            typer.echo(f"{dim:<12} {a_val:<10.4f} {b_val:<10.4f} {sign}{delta:.4f}")
