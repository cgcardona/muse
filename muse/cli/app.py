"""Muse CLI — entry point for the ``muse`` console script.

Core VCS commands::

    init        status      log         commit      diff
    show        branch      checkout    merge       reset
    revert      stash       cherry-pick tag         domains

Music-domain semantic commands (impossible in Git)::

    notes           list every note in a MIDI track as musical notation
    note-log        note-level commit history for a track
    note-blame      per-bar attribution — which commit wrote these notes?
    harmony         chord analysis and key detection
    piano-roll      ASCII piano roll visualization
    note-hotspots   bar-level churn leaderboard
    velocity-profile dynamic range and velocity histogram
    transpose       transpose all notes by N semitones (agent command)
    mix             combine two MIDI tracks into one (agent command)
"""
from __future__ import annotations

import typer

from muse.cli.commands import (
    attributes,
    branch,
    cherry_pick,
    checkout,
    commit,
    diff,
    domains,
    harmony,
    init,
    log,
    merge,
    mix,
    note_blame,
    note_hotspots,
    note_log,
    notes,
    piano_roll,
    reset,
    revert,
    show,
    stash,
    status,
    tag,
    transpose,
    velocity_profile,
)

cli = typer.Typer(
    name="muse",
    help="Muse — domain-agnostic version control for multidimensional state.",
    no_args_is_help=True,
)

cli.add_typer(attributes.app,   name="attributes",  help="Display .museattributes merge-strategy rules.")
cli.add_typer(init.app,         name="init",        help="Initialise a new Muse repository.")
cli.add_typer(commit.app,       name="commit",      help="Record the current working tree as a new version.")
cli.add_typer(status.app,       name="status",      help="Show working-tree drift against HEAD.")
cli.add_typer(log.app,          name="log",         help="Display commit history.")
cli.add_typer(diff.app,         name="diff",        help="Compare working tree against HEAD, or two commits.")
cli.add_typer(show.app,         name="show",        help="Inspect a commit: metadata, diff, files.")
cli.add_typer(branch.app,       name="branch",      help="List, create, or delete branches.")
cli.add_typer(checkout.app,     name="checkout",    help="Switch branches or restore working tree from a commit.")
cli.add_typer(merge.app,        name="merge",       help="Three-way merge a branch into the current branch.")
cli.add_typer(reset.app,        name="reset",       help="Move HEAD to a prior commit.")
cli.add_typer(revert.app,       name="revert",      help="Create a new commit that undoes a prior commit.")
cli.add_typer(cherry_pick.app,  name="cherry-pick", help="Apply a specific commit's changes on top of HEAD.")
cli.add_typer(stash.app,        name="stash",       help="Shelve and restore uncommitted changes.")
cli.add_typer(tag.app,              name="tag",              help="Attach and query semantic tags on commits.")
cli.add_typer(domains.app,          name="domains",          help="Domain plugin dashboard — list capabilities and scaffold new domains.")
cli.add_typer(notes.app,            name="notes",            help="[music] List every note in a MIDI track as musical notation.")
cli.add_typer(note_log.app,         name="note-log",         help="[music] Note-level commit history — which notes were added or removed in each commit.")
cli.add_typer(note_blame.app,       name="note-blame",       help="[music] Per-bar attribution — which commit introduced the notes in this bar?")
cli.add_typer(harmony.app,          name="harmony",          help="[music] Chord analysis and key detection from MIDI note content.")
cli.add_typer(piano_roll.app,       name="piano-roll",       help="[music] ASCII piano roll visualization of a MIDI track.")
cli.add_typer(note_hotspots.app,    name="note-hotspots",    help="[music] Bar-level churn leaderboard — which bars change most across commits.")
cli.add_typer(velocity_profile.app, name="velocity-profile", help="[music] Dynamic range and velocity histogram for a MIDI track.")
cli.add_typer(transpose.app,        name="transpose",        help="[music] Transpose all notes in a MIDI track by N semitones.")
cli.add_typer(mix.app,              name="mix",              help="[music] Combine notes from two MIDI tracks into a single output track.")


if __name__ == "__main__":
    cli()
