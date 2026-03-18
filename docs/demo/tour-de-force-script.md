# Muse Tour de Force — Video Narration Script (v1.0)

> **Format:** YouTube walkthrough of the Tour de Force interactive demo.
> Open `artifacts/tour_de_force.html` before recording. Click **Play Tour**
> and let the demo advance step by step while you narrate. Timestamps are
> approximate at 1.2 s/step; adjust to your natural pace.
>
> **Tone:** conversational, curious, a little excited — like showing a friend
> something you built that you genuinely believe in.
>
> **What's new in v1.0:** After the original 5 acts, we now have **4 additional
> acts** covering Typed Delta Algebra (Phase 1), Domain Schema & Diff Algorithms
> (Phase 2), Operation-Level OT Merge (Phase 3), CRDT Convergent Writes (Phase 4),
> and the live Domain Dashboard. The original 41 steps are unchanged — new acts
> continue from step 42.

---

## INTRO — Before clicking anything (~90 s)

*(Camera on screen, demo paused at step 0)*

Hey — so I want to show you something I've been building called **Muse**.

The elevator pitch: Muse is a version control system for multidimensional
state. Think Git, but instead of treating a file as the smallest thing you
can reason about, Muse understands the *internal structure* of your files —
and that changes everything about what a conflict means.

To make this concrete I'm going to use music, because music is a perfect
example of something that *looks* like a file but is actually several
completely independent things layered on top of each other.

Take a MIDI file. On disk it's one blob of bytes. But inside it there are at
least five things that have nothing to do with each other:

- **Melodic** — the notes being played, the pitch and duration of each one
- **Rhythmic** — when those notes land in time, the groove, the syncopation
- **Harmonic** — chord voicings, key changes, the tonal color
- **Dynamic** — velocity, expression, how hard or soft each note hits
- **Structural** — tempo, time signature, the skeleton the rest hangs on

These are orthogonal axes. A drummer and a pianist can edit the same MIDI
file — one touching only the rhythmic dimension, the other only the harmonic
— and there is *no conflict*. They didn't touch the same thing. Git would
flag the whole file. Muse resolves it in silence.

That's the idea. Let me show you the demo.

---

## HEADER — Reading the stats (~20 s)

*(Point to the stats bar: 14 commits · 6 branches · 1 merge · 1 conflict resolved · 41 operations)*

Everything you're about to see ran against a real Muse repository — real
commits, real branches, real SHA-256 content hashes. The whole demo took
about 150 milliseconds to execute.

Fourteen commits. Six branches. One merge conflict. All resolved. Let's
walk through how we got here.

---

## ACT 1 — Foundation (Steps 1–5)

*(Click Play Tour — steps 1–5 advance. Pause after step 5.)*

### Step 1 — `muse init`

We start with an empty directory. `muse init` creates the `.muse/` folder —
the repository root. Content-addressed object store, branch metadata, config
file. Same idea as `git init`, but designed from scratch to be domain-agnostic.

### Step 2 — `muse commit -m "Root: initial state snapshot"`

This is the first commit. Notice in the dimension matrix at the bottom —
every single dimension lights up on this first commit. Melodic, rhythmic,
harmonic, dynamic, structural — all five. Because this is the root: we're
establishing the baseline state for every dimension simultaneously.

Under the hood, Muse called `MusicPlugin.snapshot()` — which walked the
working directory, hashed every MIDI file with SHA-256, and returned a
content-addressed manifest. That manifest is what got committed to the DAG.

### Step 3 — `muse commit -m "Layer 1: add rhythmic dimension"`

Now we add a layer. Look at the dimension matrix: only **rhythmic** and
**structural** light up. Rhythmic because we're adding a new rhythmic layer
file. Structural because adding a file changes the shape of the snapshot.
The melodic, harmonic, and dynamic dimensions? Untouched. Muse sees that.

### Step 4 — `muse commit -m "Layer 2: add harmonic dimension"`

