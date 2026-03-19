# Muse Code Plugin — Demo

> **The question is not "why would you use Muse instead of Git?"**
> **The question is: "how did you ever live without this?"**

This is a walk-through of every code-domain capability in Muse — 12 commands
that treat your codebase as what it actually is: a **typed, content-addressed
graph of named, versioned symbols**.  Not lines.  Not files.  Symbols.

Every command below is strictly impossible in Git.  Read on.

---

## Setup

```bash
muse init --domain code
echo "class Invoice: ..." > src/billing.py
muse commit -m "Add billing module"
# ... several more commits ...
```

---

## Act I — What's in the Snapshot?

### `muse symbols` — see every named thing

```
$ muse symbols

src/billing.py
  class       Invoice                            line   4
  method      Invoice.__init__                   line   8
  method      Invoice.compute_total              line  18
  method      Invoice.apply_discount             line  25

src/auth.py
  class       AuthService                        line   3
  method      AuthService.validate_token         line  11
  function    generate_token                     line  28

src/utils.py
  function    retry                              line   2
  function    sha256_bytes                       line  12

3 files, 9 symbols
```

**Why Git can't do this:** `git ls-files` gives you filenames.  `muse symbols`
gives you the semantic inventory — functions, classes, methods — extracted from
actual ASTs across 10 languages (Python, TypeScript, JavaScript, Go, Rust, Java,
C, C++, C#, Ruby, Kotlin).

---

## Act II — Grep the Symbol Graph

### `muse grep` — semantic symbol search

```
$ muse grep "validate"

  src/auth.py::AuthService.validate_token    method     line 11
  src/billing.py::validate_amount            function   line 34

2 match(es) across 2 file(s)
```

```
$ muse grep "^Invoice" --kind class --regex

  src/billing.py::Invoice                    class      line  4

1 match(es) across 1 file(s)
```

```
$ muse grep "handle" --language Go

  api/server.go::Server.HandleRequest        method     line 12
  api/server.go::handleError                 function   line 28

2 match(es) across 1 file(s)
```

**Why Git can't do this:** `git grep "validate"` searches raw text lines.  It
finds every comment, every string literal, every `# validate_token is deprecated`
in your codebase.  `muse grep` searches the *typed symbol graph* — only actual
symbol declarations, with their kind, language, and stable identity hash.  Zero
false positives.

---

## Act III — Query the Symbol Graph

### `muse query` — SQL for your codebase

```
$ muse query "kind=function" "language=Python" "name~=validate"

  src/billing.py::validate_amount    fn   line 34
  src/auth.py::validate_token        fn   line 11

2 match(es) across 2 file(s)  [kind=function  AND  language=Python  AND  name~=validate]
```

```
$ muse query "kind=method" "name^=__"

  src/billing.py::Invoice.__init__      method   line  8
  src/models.py::User.__init__          method   line  9
  src/models.py::User.__repr__          method   line 24

3 match(es) across 2 file(s)  [kind=method  AND  name^=__]
```

```
$ muse query "hash=a3f2c9" --hashes

  src/billing.py::validate_amount        fn   line 34   a3f2c9..
  src/payments.py::validate_payment      fn   line  7   a3f2c9..

2 match(es) across 2 file(s)  [hash=a3f2c9]
```

**The `hash` predicate is uniquely powerful.** `muse query "hash=a3f2c9"` finds
every symbol across your entire repo whose normalized AST is byte-for-byte
identical to the one with that hash prefix.  Copy detection.  Duplication
tracking.  Cross-module clone detection.  This has no analogue anywhere in
Git's model — or any other VCS.

Predicate operators: `=` (exact), `~=` (contains), `^=` (starts with), `$=` (ends with).
Predicate keys: `kind`, `language`, `name`, `file`, `hash`.

---

## Act IV — Language Breakdown

### `muse languages` — composition at a glance

```
$ muse languages

Language breakdown — commit cb4afaed

  Python       8 files    43 symbols  (fn: 18,  class: 5,  method: 20)
  TypeScript   3 files    12 symbols  (fn:  4,  class: 3,  method:  5)
  Go           2 files     8 symbols  (fn:  6,  method: 2)
  Rust         1 file      4 symbols  (fn:  2,  method: 2)
  ────────────────────────────────────────────────────────────────────
  Total       14 files    67 symbols  (4 languages)
```

One command.  Instant polyglot codebase inventory.  No scripts, no cloc,
no custom tooling.

---

## Act V — Who Changed What?

### `muse blame` — per-symbol attribution

```
$ muse blame "src/billing.py::Invoice.compute_total"

src/billing.py::Invoice.compute_total
──────────────────────────────────────────────────────────────
last touched:  cb4afaed  2026-03-16
author:        alice
message:       "Perf: optimise compute_total with vectorisation"
change:        implementation changed

previous:      1d2e3faa  2026-03-15
change:        renamed from calculate_total

before that:   a3f2c9e1  2026-03-14
change:        created
```

**Why Git can't do this:** `git blame src/billing.py` gives you 300 attribution
entries for a 300-line file — one per line, including blank lines, docstrings,
and closing braces.  `muse blame` gives you **one answer per function**: this
commit, this author, this specific kind of change.  That's the level of
precision code review actually needs.

---

## Act VI — Symbol History

### `muse symbol-log` — the life of a function

```
$ muse symbol-log "src/billing.py::Invoice.compute_total"

Symbol timeline: src/billing.py::Invoice.compute_total

  cb4afaed  2026-03-16  implementation changed  "Perf: optimise..."
  1d2e3faa  2026-03-15  renamed from calculate_total  "Refactor billing API"
  a3f2c9e1  2026-03-14  created  "Add billing module"

3 events tracked across 3 commits
```

`muse symbol-log` follows renames and cross-file moves automatically.
If `compute_total` was called `calculate_total` last week, you get the
full continuous history — not the truncated stub that `git log -- src/billing.py`
would give you after a rename.

---

## Act VII — Detect Refactoring

### `muse detect-refactor` — classify semantic changes

```
$ muse detect-refactor HEAD~5..HEAD

Semantic refactoring — HEAD~5..HEAD
Commits analysed: 5

  rename    src/billing.py::calculate_total      → compute_total          (cb4afaed)
  rename    src/billing.py::apply_tax            → apply_vat              (cb4afaed)
  move      src/billing.py::validate_amount      → src/validation.py      (1d2e3faa)
  signature src/auth.py::AuthService.login       signature changed        (a3f2c9e1)

4 refactoring operations across 3 commits
```

**Why Git can't do this:** Git knows nothing about renames at the function
level.  It might guess at file renames if the diff is similar enough.
`muse detect-refactor` reads the structured delta stored in every commit
and classifies operations with precision: rename, move, signature change,
implementation change.  No guessing.

---

## Act VIII — Where is the Instability?

### `muse hotspots` — symbol churn leaderboard

```
$ muse hotspots --top 10

Symbol churn — top 10 most-changed symbols
Commits analysed: 47

  1   src/billing.py::Invoice.compute_total     12 changes
  2   src/api.py::handle_request                 9 changes
  3   src/auth.py::AuthService.validate_token    7 changes
  4   src/models.py::User.save                   5 changes
  5   src/billing.py::Invoice.apply_discount     4 changes

High churn = instability signal. Consider refactoring or adding tests.
```

`muse hotspots` is the complexity map of your codebase.  The functions that
change most are the ones most likely to harbour bugs, missing abstractions,
or untested edge cases.  In a mature CI pipeline, this list drives test
coverage prioritisation.

```bash
# Scope to Python functions only, last 30 commits
muse hotspots --kind function --language Python --from HEAD~30 --top 5
```

**Why Git can't do this:** File-level churn (how many lines changed in
`billing.py`) misses the signal.  A 1,000-line file might have 999 stable
lines and one function that burns.  `muse hotspots` finds that function.

