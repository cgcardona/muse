# Music Query DSL

## Overview

The music query DSL allows agents and humans to search the Muse commit history
for specific musical content without parsing MIDI bytes for every commit.

```bash
muse midi query "note.pitch_class == 'Eb' and bar == 12"
muse midi query "harmony.quality == 'dim' and bar == 8"
muse midi query "agent_id == 'counterpoint-bot'"
muse midi query "note.velocity > 80 and track == 'cello.mid'"
```

## Grammar (EBNF)

```
query      = or_expr
or_expr    = and_expr ( 'or' and_expr )*
and_expr   = not_expr ( 'and' not_expr )*
not_expr   = 'not' not_expr | atom
atom       = '(' query ')' | comparison
comparison = FIELD OP VALUE
FIELD      = <see field table below>
OP         = '==' | '!=' | '>' | '<' | '>=' | '<='
VALUE      = QUOTED_STRING | INTEGER | FLOAT
```

## Supported fields

| Field | Type | Description |
|-------|------|-------------|
| `note.pitch` | int | MIDI pitch (0–127) |
| `note.pitch_class` | str | Pitch class name ("C", "C#", …, "B") |
| `note.velocity` | int | MIDI velocity (0–127) |
| `note.channel` | int | MIDI channel (0–15) |
| `note.duration` | float | Duration in beats |
| `bar` | int | 1-indexed bar number (assumes 4/4) |
| `track` | str | Workspace-relative MIDI file path |
| `harmony.chord` | str | Detected chord name ("Cmaj", "Fdim7", …) |
| `harmony.quality` | str | Chord quality suffix ("maj", "min", "dim", "dim7", …) |
| `author` | str | Commit author string |
| `agent_id` | str | Agent ID from commit provenance |
| `model_id` | str | Model ID from commit provenance |
| `toolchain_id` | str | Toolchain ID from commit provenance |

**Note fields** match if *any* note in the bar satisfies the predicate —
i.e. `note.pitch > 60` is true for a bar if it contains at least one note
with MIDI pitch > 60.

## Examples

```bash
# All bars where Eb appears.
muse midi query "note.pitch_class == 'Eb'"

# Diminished chord in bar 8 specifically.
muse midi query "harmony.quality == 'dim' and bar == 8"

# High-velocity notes in the cello part authored by an agent.
muse midi query "note.velocity > 100 and track == 'cello.mid' and agent_id == 'melody-agent'"

# Notes outside a comfortable bass range.
muse midi query "note.pitch < 36 or note.pitch > 96" --track bass.mid

# Everything from a particular AI model.
muse midi query "model_id == 'claude-4'"
```

## Architecture

The DSL is implemented in three layers in `muse/plugins/midi/_music_query.py`:

1. **Tokenizer** (`_tokenize`) — regex-based lexer producing `Token` objects.
2. **Recursive descent parser** (`_Parser`) — produces an AST of `EqNode`,
   `AndNode`, `OrNode`, `NotNode`.
3. **Evaluator** (`evaluate_node`) — walks the AST against a `QueryContext`
   that provides the bar's notes, chord, and commit provenance.

The top-level `run_query()` function walks the commit DAG from HEAD, loading
each MIDI track from the object store, grouping notes by bar, and evaluating
the predicate.

## CLI flags

```
muse midi query QUERY
  --track  PATH     Restrict to one MIDI file
  --from   COMMIT   Start commit (default: HEAD)
  --to     COMMIT   Stop before this commit
  -n       N        Max results (default: 100)
  --json            Machine-readable JSON output
```

## Related files

| File | Role |
|------|------|
| `muse/plugins/midi/_music_query.py` | Tokenizer, parser, evaluator, `run_query` |
| `muse/cli/commands/music_query.py` | CLI command `muse midi query` |
| `tests/test_music_query.py` | Unit tests |