Same pattern — **harmonic** and **structural** light up. We're adding a
harmonic layer. The rhythmic work from the previous commit is preserved
exactly as-is. These are independent operations on independent dimensions.

### Step 5 — `muse log --oneline`

Quick sanity check. Three commits, linear history, on `main`. This is your
foundation — the musical canvas everyone will branch from.

---

## ACT 2 — Divergence (Steps 6–16)

*(Resume Play Tour — steps 6–16. Pause after step 16.)*

*(Point to the DAG as branches appear)*

This is where it gets interesting. We're going to branch the repository three
ways simultaneously — three different creative directions diverging from the
same base.

### Steps 6–8 — Branch `alpha`

`muse checkout -b alpha` creates a new branch. We commit two texture patterns:

- **"Alpha: texture pattern A (sparse)"** — melodic and rhythmic dimensions.
  A sparse arrangement: few notes, lots of space.
- **"Alpha: texture pattern B (dense)"** — melodic and dynamic dimensions.
  The dense version: more notes, more expression.

Watch the dimension dots on the DAG nodes — each commit shows exactly which
dimensions it touched. Alpha is doing melodic work.

### Steps 9–11 — Branch `beta`

Back to `main`, then `muse checkout -b beta`. One commit:

- **"Beta: syncopated rhythm pattern"** — rhythmic and dynamic dimensions.

Beta is a completely different musical idea. It's not touching melody at all —
it's a rhythm section, working in its own lane. Rhythmic and dynamic only.

### Steps 12–15 — Branch `gamma`

Back to `main`, then `muse checkout -b gamma`. Two commits:

- **"Gamma: ascending melody A"** — pure melodic dimension.
- **"Gamma: descending melody B"** — melodic and harmonic. The descending
  line implies a harmonic movement, so two dimensions change.

### Step 16 — `muse log --oneline`

Three parallel stories. Alpha is building texture. Beta is building rhythm.
Gamma is building melody. None of them know about each other. The DAG is
starting to look like a real project.

---

## ACT 3 — Clean Merges (Steps 17–21)

*(Resume Play Tour — steps 17–21. Pause after step 21.)*

Now we bring it together. This is the part that's usually painful in Git.
In Muse, it's going to be boring — which is the point.

### Steps 17–18 — Merge `alpha` → `main`

`muse checkout main`, then `muse merge alpha`. The output says:
`Fast-forward to cb4afaed`.

Alpha was strictly ahead of main — no divergence. Fast-forward. Zero conflict.

### Step 19 — `muse status`

`Nothing to commit, working tree clean.` Main now has all of alpha's work.

### Step 20 — Merge `beta` → `main`

`muse merge beta`. This one creates a real merge commit —
`Merged 'beta' into 'main'`.

Here's what happened under the hood: Muse found the common ancestor (the
`Layer 2` commit), computed the three-way delta, and asked: did the same
dimension change on both sides?

- Alpha touched **melodic** and **dynamic**.
- Beta touched **rhythmic** and **dynamic**.
- Dynamic changed on *both sides*.

In Git: `CONFLICT`. In Muse: the dynamic changes are on different files, so
the union is clean. Merge commit. No human intervention. Done.

*(Point to the merge commit node in the DAG — it has the double-ring that marks it as a merge)*

### Step 21 — `muse log --oneline`

The DAG shows the merge. Main now contains the work of three contributors.
Clean.

---

## ACT 4 — Conflict & Resolution (Steps 22–31)

*(Resume Play Tour — steps 22–31. Pause after step 31.)*

*(Lean in a little — this is the money shot)*

Now we're going to manufacture a real conflict. Two branches are going to
modify the *same file* on the *same dimension*. This is where Muse shows
what makes it different.

### Steps 22–23 — Branch `conflict/left`

`muse checkout -b conflict/left`. Commit: **"Left: introduce shared state
(version A)"**.

