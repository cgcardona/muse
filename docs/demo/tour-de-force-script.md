# Muse Tour de Force — Video Narration Script

> **Format:** YouTube walkthrough of the Tour de Force interactive demo.
> Open `artifacts/tour_de_force.html` before recording. Click **Play Tour**
> and let the demo advance step by step while you narrate. Timestamps are
> approximate at 1.2 s/step; adjust to your natural pace.
>
> **Tone:** conversational, curious, a little excited — like showing a friend
> something you built that you genuinely believe in.

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

## OUTRO (~45 s)

*(Back to camera or full screen)*

So that's Muse. It's version zero — local-only right now, music as the
reference domain. But the architecture is domain-agnostic by design.

The same five-method plugin protocol that powers the music domain can power
a genomics sequencer, a scientific simulation, a 3D spatial field, a neural
network checkpoint. If your data has structure — and it does — Muse can
understand it.

What's next: **MuseHub** — the remote layer. Push, pull, and a PR interface
that shows you the dimension matrix for every proposed merge before you
accept it. The kind of diff interface that actually tells you what changed
and why it matters.

If this resonated — the code is on GitHub, link in the description. Star it
if you want to follow along. And if you're building something with structured
state that deserves better version control — reach out. I'd love to talk.

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

**"Is this production-ready?"**
v0.1.1. It's a solid foundation with strict typing, CI, tests. Not production
for a studio yet — but the architecture is sound and the hard parts
(content-addressed storage, three-way merge) are working.

**"What about performance?"**
The demo runs in 150ms for 14 commits and 41 operations. The bottleneck will
be large files, which is a known problem (handled by chunked object storage
in future). The merge algorithm is O(n) in the number of MIDI events per
dimension — fast in practice.

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
| 20:00 | Outro and what's next |
