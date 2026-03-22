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

import argparse
import sys

from muse.cli.commands import (
    agent_map,
    annotate,
    api_surface,
    archive,
    arpeggiate,
    attributes,
    auth,
    bisect,
    blame,
    branch,
    breakage,
    bundle,
    cadence,
    cat,
    check,
    checkout,
    checkout_symbol,
    cherry_pick,
    clean,
    clone,
    clones,
    code_check,
    code_query,
    codemap,
    commit,
    compare,
    config_cmd,
    content_grep,
    contour,
    core_blame,
    coupling,
    coverage,
    dead,
    density,
    deps,
    describe,
    detect_refactor,
    diff,
    domains,
    fetch,
    find_phrase,
    find_symbol,
    forecast,
    gc,
    grep,
    harmony,
    hotspots,
    hub,
    humanize,
    impact,
    index_rebuild,
    init,
    instrumentation,
    intent,
    invariants,
    invert,
    languages,
    lineage,
    log,
    merge,
    midi_check,
    midi_compare,
    midi_query,
    midi_shard,
    mix,
    motif_detect,
    note_blame,
    note_hotspots,
    note_log,
    notes,
    patch,
    piano_roll,
    plan_merge,
    pull,
    push,
    quantize,
    query,
    query_history,
    rebase,
    reconcile,
    reflog,
    remote,
    rerere,
    reserve,
    reset,
    retrograde,
    revert,
    rhythm,
    scale_detect,
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
    tempo,
    tension,
    transpose,
    velocity_normalize,
    velocity_profile,
    verify,
    voice_leading,
    whoami,
    worktree,
    workspace,
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


def _no_command(parser: argparse.ArgumentParser) -> None:
    """Print help when no subcommand is provided."""
    parser.print_help()
    raise SystemExit(0)


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and dispatch to the appropriate command handler."""
    parser = argparse.ArgumentParser(
        prog="muse",
        description="Muse — domain-agnostic version control for multidimensional state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", "-V",
        action="store_true",
        help="Show the muse version and exit.",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ------------------------------------------------------------------
    # Tier 1 — Plumbing sub-namespace (muse plumbing <cmd>)
    # ------------------------------------------------------------------
    plumbing_parser = subparsers.add_parser(
        "plumbing",
        help="[Tier 1] Machine-readable plumbing commands. JSON output, pipeable, stable API.",
        description="Machine-readable plumbing commands — analogous to git plumbing.",
    )
    plumbing_subs = plumbing_parser.add_subparsers(dest="plumbing_command", metavar="PLUMBING_COMMAND")
    plumbing_subs.required = True

    hash_object.register(plumbing_subs)
    cat_object.register(plumbing_subs)
    rev_parse.register(plumbing_subs)
    ls_files.register(plumbing_subs)
    read_commit.register(plumbing_subs)
    read_snapshot.register(plumbing_subs)
    commit_tree.register(plumbing_subs)
    update_ref.register(plumbing_subs)
    commit_graph.register(plumbing_subs)
    pack_objects.register(plumbing_subs)
    unpack_objects.register(plumbing_subs)
    ls_remote.register(plumbing_subs)
    merge_base.register(plumbing_subs)
    snapshot_diff.register(plumbing_subs)
    domain_info.register(plumbing_subs)
    show_ref.register(plumbing_subs)
    check_ignore.register(plumbing_subs)
    check_attr.register(plumbing_subs)
    verify_object.register(plumbing_subs)
    symbolic_ref.register(plumbing_subs)
    for_each_ref.register(plumbing_subs)
    name_rev.register(plumbing_subs)
    check_ref_format.register(plumbing_subs)
    verify_pack.register(plumbing_subs)

    # ------------------------------------------------------------------
    # Tier 2 — Core Porcelain (top-level muse <cmd>)
    # ------------------------------------------------------------------
    init.register(subparsers)
    status.register(subparsers)
    log.register(subparsers)
    commit.register(subparsers)
    diff.register(subparsers)
    show.register(subparsers)
    branch.register(subparsers)
    checkout.register(subparsers)
    merge.register(subparsers)
    reset.register(subparsers)
    revert.register(subparsers)
    cherry_pick.register(subparsers)
    stash.register(subparsers)
    tag.register(subparsers)
    push.register(subparsers)
    pull.register(subparsers)
    fetch.register(subparsers)
    clone.register(subparsers)
    remote.register(subparsers)
    auth.register(subparsers)
    hub.register(subparsers)
    config_cmd.register(subparsers)
    domains.register(subparsers)
    attributes.register(subparsers)
    annotate.register(subparsers)
    core_blame.register(subparsers)
    reflog.register(subparsers)
    rerere.register(subparsers)
    gc.register(subparsers)
    archive.register(subparsers)
    bisect.register(subparsers)
    worktree.register(subparsers)
    workspace.register(subparsers)
    rebase.register(subparsers)
    clean.register(subparsers)
    describe.register(subparsers)
    shortlog.register(subparsers)
    verify.register(subparsers)
    snapshot_cmd.register(subparsers)
    bundle.register(subparsers)
    content_grep.register(subparsers)
    whoami.register(subparsers)
    check.register(subparsers)
    cat.register(subparsers)

    # ------------------------------------------------------------------
    # Tier 3 — MIDI domain (muse midi <cmd>)
    # ------------------------------------------------------------------
    midi_parser = subparsers.add_parser(
        "midi",
        help="[Tier 3] MIDI domain semantic commands.",
        description="MIDI domain semantic commands — analysis, transformation, and multi-agent.",
    )
    midi_subs = midi_parser.add_subparsers(dest="midi_command", metavar="MIDI_COMMAND")
    midi_subs.required = True

    notes.register(midi_subs)
    note_log.register(midi_subs)
    note_blame.register(midi_subs)
    harmony.register(midi_subs)
    piano_roll.register(midi_subs)
    note_hotspots.register(midi_subs)
    velocity_profile.register(midi_subs)
    transpose.register(midi_subs)
    mix.register(midi_subs)
    midi_query.register(midi_subs)
    midi_check.register(midi_subs)
    rhythm.register(midi_subs)
    scale_detect.register(midi_subs)
    contour.register(midi_subs)
    density.register(midi_subs)
    tension.register(midi_subs)
    cadence.register(midi_subs)
    motif_detect.register(midi_subs)
    voice_leading.register(midi_subs)
    instrumentation.register(midi_subs)
    tempo.register(midi_subs)
    midi_compare.register(midi_subs)
    quantize.register(midi_subs)
    humanize.register(midi_subs)
    invert.register(midi_subs)
    retrograde.register(midi_subs)
    arpeggiate.register(midi_subs)
    velocity_normalize.register(midi_subs)
    midi_shard.register(midi_subs)
    agent_map.register(midi_subs)
    find_phrase.register(midi_subs)

    # ------------------------------------------------------------------
    # Tier 3 — Code domain (muse code <cmd>)
    # ------------------------------------------------------------------
    code_parser = subparsers.add_parser(
        "code",
        help="[Tier 3] Code domain semantic commands — symbol graph, call graph, and provenance.",
        description="Code domain semantic commands.",
    )
    code_subs = code_parser.add_subparsers(dest="code_command", metavar="CODE_COMMAND")
    code_subs.required = True

    cat.register(code_subs)
    symbols.register(code_subs)
    symbol_log.register(code_subs)
    detect_refactor.register(code_subs)
    grep.register(code_subs)
    blame.register(code_subs)
    hotspots.register(code_subs)
    stable.register(code_subs)
    coupling.register(code_subs)
    compare.register(code_subs)
    languages.register(code_subs)
    patch.register(code_subs)
    query.register(code_subs)
    query_history.register(code_subs)
    deps.register(code_subs)
    find_symbol.register(code_subs)
    impact.register(code_subs)
    dead.register(code_subs)
    coverage.register(code_subs)
    lineage.register(code_subs)
    api_surface.register(code_subs)
    codemap.register(code_subs)
    clones.register(code_subs)
    checkout_symbol.register(code_subs)
    semantic_cherry_pick.register(code_subs)
    index_rebuild.register(code_subs)
    breakage.register(code_subs)
    invariants.register(code_subs)
    code_check.register(code_subs)
    code_query.register(code_subs)

    # ------------------------------------------------------------------
    # Tier 3 — Coordination (muse coord <cmd>)
    # ------------------------------------------------------------------
    coord_parser = subparsers.add_parser(
        "coord",
        help="[Tier 3] Multi-agent coordination commands — reservations, intent, conflict forecasting.",
        description="Multi-agent coordination commands.",
    )
    coord_subs = coord_parser.add_subparsers(dest="coord_command", metavar="COORD_COMMAND")
    coord_subs.required = True

    reserve.register(coord_subs)
    intent.register(coord_subs)
    forecast.register(coord_subs)
    plan_merge.register(coord_subs)
    shard.register(coord_subs)
    reconcile.register(coord_subs)

    # ------------------------------------------------------------------
    # Parse and dispatch
    # ------------------------------------------------------------------
    args = parser.parse_args(argv)

    if args.version:
        from muse._version import __version__
        print(f"muse {__version__}")
        raise SystemExit(0)

    if args.command is None:
        _no_command(parser)
        return

    if not hasattr(args, "func"):
        # A namespace command (plumbing/midi/code/coord) was given without a subcommand.
        # argparse already printed an error above via subs.required = True.
        raise SystemExit(2)

    args.func(args)


if __name__ == "__main__":
    main()