This branch adds `shared-state.mid` and edits it with a **melodic** approach
and a **structural** change.

### Steps 24–26 — Branch `conflict/right`

Back to main, then `muse checkout -b conflict/right`. Commit: **"Right:
introduce shared state (version B)"**.

This branch adds its own version of `shared-state.mid` with a **harmonic**
approach and also a **structural** change.

*(Point to dimension matrix — both conflict/left and conflict/right columns)*

Look at the dimension matrix. Left touched melodic + structural. Right touched
harmonic + structural. **Structural appears on both sides.** That's the conflict.

### Steps 27–28 — Merge `conflict/left`

Fast-forward. Clean.

### Step 29 — Merge `conflict/right`

```
❌ Merge conflict in 1 file(s):
  CONFLICT (both modified): shared-state.mid
```

Here it is. Now — in Git, you'd open the file, see angle-bracket markers,
and try to figure out what "their" version of a binary MIDI file even means.
Good luck.

In Muse, the merge engine already knows *which dimensions* conflicted.
It ran `MusicPlugin.merge()` with `repo_root` set, which:

1. Loaded `.museattributes` to check for strategy rules
2. Called `merge_midi_dimensions()` on `shared-state.mid`
3. Extracted the five dimension slices from base, left, and right
4. Compared them: melodic only changed on the left. Harmonic only changed on
   the right. Structural changed on **both**.
5. Auto-merged melodic from left. Auto-merged harmonic from right.
6. Flagged structural as the one dimension that needs a human decision.

*(Point to the red-bordered structural cell in the dimension matrix for that commit)*

**One dimension conflicted. Four resolved automatically.** Git would have
thrown the entire file at you.

### Step 30 — Resolve and commit

The human makes a decision on the structural dimension and commits:
`"Resolve: integrate shared-state (A+B reconciled)"`.

Look at the dimension matrix for this commit — only **structural** lights up.
That's the exact scope of what was resolved. The merge result carries
`applied_strategies` and `dimension_reports` that document exactly which
dimension was manually resolved and which were auto-merged.

### Step 31 — `muse status`

`Nothing to commit, working tree clean.`

The conflict is history. Literally — it's in the DAG, attributed, auditable,
permanent.

---

## ACT 5 — Advanced Operations (Steps 32–41)

*(Resume Play Tour — steps 32–41. Let it play to completion.)*

The last act shows that Muse has the full surface area you'd expect from a
modern VCS.

### Steps 32–38 — The audition arc *(pause here and explain this slowly)*

This sequence is subtle but it's one of the most important things Muse
demonstrates. Watch what happens.

`muse cherry-pick` — we grab the *ascending melody* commit from the `gamma`
branch and replay it on top of `main`. This is an **audition**. We're not
merging gamma — we're borrowing one idea and trying it on.

Notice: this cherry-pick has no structural connection to gamma in the DAG.
It's a content copy — a new commit with a new hash, parented to main's HEAD.
Gamma doesn't know it happened. The two commits are not linked by an edge.

*(Brief pause — let the cherry-pick commit appear in the DAG)*

`muse show` — we inspect what actually changed. `muse diff` — working tree
is clean. The idea is in. Now we listen.

`muse stash`, `muse stash pop` — showing you can shelve unfinished work
mid-session without losing anything. Bread and butter.

Now — `muse revert`. The ascending melody doesn't fit. We undo it.

*(Point to the revert commit in the DAG — pause)*

This is the moment. Look at what just happened in the DAG: there are now
**two** commits sitting between the resolve and the tag. One that says
"here's the melody." One that says "never mind." Both are permanent. Both
are in the history forever.

That's not a mistake — that's the *point*. Six months from now, when someone
asks "did we ever try a melody in this section?" the answer is in the DAG:
yes, on this date, here's exactly what it was, and here's the explicit
decision to reject it. You can check it out, listen to it, and put it back
if you change your mind.

