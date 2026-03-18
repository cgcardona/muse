# Muse Code Plugin — Tour de Force Demo Script

> **Format:** Terminal walkthrough narration. Run the commands live while narrating.
> Everything you type is real — no mocks, no pre-recorded output.
> Tone: direct, a little incredulous — like showing someone a tool you genuinely think
> changes the rules.
>
> **Companion:** Read `docs/demo/tour-de-force-script.md` first (music demo) to
> understand the shared infrastructure. This script builds on those ideas and
> applies them to the domain most engineers live in every day: source code.

---

## INTRO — Before typing anything (~90 s)

*(Camera on screen, terminal open, empty directory)*

Okay so — Git treats your code as text files.

Not functions. Not classes. Not imports. Not the call graph between modules.
Text files. Lines. Diffs.

That was the right abstraction in 1972. You had flat files, line editors, no
structure to speak of. A line diff was the best you could do.

But we've been doing version control the same way for fifty years, and
meanwhile the code we're versioning has gotten enormously more structured.
Your IDE knows what a function is. Your type checker knows what a function is.
Your language server knows what a function is.

Git still doesn't know what a function is.

Here's what that costs you. Rename a function across ten files, Git sees
ten modifications. Move a module, Git sees a delete and an add — it has a
flag called `--find-renames` that uses a heuristic based on line similarity
to *guess* that maybe these two things are the same function. Maybe.

Two engineers touch the same file — one modifies `calculate_total`, the other
modifies `validate_amount` — Git calls that a conflict. They didn't touch the
same thing. But Git doesn't know that. It sees the same file, both modified,
conflict.

I want to show you what happens when your version control system actually
understands what it's versioning.

This is Muse — `muse init --domain code`.

---

## ACT 1 — First Commit: Muse Sees Symbols, Not Lines (Steps 1–4)

*(Create and move into a new directory)*

```
$ mkdir my-api && cd my-api
$ muse init --domain code
✅ Initialized Muse repository (domain: code)
   .muse/ created
   Plugin: CodePlugin — AST-based semantic versioning
   Languages: Python · TypeScript · JavaScript · Go · Rust
              Java · C · C++ · C# · Ruby · Kotlin
```

Already different. Git's `git init` doesn't tell you what it understands about
your files. Muse tells you: eleven languages, AST-based, semantic.

Let me write some code.

```python
# src/billing.py
def calculate_total(items: list[Item]) -> Decimal:
    """Sum all item prices after applying discounts."""
    return sum(item.price * (1 - item.discount) for item in items)

def validate_amount(amount: Decimal) -> bool:
    """Return True if amount is positive and within policy limits."""
    return Decimal("0") < amount <= Decimal("1000000")

class Invoice:
    def __init__(self, customer_id: str, items: list[Item]) -> None:
        self.customer_id = customer_id
        self.items = items
        self.total = calculate_total(items)

    def to_dict(self) -> dict[str, str | Decimal]:
        return {"customer_id": self.customer_id, "total": str(self.total)}
```

```
$ muse commit -m "Add billing module: Invoice, calculate_total, validate_amount"
```

Now watch what `muse show` tells you:

```
$ muse show HEAD

commit a3f2c9e1  "Add billing module: Invoice, calculate_total, validate_amount"
Author: alice
Date:   2026-03-14

 A  src/billing.py
    └─ added function calculate_total
    └─ added function validate_amount
    └─ added class Invoice
    └─ added method Invoice.__init__
    └─ added method Invoice.to_dict

 5 symbols added across 1 file
```

Git's `git show` would tell you: five lines added, eight lines added. You'd
read the diff and reconstruct the meaning. Muse tells you the meaning directly:
five specific symbols — their names, their kinds, their identities — were added.

---

## ACT 2 — `muse symbols`: The Full Symbol Graph (~60 s)

*(Pause here — this is a brand new capability)*

Git has no command like this. There is no `git symbols`. Because Git doesn't
know what a symbol is.

```
$ muse symbols

commit a3f2c9e1  "Add billing module"

src/billing.py
  fn          calculate_total                        line   2
  fn          validate_amount                        line   8
  class       Invoice                                line   13
  method      Invoice.__init__                       line   14
  method      Invoice.to_dict                        line   19

5 symbol(s) across 1 file  (Python: 5)
```

