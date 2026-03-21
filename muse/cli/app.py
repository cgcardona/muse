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
    muse plumbing merge-base      Find the lowest common ancestor of two commits
    muse plumbing snapshot-diff   Diff two snapshot manifests (added/modified/deleted)
    muse plumbing domain-info     Inspect active domain plugin and its capabilities
    muse plumbing show-ref        List all branch refs and their commit IDs
    muse plumbing check-ignore    Test paths against .museignore rules
    muse plumbing check-attr      Query merge-strategy attributes for paths
    muse plumbing verify-object   Re-hash stored objects to detect corruption
    muse plumbing symbolic-ref    Read or write HEAD's symbolic branch reference
    muse plumbing for-each-ref    Iterate all refs with rich commit metadata
    muse plumbing name-rev        Map commit IDs to descriptive branch-relative names
    muse plumbing check-ref-format  Validate branch/ref names against naming rules
    muse plumbing verify-pack     Verify the integrity of a PackBundle

Tier 2 — Core Porcelain commands::

    init        status      log         commit      diff
    show        branch      checkout    merge       reset
    revert      stash       cherry-pick tag         domains
    attributes  remote      clone       fetch       pull
    push        check       annotate    blame
    reflog      rerere      gc          archive     bisect
    worktree    workspace

Identity & hub fabric::

    auth login      muse auth login [--token TOKEN] [--hub HUB] [--agent]
    auth whoami     muse auth whoami [--json]
    auth logout     muse auth logout [--hub HUB]
    hub connect     muse hub connect <url>
    hub status      muse hub status [--json]
    hub disconnect  muse hub disconnect
    hub ping        muse hub ping
    config show     muse config show [--json]
    config get      muse config get <key>
    config set      muse config set <key> <value>
    config edit     muse config edit

Tier 3 — MIDI domain commands (``muse midi …``)::

    Analysis:
    notes           note-log        note-blame      harmony         piano-roll
    hotspots        velocity-profile rhythm          scale           contour
    density         tension         cadence         motif           voice-leading
    instrumentation tempo           compare

    Transformation:
    transpose       mix             quantize        humanize        invert
    retrograde      arpeggiate      normalize

    Multi-agent & search:
    shard           agent-map       find-phrase

    Invariants:
    query           check

Tier 3 — Code domain commands (``muse code …``)::

    symbols         symbol-log      detect-refactor  grep
    blame           hotspots        stable           coupling
    compare         languages       patch            query
    query-history   deps            find-symbol      impact
    dead            coverage        lineage          api-surface
    codemap         clones          checkout-symbol  semantic-cherry-pick
    index           breakage        invariants       check

Tier 3 — Coordination commands (``muse coord …``)::

    reserve          intent           forecast
    plan-merge       predict-conflicts  shard
    reconcile