The `descending melody B` commit on gamma? It's still there too — never
used, never merged, never deleted. Muse doesn't garbage-collect ideas. The
entire creative history of the project — including the roads not taken — is
preserved.

*(Let that land before moving on)*

### Steps 39–40 — `muse tag add release:v1.0` / `muse tag list`

Tag the final state. `release:v1.0`. Permanent named reference to this point
in history.

### Step 41 — `muse log --stat`

Full log with file-level stats for every commit. The entire history of this
project — 14 commits, 6 branches, 1 real conflict, resolved — in one
scrollable view.

*(Let the demo settle. Let it breathe for a second.)*

---

## DIMENSION MATRIX — Closing walkthrough (~60 s)

*(Scroll down to the Dimension State Matrix. Let the audience take it in.)*

This is the view I want to leave you with.

Every column is a commit. Every row is a dimension. Every colored cell is
a dimension that changed in that commit. The red-bordered cell — that one
structural change — is the only moment in this entire session where a human
had to make a decision.

*(Trace across the rows)*

Look at the melodic row. It moves. It's active on alpha commits, on gamma
commits, on the cherry-pick, on the revert. A continuous creative thread.

Look at the rhythmic row. It's its own thread. Beta's work. Completely
parallel. Never interfered with melody.

Look at structural — it barely touches anything until the conflict commit.
Then it lights up on both sides at once. That's the red cell. That's the
conflict. One cell out of seventy.

This is what multidimensional version control means. Not "track files better."
Track the *dimensions of your work* so that conflicts only happen when two
people genuinely disagree about the same thing — not because they happened
to edit the same file on the same day.

---

## ACT 6 — Typed Delta Algebra (Steps 42–46, Phase 1)

*(New section — show terminal, not the HTML demo)*

*(Camera on terminal. Switch to showing raw muse commands.)*

I want to show you what's under the hood now — the new engine that powers
every operation you just saw.

In v0.x, `muse show` gave you: `files modified: shared-state.mid`. That's it.
A filename. A black box. You had no idea what changed inside the file.

### Step 42 — `muse show` on a commit with note-level changes

```
$ muse show HEAD
commit: a3f2c9...  "Alpha: texture pattern B (dense)"

  patch shared-state.mid  (melodic layer)
    insert  note C4  tick=480  vel=80  dur=240   +
    insert  note E4  tick=720  vel=64  dur=120   +
    delete  note G3  tick=240  vel=90  dur=480   -

  3 operations: 2 added, 1 removed
```

That's Phase 1 — the **Typed Delta Algebra**. Every commit now carries a
`StructuredDelta` — a list of typed operations. Not "file changed." Note added
at tick 480 with velocity 80 and duration 240. That's the actual thing that
happened.

*(Pause to let that sink in)*

There are five operation types:

- `InsertOp` — something was added (note, row, element)
- `DeleteOp` — something was removed
- `MoveOp` — something was repositioned
- `ReplaceOp` — something's value changed (has before/after content hashes)
- `PatchOp` — a container was internally modified (carries child ops)

### Step 43 — `muse diff` between two commits

```
$ muse diff HEAD~2 HEAD
  patch tracks/bass.mid  (rhythmic layer)
    insert  note F2  tick=0    vel=100  dur=120   +
    insert  note F2  tick=480  vel=80   dur=120   +
    replace note G2  tick=240  vel=90   dur=480  →  vel=70

  2 added, 1 modified
```

Every diff is now operation-level. You're not comparing binary blobs —
you're comparing structured semantic events. The merge engine works on these
typed operations. So does the conflict detector.

### Step 44 — `muse log --stat`

The log now shows operation summaries inline:

```
a3f2c9  Alpha: texture pattern B (dense)
        2 notes added, 1 note removed
cb4afa  Alpha: texture pattern A (sparse)
        1 note added, 3 notes removed
```

