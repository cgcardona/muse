"""Muse CLI — entry point for the ``muse`` console script.

Three-tier command architecture
--------------------------------

**Tier 1 — Plumbing** (``muse plumbing …``)
    Machine-readable, JSON-outputting, pipeable primitives.  Stable API used
    by scripts, agents, and Tier 2 porcelain commands.

**Tier 2 — Core Porcelain** (top-level ``muse …``)
    Human and agent VCS commands.  Each command delegates business logic to
    ``muse.core.*``; no domain-specific code lives here.

**Tier 3 — Semantic Porcelain** (``muse midi …``, ``muse code …``, ``muse coord …``)
    Domain-specific commands that interpret multidimensional state.  Each
    sub-namespace is served by the corresponding ``muse/plugins/`` plugin.

Tier 1 — Plumbing commands::

    muse plumbing hash-object     SHA-256 a file; optionally store it
    muse plumbing cat-object      Emit raw bytes of a stored object
    muse plumbing rev-parse       Resolve branch/HEAD/prefix → commit_id
    muse plumbing ls-files        List tracked files and object IDs
    muse plumbing read-commit     Emit full commit JSON
    muse plumbing read-snapshot   Emit full snapshot JSON
    muse plumbing commit-tree     Create a commit from an explicit snapshot_id
    muse plumbing update-ref      Move a branch HEAD to a specific commit
    muse plumbing commit-graph    Emit commit DAG as JSON
    muse plumbing pack-objects    Build a PackBundle JSON to stdout
    muse plumbing unpack-objects  Read PackBundle JSON from stdin, write to store
    muse plumbing ls-remote       List remote branch heads

Tier 2 — Core Porcelain commands::

    init        status      log         commit      diff
    show        branch      checkout    merge       reset
    revert      stash       cherry-pick tag         domains
    attributes  remote      clone       fetch       pull
    push        check       annotate

Tier 3 — MIDI domain commands (``muse midi …``)::

    notes           note-log        note-blame      harmony
    piano-roll      hotspots        velocity-profile
    transpose       mix             query           check

Tier 3 — Code domain commands (``muse code …``)::

    symbols         symbol-log      detect-refactor  grep
    blame           hotspots        stable           coupling
    compare         languages       patch            query
    query-history   deps            find-symbol      impact
    dead            coverage        lineage          api-surface
    codemap         clones          checkout-symbol  semantic-cherry-pick
    index           breakage        invariants       check

Tier 3 — Coordination commands (``muse coord …``)::

    reserve     intent      forecast
    plan-merge  shard       reconcile
"""

from __future__ import annotations

import typer

from muse.cli.commands import (
    annotate,
    api_surface,
    attributes,
    blame,
    branch,
    cherry_pick,
    checkout,
    checkout_symbol,
    check,
    clone,
    clones,
    codemap,
    code_check,
    code_query,
    commit,
    compare,
    coupling,
    coverage,
    dead,
    deps,
    detect_refactor,
    diff,
    domains,
    fetch,
    find_symbol,
    forecast,
    grep,
    harmony,
    hotspots,
    impact,
    index_rebuild,
    init,
    intent,
    invariants,
    languages,
    lineage,
    log,
    merge,
    mix,
    midi_check,
    midi_query,
    note_blame,
    note_hotspots,
    note_log,
    notes,
    patch,
    piano_roll,
    plan_merge,
    pull,
    push,
    query,
    query_history,
    reconcile,
    remote,
    reserve,
    reset,
    revert,
    semantic_cherry_pick,
    shard,
    show,
    stable,
    stash,
    status,
    symbol_log,
    symbols,
    tag,
    breakage,
    transpose,
    velocity_profile,
)
from muse.cli.commands.plumbing import (
    cat_object,
    commit_graph,
    commit_tree,
    hash_object,
    ls_files,
    ls_remote,
    pack_objects,
    read_commit,
    read_snapshot,
    rev_parse,
    unpack_objects,
    update_ref,
)

# ---------------------------------------------------------------------------
# Root CLI
# ---------------------------------------------------------------------------

cli = typer.Typer(
    name="muse",
    help="Muse — domain-agnostic version control for multidimensional state.",
    no_args_is_help=True,
)

# ---------------------------------------------------------------------------
# Tier 1 — Plumbing sub-namespace
# ---------------------------------------------------------------------------

plumbing_cli = typer.Typer(
    name="plumbing",
    help="[Tier 1] Machine-readable plumbing commands. JSON output, pipeable, stable API.",
    no_args_is_help=True,
)