---

## Act IX — Where is the Bedrock?

### `muse stable` — symbol stability leaderboard

```
$ muse stable --top 10

Symbol stability — top 10 most stable symbols
Commits analysed: 47

  1   src/core.py::sha256_bytes          unchanged for 47 commits  (since first commit)
  2   src/core.py::content_hash          unchanged for 43 commits
  3   src/utils.py::retry                unchanged for 38 commits
  4   src/models.py::BaseModel.__init__  unchanged for 34 commits

These are your bedrock. High stability = safe to build on.
```

These are your load-bearing walls.  New engineers can build on them.
Agents can call them without reading their implementation.  If you're
designing a new feature, start here — find the stable primitives and
compose upward.

---

## Act X — Hidden Dependencies

### `muse coupling` — co-change analysis

```
$ muse coupling --top 10

File co-change analysis — top 10 most coupled pairs
Commits analysed: 47

  1   src/billing.py      ↔  src/models.py             co-changed in 18 commits
  2   src/api.py          ↔  src/auth.py                co-changed in 12 commits
  3   src/billing.py      ↔  tests/test_billing.py      co-changed in 11 commits
  4   src/models.py       ↔  src/billing.py             co-changed in  9 commits

High coupling = hidden dependency. Consider extracting a shared interface.
```

`billing.py` and `models.py` co-change in 18 out of 47 commits — 38% of
your commit history.  There's no import between them that would reveal
this dependency to a static analyser.  But Muse sees it in the commit
graph.  Extract a `BillingProtocol` interface, define the contract explicitly,
and watch the coupling drop.

**Why Git can't do this cleanly:** A Git tool could count raw file
co-modifications.  `muse coupling` counts *semantic* co-changes — commits
where both files had AST-level symbol modifications.  Formatting-only
edits and non-code files are excluded.  The signal is real.

---

## Act XI — Release Semantic Diff

### `muse compare` — any two historical snapshots