Every commit has a `summary` field — computed by the plugin at commit time,
stored for free display later. No re-scanning. No re-parsing.

---

## ACT 7 — Domain Schema & Diff Algorithms (Steps 47–51, Phase 2)

*(Show `muse domains` output)*

### Step 47 — `muse domains`

```
╔══════════════════════════════════════════════════════════════╗
║              Muse Domain Plugin Dashboard                    ║
╚══════════════════════════════════════════════════════════════╝

Registered domains: 2
──────────────────────────────────────────────────────────────

  ●  music  ← active repo domain
     Module:        plugins/music/plugin.py
     Capabilities:  Phase 1 · Phase 2 · Phase 3 · Phase 4
     Schema:        v1 · top_level: set · merge_mode: three_way
     Dimensions:    melodic, harmonic, dynamic, structural
     Description:   MIDI and audio file versioning with note-level diff

  ○  scaffold
     Module:        plugins/scaffold/plugin.py
     Capabilities:  Phase 1 · Phase 2 · Phase 3 · Phase 4
     Schema:        v1 · top_level: set · merge_mode: three_way
     Dimensions:    primary, metadata
     Description:   Scaffold domain — copy-paste this to build your own

──────────────────────────────────────────────────────────────
To scaffold a new domain:
  muse domains --new <name>
```

This is the **Domain Dashboard** — a live inventory of every registered domain
plugin, its capability level, and its declared schema. The `●` bullet marks
the domain of the current repository. The `○` is a template.

### Step 48 — What the schema does

The `schema()` method is Phase 2. When a plugin declares its schema, the core
engine knows:

- **Melodic dimension**: `sequence` of `note_event` elements → use Myers LCS diff
- **Harmonic dimension**: `sequence` of `chord_event` elements → use Myers LCS diff
- **Dynamic dimension**: `tensor` of float32 values → use epsilon-tolerant numerical diff
- **Structural dimension**: `tree` of track nodes → use Zhang-Shasha tree edit diff

One schema declaration. Four different algorithms. The right algorithm for each
dimension, automatically.

### Step 49 — `muse domains --new genomics`

```
$ muse domains --new genomics
✅ Scaffolded new domain plugin: muse/plugins/genomics/
   Class name: GenomicsPlugin

Next steps:
  1. Implement every NotImplementedError in muse/plugins/genomics/plugin.py
  2. Register the plugin in muse/plugins/registry.py
  3. muse init --domain genomics
  4. See docs/guide/plugin-authoring-guide.md for the full walkthrough
```

That's it. Thirty seconds to scaffold a fully typed, Phase 1–4 capable domain
plugin for any structured data type you can imagine. Copy, fill in, register.

*(Point to the scaffold file)*

The scaffold is fully typed. Zero `Any`. Zero `object`. Zero `cast()`. It passes
`mypy --strict` out of the box. It's a copy-paste starting point, not a toy.

---

## ACT 8 — Operation-Level OT Merge (Steps 52–56, Phase 3)

*(Back to the conflict scenario — two branches editing the same MIDI file)*

Let me show you what happens when two musicians edit the same MIDI file at
the note level, concurrently.

### Step 50 — Two branches, same file, different notes

```
Branch alpha-notes: inserts note C4 at tick 480
Branch beta-notes:  inserts note G4 at tick 960
```

Both branches modified `melody.mid`. In old Muse — file-level merge — this
would be a conflict. One file, both modified, done.

In Phase 3 — **Operational Transformation merge** — the engine asks:

> Do these two operations commute? Can I apply them in either order and
> get the same result?

### Step 51 — `muse merge beta-notes` with Phase 3

```
$ muse merge beta-notes
✅ Merged 'beta-notes' into 'alpha-notes' (clean)
   2 operations auto-merged:
     insert note C4 tick=480 (from alpha-notes)
     insert note G4 tick=960 (from beta-notes)
   Conflicts: none
```