"""

from __future__ import annotations

import typer

from muse.cli.commands import (
    annotate,
    api_surface,
    archive,
    attributes,
    auth,
    bisect,
    blame,
    branch,
    bundle,
    cat,
    cherry_pick,
    checkout,
    checkout_symbol,
    check,
    clean,
    clone,
    clones,
    codemap,
    code_check,
    code_query,
    commit,
    compare,
    config_cmd,
    core_blame,
    coupling,
    coverage,
    dead,
    deps,
    describe,
    detect_refactor,
    diff,
    domains,
    fetch,
    find_symbol,
    forecast,
    content_grep,
    gc,
    grep,
    harmony,
    hotspots,
    hub,
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
    rebase,
    reconcile,
    reflog,
    remote,
    rerere,
    reserve,
    reset,
    revert,
    semantic_cherry_pick,
    shard,
    shortlog,
    show,
    snapshot_cmd,
    stable,
    stash,
    status,
    symbol_log,
    symbols,
    tag,
    breakage,
    transpose,
    verify,
    velocity_profile,
    whoami,
    worktree,
    workspace,
    # New MIDI semantic porcelain — analysis
    agent_map,
    arpeggiate,
    cadence,
    contour,
    density,
    find_phrase,
    humanize,
    instrumentation,
    invert,
    midi_compare,
    midi_shard,
    motif_detect,
    quantize,
    retrograde,
    rhythm,
    scale_detect,
    tempo,
    tension,
    velocity_normalize,
    voice_leading,
)

from muse.cli.commands.plumbing import (
    cat_object,
    check_attr,
    check_ignore,
    check_ref_format,
    commit_graph,
    commit_tree,
    domain_info,
    for_each_ref,
    hash_object,
    ls_files,
    ls_remote,
    merge_base,
    name_rev,
    pack_objects,
    read_commit,
    read_snapshot,
    rev_parse,
    show_ref,
    snapshot_diff,
    symbolic_ref,
    unpack_objects,
    update_ref,
    verify_object,
    verify_pack,
)

# ---------------------------------------------------------------------------
# Root CLI
# ---------------------------------------------------------------------------

# Allow both -h and --help everywhere in the CLI tree.
_HELP_SETTINGS: dict[str, list[str]] = {"help_option_names": ["-h", "--help"]}

cli = typer.Typer(
    name="muse",
    help="Muse — domain-agnostic version control for multidimensional state.",
    no_args_is_help=True,
    context_settings=_HELP_SETTINGS,
)

# ---------------------------------------------------------------------------
# Tier 1 — Plumbing sub-namespace
# ---------------------------------------------------------------------------

plumbing_cli = typer.Typer(
    name="plumbing",
    help="[Tier 1] Machine-readable plumbing commands. JSON output, pipeable, stable API.",
    no_args_is_help=True,
    context_settings=_HELP_SETTINGS,
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
plumbing_cli.add_typer(merge_base.app,      name="merge-base",     help="Find the lowest common ancestor of two commits.")
plumbing_cli.add_typer(snapshot_diff.app,   name="snapshot-diff",  help="Diff two snapshot manifests: added, modified, deleted paths.")
plumbing_cli.add_typer(domain_info.app,     name="domain-info",    help="Inspect active domain plugin capabilities and schema.")
plumbing_cli.add_typer(show_ref.app,        name="show-ref",       help="List all branch refs and the commit IDs they point to.")
plumbing_cli.add_typer(check_ignore.app,    name="check-ignore",   help="Test paths against .museignore rules.")
plumbing_cli.add_typer(check_attr.app,      name="check-attr",     help="Query merge-strategy attributes for workspace paths.")
plumbing_cli.add_typer(verify_object.app,   name="verify-object",    help="Re-hash stored objects to detect data corruption.")
plumbing_cli.add_typer(symbolic_ref.app,    name="symbolic-ref",     help="Read or write HEAD's symbolic branch reference.")
plumbing_cli.add_typer(for_each_ref.app,    name="for-each-ref",     help="Iterate all refs with rich commit metadata.")
plumbing_cli.add_typer(name_rev.app,        name="name-rev",         help="Map commit IDs to descriptive branch-relative names.")
plumbing_cli.add_typer(check_ref_format.app, name="check-ref-format", help="Validate branch/ref names against Muse naming rules.")
plumbing_cli.add_typer(verify_pack.app,     name="verify-pack",      help="Verify the integrity of a PackBundle JSON.")

cli.add_typer(plumbing_cli, name="plumbing")

# ---------------------------------------------------------------------------
# Tier 2 — Core Porcelain (top-level VCS commands)
# ---------------------------------------------------------------------------

cli.add_typer(attributes.app,   name="attributes",  help="Display .museattributes merge-strategy rules.")
cli.add_typer(init.app,         name="init",        help="Initialise a new Muse repository.")

# Identity & hub fabric
cli.add_typer(auth.app,         name="auth",        help="Identity management — login as human or agent, inspect credentials.")
cli.add_typer(hub.app,          name="hub",         help="MuseHub fabric — connect, inspect, and disconnect this repo from the hub.")
cli.add_typer(config_cmd.app,   name="config",      help="Local repository configuration — show, get, set typed config values.")

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
cli.add_typer(cat.app,          name="cat",         help="Print the source of a single symbol: 'muse cat file.py::ClassName.method'.")
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

# VCS completeness — safety net, search, export, multi-repo
cli.add_typer(core_blame.app,   name="blame",       help="Line-level attribution for any text file — which commit last changed each line.")
cli.add_typer(reflog.app,       name="reflog",      help="Show the history of HEAD and branch-ref movements — the undo safety net.")
cli.add_typer(rerere.app,       name="rerere",      help="Reuse recorded conflict resolutions — auto-apply cached fixes on future merges.")
cli.add_typer(gc.app,           name="gc",          help="Garbage-collect unreachable objects from the object store.")
cli.add_typer(archive.app,      name="archive",     help="Export any historical snapshot as a portable tar.gz or zip archive.")
cli.add_typer(bisect.app,       name="bisect",      help="Binary search through commit history to isolate the first bad commit.")
cli.add_typer(worktree.app,     name="worktree",    help="Manage multiple simultaneous branch checkouts (one state/ per branch).")
cli.add_typer(workspace.app,    name="workspace",   help="Compose and manage multi-repository workspaces.")

# New porcelain gap-fill commands
cli.add_typer(rebase.app,       name="rebase",      help="Replay commits from the current branch onto a new base.")
cli.add_typer(clean.app,        name="clean",       help="Remove untracked files from the working tree.")
cli.add_typer(describe.app,     name="describe",    help="Label a commit by its nearest tag and hop distance.")
cli.add_typer(shortlog.app,     name="shortlog",    help="Commit summary grouped by author or agent.")
cli.add_typer(verify.app,       name="verify",      help="Whole-repository integrity check — re-hash objects and verify DAG.")
cli.add_typer(snapshot_cmd.app, name="snapshot",    help="Explicit snapshot management — capture, list, show, and export.")
cli.add_typer(bundle.app,           name="bundle",          help="Pack and unpack commits into a single portable bundle file.")
cli.add_typer(content_grep.app,     name="content-grep",    help="Full-text search across tracked file content (raw bytes, regex).")
cli.add_typer(whoami.app,           name="whoami",          help="Show the current identity (shortcut for muse auth whoami).")

# ---------------------------------------------------------------------------
# Tier 3 — MIDI domain semantic commands (muse midi …)
# ---------------------------------------------------------------------------

midi_cli = typer.Typer(
    name="midi",
    help="[Tier 3] MIDI domain semantic commands — music-aware version control operations.",
    no_args_is_help=True,
    context_settings=_HELP_SETTINGS,
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
# --- Analysis porcelain ---
midi_cli.add_typer(rhythm.app,           name="rhythm",           help="Quantify syncopation, swing ratio, and quantisation accuracy of a MIDI track.")
midi_cli.add_typer(scale_detect.app,     name="scale",            help="Detect scale or mode (major, dorian, blues, whole-tone…) from pitch-class analysis.")
midi_cli.add_typer(contour.app,          name="contour",          help="Classify melodic contour shape (arch, ascending, wave…) and report interval sequence.")
midi_cli.add_typer(density.app,          name="density",          help="Note density (notes per beat) per bar — reveals textural arc of a composition.")
midi_cli.add_typer(tension.app,          name="tension",          help="Harmonic tension curve per bar — consonance/dissonance arc from interval dissonance weights.")
midi_cli.add_typer(cadence.app,          name="cadence",          help="Detect phrase-ending cadences (authentic, deceptive, half, plagal) at bar boundaries.")
midi_cli.add_typer(motif_detect.app,     name="motif",            help="Find recurring melodic interval patterns (motifs) in a MIDI track.")
midi_cli.add_typer(voice_leading.app,    name="voice-leading",    help="Detect parallel fifths, octaves, and large leaps — classical voice-leading lint.")
midi_cli.add_typer(instrumentation.app,  name="instrumentation",  help="Per-channel note distribution, pitch range, register, and velocity map.")
midi_cli.add_typer(tempo.app,            name="tempo",            help="Estimate BPM from inter-onset intervals; report ticks-per-beat metadata.")
midi_cli.add_typer(midi_compare.app,     name="compare",          help="Semantic comparison between two MIDI snapshots across key, rhythm, density, and swing.")
# --- Transformation porcelain ---
midi_cli.add_typer(quantize.app,         name="quantize",         help="Snap note onsets to a rhythmic grid (16th, 8th, triplet, …) with adjustable strength.")
midi_cli.add_typer(humanize.app,         name="humanize",         help="Add subtle timing and velocity variation to give quantised MIDI a human feel.")
midi_cli.add_typer(invert.app,           name="invert",           help="Melodic inversion — reflect all intervals around a pivot pitch.")
midi_cli.add_typer(retrograde.app,       name="retrograde",       help="Retrograde transformation — reverse the pitch order of all notes.")
midi_cli.add_typer(arpeggiate.app,       name="arpeggiate",       help="Convert simultaneous chord voicings into a sequential arpeggio pattern.")
midi_cli.add_typer(velocity_normalize.app, name="normalize",      help="Rescale note velocities to a target dynamic range [min, max].")
# --- Multi-agent & search porcelain ---
midi_cli.add_typer(midi_shard.app,       name="shard",            help="Partition a MIDI composition into bar-range shards for parallel agent work.")
midi_cli.add_typer(agent_map.app,        name="agent-map",        help="Show which agent last edited each bar of a MIDI track (bar-level blame).")
midi_cli.add_typer(find_phrase.app,      name="find-phrase",      help="Search for a melodic phrase across MIDI commit history by similarity scoring.")

cli.add_typer(midi_cli, name="midi")

# ---------------------------------------------------------------------------
# Tier 3 — Code domain semantic commands (muse code …)
# ---------------------------------------------------------------------------

code_cli = typer.Typer(
    name="code",
    help="[Tier 3] Code domain semantic commands — symbol graph, call graph, and provenance.",
    no_args_is_help=True,
    context_settings=_HELP_SETTINGS,
)

code_cli.add_typer(cat.app,                  name="cat",                  help="Print the source of a single symbol: 'muse code cat file.py::ClassName.method'.")
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
    context_settings=_HELP_SETTINGS,
)

coord_cli.add_typer(reserve.app,    name="reserve",    help="Advisory symbol reservation — announce intent before editing.")
coord_cli.add_typer(intent.app,     name="intent",     help="Declare a specific operation before executing it.")
coord_cli.add_typer(forecast.app,   name="forecast",   help="Predict merge conflicts from active reservations and intents.")
coord_cli.add_typer(plan_merge.app, name="plan-merge",          help="Dry-run semantic merge plan — classify conflicts without writing.")
coord_cli.add_typer(plan_merge.app, name="predict-conflicts",    help="Alias for plan-merge — predict which symbols will conflict before merging.")
coord_cli.add_typer(shard.app,      name="shard",      help="Partition the codebase into N low-coupling work zones for parallel agents.")
coord_cli.add_typer(reconcile.app,  name="reconcile",  help="Recommend merge ordering and integration strategy from coordination state.")

cli.add_typer(coord_cli, name="coord")


if __name__ == "__main__":
    cli()
