# Muse: Domain Agnostic Version Control for Multi-Dimensional State

*Carlos Gabriel Cardona · Muse · March 2026*

---

> *"Git versions files. Muse versions meaning"*

---

## The Problem in One Sentence

Git is brilliant at version-controlling text. Everything that isn't text — music, genomes, 3D scenes, simulation states — Git stores blindly and manages badly. Muse fixes this.

---

## What Git Actually Does

Git treats every file as a sequence of bytes. It finds the difference between two versions by scanning for changed lines. It detects a conflict when two branches both changed lines in the same region.

This is a deeply elegant model for source code. Source code is text. Lines have meaning. Changes to line 47 rarely affect line 3. The model fits.

But what happens when you commit a MIDI file?

```
$ git diff before.mid after.mid
Binary files before.mid and after.mid differ
```

That's it. That's all you get. Git sees bytes — it doesn't see notes, velocities, tempo, sustain pedal, key signature. It doesn't know which bytes matter or why. And when two people edit the same MIDI file on different branches, Git calls it a conflict even if one person only touched the sustain pedal and the other only changed a note's velocity. Those changes have nothing to do with each other. Git can't tell.

This isn't a limitation of Git specifically. It's a limitation of the model: **bytes don't remember what they mean.**

---

## The World Git Was Built For Is Shrinking

For thirty years, version control was a programmer's tool. The things being versioned were mostly code. That world is ending fast.

AI agents write music. They generate genomic annotations. They construct 3D environments. They run scientific parameter sweeps that produce petabytes of structured simulation state. They compose, iterate, branch, and merge at a rate no human team ever could.

When ten AI agents are concurrently editing a MIDI composition — each working on a different aspect, each on its own branch — they need to merge. With byte-level VCS, that merge is a disaster. With Muse, it just works.

The pace of AI-assisted creative and scientific work is going to expose the byte-level model as the bottleneck it has always been. Muse is the answer to that bottleneck.

---

## What Muse Does Differently

Muse doesn't see bytes. It sees **things**.

When Muse looks at a MIDI file, it sees 21 distinct dimensions of musical state: the notes, the sustain pedal, the tempo map, the pitch bend, the volume automation, the reverb sends, the key signatures, the marker cues, and more. Each dimension is declared independently. Each has its own diff algorithm. Each can be merged independently.

When two branches both modify the same MIDI file, Muse doesn't ask "which bytes overlapped?" It asks: "did they touch the same dimension?" If Alice changed the sustain pedal and Bob changed a note velocity, those are two independent dimensions. Muse merges them automatically. No conflict. No human required.

Of the 21 MIDI dimensions Muse tracks, 18 are fully independent. Two collaborators can edit any two of those 18 simultaneously and always merge cleanly. Only the three structural dimensions — tempo, time signature, and track identity — require coordination, because changes to those shift the meaning of everything else.

Git would have called every one of those 18×18 combinations a conflict. Muse calls them clean merges.

---

## A Concrete Example

Imagine two musicians collaborating on a MIDI file.

**Alice** records a sustain pedal pass — she's cleaning up the piano performance, adding lifts and holds through the chord progression.

**Bob** adjusts note velocities in the same section — he's working on dynamics, making the melody swell in bars 5–8.

They're working on the same file. They're working in the same bars. In Git:

```
CONFLICT (content): Merge conflict in lead.mid
```

Git can't tell that Alice's sustain edits and Bob's velocity edits are completely independent. All it knows is that both branches produced different bytes in the same file.

In Muse:

```
✅ Auto-merged: cc_sustain (Alice) + notes (Bob) — independent dimensions
```

Muse parsed both files, classified every event by dimension, checked independence, and merged. No conflict because there was no real conflict. The byte-level appearance of conflict was an artifact of flat storage, not a real semantic collision.

This is the whole idea. **The conflict wasn't real. The byte model made it look real.**

---

## The Plugin Model: Teaching Muse New Domains

Muse's core engine is domain-agnostic. It doesn't know anything about MIDI, genomes, or scene graphs. What it knows is: there are plugins, and plugins understand domains.

Every domain plugin implements six things:

1. **Snapshot** — capture the current state of the artifact
2. **Diff** — describe what changed, in domain terms (not byte offsets)
3. **Merge** — combine two change streams, dimension by dimension
4. **Drift** — detect uncommitted changes in the working copy
5. **Apply** — apply a delta to a live state
6. **Schema** — declare the structure: which dimensions exist, which are independent

That's the entire contract. If you can implement those six methods for your domain, Muse can version-control it — with semantic diffs, clean merges, entity lineage tracking, and selective cherry-pick.

The core engine never changes. New domains are new plugins. Music was first. Source code, Bitcoin's UTXO state, and a scaffold for new domains are next. Genomics, 3D spatial design, and climate simulation are the logical next steps.

---

## What "Semantic Diff" Actually Feels Like

Git's diff for a note velocity change:

```
Binary files before.mid and after.mid differ
```

Muse's diff for the same change:

```
~ C4 vel=80 → vel=100  @beat=1.00  bar 2
  (expression edit — velocity increased by 20)
```

Git's diff for a new note:

```
Binary files before.mid and after.mid differ
```

Muse's diff:

```
+ E4 vel=90 @beat=3.50  dur=0.25  bar 7
  (new eighth note inserted in melody)
```

These aren't cosmetic differences. They're the difference between information you can act on and noise. A semantic diff tells you what changed in the language of the domain. A byte diff tells you that something changed — somewhere — in a blob.

