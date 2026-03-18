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

Code-domain semantic commands — Phase 1 (impossible in Git)::

    symbols         list every semantic symbol in a snapshot
    symbol-log      track a single symbol through commit history
    detect-refactor report semantic refactoring operations across commits

Code-domain semantic commands — Phase 2 (paradigm shift)::

    grep            search the symbol graph by name / kind / language
    blame           show which commit last touched a specific symbol
    hotspots        symbol churn leaderboard — which functions change most
    stable          symbol stability leaderboard — your bedrock
    coupling        file co-change analysis — hidden dependencies
    compare         semantic comparison between any two historical snapshots
    languages       language and symbol-type breakdown
    patch           surgical semantic patch — modify exactly one symbol (all-language validation)
    query           symbol graph predicate DSL — SQL for your codebase (--all-commits mode)

Code-domain semantic commands — Phase 3 (gap-closers)::

    deps            import graph + Python call-graph with --reverse
    find-symbol     cross-commit, cross-branch content_id / name search

Code-domain semantic commands — Phase 4 (call-graph tier)::

    impact          transitive blast-radius — what breaks if this function changes?
    dead            dead code detection — symbols with no callers and no importers
    coverage        class interface call-coverage — which methods are actually used?

Code-domain semantic commands — Phase 5 (query v2 + temporal)::

    query           predicate DSL v2 — OR, NOT, grouping, new fields, schema_version
    query-history   temporal symbol search across a commit range

Code-domain semantic commands — Phase 6 (provenance + topology)::

    lineage         full provenance chain of a symbol through commit history
    api-surface     public API surface and how it changed between commits
    codemap         semantic topology — cycles, centrality, boundary files
    clones          find exact and near-duplicate symbols across the snapshot
    checkout-symbol restore a historical version of a specific symbol
    semantic-cherry-pick  cherry-pick named symbols from a historical commit
"""
from __future__ import annotations

import typer

from muse.cli.commands import (
    api_surface,
    attributes,
    blame,
    branch,
    cherry_pick,
    checkout,
    checkout_symbol,
    clones,
    codemap,
    commit,
    compare,
    coupling,
    coverage,
    dead,
    deps,
    detect_refactor,
    diff,
    domains,
    find_symbol,
    grep,
    harmony,
    hotspots,
    impact,
    index_rebuild,
    init,
    languages,
    lineage,
    log,
    merge,
    mix,
    note_blame,
    note_hotspots,
    note_log,
    notes,
    patch,
    piano_roll,
    query,
    query_history,
    reset,
    revert,
    semantic_cherry_pick,
    show,
    stable,
    stash,
    status,
    symbol_log,
    symbols,
    tag,
    transpose,
    velocity_profile,
)

cli = typer.Typer(
    name="muse",
    help="Muse — domain-agnostic version control for multidimensional state.",
    no_args_is_help=True,
)

# Core VCS
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
cli.add_typer(tag.app,          name="tag",         help="Attach and query semantic tags on commits.")
cli.add_typer(domains.app,      name="domains",     help="Domain plugin dashboard — list capabilities and scaffold new domains.")

# Music-domain commands
cli.add_typer(notes.app,            name="notes",            help="[music] List every note in a MIDI track as musical notation.")
cli.add_typer(note_log.app,         name="note-log",         help="[music] Note-level commit history — which notes were added or removed in each commit.")
cli.add_typer(note_blame.app,       name="note-blame",       help="[music] Per-bar attribution — which commit introduced the notes in this bar?")
cli.add_typer(harmony.app,          name="harmony",          help="[music] Chord analysis and key detection from MIDI note content.")
cli.add_typer(piano_roll.app,       name="piano-roll",       help="[music] ASCII piano roll visualization of a MIDI track.")
cli.add_typer(note_hotspots.app,    name="note-hotspots",    help="[music] Bar-level churn leaderboard — which bars change most across commits.")
cli.add_typer(velocity_profile.app, name="velocity-profile", help="[music] Dynamic range and velocity histogram for a MIDI track.")
cli.add_typer(transpose.app,        name="transpose",        help="[music] Transpose all notes in a MIDI track by N semitones.")
cli.add_typer(mix.app,              name="mix",              help="[music] Combine notes from two MIDI tracks into a single output track.")

# Code-domain commands
cli.add_typer(symbols.app,         name="symbols",          help="[code] List every semantic symbol (function, class, method…) in a snapshot.")
cli.add_typer(symbol_log.app,      name="symbol-log",       help="[code] Track a single symbol through the full commit history.")
cli.add_typer(detect_refactor.app, name="detect-refactor",  help="[code] Detect semantic refactoring operations (renames, moves, extractions) across commits.")
cli.add_typer(grep.app,            name="grep",             help="[code] Search the symbol graph by name, kind, or language — not file text.")
cli.add_typer(blame.app,           name="blame",            help="[code] Show which commit last touched a specific symbol (function, class, method).")
cli.add_typer(hotspots.app,        name="hotspots",         help="[code] Symbol churn leaderboard — which functions change most often.")
cli.add_typer(stable.app,          name="stable",           help="[code] Symbol stability leaderboard — the bedrock of your codebase.")
cli.add_typer(coupling.app,        name="coupling",         help="[code] File co-change analysis — discover hidden semantic dependencies.")
cli.add_typer(compare.app,         name="compare",          help="[code] Deep semantic comparison between any two historical snapshots.")
cli.add_typer(languages.app,       name="languages",        help="[code] Language and symbol-type breakdown of a snapshot.")
cli.add_typer(patch.app,           name="patch",            help="[code] Surgical semantic patch — modify exactly one named symbol (all-language syntax validation).")
cli.add_typer(query.app,           name="query",            help="[code] Symbol graph predicate DSL v2 — OR/NOT/grouping, --all-commits temporal search.")
cli.add_typer(query_history.app,   name="query-history",    help="[code] Temporal symbol search — first seen, last seen, change count across a commit range.")
cli.add_typer(deps.app,            name="deps",             help="[code] Import graph + Python call-graph; --reverse for callers/importers.")
cli.add_typer(find_symbol.app,     name="find-symbol",      help="[code] Cross-commit, cross-branch symbol search by hash, name, or kind.")
cli.add_typer(impact.app,          name="impact",           help="[code] Transitive blast-radius — every caller affected if this symbol changes.")
cli.add_typer(dead.app,            name="dead",             help="[code] Dead code candidates — symbols with no callers and no importers.")
cli.add_typer(coverage.app,        name="coverage",         help="[code] Class interface call-coverage — which methods are actually called?")
cli.add_typer(lineage.app,         name="lineage",          help="[code] Full provenance chain of a symbol — created, renamed, moved, copied, deleted.")
cli.add_typer(api_surface.app,     name="api-surface",      help="[code] Public API surface at a commit; --diff to show added/removed/changed symbols.")
cli.add_typer(codemap.app,         name="codemap",          help="[code] Semantic topology — module sizes, import cycles, centrality, boundary files.")
cli.add_typer(clones.app,          name="clones",           help="[code] Find exact and near-duplicate symbols (body_hash / signature_id clusters).")
cli.add_typer(checkout_symbol.app, name="checkout-symbol",  help="[code] Restore a historical version of one symbol into the working tree.")
cli.add_typer(semantic_cherry_pick.app, name="semantic-cherry-pick", help="[code] Cherry-pick named symbols from a historical commit into the working tree.")
cli.add_typer(index_rebuild.app,   name="index",            help="[code] Manage local indexes: status, rebuild symbol_history / hash_occurrence.")


if __name__ == "__main__":
    cli()
