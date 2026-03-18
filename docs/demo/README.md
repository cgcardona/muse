# Muse — Demo Hub

> Domain-agnostic version control for multidimensional state.
> Music is the first domain. Code is the second. Genomics, 3D design, and
> spacetime simulation are next.

Choose a domain to see Muse's full power:

---

## [Code Tour de Force →](tour-de-force-code.md)

**12 commands that are strictly impossible in Git.**

Muse treats code as a typed, content-addressed graph of named symbols — not
a bag of text lines.  Every commit stores a symbol-level structured delta.
Every function has a stable identity hash that survives renames and moves.

| Command | One-line description |
|---------|---------------------|
| `muse symbols` | Every function, class, and method in the snapshot — extracted from real ASTs |
| `muse grep` | Search the symbol graph by name, kind, or language — no false positives |
| `muse query` | Predicate DSL: `kind=function language=Go name~=handle` |
| `muse languages` | Language + symbol-type breakdown across the whole repo |
| `muse blame` | Which commit last touched this exact function? One answer. |
| `muse symbol-log` | Full history of one symbol — renames and moves included |
| `muse detect-refactor` | Classify semantic operations: rename / move / signature / impl |
| `muse hotspots` | Symbol churn leaderboard — which functions change most? |
| `muse stable` | Symbol stability leaderboard — your bedrock, safe to build on |
| `muse coupling` | File co-change analysis — semantic hidden dependencies |
| `muse compare` | Deep semantic diff between any two historical snapshots |
| `muse patch` | Surgical per-symbol modification — the agent interface |

**Supported languages:** Python, TypeScript, JavaScript, Go, Rust, Java, C, C++, C#, Ruby, Kotlin

---

## [Music Tour de Force →](tour-de-force-script.md)

**The reference domain — version control for audio compositions.**

Muse treats MIDI and MusicXML as structured state.  Every note is a semantic
element.  Every commit records which bars changed, which instruments were added,
which tempo markers shifted.  Three-way merges happen at the note level —
two musicians can independently arrange the same song and merge without conflicts.

---

## Shared Architecture

Both domains build on the same engine:

```
Content-addressed object store  ← immutable, SHA-256
Snapshot manifest               ← file path → object hash
Structured delta                ← typed DomainOp tree (insert / delete / replace / move / patch)
Commit graph                    ← parent chain with structured deltas on every node
```

The code plugin adds:

```
AST symbol trees      ← SymbolRecord (kind, name, body_hash, signature_id, content_id)
Symbol-level diffs    ← PatchOp with child InsertOp/DeleteOp/ReplaceOp per symbol
Rename detection      ← body_hash match across addresses
Move detection        ← content_id match across files
```

Every code-domain command is a consumer of this data.  No new storage format.
No new protocol.  Just queries over the structured commit history.

---

## Four Semantic Layers

| Layer | What it stores | Used by |
|-------|---------------|---------|
| **Object store** | Raw file bytes, content-addressed | All domains |
| **Snapshot manifest** | `file_path → sha256` | `symbols`, `languages`, `compare` |
| **Structured delta** | Typed op tree per commit | `blame`, `hotspots`, `stable`, `coupling`, `detect-refactor`, `symbol-log` |
| **Symbol graph** | AST-parsed `SymbolRecord` per file | `grep`, `query`, `patch` |

---

*Muse v2 · Python 3.11 · zero runtime dependencies except `tree-sitter`*