Two insertions at different tick positions → they commute → auto-merged.
No human needed. The notes are in the same file. They're at different positions.
They don't interfere.

### Step 52 — When ops genuinely conflict

```
Branch left:  replace note C4 tick=480 vel=64 → vel=80
Branch right: replace note C4 tick=480 vel=64 → vel=100
```

Now we have a real conflict: both branches changed the velocity of the *same
note* from the *same base value* to *different target values*. That's a
genuine disagreement. The engine flags it:

```
$ muse merge right
❌ Merge conflict:
  CONFLICT (op-level): melody.mid note C4 tick=480 vel (64→80 vs 64→100)
```

One note. One parameter. That's the minimal conflict unit.

*(This is what "operation-level" means — the conflict detector understood
the internal structure of your file well enough to isolate the conflict to
a single note parameter.)*

---

## ACT 9 — CRDT Convergent Writes (Steps 57–61, Phase 4)

*(Switch to CRDT scenario — many agents writing simultaneously)*

Everything I've shown so far is for human-paced collaboration: commits once
an hour, a day, a week. Three-way merge is exactly right for that.

But what if you have twenty automated agents writing simultaneously? What if
you're collecting telemetry from a distributed sensor network? What if your
domain is a set of annotations where a thousand researchers are contributing
concurrently?

Three-way merge breaks down. You can't resolve 10,000 conflicts per second
with a human.

### Step 57 — CRDT mode: join always succeeds

Phase 4 introduces **CRDT Semantics**. A plugin that implements `CRDTPlugin`
replaces `merge()` with `join()`. The join is a mathematical operation on a
**lattice** — a partial order where any two states have a unique least upper bound.

The key property: **join always succeeds. No conflict state ever exists.**

### Step 58 — The six CRDT primitives

```python
from muse.core.crdts import (
    VectorClock,   # causal ordering between agents
    LWWRegister,   # last-write-wins scalar
    ORSet,         # unordered set — adds always win
    RGA,           # ordered sequence — commutative insertion
    AWMap,         # key-value map — adds win
    GCounter,      # grow-only counter
)
```

Each one satisfies the three lattice laws:

1. `join(a, b) == join(b, a)` — order of messages doesn't matter
2. `join(join(a, b), c) == join(a, join(b, c))` — batching is fine
3. `join(a, a) == a` — duplicates are harmless

These three laws are the mathematical guarantee that all replicas converge to
the same state — regardless of how late or how many times messages arrive.

### Step 59 — ORSet: adds always win

```python
# Agent A and Agent B write simultaneously:
s_a, tok_a = ORSet().add("annotation-GO:0001234")
s_b = ORSet().remove("annotation-GO:0001234")  # B doesn't know about A's add

# After join:
merged = s_a.join(s_b)
assert "annotation-GO:0001234" in merged.elements()  # A's add survives
```

This is the semantics for a genomics annotation set: concurrent adds win.
If you didn't know I added that annotation, your remove doesn't count. No
silent data loss. Ever.

### Step 60 — `muse merge` in CRDT mode

```
$ muse merge agent-branch-1  (CRDT mode detected)
✅ Joined 'agent-branch-1' into HEAD
   CRDT join: 847ms
   Labels joined:    3 adds merged
   Sequence joined:  RGA converged (12 elements)
   Conflicts: none  (CRDT join never conflicts)
```

Joins never conflict. You can run `muse merge` on a thousand agent branches
and it always succeeds. The result is always the convergent state — the
least upper bound — of all writes.

### Step 61 — When to use CRDT vs. three-way

This is the judgment call every plugin author makes:

| Scenario | Right choice |
|----------|-------------|
| Human composer editing a MIDI score | Three-way merge (Phase 3) |
| 100 agents annotating a genome | CRDT `ORSet` |
| DAW with multi-cursor note input | CRDT `RGA` |
| Distributed IoT telemetry counter | CRDT `GCounter` |
| Configuration parameter (one writer) | `LWWRegister` |