Every function. Every class. Every method. Line number, kind, stable hash
identity. This is the semantic interior of your repository — not a file list,
not a diff — the actual *structure* of your code, queryable at any commit.

Filter by kind:

```
$ muse symbols --kind class

src/billing.py
  class       Invoice                                line   13

1 symbol(s) across 1 file  (Python: 1)
```

Filter to a specific file:

```
$ muse symbols --file src/billing.py

src/billing.py
  fn          calculate_total                        line   2
  fn          validate_amount                        line   8
  class       Invoice                                line   13
  method      Invoice.__init__                       line   14
  method      Invoice.to_dict                        line   19

5 symbol(s) across 1 file  (Python: 5)
```

Include content hashes — the stable identity Muse uses to detect renames
and cross-file moves:

```
$ muse symbols --hashes

src/billing.py
  fn          calculate_total                        line   2  a3f2c9..
  fn          validate_amount                        line   8  cb4afa..
  class       Invoice                                line   13  1d2e3f..
  method      Invoice.__init__                       line   14  4a5b6c..
  method      Invoice.to_dict                        line   19  7d8e9f..
```

*(Pause)*

That `a3f2c9..` is the SHA-256 hash of `calculate_total`'s **normalized AST**.
Not the raw text. The AST. If you reformat the function — add spaces, change
indentation, adjust comments — the hash is unchanged. The *semantic content*
didn't change. Muse knows that.

---

## ACT 3 — Rename Is Not a Delete + Add: The Body Hash (~90 s)

*(This is the thing that makes engineers stop and stare)*

Let me add some commits and then do a rename.

We're going to add a Go file to show multi-language support:

```go
// api/server.go
func HandleRequest(w http.ResponseWriter, r *http.Request) {
    ctx := r.Context()
    process(ctx, w, r)
}

func process(ctx context.Context, w http.ResponseWriter, r *http.Request) {
    // core request processing logic
}
```

```
$ muse commit -m "Add Go API server"
$ muse show HEAD
commit 8f9a0b1c  "Add Go API server"

 A  api/server.go
    └─ added function HandleRequest
    └─ added function process

 2 symbols added across 1 file
```

Now — a rename. Product decides `calculate_total` should be `compute_invoice_total`.
Clearer, more domain-specific.

```python
# src/billing.py — rename only, body unchanged
def compute_invoice_total(items: list[Item]) -> Decimal:
    """Sum all item prices after applying discounts."""
    return sum(item.price * (1 - item.discount) for item in items)
```

```
$ muse commit -m "Rename: calculate_total → compute_invoice_total (domain clarity)"
$ muse show HEAD

commit 1d2e3faa  "Rename: calculate_total → compute_invoice_total (domain clarity)"

 M  src/billing.py
    └─ calculate_total → renamed to compute_invoice_total

 1 rename detected
```

Not "1 line deleted, 1 line added." Not "function name changed." Specifically:
**renamed to compute_invoice_total**. Muse detected this by comparing the
`body_hash` of every removed symbol against every added symbol. Same body,
different name — that's a rename. It's mathematically certain, not a heuristic.

Git's `--find-renames` flag gets confused when files get refactored. It needs
enough unchanged lines to fire the similarity threshold. A one-line function?
Git has no idea. Muse doesn't care — it's comparing AST hashes, not counting
lines.

---

## ACT 4 — Cross-File Move: Content Identity Across the Repository (~90 s)

Now — a module extraction. The billing team grows. `validate_amount` should live
in a dedicated `validation` module. We move it.

```python
# src/validation.py — new file
def validate_amount(amount: Decimal) -> bool:
    """Return True if amount is positive and within policy limits."""
    return Decimal("0") < amount <= Decimal("1000000")
```

```python
# src/billing.py — validate_amount removed
from src.validation import validate_amount
```

```
$ muse commit -m "Extract: move validate_amount to validation module"
$ muse show HEAD

commit 4b5c6d7e  "Extract: move validate_amount to validation module"

 A  src/validation.py
    └─ added function validate_amount  [moved from src/billing.py::validate_amount]

 M  src/billing.py
    └─ validate_amount  [moved to src/validation.py::validate_amount]
    └─ added import::src.validation

 cross-file move detected: validate_amount
```

