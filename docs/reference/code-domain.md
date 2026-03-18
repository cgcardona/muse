# Code Domain — Complete Reference

> **Engine:** `muse/plugins/code/` · **No external deps for core analysis**
> **Scope:** Every command, module, type, and protocol in the code domain plugin

---

## Overview

The code domain plugin treats a codebase as a **typed, content-addressed symbol graph** — not as a bag of text lines.  Every function, class, method, variable, and import becomes a `SymbolRecord` with a stable content-addressed identity (SHA-256).  This unlocks operations that are structurally impossible in Git:

- Track a function through renames and cross-file moves with perfect identity.
- Cherry-pick a single named function out of a historical commit.
- Detect exact and near-duplicate code across an entire snapshot in O(1).
- Predict merge conflicts before writing a single byte.
- Enforce architectural invariants as committed rules.
- Assign semantic version bumps automatically at commit time.
- Coordinate thousands of parallel agents without a central lock server.

---

## Contents

1. [Symbol Identity Model](#1-symbol-identity-model)
2. [Provenance & Topology Commands](#2-provenance--topology-commands)
3. [Query & Temporal Search](#3-query--temporal-search)
4. [Index Infrastructure](#4-index-infrastructure)
5. [Symbol Identity Detail](#5-symbol-identity-detail)
6. [Multi-Agent Coordination Layer](#6-multi-agent-coordination-layer)
7. [Merge Engine & Architectural Enforcement](#7-merge-engine--architectural-enforcement)
8. [Semantic Versioning](#8-semantic-versioning)
9. [Call-Graph Tier Commands](#9-call-graph-tier-commands)
10. [Architecture Internals](#10-architecture-internals)
11. [Type Reference](#11-type-reference)

---

## 1. Symbol Identity Model

Every symbol carries four content-addressed hashes and two stable keys:

| Field | Description |
|---|---|
| `content_id` | SHA-256 of the full normalized AST (signature + body + metadata). Two symbols are identical iff their `content_id` matches. |
| `body_hash` | SHA-256 of the function/class body only (excluding signature and decorators). Matches across renames and decorator changes. |
| `signature_id` | SHA-256 of the normalized parameter list and return annotation. Matches across implementation-only changes. |
| `metadata_id` *(v2)* | SHA-256 of decorator list + async flag + base classes. Matches when only the implementation or signature changed. |
| `canonical_key` *(v2)* | `{file}#{scope}#{kind}#{name}#{lineno}` — stable machine handle for agent-to-agent symbol handoff. |
| `qualified_name` | Dotted path within the file (e.g. `MyClass.my_method`). |

### Exact Refactor Classification

Two symbols are classified by comparing their four hashes:

| Classification | Condition |
|---|---|
| `unchanged` | `content_id` matches |
| `rename` | `body_hash` matches, name differs, same file |
| `move` | `content_id` matches, different file, same name |
| `rename+move` | `body_hash` matches, different file, different name |
| `signature_only` | `body_hash` matches, `signature_id` differs |
| `impl_only` | `signature_id` matches, `body_hash` differs |
| `metadata_only` | `body_hash` + `signature_id` match, `metadata_id` differs |
| `full_rewrite` | Both signature and body changed |

---

## 2. Provenance & Topology Commands

### `muse lineage ADDRESS`

Full provenance chain of a named symbol from its first appearance to the present.

```
muse lineage src/billing.py::compute_total
muse lineage src/billing.py::compute_total --commit HEAD~10
muse lineage src/billing.py::compute_total --json
```

**How it works:** Walks all commits in chronological order, scanning `InsertOp`/`DeleteOp`/`ReplaceOp` entries in each `structured_delta`.  Rename detection uses `content_id` matching across Insert+Delete pairs within a single commit.

**Output events:** `created`, `modified`, `renamed_from`, `moved_from`, `deleted`.

**Flags:**
- `--commit REF` — stop history walk at this commit (default: HEAD)
- `--json` — emit a JSON array of event objects

**JSON schema:**
```json
[
  {
    "event": "created",
    "commit_id": "a1b2c3d4...",
    "committed_at": "2026-01-01T00:00:00+00:00",
    "message": "Initial commit",
    "address": "src/billing.py::compute_total",
    "content_id": "sha256..."
  }
]
```

---

### `muse api-surface`

Public API surface of a snapshot — every non-underscore function, class, and method.

```
muse api-surface
muse api-surface --commit v1.0
muse api-surface --diff v1.0
muse api-surface --json
```

**With `--diff REF`:** Shows three sections — **Added** (new public symbols), **Removed** (deleted public symbols), **Changed** (same address, different `content_id`).

**Public** means: `kind` in `{function, class, method, async_function}` and `name` not starting with `_`.

---

### `muse codemap`

Semantic topology of the entire codebase at a snapshot.

```
muse codemap
muse codemap --top 10
muse codemap --commit HEAD~5
muse codemap --json
```

**What it shows:**
- **Modules by size** — ranked by symbol count
- **Import graph** — in-degree (how many modules import this one)
- **Cycles** — import cycles detected via DFS (a hard architectural smell)
- **High-centrality symbols** — functions called from many places (blast-radius risk)
- **Boundary files** — high fan-out (imports many), zero fan-in (nothing imports them)

**Flags:**
- `--top N` — show top N entries per section (default: 5)
- `--commit REF` — snapshot to analyse
- `--json` — structured output

---

### `muse clones`

Find exact and near-duplicate symbol clusters across the snapshot.

```
muse clones
muse clones --tier exact
muse clones --tier near
muse clones --tier both
muse clones --commit HEAD~3 --json
```

**Exact clones:** Same `body_hash` at different addresses.  These are literal copy-paste duplicates — same implementation, possibly different name.

**Near-clones:** Same `signature_id`, different `body_hash`.  Same public contract (parameters + return type), diverged implementation — a maintainability risk.

**Output:** Clusters, one per group, listing all member addresses.

---

### `muse checkout-symbol ADDRESS --commit REF`

Restore a single named symbol from a historical commit into the current working tree.  Only the target symbol's lines change; everything else is untouched.

```
muse checkout-symbol src/billing.py::compute_total --commit v1.0
muse checkout-symbol src/billing.py::compute_total --commit abc123 --dry-run
```

**Flags:**
- `--commit REF` *(required)* — source commit
- `--dry-run` — print the unified diff without writing

**Safety:** Rejects the operation if the target file cannot be parsed (syntax error) or if the symbol no longer exists at the destination location and the file cannot be safely patched.

---

### `muse semantic-cherry-pick ADDRESS... --from REF`

Cherry-pick one or more named symbols from a historical commit.  Applies each symbol patch to the working tree at the symbol's current location; appends at the end of the file if the symbol is not present in the current tree.

```
muse semantic-cherry-pick src/billing.py::compute_total --from v1.0
muse semantic-cherry-pick src/billing.py::f1 src/billing.py::f2 --from abc123
muse semantic-cherry-pick src/billing.py::compute_total --from v1.0 --dry-run --json
```

**Flags:**
- `--from REF` *(required)* — source commit
- `--dry-run` — show what would change without writing
- `--json` — structured output with per-symbol patch results

---

## 3. Query & Temporal Search

### `muse query PREDICATE...`

Symbol graph predicate DSL — SQL for your codebase.

```
muse query kind=function language=Python
muse query "(kind=function OR kind=method) name^=_"
muse query "NOT kind=import file~=billing"
muse query kind=function name~=validate --all-commits
muse query hash=a3f2c9 --all-commits --first
muse query --commit v1.0 kind=class
muse query kind=function --json
```

#### Predicate Grammar (v2)

```
expr    = or_expr
or_expr = and_expr ( "OR" and_expr )*
and_expr = not_expr ( and_expr )*    # implicit AND
not_expr = "NOT" primary | primary
primary  = "(" expr ")" | atom
atom     = KEY OP VALUE
```

#### Operators

| Operator | Meaning |
|---|---|
| `=` | Exact match (case-insensitive for strings) |
| `~=` | Contains substring |
| `^=` | Starts with |
| `$=` | Ends with |
| `!=` | Not equal |
| `>=` | Greater than or equal (lineno keys only) |
| `<=` | Less than or equal (lineno keys only) |

#### Keys

| Key | Type | Description |
|---|---|---|
| `kind` | string | `function`, `class`, `method`, `variable`, `import`, … |
| `language` | string | `Python`, `Go`, `Rust`, `TypeScript`, … |
| `name` | string | Bare symbol name |
| `qualified_name` | string | Dotted qualified name (e.g. `MyClass.save`) |
| `file` | string | File path (relative to repo root) |
| `hash` | string | `content_id` prefix (hex) |
| `body_hash` | string | `body_hash` prefix |
| `signature_id` | string | `signature_id` prefix |
| `lineno_gt` | integer | Symbol starts *after* this line number |
| `lineno_lt` | integer | Symbol starts *before* this line number |

#### Flags

| Flag | Description |
|---|---|
| `--commit REF` | Query a specific commit (mutually exclusive with `--all-commits`) |
| `--all-commits` | Walk all commits, deduplicate by `content_id`, annotate first-seen commit |
| `--first` | With `--all-commits`: keep only the first appearance of each unique body |
| `--json` | JSON output with `schema_version: 2` wrapper |

---

### `muse query-history PREDICATE... [--from REF] [--to REF]`

Temporal symbol search — track matching symbols across a commit range.

```
muse query-history kind=function language=Python
muse query-history name~=validate --from v1.0 --to HEAD
muse query-history kind=class --json
```

**Output:** For each matching symbol address, reports `first_seen`, `last_seen`, `commit_count` (how many commits touched it), and `change_count` (how many times its `content_id` changed).

**JSON schema:**
```json
{
  "schema_version": 2,
  "query": "kind=function language=Python",
  "from_ref": "v1.0",
  "to_ref": "HEAD",
  "results": [
    {
      "address": "src/billing.py::compute_total",
      "first_seen": "commit_id...",
      "last_seen": "commit_id...",
      "commit_count": 12,
      "change_count": 3
    }
  ]
}
```

---

## 4. Index Infrastructure

### `muse index status`

Show present/absent/corrupt status and entry counts for all local indexes.

```
muse index status
muse index status --json
```

### `muse index rebuild`

Rebuild one or all indexes by walking the full commit history.

```
muse index rebuild
muse index rebuild --index symbol_history
muse index rebuild --index hash_occurrence
```

**Flags:**
- `--index NAME` — rebuild only this index (default: all)

### Index Design

Indexes live under `.muse/indices/` and are:
- **Derived** — computed entirely from the commit history.
- **Optional** — no command requires them for correctness; they only provide speed.
- **Fully rebuildable** — `muse index rebuild` reconstructs them from scratch in one pass.
- **Versioned** — `schema_version` field for forward compatibility.

#### `symbol_history` index

Maps `symbol_address → list[HistoryEntry]` (chronological).  Enables O(1) lineage lookups instead of O(commits × files) scans.

#### `hash_occurrence` index

Maps `body_hash → list[symbol_address]`.  Enables O(1) clone detection and `muse find-symbol hash=` queries.

---

## 5. Symbol Identity Detail

### New `SymbolRecord` fields

`SymbolRecord` gains two backward-compatible fields (empty string `""` for pre-v2 records):

**`metadata_id`**
: SHA-256 of the symbol's *metadata wrapper* — decorators + async flag for Python functions, decorator list + base classes for Python classes.  Allows distinguishing a decorator change from a body change.

**`canonical_key`**
: `{file}#{scope}#{kind}#{name}#{lineno}` — a stable, unique machine handle for a symbol within a snapshot.  Enables agent-to-agent symbol handoff without re-querying.  Disambiguates overloaded names and nested scopes.

### `muse detect-refactor` (v2 output)

With `--json`, emits `schema_version: 2` with a richer classification:

```json
{
  "schema_version": 2,
  "from_commit": "abc...",
  "to_commit": "def...",
  "total": 3,
  "events": [
    {
      "old_address": "src/billing.py::compute_total",
      "new_address": "src/billing.py::compute_invoice_total",
      "old_kind": "function",
      "new_kind": "function",
      "exact_classification": "rename",
      "inferred_refactor": "none",
      "confidence": 1.0,
      "evidence": ["body_hash matches a1b2c3d4"],
      "old_content_id": "ab12cd34",
      "new_content_id": "ef56gh78",
      "old_body_hash": "a1b2c3d4",
      "new_body_hash": "a1b2c3d4"
    }
  ]
}
```

**`exact_classification`** values: `rename`, `move`, `rename+move`, `signature_only`, `impl_only`, `metadata_only`, `full_rewrite`, `unchanged`.

**`inferred_refactor`** values: `extract`, `inline`, `split`, `merge`, `none`.

---

## 6. Multi-Agent Coordination Layer

The coordination layer enables thousands of agents to work on the same codebase simultaneously without stepping on each other.  It is **purely advisory** — the VCS engine never reads coordination data for correctness decisions.  Agents that ignore it still produce correct commits.

### Storage Layout

```
.muse/coordination/
  reservations/<uuid>.json   advisory symbol lease
  intents/<uuid>.json        declared operation before edit
```

All records are **write-once** (never mutated) and use TTL-based expiry.  Expired records are kept for audit purposes but ignored by all commands.

---

### `muse reserve ADDRESS... [OPTIONS]`

Announce intent to edit one or more symbol addresses.

```
muse reserve src/billing.py::compute_total
muse reserve src/billing.py::f1 src/billing.py::f2 --run-id agent-007 --ttl 7200
muse reserve src/billing.py::compute_total --op rename
muse reserve src/billing.py::compute_total --json
```

**Flags:**
- `--run-id ID` — identifier for this agent/run (default: random UUID)
- `--ttl SECONDS` — reservation expiry in seconds (default: 3600)
- `--op OPERATION` — declared operation: `rename`, `move`, `extract`, `modify`, `delete`
- `--json` — JSON output

**Conflict detection:** Warns (but never blocks) if any of the requested addresses are already reserved by another active reservation.

**Reservation schema (v1):**
```json
{
  "schema_version": 1,
  "reservation_id": "<uuid>",
  "run_id": "<agent-supplied ID>",
  "branch": "<current branch>",
  "addresses": ["src/billing.py::compute_total"],
  "created_at": "2026-03-18T12:00:00+00:00",
  "expires_at": "2026-03-18T13:00:00+00:00",
  "operation": "rename"
}
```

---

### `muse intent ADDRESS... --op OPERATION [OPTIONS]`

Declare a specific operation before executing it.  More precise than a reservation; enables `muse forecast` to produce accurate conflict predictions.

```
muse intent src/billing.py::compute_total --op rename --detail "rename to compute_invoice_total"
muse intent src/billing.py::compute_total --op modify --reservation-id <uuid>
```

**Flags:**
- `--op OPERATION` *(required)* — `rename`, `move`, `extract`, `modify`, `delete`, `refactor`
- `--detail TEXT` — free-text description of the planned change
- `--reservation-id UUID` — link to an existing reservation
- `--run-id ID` — agent identifier
- `--json` — JSON output

**Intent schema (v1):**
```json
{
  "schema_version": 1,
  "intent_id": "<uuid>",
  "reservation_id": "<uuid or empty>",
  "run_id": "<agent ID>",
  "branch": "<current branch>",
  "addresses": ["src/billing.py::compute_total"],
  "operation": "rename",
  "created_at": "2026-03-18T12:00:00+00:00",
  "detail": "rename to compute_invoice_total"
}
```

---

### `muse forecast [OPTIONS]`

Predict merge conflicts from active reservations and intents — **before** writing any code.

```
muse forecast
muse forecast --branch feature-x
muse forecast --json
```

**Conflict types detected:**

| Type | Confidence | Condition |
|---|---|---|
| `address_overlap` | 1.0 | Two reservations on the same symbol address |
| `blast_radius_overlap` | 0.75 | Reservations on symbols that call each other (via call graph) |
| `operation_conflict` | 0.9 | Two reservations declare incompatible operations (e.g. both `rename`) |

**Flags:**
- `--branch BRANCH` — restrict to reservations on this branch
- `--json` — structured conflict list

---

### `muse plan-merge OURS THEIRS [OPTIONS]`

Dry-run semantic merge plan — classify all symbol conflicts without writing anything.

```
muse plan-merge main feature-x
muse plan-merge HEAD~5 HEAD --json
```

**Output:** Classifies each diverging symbol into one of:
- `no_conflict` — diverged in disjoint symbols
- `symbol_edit_overlap` — both sides modified the same symbol
- `rename_edit` — one side renamed, the other modified
- `delete_use` — one side deleted a symbol still used by the other

**Flags:**
- `--json` — structured output with full classification details

---

### `muse shard --agents N [OPTIONS]`

Partition the codebase into N low-coupling work zones for parallel agent assignment.

```
muse shard --agents 4
muse shard --agents 8 --language Python
muse shard --agents 4 --json
```

**Algorithm:** Builds the import graph, finds connected components, greedily merges small components into N balanced shards (by symbol count).  Reports cross-shard edges as a coupling score (lower is better).

**Flags:**
- `--agents N` *(required)* — number of shards
- `--language LANG` — restrict to files of this language
- `--json` — shard assignments as JSON

---

### `muse reconcile [OPTIONS]`

Recommend merge ordering and integration strategy from the current coordination state.

```
muse reconcile
muse reconcile --json
```

**Output:** For each active branch with reservations, recommends:
- **Merge order** — branches with fewer predicted conflicts should merge first
- **Integration strategy** — `fast-forward`, `rebase`, or `manual` (when conflicts are predicted)
- **Conflict hotspots** — addresses that appear in the most reservations

---

## 7. Merge Engine & Architectural Enforcement

### `ConflictRecord` — Structured Conflict Taxonomy

`MergeResult` now carries `conflict_records: list[ConflictRecord]` alongside the existing `conflicts: list[str]`.  Each `ConflictRecord` provides structured metadata for programmatic conflict handling:

```python
@dataclass
class ConflictRecord:
    path: str
    conflict_type: str = "file_level"   # see taxonomy below
    ours_summary: str = ""
    theirs_summary: str = ""
    addresses: list[str] = field(default_factory=list)
```

**`conflict_type` taxonomy:**

| Value | Meaning |
|---|---|
| `symbol_edit_overlap` | Both branches modified the same symbol |
| `rename_edit` | One branch renamed, the other modified |
| `move_edit` | One branch moved, the other modified |
| `delete_use` | One branch deleted a symbol still used by the other |
| `dependency_conflict` | Conflicting changes to interdependent symbols |
| `file_level` | Legacy — no symbol-level information available |

---

### `muse breakage`

Detect symbol-level structural breakage in the current working tree vs HEAD.

```
muse breakage
muse breakage --language Python
muse breakage --json
```

**Checks performed:**

1. **`stale_import`** — a `from X import Y` where `Y` no longer exists in the committed version of `X` (detected via symbol graph, not execution).
2. **`missing_interface_method`** — a class body is missing a method that exists in the HEAD snapshot's version of that class.

**What it does NOT do:** Execute code, install packages, run mypy or a type checker, or access the network.  Pure structural analysis.

**JSON output:**
```json
{
  "breakage_count": 2,
  "issues": [
    {
      "issue_type": "stale_import",
      "file": "src/billing.py",
      "description": "imports compute_total from src/utils.py but compute_total was removed"
    }
  ]
}
```

---

### `muse invariants`

Enforce architectural rules declared in `.muse/invariants.toml`.

```
muse invariants
muse invariants --commit HEAD~5
muse invariants --json
```

**Rule types:**

#### `no_cycles`
```toml
[[rules]]
type = "no_cycles"
name = "no import cycles"
```
The import graph must be a DAG.  Reports every cycle as a violation.

#### `forbidden_dependency`
```toml
[[rules]]
type = "forbidden_dependency"
name = "core must not import cli"
source_pattern = "muse/core/"
forbidden_pattern = "muse/cli/"
```
Files matching `source_pattern` must not import from files matching `forbidden_pattern`.

#### `layer_boundary`
```toml
[[rules]]
type = "layer_boundary"
name = "plugins must not import from cli"
lower = "muse/plugins/"
upper = "muse/cli/"
```
Files in `lower` must not import from files in `upper` (enforces layered architecture).

#### `required_test`
```toml
[[rules]]
type = "required_test"
name = "all billing functions must have tests"
source_pattern = "src/billing.py"
test_pattern = "tests/test_billing.py"
```
Every public function in `source_pattern` must have a corresponding test function in `test_pattern` (matched by bare name).

**Bootstrapping:** If `.muse/invariants.toml` does not exist, `muse invariants` creates it with a commented template and exits with a guided onboarding message.

---

## 8. Semantic Versioning

Muse automatically assigns semantic version bumps at commit time based on the `StructuredDelta`.

### `SemVerBump`

```python
SemVerBump = Literal["major", "minor", "patch", "none"]
```

### Inference rules (`infer_sem_ver_bump`)

| Change type | Bump | Breaking? |
|---|---|---|
| Delete a public symbol | `major` | yes — address added to `breaking_changes` |
| Rename a public symbol | `major` | yes — old address added to `breaking_changes` |
| `signature_only` change | `major` | yes — callers may break |
| Insert a new public symbol | `minor` | no |
| `impl_only` change (body only) | `patch` | no |
| `metadata_only` change | `none` | no |
| Formatting-only change | `none` | no |
| Non-public symbol changes | `patch` or `none` | no |

**Public** = name does not start with `_` and kind is `function`, `class`, `method`, or `async_function`.

### Storage

Both `StructuredDelta` and `CommitRecord` carry:
- `sem_ver_bump: SemVerBump` (default `"none"`)
- `breaking_changes: list[str]` (default `[]`)

These fields are backward-compatible — pre-v2 commits read as `"none"` / `[]`.

### `muse log` display

When a commit's `sem_ver_bump` is non-`none`, long-form `muse log` output appends:
```
SemVer:   MAJOR
Breaking: src/billing.py::compute_total, src/billing.py::Invoice (+2 more)
```

---

## 9. Call-Graph Tier Commands

### `muse impact ADDRESS [OPTIONS]`

Transitive blast-radius analysis — what else breaks if this function changes?

```
muse impact src/billing.py::compute_total
muse impact src/billing.py::compute_total --commit HEAD~5
muse impact src/billing.py::compute_total --json
```

**Algorithm:** BFS over the reverse call graph (Python only via `ast`).  Traverses until the transitive closure is exhausted, annotating each affected symbol with its depth.

**Risk levels:** 🟢 (0–2 callers), 🟡 (3–9 callers), 🔴 (10+ callers).

---

### `muse dead [OPTIONS]`

Dead code detection — symbols with no callers and no importers.

```
muse dead
muse dead --kind function
muse dead --exclude-tests
muse dead --json
```

**Detection logic:** A symbol is a dead-code candidate when:
1. Its bare name appears in no `ast.Call` node in the snapshot **and**
2. Its module is not imported anywhere in the snapshot.

**Distinction:** `definite_dead` (module never imported) vs `soft_dead` (module imported but function never called directly).

---

### `muse coverage CLASS_ADDRESS [OPTIONS]`

Class interface call-coverage — which methods of a class are actually called?

```
muse coverage src/billing.py::Invoice
muse coverage src/billing.py::Invoice --show-callers
muse coverage src/billing.py::Invoice --json
```

**Output:** Lists every method of the class, marks which ones appear in `ast.Call` nodes anywhere in the snapshot, and prints a coverage percentage.  No test suite required.

---

### `muse deps ADDRESS_OR_FILE [OPTIONS]`

Import graph + call-graph analysis.

```
muse deps src/billing.py
muse deps src/billing.py --reverse
muse deps src/billing.py::compute_total
muse deps src/billing.py::compute_total --reverse
muse deps src/billing.py --commit v1.0 --json
```

**File mode:** Lists all `import`-kind symbols from the file (what does it import?).  With `--reverse`: which other files import this one.

**Symbol mode** (`address` contains `::`): Python-only call extraction — which functions does this function call?  With `--reverse`: which functions call this one.

---

### `muse find-symbol [OPTIONS]`

Cross-commit, cross-branch symbol search by hash, name, or kind.

```
muse find-symbol --hash a3f2c9
muse find-symbol --name compute_total
muse find-symbol --name compute_* --kind function
muse find-symbol --hash a3f2c9 --all-branches --first
muse find-symbol --name validate --json
```

**Flags:**
- `--hash HEX` — match `content_id` prefix (exact body match across history)
- `--name NAME` — exact name or prefix glob with `*`
- `--kind KIND` — restrict to symbol kind
- `--all-branches` — also scan all branch tips in `.muse/refs/heads/`
- `--first` — deduplicate on `content_id`, keeping only the first appearance
- `--json` — structured output

---

### `muse patch ADDRESS SOURCE [OPTIONS]`

Surgical semantic patch — replace exactly one named symbol in the working tree.

```
muse patch src/billing.py::compute_total new_impl.py
echo "def compute_total(x): return x * 2" | muse patch src/billing.py::compute_total -
muse patch src/billing.py::compute_total new_impl.py --dry-run
```

**Syntax validation:** Before writing, validates the replacement source with:
- `ast.parse` for Python
- `tree-sitter` CST error-node check for all 11 supported languages

Rejects the patch and exits non-zero if the source has syntax errors.

**Flags:**
- `--dry-run` — print the unified diff without writing
- `--json` — structured output with patch result

---

## 10. Architecture Internals

### Module Map

```
muse/
  plugins/code/
    plugin.py           MidiPlugin → CodePlugin (MuseDomainPlugin + StructuredMergePlugin)
    ast_parser.py       Python AST → SymbolRecord; validate_syntax() for all 11 languages
    symbol_diff.py      diff_symbol_trees() — O(n) diffing, rename/move annotation
    _query.py           symbols_for_snapshot(), walk_commits(), language_of()
    _predicate.py       Predicate DSL parser — tokenise → recursive descent → Predicate callable
    _callgraph.py       ForwardGraph, ReverseGraph, build_*, transitive_callers BFS
    _refactor_classify.py  classify_exact(), classify_composite(), RefactorClassification
  core/
    coordination.py     Reservation, Intent, create/load helpers, .muse/coordination/
    indices.py          SymbolHistoryIndex, HashOccurrenceIndex, save/load/rebuild
```

### Language Support

| Language | Extension(s) | Parser | Symbol types |
|---|---|---|---|
| Python | `.py` | `ast` (stdlib) | function, async_function, class, method, variable, import |
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` | tree-sitter | function, class, method |
| TypeScript | `.ts` `.tsx` | tree-sitter | function, class, method, interface, type_alias, enum |
| Go | `.go` | tree-sitter | function (method qualified as `Type.Method`) |
| Rust | `.rs` | tree-sitter | function (impl method qualified as `Type.method`) |
| Java | `.java` | tree-sitter | class, interface, method, constructor, enum |
| C | `.c` `.h` | tree-sitter | function_definition |
| C++ | `.cpp` `.cc` `.cxx` `.hpp` | tree-sitter | function, class, struct |
| C# | `.cs` | tree-sitter | class, interface, struct, method, constructor, enum |
| Ruby | `.rb` | tree-sitter | class, module, method, singleton_method |
| Kotlin | `.kt` `.kts` | tree-sitter | function, class, method |

### Layer Rules

- `muse/core/*` is domain-agnostic — never imports from `muse/plugins/*`
- `muse/cli/commands/*` are thin — delegate all logic to `muse/core/*` or plugin helpers
- `muse/plugins/code/*` is the only layer that imports domain-specific AST logic
- `muse/core/coordination.py` and `muse/core/indices.py` are domain-agnostic helpers

---

## 11. Type Reference

### `SymbolRecord` (TypedDict)

```python
class SymbolRecord(TypedDict):
    kind: str              # function | class | method | variable | import | …
    name: str              # bare name
    qualified_name: str    # dotted path (e.g. MyClass.save)
    lineno: int
    end_lineno: int
    content_id: str        # SHA-256 of full normalized AST
    body_hash: str         # SHA-256 of body only
    signature_id: str      # SHA-256 of signature only
    metadata_id: str       # SHA-256 of decorators + async + bases (v2, "" for pre-v2)
    canonical_key: str     # {file}#{scope}#{kind}#{name}#{lineno} (v2, "" for pre-v2)
```

### `StructuredDelta`

```python
class StructuredDelta(TypedDict):
    domain: str
    ops: list[DomainOp]
    summary: str
    sem_ver_bump: SemVerBump          # default "none"
    breaking_changes: list[str]       # default []
```

### `DomainOp` union

```python
DomainOp = InsertOp | DeleteOp | ReplaceOp | MoveOp | PatchOp
```

Each op is a `TypedDict` discriminated by a `Literal` `"op"` field.

### `ConflictRecord` (dataclass)

```python
@dataclass
class ConflictRecord:
    path: str
    conflict_type: str = "file_level"
    ours_summary: str = ""
    theirs_summary: str = ""
    addresses: list[str] = field(default_factory=list)
```

### `Reservation`

```python
class Reservation:
    reservation_id: str
    run_id: str
    branch: str
    addresses: list[str]
    created_at: datetime
    expires_at: datetime
    operation: str | None
    def is_active(self) -> bool: ...
    def to_dict(self) -> dict[str, str | int | list[str] | None]: ...
    @classmethod
    def from_dict(cls, d) -> Reservation: ...
```

### `Intent`

```python
class Intent:
    intent_id: str
    reservation_id: str
    run_id: str
    branch: str
    addresses: list[str]
    operation: str
    created_at: datetime
    detail: str
    def to_dict(self) -> dict[str, str | int | list[str]]: ...
    @classmethod
    def from_dict(cls, d) -> Intent: ...
```

### `SemVerBump`

```python
SemVerBump = Literal["major", "minor", "patch", "none"]
```

### `Predicate`

```python
Predicate = Callable[[str, SymbolRecord], bool]
# first arg: file_path
# second arg: SymbolRecord
# returns: True if the symbol matches the predicate
```

### `ExactClassification`

```python
ExactClassification = Literal[
    "rename", "move", "rename+move",
    "signature_only", "impl_only", "metadata_only",
    "full_rewrite", "unchanged",
]
```

### `InferredRefactor`

```python
InferredRefactor = Literal["extract", "inline", "split", "merge", "none"]
```

---

## Further Reading

- [Plugin Authoring Guide](plugin-authoring-guide.md) — implementing `MuseDomainPlugin`
- [Type Contracts](type-contracts.md) — strict typing rules and enforcement
- [CRDT Reference](crdt-reference.md) — CRDT and OT merge primitives
- [Tour de Force — Code](../demo/tour-de-force-code.md) — full narrative walkthrough of all code commands
- [Tour de Force — Music](../demo/tour-de-force-music.md) — MIDI domain reference demo