plumbing_cli.add_typer(hash_object.app,     name="hash-object",    help="SHA-256 a file; optionally store it in the object store.")
plumbing_cli.add_typer(cat_object.app,      name="cat-object",     help="Emit raw bytes of a stored object to stdout.")
plumbing_cli.add_typer(rev_parse.app,       name="rev-parse",      help="Resolve branch/HEAD/SHA prefix → full commit_id.")
plumbing_cli.add_typer(ls_files.app,        name="ls-files",       help="List all tracked files and their object IDs in a snapshot.")
plumbing_cli.add_typer(read_commit.app,     name="read-commit",    help="Emit full commit metadata as JSON.")
plumbing_cli.add_typer(read_snapshot.app,   name="read-snapshot",  help="Emit full snapshot metadata and manifest as JSON.")
plumbing_cli.add_typer(commit_tree.app,     name="commit-tree",    help="Create a commit from an explicit snapshot ID.")
plumbing_cli.add_typer(update_ref.app,      name="update-ref",     help="Move a branch HEAD to a specific commit ID.")
plumbing_cli.add_typer(commit_graph.app,    name="commit-graph",   help="Emit the commit DAG as a JSON node list.")
plumbing_cli.add_typer(pack_objects.app,    name="pack-objects",   help="Build a PackBundle JSON from wanted commits and write to stdout.")
plumbing_cli.add_typer(unpack_objects.app,  name="unpack-objects", help="Read a PackBundle JSON from stdin and write to the local store.")
plumbing_cli.add_typer(ls_remote.app,       name="ls-remote",      help="List branch heads on a remote without modifying local state.")

cli.add_typer(plumbing_cli, name="plumbing")

# ---------------------------------------------------------------------------
# Tier 2 — Core Porcelain (top-level VCS commands)
# ---------------------------------------------------------------------------

cli.add_typer(attributes.app,   name="attributes",  help="Display .museattributes merge-strategy rules.")
cli.add_typer(init.app,         name="init",        help="Initialise a new Muse repository.")

# Remote sync
cli.add_typer(remote.app,       name="remote",      help="Manage remote repository connections (add, remove, rename, list).")
cli.add_typer(clone.app,        name="clone",       help="Create a local copy of a remote Muse repository.")
cli.add_typer(fetch.app,        name="fetch",       help="Download commits, snapshots, and objects from a remote.")
cli.add_typer(pull.app,         name="pull",        help="Fetch from a remote and merge into the current branch.")
cli.add_typer(push.app,         name="push",        help="Upload local commits, snapshots, and objects to a remote.")

# Core VCS
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

# Cross-domain
cli.add_typer(check.app,        name="check",       help="[*] Domain-agnostic invariant check — dispatches to the active domain plugin.")
cli.add_typer(annotate.app,     name="annotate",    help="[*] CRDT-backed commit annotations — reviewed-by (ORSet) and test-run counter (GCounter).")

# ---------------------------------------------------------------------------
# Tier 3 — MIDI domain semantic commands (muse midi …)
# ---------------------------------------------------------------------------

midi_cli = typer.Typer(
    name="midi",
    help="[Tier 3] MIDI domain semantic commands — music-aware version control operations.",
    no_args_is_help=True,
)

midi_cli.add_typer(notes.app,            name="notes",            help="List every note in a MIDI track as musical notation.")
midi_cli.add_typer(note_log.app,         name="note-log",         help="Note-level commit history — which notes were added or removed in each commit.")
midi_cli.add_typer(note_blame.app,       name="note-blame",       help="Per-bar attribution — which commit introduced the notes in this bar?")
midi_cli.add_typer(harmony.app,          name="harmony",          help="Chord analysis and key detection from MIDI note content.")
midi_cli.add_typer(piano_roll.app,       name="piano-roll",       help="ASCII piano roll visualization of a MIDI track.")
midi_cli.add_typer(note_hotspots.app,    name="hotspots",         help="Bar-level churn leaderboard — which bars change most across commits.")
midi_cli.add_typer(velocity_profile.app, name="velocity-profile", help="Dynamic range and velocity histogram for a MIDI track.")
midi_cli.add_typer(transpose.app,        name="transpose",        help="Transpose all notes in a MIDI track by N semitones.")
midi_cli.add_typer(mix.app,              name="mix",              help="Combine notes from two MIDI tracks into a single output track.")
midi_cli.add_typer(midi_query.app,       name="query",            help="MIDI DSL predicate query over commit history — bars, chords, agents, pitches.")
midi_cli.add_typer(midi_check.app,       name="check",            help="Enforce MIDI invariant rules (polyphony, pitch range, key consistency, parallel fifths).")

cli.add_typer(midi_cli, name="midi")

# ---------------------------------------------------------------------------
# Tier 3 — Code domain semantic commands (muse code …)
# ---------------------------------------------------------------------------

code_cli = typer.Typer(
    name="code",
    help="[Tier 3] Code domain semantic commands — symbol graph, call graph, and provenance.",
    no_args_is_help=True,
)