*(Stop and let this breathe)*

Git would show you: delete lines from `billing.py`, add lines to `validation.py`.
No connection between them. The cross-file relationship is invisible.

Muse shows you: `validate_amount` moved. Same `content_id` on both sides — SHA-256
of the normalized function AST — proves they're the same symbol. The connection is
explicit, permanent, in the DAG.

This matters six months from now when a new engineer asks: "Where did
`validate_amount` come from? Why does it live in `validation.py`?" With Git,
the answer is in a deleted-line diff somewhere in history. With Muse, the answer
is in the commit graph: it was extracted from `billing.py` on this date, in this
commit, for this reason.

---

## ACT 5 — Branching: Symbol-Level Merges (~2 min)

*(This is the moment Git breaks. This is where Muse wins.)*

Two engineers, `alice` and `bob`, both need to modify `billing.py`.

Alice is improving `compute_invoice_total`'s performance:

```
$ muse checkout -b alice/optimise-total
```

```python
# Alice's version — vectorised with numpy
def compute_invoice_total(items: list[Item]) -> Decimal:
    """Sum all item prices after applying discounts — vectorised."""
    prices = [item.price for item in items]
    discounts = [item.discount for item in items]
    return Decimal(str(sum(p * (1 - d) for p, d in zip(prices, discounts))))
```

```
$ muse commit -m "Perf: optimise compute_invoice_total with explicit vectorisation"
```

Bob is refactoring `Invoice.to_dict()` for a new API contract:

```
$ muse checkout main
$ muse checkout -b bob/invoice-v2
```

```python
# Bob's version — richer to_dict output
def to_dict(self) -> dict[str, str | Decimal | list[str]]:
    return {
        "customer_id": self.customer_id,
        "total": str(self.total),
        "item_count": str(len(self.items)),
        "currency": "USD",
    }
```

```
$ muse commit -m "API: extend Invoice.to_dict with item_count and currency"
```

Now we merge. In Git, this would be a conflict — same file, both modified.

```
$ muse checkout main
$ muse merge alice/optimise-total
✅ Merged 'alice/optimise-total' into 'main' (fast-forward)
```

```
$ muse merge bob/invoice-v2

✅ Merged 'bob/invoice-v2' into 'main' (clean)

   Symbol-level auto-merge:
     alice modified: src/billing.py::compute_invoice_total
     bob   modified: src/billing.py::Invoice.to_dict
     No shared symbols — operations commute
```

*(Hold on that output)*

Let that sink in. Same file. Both modified. Different symbols.

The operations **commute**: Alice's change to `compute_invoice_total` and Bob's
change to `Invoice.to_dict` have no dependency on each other. Applying them
in either order produces the same result. Muse detects this at the symbol level
and auto-merges without asking a human.

Git's answer to this is: conflict. You open the file, you see `<<<<<<< HEAD`,
you figure out which half is Alice and which half is Bob, you manually combine
them, you mark it resolved. Every time. Even when the two changes are trivially
independent.

---

## ACT 6 — Symbol Conflict: When They Actually Disagree (~90 s)

Let me show you what a *real* conflict looks like in Muse.

Two engineers both touch `compute_invoice_total`:

```
$ muse checkout -b carol/tax-handling
```

Carol adds tax calculation:
```python
def compute_invoice_total(items: list[Item], tax_rate: Decimal = Decimal("0")) -> Decimal:
    subtotal = sum(item.price * (1 - item.discount) for item in items)
    return subtotal * (1 + tax_rate)
```

```
$ muse commit -m "Feature: add tax_rate parameter to compute_invoice_total"
```

```
$ muse checkout main
$ muse checkout -b dave/currency-rounding
```

Dave adds currency rounding:
```python
def compute_invoice_total(items: list[Item]) -> Decimal:
    raw = sum(item.price * (1 - item.discount) for item in items)
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
```

```
$ muse commit -m "Fix: round compute_invoice_total to 2 decimal places"
```

Merge:

```
$ muse checkout main
$ muse merge carol/tax-handling
✅ Fast-forward

$ muse merge dave/currency-rounding

❌ Merge conflict — symbol-level:

   CONFLICT src/billing.py::compute_invoice_total
     carol modified: signature changed (added tax_rate parameter)
                     implementation changed
     dave  modified: signature unchanged
                     implementation changed
   
   These changes are not commutative.
   Resolve at: src/billing.py::compute_invoice_total
```

