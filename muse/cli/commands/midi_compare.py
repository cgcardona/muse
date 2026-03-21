"""muse compare — semantic comparison between two MIDI snapshots.

Diffs two commits (or a commit and the working tree) on multiple musical
dimensions: note count, harmonic content, rhythmic feel, pitch range, and
density.  Where ``muse diff`` shows note-level insertions/deletions, this
command shows the *musical meaning* of what changed.

Usage::

    muse compare tracks/melody.mid HEAD~1 HEAD
    muse compare tracks/piano.mid HEAD~3 HEAD~1
    muse compare tracks/bass.mid HEAD --working-tree
    muse compare tracks/chords.mid HEAD~1 HEAD --json

Output::

    Semantic comparison: tracks/melody.mid
    A: HEAD~1 (cb4afaed)   B: HEAD (9f3a12e7)

    Dimension          A              B              Δ
    ──────────────────────────────────────────────────────────
    Notes              48             56             +8
    Bars               16             16              0
    Key                G major        G major         =
    Density avg        3.0/beat       3.5/beat       +0.5
    Swing ratio        1.00           1.38           +0.38 (swing added)
    Syncopation        0.12           0.31           +0.19 (more syncopated)
    Quantisation       0.98           0.84           -0.14 (more human)
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.midi._analysis import analyze_rhythm, analyze_density
from muse.plugins.midi._query import (
    NoteInfo,
    key_signature_guess,
    load_track,
    load_track_from_workdir,
)

logger = logging.getLogger(__name__)
app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _load(
    root: pathlib.Path,
    track: str,
    ref: str,
    repo_id: str,
    branch: str,
) -> tuple[list[NoteInfo], int, str]:
    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    result = load_track(root, commit.commit_id, track)
    if result is None:
        typer.echo(f"❌ Track '{track}' not found in commit '{ref}'.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    return result[0], result[1], commit.commit_id[:8]


@app.callback(invoke_without_command=True)
def compare(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref_a: str = typer.Argument(..., metavar="REF_A", help="First commit reference (older)."),
    ref_b: str | None = typer.Argument(
        None, metavar="REF_B",
        help="Second commit reference. Omit to compare REF_A against the working tree.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Compare two MIDI snapshots across musical dimensions.

    ``muse compare`` goes beyond raw note diffs — it shows how key, density,
    swing, syncopation, and quantisation changed between two points in history.

    For agents: use this after a merge to verify that the merged result
    preserves the intended musical character of both parent branches.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    notes_a, _tpb_a, label_a = _load(root, track, ref_a, repo_id, branch)

    if ref_b is not None:
        notes_b, _tpb_b, label_b = _load(root, track, ref_b, repo_id, branch)
    else:
        raw_b = load_track_from_workdir(root, track)
        if raw_b is None:
            typer.echo(f"❌ Track '{track}' not found in working tree.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        notes_b, _tpb_b = raw_b
        label_b = "working tree"

    rh_a = analyze_rhythm(notes_a)
    rh_b = analyze_rhythm(notes_b)
    dens_a = analyze_density(notes_a)
    dens_b = analyze_density(notes_b)
    avg_dens_a = sum(d["notes_per_beat"] for d in dens_a) / max(len(dens_a), 1)
    avg_dens_b = sum(d["notes_per_beat"] for d in dens_b) / max(len(dens_b), 1)
    key_a = key_signature_guess(notes_a)
    key_b = key_signature_guess(notes_b)

    if as_json:
        typer.echo(json.dumps({
            "track": track,
            "a": {"ref": ref_a, "sha": label_a, "rhythm": rh_a, "key": key_a, "density_avg": round(avg_dens_a, 2)},
            "b": {"ref": ref_b or "working tree", "sha": label_b, "rhythm": rh_b, "key": key_b, "density_avg": round(avg_dens_b, 2)},
        }, indent=2))
        return

    typer.echo(f"\nSemantic comparison: {track}")
    typer.echo(f"A: {ref_a} ({label_a})   B: {ref_b or 'working tree'} ({label_b})\n")
    typer.echo(f"  {'Dimension':<22}  {'A':>16}  {'B':>16}  {'Δ':<30}")
    typer.echo("  " + "─" * 90)

    def row(dim: str, va: str, vb: str, delta: str) -> None:
        typer.echo(f"  {dim:<22}  {va:>16}  {vb:>16}  {delta:<30}")

    row("Notes", str(rh_a["total_notes"]), str(rh_b["total_notes"]),
        f"{rh_b['total_notes'] - rh_a['total_notes']:+d}")
    row("Bars", str(rh_a["bars"]), str(rh_b["bars"]),
        f"{rh_b['bars'] - rh_a['bars']:+d}")
    row("Key", key_a, key_b, "=" if key_a == key_b else f"{key_a} → {key_b}")
    row("Density avg", f"{avg_dens_a:.2f}/beat", f"{avg_dens_b:.2f}/beat",
        f"{avg_dens_b - avg_dens_a:+.2f}")
    row("Swing ratio", f"{rh_a['swing_ratio']:.3f}", f"{rh_b['swing_ratio']:.3f}",
        f"{rh_b['swing_ratio'] - rh_a['swing_ratio']:+.3f}")
    row("Syncopation", f"{rh_a['syncopation_score']:.3f}", f"{rh_b['syncopation_score']:.3f}",
        f"{rh_b['syncopation_score'] - rh_a['syncopation_score']:+.3f}")
    row("Quantisation", f"{rh_a['quantization_score']:.3f}", f"{rh_b['quantization_score']:.3f}",
        f"{rh_b['quantization_score'] - rh_a['quantization_score']:+.3f}")
    row("Subdivision", rh_a["dominant_subdivision"], rh_b["dominant_subdivision"],
        "=" if rh_a["dominant_subdivision"] == rh_b["dominant_subdivision"] else "changed")