code_cli.add_typer(symbols.app,              name="symbols",              help="List every semantic symbol (function, class, method…) in a snapshot.")
code_cli.add_typer(symbol_log.app,           name="symbol-log",           help="Track a single symbol through the full commit history.")
code_cli.add_typer(detect_refactor.app,      name="detect-refactor",      help="Detect semantic refactoring operations (renames, moves, extractions) across commits.")
code_cli.add_typer(grep.app,                 name="grep",                 help="Search the symbol graph by name, kind, or language — not file text.")
code_cli.add_typer(blame.app,                name="blame",                help="Show which commit last touched a specific symbol (function, class, method).")
code_cli.add_typer(hotspots.app,             name="hotspots",             help="Symbol churn leaderboard — which functions change most often.")
code_cli.add_typer(stable.app,               name="stable",               help="Symbol stability leaderboard — the bedrock of your codebase.")
code_cli.add_typer(coupling.app,             name="coupling",             help="File co-change analysis — discover hidden semantic dependencies.")
code_cli.add_typer(compare.app,              name="compare",              help="Deep semantic comparison between any two historical snapshots.")
code_cli.add_typer(languages.app,            name="languages",            help="Language and symbol-type breakdown of a snapshot.")
code_cli.add_typer(patch.app,                name="patch",                help="Surgical semantic patch — modify exactly one named symbol (all-language syntax validation).")
code_cli.add_typer(query.app,                name="query",                help="Symbol graph predicate DSL — OR/NOT/grouping, --all-commits temporal search.")
code_cli.add_typer(query_history.app,        name="query-history",        help="Temporal symbol search — first seen, last seen, change count across a commit range.")
code_cli.add_typer(deps.app,                 name="deps",                 help="Import graph + Python call-graph; --reverse for callers/importers.")
code_cli.add_typer(find_symbol.app,          name="find-symbol",          help="Cross-commit, cross-branch symbol search by hash, name, or kind.")
code_cli.add_typer(impact.app,               name="impact",               help="Transitive blast-radius — every caller affected if this symbol changes.")
code_cli.add_typer(dead.app,                 name="dead",                 help="Dead code candidates — symbols with no callers and no importers.")
code_cli.add_typer(coverage.app,             name="coverage",             help="Class interface call-coverage — which methods are actually called?")
code_cli.add_typer(lineage.app,              name="lineage",              help="Full provenance chain of a symbol — created, renamed, moved, copied, deleted.")
code_cli.add_typer(api_surface.app,          name="api-surface",          help="Public API surface at a commit; --diff to show added/removed/changed symbols.")
code_cli.add_typer(codemap.app,              name="codemap",              help="Semantic topology — module sizes, import cycles, centrality, boundary files.")
code_cli.add_typer(clones.app,               name="clones",               help="Find exact and near-duplicate symbols (body_hash / signature_id clusters).")
code_cli.add_typer(checkout_symbol.app,      name="checkout-symbol",      help="Restore a historical version of one symbol into the working tree.")
code_cli.add_typer(semantic_cherry_pick.app, name="semantic-cherry-pick", help="Cherry-pick named symbols from a historical commit into the working tree.")
code_cli.add_typer(index_rebuild.app,        name="index",                help="Manage local indexes: status, rebuild symbol_history / hash_occurrence.")
code_cli.add_typer(breakage.app,             name="breakage",             help="Detect symbol-level structural breakage in the working tree vs HEAD.")
code_cli.add_typer(invariants.app,           name="invariants",           help="Enforce architectural rules from .muse/invariants.toml.")
code_cli.add_typer(code_check.app,           name="check",                help="Semantic invariant enforcement — complexity, import cycles, dead exports, test coverage.")
code_cli.add_typer(code_query.app,           name="code-query",           help="Predicate query over code commit history — symbol, file, language, agent_id, sem_ver_bump.")

cli.add_typer(code_cli, name="code")

# ---------------------------------------------------------------------------
# Tier 3 — Multi-agent coordination commands (muse coord …)
# ---------------------------------------------------------------------------

coord_cli = typer.Typer(
    name="coord",
    help="[Tier 3] Multi-agent coordination commands — reservations, intent, conflict forecasting.",
    no_args_is_help=True,
)

coord_cli.add_typer(reserve.app,    name="reserve",    help="Advisory symbol reservation — announce intent before editing.")
coord_cli.add_typer(intent.app,     name="intent",     help="Declare a specific operation before executing it.")
coord_cli.add_typer(forecast.app,   name="forecast",   help="Predict merge conflicts from active reservations and intents.")
coord_cli.add_typer(plan_merge.app, name="plan-merge", help="Dry-run semantic merge plan — classify conflicts without writing.")
coord_cli.add_typer(shard.app,      name="shard",      help="Partition the codebase into N low-coupling work zones for parallel agents.")
coord_cli.add_typer(reconcile.app,  name="reconcile",  help="Recommend merge ordering and integration strategy from coordination state.")

cli.add_typer(coord_cli, name="coord")


if __name__ == "__main__":
    cli()