*(Point at the output)*

Notice what Muse tells you:
- **One symbol** — `compute_invoice_total`.
- **Exactly two descriptions** of what changed on each side.
- **Why they conflict** — Carol changed both signature and body; Dave changed body only.

Git would show you: `<<<<<<< HEAD` around the entire function, yours vs. theirs.
You'd manually reconstruct what both engineers were trying to do.

Muse shows you the conflict *at the semantic level*. The symbol name, the kind of
change on each side, exactly what you need to make a decision. And every other
symbol in the file — `validate_amount`, `Invoice.__init__`, `Invoice.to_dict` —
is already merged. You resolve one thing, not a file.

---

## ACT 7 — `muse symbol-log`: The History of a Single Function (~90 s)

*(This command does not exist in Git. Period.)*

```
$ muse symbol-log "src/billing.py::compute_invoice_total"

Symbol: src/billing.py::compute_invoice_total
──────────────────────────────────────────────────────────────

● a3f2c9e1  2026-03-14  "Add billing module"
  created       added function calculate_total

● 1d2e3faa  2026-03-15  "Rename: calculate_total → compute_invoice_total"
  renamed       calculate_total → compute_invoice_total
  (tracking continues as src/billing.py::compute_invoice_total)

● cb4afaed  2026-03-16  "Perf: optimise compute_invoice_total"
  modified      implementation changed

● 8f9a0b1c  2026-03-17  "Feature: add tax_rate parameter"
  signature     signature changed (implementation changed)

4 event(s)  (created: 1,  modified: 1,  renamed: 1,  signature: 1)
```

*(Pause)*

Every event in the life of this one function. When it was created. When it was
renamed — and the name it had *before* the rename. When its implementation changed.
When its signature changed.

In Git: `git log -p src/billing.py`. You get every commit that touched the file,
and you wade through line diffs for every function in the file to find the ones
that touched your function. Then you try to figure out if a rename happened by
looking at the diffs by eye.

In Muse: one command, one symbol address, complete semantic history.

And notice — Muse tracked through the rename. The function was called
`calculate_total` at the beginning. When it was renamed, Muse updated its
tracking identity. The entire history follows the function, not the name.

---

## ACT 8 — `muse detect-refactor`: The Refactoring Report (~60 s)

*(Another impossible-in-Git command)*

You've just onboarded to a new codebase. You want to understand what
structural changes happened in the last sprint.

```
$ muse detect-refactor --from HEAD~8 --to HEAD

Semantic refactoring report
From: a3f2c9e1  "Add billing module"
To:   4b5c6d7e  "Extract: move validate_amount to validation module"
──────────────────────────────────────────────────────────────

RENAME          src/billing.py::calculate_total
                → compute_invoice_total
                commit 1d2e3faa  "Rename: calculate_total → compute_invoice_total"

MOVE            src/billing.py::validate_amount
                → src/validation.py::validate_amount
                commit 4b5c6d7e  "Extract: move validate_amount to validation module"

SIGNATURE       src/billing.py::compute_invoice_total
                signature changed (added tax_rate parameter)
                commit 8f9a0b1c  "Feature: add tax_rate parameter"

IMPLEMENTATION  src/billing.py::compute_invoice_total
                implementation changed (signature stable)
                commit cb4afaed  "Perf: optimise compute_invoice_total"

──────────────────────────────────────────────────────────────
4 refactoring operation(s) detected
(1 implementation · 1 move · 1 rename · 1 signature)
```

In Git: `git log --oneline HEAD~8..HEAD`. You get commit messages. You read each
one and try to infer from the prose what structurally changed. If the commit
messages are good, great. If not, you're reading diffs.

In Muse: a classified report of every semantic refactoring operation. Rename.
Move. Signature change. Implementation change. Automatically, from the symbol graph.

Filter to just renames:

```
$ muse detect-refactor --kind rename

RENAME          src/billing.py::calculate_total
                → compute_invoice_total
                commit 1d2e3faa  "Rename: calculate_total → compute_invoice_total"

1 refactoring operation(s) detected
(1 rename)
```