This matters even more for blame and history. Git blame on a MIDI file: meaningless. Muse's equivalent:

```
$ muse note-blame lead.mid C4@beat=1.00
note C4 @beat=1.00: commit a3f8...  "adjusted expression in bar 2"
                    by Alice · 2026-03-10
```

"Who last changed the velocity of this specific note, and why?" That question is structurally unanswerable with byte-level VCS. Muse answers it because it has been tracking note identity — not byte offsets — across the entire commit history.

---

## Entity Identity

Here's a subtle but important point: byte-level diff doesn't just lose semantic meaning — it loses the ability to track whether two versions of something are the *same thing*.

Consider a note C4 at beat 1.00. Someone changes its velocity from 80 to 100. To Git, this is: "some bytes changed somewhere in the file." It can't say "the same note, mutated." It can only say "these bytes were removed, these bytes were added."

Muse tracks a stable identity for each entity — a note, a gene, an AST node — across its entire mutation history. When a note's velocity changes, Muse records:

```
MUTATE note:C4@beat=1.00
  velocity: 80 → 100
  (same note, different expression)
```

This unlocks a capability byte-level systems can never have: **field-level merge**. Two concurrent edits to the same note — one changes velocity, one changes timing — are independent field mutations. They don't conflict. Muse auto-merges them. Git can't even see that they're about the same note.

---

## Why This Matters for AI Agents Specifically

Human collaboration is slow. Two musicians editing the same file in the same week is unusual. Two AI agents editing the same file in the same second is routine.

At human timescales, coarse conflict detection is tolerable. You can open the file, look at the conflict markers, make a judgment call. At AI timescales — ten agents running in parallel, each committing dozens of changes per minute — coarse conflict detection is a wall. Every false conflict is a stall. Every stall requires human intervention. The entire point of using AI agents is lost.

Muse is built for this reality. The dimension-level independence model means that AI agents working on different aspects of the same artifact — melody vs. dynamics vs. automation — never conflict, because they're declared independent. The CRDT layer goes further: for domains that support it, concurrent writes by any number of agents converge mathematically to the same result, with no conflicts possible at all.

This isn't a future aspiration. It's in the codebase now.

---

## Muse Today

Muse is a working CLI tool — `muse init`, `muse commit`, `muse diff`, `muse merge`, `muse log` — built on a content-addressed object store, a commit DAG, and a plugin architecture. The MIDI plugin is the reference implementation. The code plugin adds AST-level diffing for 15 programming languages and automatic semver impact detection. A scaffold plugin makes it straightforward to add new domains.

The test suite has 3000+ passing tests across the core engine and plugin layer. The architecture is strict: the core never imports from plugins, plugins are fully isolated, and every public interface is typed end-to-end.

This is not a prototype or a thought experiment. It's a real system with real constraints, real tradeoffs, and a real path to the domains that need it most.

---

## The Domains Waiting

The same problem that breaks MIDI in Git breaks everything else that isn't text:

**Genomics** — A team annotating a reference genome needs to track edits across chromosomes, gene boundaries, and structural variants. Each chromosome is an independent dimension. Concurrent edits to different chromosomes should never conflict. `muse blame genome.gff3 chr3:14523` should tell you exactly which researcher established that annotation. None of this is possible with byte-level VCS.

**3D Design** — A game studio versioning scene graphs, animation rigs, material libraries, and physics parameters. Mesh positions, light targets, and camera frustums all depend on the coordinate system and scale — a structural global parameter, like tempo in MIDI. Editing a mesh is independent of editing a material. These should merge cleanly.

**Scientific Simulation** — Climate models, molecular dynamics, orbital simulations. Floating-point arrays where "changed" means "changed by more than epsilon." Multiple researchers running parameter sweeps on different variables simultaneously. The sweep results need to be versioned, diffed, and merged — not stored as opaque binary blobs.

The plugin protocol is the same for all of them. The core engine doesn't change. The problem is the same problem — bytes that don't remember what they mean — and the solution is the same solution: give the VCS a domain model.

---

## What Muse Is Not

Muse is not trying to replace Git for source code. Git is excellent at source code. Muse's code plugin adds semantic diffing on top of a Git-like foundation, but the point isn't "Git, but better for Python." The point is: **the 98% of structured data that isn't source code has never had a real version control system.** Muse is that system.

Muse is also not a database, not a file sync service, not a cloud storage layer. It's a version control system — the same abstraction as Git, operating at a different semantic level.

And Muse is not finished. It's a working foundation with proven architecture and a clear path forward. The hard part — getting the plugin model right, getting the typed operation algebra right, getting the merge hierarchy right — is done. The easy part (relatively speaking) is implementing more domain plugins.

---

## The Core Idea, Distilled

Version control has always been about tracking **what changed**. Git answers that question for bytes. Muse answers it for **things** — notes, genes, mesh vertices, AST nodes, simulation parameters.

The things have structure. The structure has independence. The independence enables conflict-free collaboration at a scale and speed that byte-level systems can never reach.

That's the whole idea. The implementation is in `/muse/`. The plugin protocol is six methods. The first domain is music.

The rest is an open invitation.

---

*Muse is open source. Implementation: `muse/`. Plugin protocol: `muse/domain.py`. MIDI plugin: `muse/plugins/midi/plugin.py`. Tests: `tests/`. Version 0.1.4 · 419 commits · 3,273 passing tests · March 2026.*
