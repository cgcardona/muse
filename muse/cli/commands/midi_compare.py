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

import argparse
import json
import logging
import pathlib
import sys

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
        print(f"❌ Commit '{ref}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    result = load_track(root, commit.commit_id, track)
    if result is None:
        print(f"❌ Track '{track}' not found in commit '{ref}'.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    return result[0], result[1], commit.commit_id[:8]


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the compare subcommand."""
    parser = subparsers.add_parser("compare", help="Compare two MIDI snapshots across musical dimensions.", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("ref_a", metavar="REF_A", help="First commit reference (older).")
    parser.add_argument("ref_b", nargs="?", metavar="REF_B", default=None, help="Second commit reference. Omit to compare REF_A against the working tree.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Compare two MIDI snapshots across musical dimensions.

    ``muse compare`` goes beyond raw note diffs — it shows how key, density,
    swing, syncopation, and quantisation changed between two points in history.

    For agents: use this after a merge to verify that the merged result
    preserves the intended musical character of both parent branches.
    """
    track: str = args.track
    ref_a: str = args.ref_a
    ref_b: str | None = args.ref_b
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    notes_a, _tpb_a, label_a = _load(root, track, ref_a, repo_id, branch)

    if ref_b is not None:
        notes_b, _tpb_b, label_b = _load(root, track, ref_b, repo_id, branch)
    else:
        raw_b = load_track_from_workdir(root, track)
        if raw_b is None:
            print(f"❌ Track '{track}' not found in working tree.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
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
        print(json.dumps({
            "track": track,
            "a": {"ref": ref_a, "sha": label_a, "rhythm": rh_a, "key": key_a, "density_avg": round(avg_dens_a, 2)},
            "b": {"ref": ref_b or "working tree", "sha": label_b, "rhythm": rh_b, "key": key_b, "density_avg": round(avg_dens_b, 2)},
        }, indent=2))
        return

    print(f"\nSemantic comparison: {track}")
    print(f"A: {ref_a} ({label_a})   B: {ref_b or 'working tree'} ({label_b})\n")
    print(f"  {'Dimension':<22}  {'A':>16}  {'B':>16}  {'Δ':<30}")
    print("  " + "─" * 90)

    def row(dim: str, va: str, vb: str, delta: str) -> None:
        print(f"  {dim:<22}  {va:>16}  {vb:>16}  {delta:<30}")

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