---

## ACT 9 — Multi-Language: One Repository, Eleven Languages (~60 s)

*(The moment to widen the aperture)*

Everything I just showed you works across all eleven supported languages.

Let me add a TypeScript service file:

```typescript
// services/payment.ts
export class PaymentService {
  async processPayment(invoice: Invoice): Promise<PaymentResult> {
    const validated = await this.validate(invoice);
    return this.charge(validated);
  }

  private async validate(invoice: Invoice): Promise<Invoice> {
    if (!invoice.total) throw new Error("Invalid invoice");
    return invoice;
  }
}
```

```
$ muse commit -m "Add TypeScript PaymentService"
$ muse symbols --file services/payment.ts

services/payment.ts
  class       PaymentService                         line   2
  method      PaymentService.processPayment          line   3
  method      PaymentService.validate                line   8

3 symbol(s) across 1 file  (TypeScript: 3)
```

And a Rust domain model:

```rust
// domain/invoice.rs
pub struct Invoice {
    pub customer_id: String,
    pub total: Decimal,
}

impl Invoice {
    pub fn new(customer_id: String, items: Vec<Item>) -> Self {
        Invoice { customer_id, total: compute_total(&items) }
    }

    pub fn is_valid(&self) -> bool {
        self.total > Decimal::ZERO
    }
}
```

```
$ muse commit -m "Add Rust Invoice domain model"
$ muse symbols --file domain/invoice.rs

domain/invoice.rs
  class       Invoice                                line   2
  method      Invoice.new                            line   7
  method      Invoice.is_valid                       line   11

3 symbol(s) across 1 file  (Rust: 3)
```

Python. TypeScript. Rust. Go. Java. C. C++. C#. Ruby. Kotlin. JavaScript.
Same commands. Same symbol addresses. Same rename and move detection. Same
semantic merge engine.

One abstraction. Eleven languages. The entire symbol graph queryable and
version-controlled at the function level.

---

## ACT 10 — Why This Beats Git for Real Engineering Teams (~60 s)

*(Step back from the terminal)*

Let me tell you the scenarios where this matters most — where teams actually feel it.

**Code review.** Instead of reading a 400-line diff and reconstructing what changed
semantically, your reviewer sees: "three functions modified, one renamed, one moved
to a new module." The review is scoped to the real changes.

**Onboarding.** A new engineer asks: "Who owns `compute_invoice_total` and why does
it work the way it does?" With Muse: `muse symbol-log "src/billing.py::compute_invoice_total"`.
Full history, including the rename from `calculate_total`, including the perf
improvement, including the tax parameter. The story of the function, not the file.

**Merge conflicts.** Your team's most expensive meetings are merge conflict
resolution sessions. With Muse: only functions that genuinely conflict get flagged.
Not "same file, different lines" false positives — actual semantic disagreements
between two engineers about the same named function.

**Refactoring fearlessly.** Extract a module. Move a function. Rename a class
across ten files. Muse records the semantic intent, not the text change. Future
readers see what you did, not just that bytes changed.

**Audit and compliance.** "Has this function's signature ever changed?" 
`muse symbol-log --kind signature`. "What functions did we move last quarter?"
`muse detect-refactor --kind move --from Q1-tag`. Answers in milliseconds.

---

## ACT 11 — The Domain Schema: Code as Five Dimensions (~60 s)

```
$ muse domains

╔══════════════════════════════════════════════════════════════╗
║                Muse Domain Plugin Dashboard                  ║
╚══════════════════════════════════════════════════════════════╝

Registered domains: 2
──────────────────────────────────────────────────────────────

  ●  code  ← active repo domain
     Module:        plugins/code/plugin.py
     Capabilities:  Typed Deltas · Domain Schema · OT Merge
     Schema:        v1 · top_level: set · merge_mode: three_way
     Dimensions:    structure, symbols, imports, variables, metadata
     Description:   Semantic code versioning — AST-based symbol graph

  ○  music
     Module:        plugins/music/plugin.py
     Capabilities:  Typed Deltas · Domain Schema · OT Merge
     Schema:        v1 · top_level: set · merge_mode: three_way
     Dimensions:    melodic, harmonic, dynamic, structural
     Description:   MIDI and audio file versioning with note-level diff
```