```
$ muse compare v1.0 v2.0

Semantic comparison
  From: a3f2c9e1  "Release v1.0"
  To:   cb4afaed  "Release v2.0"

src/billing.py
  modified  Invoice.compute_total          (renamed from calculate_total)
  modified  Invoice.apply_discount         (signature changed)
  removed   validate_amount               (moved to src/validation.py)

src/validation.py  (new file)
  added     validate_amount               (moved from src/billing.py)
  added     validate_payment

api/server.go  (new file)
  added     Server.HandleRequest
  added     handleError
  added     process

src/auth.py
  modified  AuthService.validate_token    (implementation changed)

9 symbol change(s) across 4 file(s)
```

**This is the semantic changelog for your release** — automatically.  No
manual writing, no diff archaeology.  Every function that was added, removed,
renamed, moved, or modified between v1.0 and v2.0, classified and attributed.

`muse compare` reads both snapshots from the content-addressed object store,
parses their AST symbol trees, and diffs them.  Any two refs — tags, branches,
commit IDs, relative refs (`HEAD~10`).

---

## Act XII — The Agent Interface

### `muse patch` — surgical semantic modification

This is where Muse becomes the version control system of the AI age.

```bash
$ cat new_compute_total.py
def compute_total(self, items: list[Item], tax_rate: Decimal = Decimal("0")) -> Decimal:
    subtotal = sum(item.price * (1 - item.discount) for item in items)
    return subtotal * (1 + tax_rate)

$ muse patch "src/billing.py::Invoice.compute_total" --body new_compute_total.py

✅ Patched src/billing.py::Invoice.compute_total
   Lines 18–24 replaced (was 7 lines, now 3 lines)
   Surrounding code untouched (8 symbols preserved)
   Run `muse status` to review, then `muse commit`
```

**This is the paradigm shift for agents:**

An AI agent that needs to change `Invoice.compute_total` can do so with
surgical precision.  It constructs a new function body, calls `muse patch`,
and the change is applied at the *symbol* level — not the line level, not
the file level.  No risk of accidentally touching adjacent functions.  No
diff noise.  No merge required.

```bash
# Preview without writing
muse patch "src/billing.py::Invoice.compute_total" --body new_body.py --dry-run

# Apply from stdin — pipe directly from an AI agent's output
cat <<'EOF' | muse patch "src/auth.py::generate_token" --body -
def generate_token(user_id: str, ttl: int = 3600) -> str:
    payload = {"sub": user_id, "exp": time.time() + ttl}
    return jwt.encode(payload, SECRET_KEY)
EOF
```

Now the full agent workflow:

```bash
# Agent identifies which function to change
muse blame "src/billing.py::Invoice.compute_total"

# Agent verifies current symbol state
muse symbols --file src/billing.py

# Agent applies the change surgically
muse patch "src/billing.py::Invoice.compute_total" --body /tmp/new_impl.py

# Agent verifies semantic correctness
muse status

# Agent commits with structured metadata
muse commit -m "Optimise compute_total: vectorised sum over items"
```

The structured delta captured in that commit will record exactly:
- Which symbol changed (address: `src/billing.py::Invoice.compute_total`)
- What kind of change (implementation, signature, both)
- The old and new content hashes (for rollback, attribution, clone detection)

And immediately after, any agent in the world can run:

```bash
muse blame "src/billing.py::Invoice.compute_total"
```

And get a one-line answer: this commit, this agent, this change.

---

## The Full Command Matrix

| Command | What it does | Impossible in Git because… |
|---------|-------------|---------------------------|
| `muse symbols` | List all semantic symbols in a snapshot | Git has no AST model |
| `muse grep` | Search symbols by name/kind/language | `git grep` searches text lines |
| `muse query` | Predicate DSL over the symbol graph | Git has no typed graph |
| `muse languages` | Language + symbol-type breakdown | Git has no language awareness |
| `muse blame` | Per-symbol attribution (one answer) | `git blame` is per-line |
| `muse symbol-log` | Full history of one symbol across renames | Git loses history on rename |
| `muse detect-refactor` | Classify renames, moves, signature changes | Git cannot reason about symbols |
| `muse hotspots` | Symbol churn leaderboard | Git churn is file/line-level |
| `muse stable` | Symbol stability leaderboard | Git has no stability model |
| `muse coupling` | Semantic file co-change analysis | Git co-change is line-level noise |
| `muse compare` | Semantic diff between any two snapshots | `git diff` is line-level |
| `muse patch` | Surgical per-symbol modification | Git patches are line-level |

---

## For AI Agents

Muse is the version control system designed for the AI age.

When millions of agents are making millions of changes a minute, you need:

1. **Surgical writes** — `muse patch` modifies one symbol, leaves everything else alone
2. **Semantic reads** — `muse query`, `muse grep`, `muse symbols` return typed data, not text
3. **Instant attribution** — `muse blame` answers "who touched this?" in one command
4. **Stability signals** — `muse hotspots`, `muse stable` tell agents what's safe to build on
5. **Coupling maps** — `muse coupling` reveals the hidden dependencies agents need to respect
6. **Structured history** — every commit stores a symbol-level delta, machine-readable

Muse doesn't just store your code.  It understands it.

---

*Next: [Demo Script →](demo-script.md)*