CRDTs give up human arbitration in exchange for infinite scalability. Use them
when you have more concurrent writes than humans can handle.

---

## OUTRO — Muse v1.0 (~60 s)

*(Back to camera or full screen)*

So that's Muse v1.0.

We started with the foundation — a domain-agnostic VCS where conflicts are
defined by dimension, not by file. That's still the core. But we've now built
four layers on top of it that progressively close the gap between "a VCS that
understands files" and "a VCS that understands your domain."

**Phase 1**: Every operation is typed. Every commit carries a semantic operation
list. `muse show` tells you what changed inside the file, not just which file.

**Phase 2**: Every domain declares its data structure. The diff engine
automatically selects the right algorithm — LCS for sequences, tree-edit for
hierarchies, epsilon-tolerant for tensors, set algebra for unordered collections.

**Phase 3**: Merges happen at operation granularity. Two musicians editing
the same file at different positions don't conflict. The merge engine uses
Operational Transformation to compute the minimal, real conflict set.

**Phase 4**: For high-throughput multi-agent scenarios, CRDT semantics replace
merge with a mathematical join that always converges. No conflict state ever
exists. Hundreds of agents can write simultaneously.

And all of this is accessible to any domain through a single five-method plugin
interface. Music is the proof. Genomics, climate simulation, 3D spatial design,
neural network checkpoints — any domain with structure can be versioned with Muse.

`muse domains --new <your_domain>`. Thirty seconds to scaffold. Fill in the
methods. Register. Done.

The code is on GitHub. The link is in the description. If you're building
something with structured state that deserves better version control — reach out.

---

## APPENDIX — Speaker Notes

### On questions you might get

**"Why not just use Git with LFS?"**
Git LFS stores big files — it doesn't understand them. You still get binary
merge conflicts on the whole file. The dimension is the thing.

**"What does 'domain-agnostic' actually mean?"**
The core engine — DAG, branches, object store, merge state machine — has
zero knowledge of music. It calls five methods on a plugin object. Swap the
plugin, get a different domain. The same commit graph, the same `muse merge`,
different semantics.

**"What's the difference between Phase 3 OT and Phase 4 CRDT?"**
OT assumes you have a base and can identify the common ancestor. It produces
a minimal conflict set when ops genuinely disagree. CRDT assumes there is no
shared base — every agent writes to their local replica and the join is
always clean. OT is right for human-paced editing; CRDT is right for
machine-speed concurrent writes.

**"Is this production-ready?"**
v1.0 is solid: strict typing, 691 passing tests, CI, four semantic layers
fully implemented. Not production for a studio yet — but the architecture is
sound and the hard parts (content-addressed storage, OT, CRDT) are working.

**"What about performance?"**
The original demo runs in 150ms for 14 commits and 41 operations. The CRDT
joins run in sub-millisecond. The bottleneck will be large files — handled
by chunked object storage in the roadmap.

### Suggested chapter markers for YouTube

| Timestamp | Chapter |
|-----------|---------|
| 0:00 | Intro — what is multidimensional VCS? |
| 1:30 | The five musical dimensions |
| 3:00 | Act 1 — Foundation |
| 5:30 | Act 2 — Three branches diverge |
| 9:00 | Act 3 — Clean merges |
| 11:30 | Act 4 — The conflict (and why it's different) |
| 16:00 | Act 5 — Full VCS surface area |
| 18:30 | Dimension Matrix walkthrough |
| 20:00 | Act 6 — Typed Delta Algebra (Phase 1) |
| 23:00 | Act 7 — Domain Schema & muse domains dashboard (Phase 2) |
| 27:00 | Act 8 — Operation-level OT Merge (Phase 3) |
| 31:00 | Act 9 — CRDT Convergent Writes (Phase 4) |
| 36:00 | Outro — Muse v1.0 and what's next |