The code domain has five dimensions — parallel to music's five dimensions:

| Dimension | What it tracks | Diff algorithm |
|-----------|---------------|----------------|
| `structure` | Module and file tree | GumTree tree-edit |
| `symbols` | Functions, classes, methods | GumTree tree-edit (AST) |
| `imports` | Import statements | Set algebra |
| `variables` | Top-level assignments | Set algebra |
| `metadata` | Config, non-code files | Set algebra |

An import added on one branch and a function renamed on another? Different
dimensions. Auto-merged. No conflict.

Two branches both add the same new function name? Same dimension, same address.
Symbol conflict — one message, one decision.

---

## OUTRO — Muse: What Version Control Becomes (~60 s)

*(Back to camera)*

Git was designed when "a file" was the smallest meaningful unit of software.
That was correct in 1972. We're building differently now.

Muse treats code the way your IDE, your compiler, and your type checker already
treat it — as a collection of named, typed, structured symbols with identities
that persist across renames and moves.

Every commit carries a full semantic delta — not which lines changed, but which
functions were added, removed, renamed, moved, or internally modified.

Every merge operates at symbol granularity — only genuine semantic disagreements
produce conflicts.

Every refactoring is permanent and queryable — rename, move, extract, inline —
all recorded with their semantic meaning, visible through `muse detect-refactor`
and `muse symbol-log`.

And it works across eleven languages. Today. With the same plugin interface that
already works for music, and will work for genomics, for scientific simulation,
for any structured data type you want to version.

`muse init --domain code`. Git is still there if you need it. But once you see
symbol-level history, you don't go back.

---

## APPENDIX — Speaker Notes

### On the three new commands

**`muse symbols`** is the simplest to explain: it's `git ls-files` except it
shows the semantic *interior* of your files, not just their names. Every function.
Every class. Every method. Stable hash identities. Filter by kind, file, or
language.

**`muse symbol-log`** is the hardest for Git users to absorb because it has no
analogue. The closest is `git log -p -- <file>` but that shows line diffs for
the entire file. `muse symbol-log` follows a *specific function* through history,
including across renames and moves, and shows only the semantic events.

**`muse detect-refactor`** is the one that makes engineering managers say "I want
this." Onboarding, code review, audit trails — all benefit from a machine-generated
classification of structural changes rather than prose commit messages.

### On the rename detection

The rename detection is mathematically exact, not heuristic. It compares
`body_hash` values — SHA-256 of the normalized function body (AST, not text).
Two functions with the same `body_hash` but different names are definitionally a
rename. No line-similarity threshold. No "60% similar" guessing. Certain.

### On the multi-language support

All eleven languages use `tree-sitter` as the parsing backend — the same parser
used by GitHub Copilot, VS Code, Neovim, and Zed. This is not a hand-rolled
regex. Each language gets a grammar-specific query that extracts the same
`SymbolRecord` shape. The core engine is language-agnostic; the language detail
lives in `LangSpec` dicts of ~15 lines each.

### On "you can't do this in Git"

Be precise. Git *can* detect renames at the file level (poorly). Git *cannot*
detect renames at the function level. Git *cannot* tell you which functions
two branches modified. Git *cannot* auto-merge based on whether two changes
touched the same *symbol*. Git *cannot* track the history of a function through
renames. These are structural limitations, not missing flags.

### Suggested YouTube chapter markers

| Timestamp | Chapter |
|-----------|---------|
| 0:00 | Intro — what Git gets wrong |
| 1:30 | Act 1 — first commit, semantic show |
| 3:30 | Act 2 — muse symbols: the full symbol graph |
| 6:00 | Act 3 — rename detection via body hash |
| 8:30 | Act 4 — cross-file move detection |
| 11:00 | Act 5 — symbol-level auto-merge (no conflict) |
| 14:00 | Act 6 — symbol-level conflict (genuine disagreement) |
| 17:00 | Act 7 — muse symbol-log: the life of a function |
| 20:00 | Act 8 — muse detect-refactor: the refactoring report |
| 23:00 | Act 9 — eleven languages, one abstraction |
| 26:00 | Act 10 — why this matters for real teams |
| 29:00 | Act 11 — domain schema and five dimensions |
| 31:30 | Outro |
