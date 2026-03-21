# The Flat-Projection Impossibility Theorem for Multidimensional State Version Control

**A Technical White Paper**

*Gabriel · Muse Project · March 2026*

---

> *"The question is not whether you can serialize multidimensional state to bytes —
> you can always do that. The question is whether the bytes remember the
> dimensions. They don't."*

---

## Abstract

Modern version control systems (VCS) treat all versioned artifacts as opaque byte sequences, detecting changes through line-level longest-common-subsequence (LCS) diffs and resolving conflicts through text-merge heuristics. This works acceptably for source code, whose primary structure is a linear token sequence with line-bounded locality. It fails, and fails categorically, for every other class of multidimensional state: musical scores, genomic sequences, scientific simulations, 3D scene graphs, and animation keyframes — none of which are text and none of which can be losslessly projected to a flat byte sequence that preserves the semantic independence structure of their native dimensions.

This paper makes five formal arguments that flat-byte projection is insufficient for multidimensional state version control, then grounds each argument in the Muse implementation — a domain-agnostic VCS built to manage exactly this class of artifact. We demonstrate that:

1. **Byte-level equality is not domain equality** — two semantically equivalent states may have different byte representations, and two semantically different states may differ by only a handful of bytes with no structural signal of which bytes matter.
2. **No flat projection preserves dimensional independence** — the 21-dimensional independence structure of a MIDI file cannot survive any encoding into a byte sequence, because independence is a property of the parsing interpretation, not the bits.
3. **Merge correctness requires causal awareness** — a tempo change in bar 4 shifts the musical meaning of every subsequent tick, creating a causal dependency that no byte-level diff can detect or honor.
4. **Entity identity requires domain knowledge** — the fact that the note C4 at beat 3 with velocity 100 is the *same entity* as the note C4 at beat 3 with velocity 80 (after an expression edit) is invisible at the byte level and requires domain parsing to establish.
5. **Semantic versioning is a property of structure, not bytes** — whether a change is a breaking API change, a backward-compatible addition, or a pure refactor is derivable only from the domain's symbol structure, not from counting added and removed bytes.

The Muse implementation, across 419 commits and 691 passing tests, provides a working existence proof of the alternative: a plugin-based VCS in which each domain contributes its own diff, merge, and schema functions, and the core engine remains domain-agnostic. The result is a system where two collaborators can simultaneously edit different dimensions of the same MIDI file — sustain pedal on one branch, note velocities on the other — and merge without any conflict, because the 21 dimensions of MIDI state are declared and their independence is machine-verifiable.

---

## Table of Contents

1. [Introduction: The Version Control Abstraction](#1-introduction)
2. [The Flat-Projection Thesis and Its Appeal](#2-the-flat-projection-thesis)
3. [Argument I: Byte-Level Equality Loses Semantic Identity](#3-argument-i-byte-level-equality-loses-semantic-identity)
4. [Argument II: No Flat Projection Preserves Dimensional Independence](#4-argument-ii-no-flat-projection-preserves-dimensional-independence)
5. [Argument III: Merge Requires Causal Awareness](#5-argument-iii-merge-requires-causal-awareness)
6. [Argument IV: Entity Identity Cannot Be Recovered from Bytes](#6-argument-iv-entity-identity-cannot-be-recovered-from-bytes)
7. [Argument V: Semantic Versioning Is a Structural Property](#7-argument-v-semantic-versioning-is-a-structural-property)
8. [The Muse Architecture: A Constructive Counter-Proof](#8-the-muse-architecture-a-constructive-counter-proof)
9. [The MuseDomainPlugin Protocol: Six Methods That Cannot Be Bytes](#9-the-musedomainplugin-protocol-six-methods-that-cannot-be-bytes)
10. [The Typed Operation Algebra: The Language Bytes Don't Speak](#10-the-typed-operation-algebra-the-language-bytes-dont-speak)
11. [The Merge Hierarchy: From File to Dimension to Operation](#11-the-merge-hierarchy-from-file-to-dimension-to-operation)
12. [CRDT Convergence: When Three-Way Merge Is the Wrong Model](#12-crdt-convergence-when-three-way-merge-is-the-wrong-model)
13. [Cross-Domain Generalization: Evidence Beyond Music](#13-cross-domain-generalization-evidence-beyond-music)
14. [Related Work](#14-related-work)
15. [Discussion and Implications](#15-discussion-and-implications)
16. [Conclusion](#16-conclusion)
17. [Appendix A: The 21 Dimensions of MIDI State](#appendix-a-the-21-dimensions-of-midi-state)
18. [Appendix B: The Operation Commutativity Table](#appendix-b-the-operation-commutativity-table)
19. [Appendix C: Formal Definitions](#appendix-c-formal-definitions)

---

## 1. Introduction

Version control is one of the oldest and most consequential abstractions in software engineering. Since `diff` and `patch` were introduced in the early UNIX era, the working model has been consistent: a versioned artifact is a file; a file is a sequence of bytes; a change is the transformation from one byte sequence to another; and a conflict occurs when two independent changes touch overlapping byte ranges.

This model has been extraordinarily successful for its primary intended domain: source code. Source code is text. Text is a sequence of lines. Lines have locality — a change to line 47 rarely affects the semantics of line 3. The LCS-based diff algorithm finds minimum edit scripts with good human interpretability, and three-way merge handles the common case of non-overlapping concurrent edits correctly.

But version control is needed in many domains beyond source code. A molecular biology team maintaining a reference genome needs to track edits to nucleotide sequences, gene annotations, and structural variants — three fundamentally different data types in a single artifact. A game studio needs to version 3D scene graphs, animation rigs, material libraries, and physics parameters — each with its own identity model and merge semantics. A music production team needs to track note events, control change automation, tempo maps, and time signatures — dimensions that are sometimes independent and sometimes causally linked in ways that the file format does not expose.

The reflex response to all of these is: *"Just commit the file. Git can handle it."* After all, any artifact can be serialized to bytes, and bytes can be stored in a Git object. A MIDI file is bytes. A genome FASTA file is bytes. A Blender `.blend` file is bytes. What is the problem?

The problem is not storage. The problem is the *operations* that version control must support:

- **Diff**: what changed between two versions?
- **Blame**: which commit introduced this element?
- **Merge**: how do two independent edit streams combine?
- **Cherry-pick**: can this specific change from branch A be applied to branch B?
- **Revert**: can this specific change be undone without disturbing others?

Each of these operations, for multidimensional state, requires semantic understanding that is irretrievably absent from a byte sequence. This paper proves why.

---

## 2. The Flat-Projection Thesis

The flat-projection thesis is the claim that any multidimensional artifact can be adequately version-controlled by:

1. Serializing the artifact to a canonical byte sequence.
2. Applying standard byte-level or line-level diff algorithms.
3. Applying standard three-way merge on the resulting diffs.

The thesis has an obvious technical truth at its core: *any* finite data structure can be serialized to bytes. This is guaranteed by computability theory. A MIDI file, a genome, a scene graph — all are computable structures and therefore byte-serializable. The question is not whether the serialization exists. The question is whether the serialization *preserves the properties needed for version control operations to be meaningful*.

We will show that it does not, systematically and for principled reasons.

### 2.1 The Canonical Form Requirement

The first crack in the thesis appears immediately: for byte-level equality to mean semantic equality, the serialization must be **canonical** — a unique, deterministic byte representation of each logical state.

For text files, this is approximately true (given a fixed encoding). For binary formats, it is typically false. The MIDI specification, for example, permits:

- Events at the same tick position in any order (they are simultaneous from the sequencer's perspective).
- Running status compression (omitting status bytes when the channel and type are unchanged), which may be applied or omitted by any writer.
- Variable-length encoding of delta-times, with multiple valid bit patterns for the same value (leading zero bytes).
- Track chunk metadata (track names, copyright strings) that are optional and format-specific.

Two MIDI files that are semantically identical — they would produce the same audio output through any MIDI-compliant synthesizer — may have entirely different byte representations. Two MIDI files that differ semantically by a single note insertion may share 99.98% of their bytes, with no structural signal about where the musically meaningful change occurred.

```
$ diff --stat before.mid after.mid
Binary files before.mid and after.mid differ
```

Git's response to a binary diff. There is no actionable information here.

### 2.2 The Subset Representation Problem

Suppose we solve the canonicalization problem — we define a canonical serialization that produces a unique byte sequence for each logical state. Does this fix the flat-projection model?

No. The problem is the **subset representation** problem: a byte diff computes which bytes changed, but not which *semantic elements* those bytes represent.

Consider a 50 KB MIDI file representing a full orchestral arrangement. A one-note velocity change modifies approximately 1 byte (the velocity field of a single MIDI note-on event). A tempo change modifies 3 bytes (the BPM value in a `set_tempo` meta-event). A new 16th-note added at bar 7, beat 3 modifies approximately 8 bytes.

The byte counts are similar. The semantic significance is radically different. The tempo change shifts the musical meaning of every subsequent event. The note insertion changes the harmonic and rhythmic texture of a specific measure. The velocity change adjusts expression on a single note. A byte diff represents all three as "some bytes changed somewhere in the file" and treats them identically.

### 2.3 The Independence Destruction Problem

The deepest failure of the flat-projection thesis is what we call the **independence destruction problem**. A multidimensional artifact has a natural structure of partially-independent sub-dimensions. In a MIDI file, the note events on the melody track are independent of the sustain pedal automation — an edit to one does not, in general, affect the other. In a genome, an edit to chromosome 3 is independent of an edit to chromosome 7.

This independence is a *semantic property*, not a syntactic one. When the artifact is serialized to bytes, the independence structure is lost. The bytes for melody notes and sustain pedal automation are interleaved in the file, with no representation of which byte regions are independent. A byte-level conflict detection system sees "both branches modified bytes in this file" and declares a conflict — even when the actual changes were to completely independent dimensions.

Muse's implementation puts a precise number on this loss: in the MIDI domain, 18 of 21 semantic dimensions are fully independent (`independent_merge: True`). A naive byte-diff system treats all 21 as one dimension — the file — and produces a conflict whenever any two of those 21 are touched concurrently on different branches.

---

## 3. Argument I: Byte-Level Equality Loses Semantic Identity

**Claim**: The equality relation induced by byte-level comparison does not correspond to the domain equality relation for any non-trivial multidimensional artifact.

### 3.1 The Formal Setup

Let $\mathcal{S}$ be the set of valid semantic states of a domain (e.g., all valid MIDI compositions). Let $\text{ser}: \mathcal{S} \to \{0,1\}^*$ be a serialization function. Let $\text{des}: \{0,1\}^* \to \mathcal{S} \cup \{\perp\}$ be a deserialization function.

For flat-projection to be sound, we require:

$$\forall s_1, s_2 \in \mathcal{S}: s_1 =_{\mathcal{S}} s_2 \iff \text{ser}(s_1) =_{\text{bytes}} \text{ser}(s_2)$$

This holds only if $\text{ser}$ is **injective** and **canonical** — one state maps to one byte sequence.

### 3.2 MIDI: Non-Canonicality in Practice

The MIDI 1.0 specification (MIDI Manufacturers Association, 1983) does not mandate a canonical event ordering within a tick. The following two byte sequences both represent a valid C major chord (C4, E4, G4) at tick 0 on channel 1:

```
Sequence A: 00 90 3C 64  00 90 40 64  00 90 43 64   (C, E, G)
Sequence B: 00 90 43 64  00 90 3C 64  00 90 40 64   (G, C, E)
```

`00 90 3C 64` = delta_time=0, note_on, channel=1, pitch=60 (C4), velocity=100.

These two sequences have different bytes. They represent the same musical event. Any byte-level diff between them reports a change. Any hash-based equality check reports them as different. But `$s_1 =_{\mathcal{S}} s_2$` — they are the same musical state.

Conversely, the following two sequences have similar bytes but represent different musical events:

```
Before: 00 90 3C 64  ...  (C4, velocity=100)
After:  00 90 3C 50  ...  (C4, velocity=80)
```

Eight bytes differ. The change is semantically significant: an expression change that a performer made deliberately. But the byte diff reports it identically to any other 1-byte change anywhere in the file.

### 3.3 The Content-ID Solution

Muse's object store resolves the storage problem with content-addressed SHA-256 hashing: each blob is stored once, indexed by its hash. But this does not solve the equality problem — it makes it worse. Two semantically identical MIDI files with different event orderings will have different SHA-256 hashes and be stored as different objects, with no connection established between them.

The MIDI plugin resolves this by defining a domain equality relation on the **parsed representation**, not the bytes:

```python
class NoteKey(TypedDict):
    pitch: int           # MIDI pitch 0–127
    velocity: int        # attack velocity 0–127
    start_tick: int      # absolute tick position (delta-time resolved)
    duration_ticks: int  # note duration in ticks
    channel: int         # MIDI channel 0–15
```

Two notes are equal iff all five fields match. The content ID for a note is:

```python
sha256(f"pitch:{pitch}\nvelocity:{velocity}\nstart_tick:{start_tick}\n"
       f"duration:{duration_ticks}\nchannel:{channel}")
```

This hash is **portable** across different byte serializations of the same note. It is a content ID in semantic space, not byte space. A canonical-form MIDI and a running-status-compressed MIDI of the same composition produce the same note content IDs.

**Conclusion**: The equality relation required for meaningful version control must be defined on the parsed domain model, not on the raw byte sequence. There is no universal transformation that recovers domain equality from byte equality for binary structured formats.

---

## 4. Argument II: No Flat Projection Preserves Dimensional Independence

**Claim**: For any multidimensional artifact with $n \geq 2$ independent dimensions, no byte serialization preserves the independence structure in a form recoverable by byte-level diff algorithms.

### 4.1 Independence as a Semantic Property

Define a **dimensional decomposition** of a state space $\mathcal{S}$ as a set of projections $\{\pi_1, \pi_2, \ldots, \pi_n\}$ such that:

$$s \cong (\pi_1(s), \pi_2(s), \ldots, \pi_n(s))$$

Dimensions $\pi_i$ and $\pi_j$ are **independent** if edits to $\pi_i(s)$ and $\pi_j(s)$ can be merged without knowledge of the other:

$$\forall s, s', s'': \text{merge}(\pi_i(s), \pi_i(s'), \pi_i(s'')) \text{ is defined independently of } \pi_j$$

### 4.2 The Interleaving Problem

In a MIDI file, dimension data is **interleaved** in the byte stream by temporal position. A MIDI file at tick 4800 might contain (in order): a note-on event (dimension: notes), a control change for CC7 volume (dimension: cc_volume), a control change for CC64 sustain (dimension: cc_sustain), a pitch bend event (dimension: pitch_bend).

These four events are from four independent dimensions, but they appear contiguously in the byte stream. There is no byte-level boundary between dimensions. A byte-level diff algorithm that computes the LCS of two byte streams will see:

- If the file on branch A has the same events but with a new sustain event at tick 4896, and the file on branch B has the same events but with a new CC7 volume automation event at tick 4800 — the byte diff shows both files changed in the same region (around tick 4800).
- A three-way byte merge declares this a conflict.
- The correct answer is: no conflict. The changes are in independent dimensions.

The Muse MIDI plugin recovers independence by **parsing first, then differencing**:

```python
def extract_dimensions(events: list[MidiEvent]) -> MidiDimensions:
    slices: dict[str, DimSlice] = {dim: DimSlice(events=[], sha256="") 
                                    for dim in ALL_DIMENSIONS}
    for event in events:
        dim = classify_event(event)   # domain knowledge required
        slices[dim].events.append(event)
    for dim, slice_ in slices.items():
        slice_.sha256 = sha256_of_events(slice_.events)
    return MidiDimensions(slices=slices)
```

The `classify_event` function encodes the MIDI specification's event taxonomy:

- `note_on` / `note_off` → dimension `notes`
- CC 1 (modulation wheel) → dimension `cc_modulation`
- CC 7 (channel volume) → dimension `cc_volume`
- CC 10 (pan) → dimension `cc_pan`
- CC 64 (sustain pedal) → dimension `cc_sustain`
- `set_tempo` meta-event → dimension `tempo_map`
- `time_signature` meta-event → dimension `time_signatures`

This classification requires **domain knowledge of the MIDI CC assignment table**. There is no algorithmic way to derive it from the bytes. CC 7 and CC 64 are "both control change events" at the byte level — they have the same status byte (`0xBn`), differing only in the controller number byte. A byte-diff algorithm cannot distinguish them as belonging to different independent dimensions.

### 4.3 The Impossibility of Byte-Level Independence Recovery

**Theorem**: For a binary structured format with $n$ semantically independent dimensions interleaved in the byte stream, no purely syntactic byte transformation can recover the independence structure with zero false positives (spurious conflicts) and zero false negatives (missed real conflicts).

**Proof sketch**: Consider a MIDI file $F$ with events from dimensions $D_1$ and $D_2$ interleaved at the byte level. Construct two edits:

- Edit $A$: adds a $D_1$ event at tick $T$.
- Edit $B$: adds a $D_2$ event at tick $T$.

After serialization, both edits insert bytes at the same position in the file (since events at the same tick are serialized contiguously). Any byte-level conflict detection algorithm based on position overlap will declare a conflict. But the edits are to independent dimensions — no conflict should be declared.

To avoid the false positive, the algorithm must know that the inserted bytes belong to different semantic dimensions. This requires parsing the format — applying domain knowledge. No purely syntactic transformation of the bytes can provide this information, because the dimension membership of a byte region is determined by its semantic content (the status byte + controller number + event type), not its position.

The argument is symmetric for false negatives: two edits that change the same dimension at the same position should always conflict, but syntactic proximity is neither necessary nor sufficient for this to hold in interleaved binary formats. $\square$

### 4.4 Quantitative Impact

In the Muse MIDI implementation, 18 of 21 dimensions are marked `independent_merge: True`. A naive file-level conflict detection system (like Git) treats these 18 dimensions as a single conflict region whenever any two are touched concurrently. The false positive rate approaches:

$$P(\text{false conflict}) = 1 - P(\text{both branches touch only one shared dimension})$$

For a development team where each commit is equally likely to touch any of the 18 independent dimensions, the probability that two concurrent commits avoid a spurious conflict is $1/18 \approx 5.6\%$. The remaining $94.4\%$ of concurrent edits will be incorrectly flagged as conflicts.

---

## 5. Argument III: Merge Requires Causal Awareness

**Claim**: Correct merge of multidimensional state requires awareness of **causal dependencies** between dimensions that are invisible at the byte level and derivable only from domain semantics.

### 5.1 The Temporal Dependency Problem in MIDI

A MIDI file's temporal model uses **delta-time encoding**: each event is timestamped with the number of ticks since the previous event, not since the beginning of the track. The conversion from ticks to real time (seconds or beats) requires the tempo map — a sequence of `set_tempo` meta-events that specify beats-per-minute at specific tick positions.

This creates a **causal dependency** between the `tempo_map` dimension and all other dimensions:

$$\text{musical\_time}(\text{event}) = f(\text{event.tick}, \text{tempo\_map})$$

When Alice changes a `set_tempo` event at tick 1920 (bar 4) from 120 BPM to 132 BPM, and Bob concurrently adds a note at tick 5120, the **musical meaning** of Bob's note changes:

- At 120 BPM: tick 5120 at 480 ticks/beat = beat 10.67 = bar 3, beat 2.67 (in post-bar-4 music)
- At 132 BPM: tick 5120 at 480 ticks/beat shifts because the real-time duration of each beat shortens

The byte-level diff of Bob's edit is identical regardless of Alice's tempo change. The bytes Bob added are the same bytes either way. But the musical meaning of Bob's commit changes depending on whether Alice's tempo change was already in the base.

This is a **causal dependency** in the version history DAG, not a syntactic conflict. No byte-level diff or merge algorithm can detect it, because:

1. Bob's new bytes look the same regardless of Alice's tempo change.
2. The dependency is cross-dimensional: the `notes` dimension depends on the `tempo_map` dimension.
3. The dependency is **temporal and positional**: only notes *after* Alice's tempo change at tick 1920 are affected.

### 5.2 The Muse Response: Non-Independent Dimensions

Muse explicitly models this causal dependency by marking `tempo_map` and `time_signatures` as **non-independent**:

```python
DimensionSpec(
    name="tempo_map",
    description="set_tempo meta-events — BPM changes throughout the file",
    schema=SequenceSchema(kind="sequence", element="tempo_event"),
    independent_merge=False,  # ← causal dependency marker
),
DimensionSpec(
    name="time_signatures",
    description="time_signature meta-events — meter changes",
    schema=SequenceSchema(kind="sequence", element="time_sig_event"),
    independent_merge=False,  # ← causal dependency marker
),
```

When both branches touch a non-independent dimension, the merge engine does not attempt auto-resolution — it requires human intervention. The docstring in `midi_merge.py` explains the architectural decision:

> *"Tempo and time signature changes shift the musical meaning of every subsequent tick. Two branches that independently modify tempo cannot be merged automatically — the merge would produce a file where the musical position of every note after the tempo change is ambiguous."*

A byte-level merge would cheerfully combine the two byte streams, producing a MIDI file whose musical meaning is undefined (and likely unintended) by either author.

### 5.3 Causal Dependencies in Other Domains

The tempo-map dependency is not a MIDI quirk — it is an instance of a general class of **global parameter dependencies** that appear in every multidimensional state domain:

| Domain | Global Parameter | Dependent Dimensions |
|--------|------------------|---------------------|
| Music (MIDI) | Tempo map | All note positions, all CC automation curves |
| Music (MIDI) | Time signatures | Bar/beat interpretations, quantization grid |
| Genome | Reference sequence | All annotation positions (gene start/end coordinates) |
| 3D scene | Coordinate system / scale | All mesh positions, light targets, camera frustums |
| Animation | Playback frame rate | All keyframe positions |
| Climate simulation | Timestep / grid resolution | All spatial field arrays |
| Source code | Module dependency graph | All symbol visibility, import resolution |

In each domain, a change to the global parameter dimension shifts the semantic meaning of every element in dependent dimensions. No byte-level merge can detect this, because the dependency is **semantic, not syntactic**.

---

## 6. Argument IV: Entity Identity Cannot Be Recovered from Bytes

**Claim**: Meaningful version control requires **stable entity identity** — the ability to track a specific domain object across mutations. Byte sequences do not support entity identity because an object's byte representation changes under any mutation.

### 6.1 The Diffing Identity Crisis

Consider the standard formulation of the diff problem: given byte sequences $A$ and $B$, find the minimum edit script that transforms $A$ into $B$. For text, this is the LCS problem, solved by Myers' O(nd) algorithm.

For structured data, the diff problem requires a **domain identity function** — a function $\text{id}: \text{element} \to \text{identifier}$ that is stable across mutations. Without it, the minimum edit script for any mutated element is always `delete(old) + insert(new)` — entity lineage is lost.

In MIDI, consider two successive versions of a track:

```
Version 1:  C4 vel=80  @beat=1.00  dur=0.50
            E4 vel=80  @beat=1.50  dur=0.50
            G4 vel=80  @beat=2.00  dur=0.50

Version 2:  C4 vel=100 @beat=1.00  dur=0.50   ← velocity changed
            E4 vel=80  @beat=1.50  dur=0.50
            G4 vel=80  @beat=2.00  dur=0.50
```

A byte-level diff (operating on the MIDI binary) sees: some bytes changed at the position of the first note's velocity field. It can report "bytes changed at offset 14–15." It cannot say "the velocity of note C4 at beat 1.00 changed from 80 to 100."

A note-level LCS diff (operating on the parsed NoteKey representation) sees: `DeleteOp(C4 vel=80 @beat=1.00)` + `InsertOp(C4 vel=100 @beat=1.00)`. This correctly identifies the change as a velocity mutation on a specific note, but it loses entity identity — it treats the change as a delete-and-reinsert.

Muse's `MutateOp` represents the semantically correct view:

```python
MutateOp(
    address="note:uuid-C4@beat1.00",
    entity_id="a3f8-9b12-...",         # stable UUID4 across all mutations
    old_content_id="sha256:abc...",     # hash of (C4, vel=80, tick=480, ...)
    new_content_id="sha256:def...",     # hash of (C4, vel=100, tick=480, ...)
    fields={
        "velocity": FieldMutation(old="80", new="100")
    },
    old_summary="C4 vel=80 @beat=1.00 dur=0.50",
    new_summary="C4 vel=100 @beat=1.00 dur=0.50",
)
```

This representation:
1. Preserves entity identity (the same `entity_id` across all velocity changes to this note)
2. Identifies exactly which field changed (velocity) without full content replacement
3. Enables `muse note-blame` — attributing the current velocity to the commit that last mutated it
4. Enables detecting that two concurrent edits both changed the velocity of the same note (a real conflict) versus one changed velocity and one changed the note's timing (independent changes to the same entity)

### 6.2 The Three-Tier Entity Matching Algorithm

The entity resolution algorithm in Muse's MIDI plugin uses three tiers of matching, each requiring domain knowledge:

**Tier 1: Exact match** — same pitch, velocity, start_tick, duration, channel. No mutation.

**Tier 2: Fuzzy match** — same pitch and channel (±0), with velocity within ±20 and start_tick within ±10 ticks. This captures human expression: a performer adjusted the velocity of a specific note without changing its identity.

**Tier 3: New entity** — no prior match found. A genuinely new note was inserted.

The velocity tolerance of ±20 (±16% of the 0–127 range) and timing tolerance of ±10 ticks require **domain knowledge**:
- ±20 velocity is audible but within the range of natural performance variation.
- ±10 ticks at 480 PPQN at 120 BPM = ±10.4 ms — within MIDI clock resolution.

A byte-level diff has no mechanism to express these tolerances. A byte diff sees "8 bytes changed" regardless of whether the velocity moved by 1 or 127.

### 6.3 The Lineage Tracking Capability

Entity identity enables a class of queries that is structurally impossible with byte-level VCS:

```
$ muse note-blame tracks/lead.mid 60 480
note C4 @tick=480: commit a3f8... "adjusted expression in bar 2" by Alice (2024-03-15)
```

This answers: "who last modified the velocity/timing/channel of the note C4 at tick 480, across the entire commit history?" Git `blame` can answer this question for lines of text because text lines have natural identity (their content). MIDI notes have no byte-level identity — their bytes are not at a fixed offset, the offset changes whenever a preceding event is added or removed.

The `muse note-blame` command requires:
1. Parsing every ancestor commit's MIDI content
2. Resolving the entity registry to track stable `entity_id` values
3. Walking the commit DAG to find the last commit where the target entity's content changed

None of these steps operate on bytes. All operate on the parsed domain model.

---

## 7. Argument V: Semantic Versioning Is a Structural Property

**Claim**: For artifacts with public interfaces (source code, APIs, plugin contracts), semantic version impact is a property of the **symbol structure** of the change, not the byte count. No byte-level transformation can reliably infer `major`, `minor`, or `patch` bump without understanding the domain's structural rules.

### 7.1 The Semver Inference Problem

The semantic version specification (SemVer 2.0.0, Preston-Werner, 2013) defines:
- **MAJOR**: incompatible API change
- **MINOR**: backward-compatible new functionality
- **PATCH**: backward-compatible bug fixes

For source code, these map to structural change categories:
- MAJOR: public function/class deleted, public function signature changed
- MINOR: public function/class added
- PATCH: public function body changed, private function changed, reformatted

None of these categories are computable from a byte diff. Consider:

```
Byte diff: 47 lines added, 23 lines removed in src/processor.py
```

Is this MAJOR, MINOR, or PATCH? Impossible to determine without parsing the source. The same byte statistics could represent: renaming a public function (MAJOR), adding a new public method to a class (MINOR), refactoring a private method (PATCH), or reformatting the entire file (none).

### 7.2 The Muse Implementation

Muse's code domain plugin uses tree-sitter to parse 15 programming languages into ASTs, then applies `symbol_diff.py` to produce `StructuredDelta` entries with explicit semver inference:

```python
def infer_sem_ver_bump(ops: list[DomainOp]) -> Literal["major", "minor", "patch", "none"]:
    bump = "none"
    for op in ops:
        if isinstance(op, DeleteOp) and op["address"].startswith("public:"):
            return "major"   # public symbol deletion — immediate major
        if isinstance(op, ReplaceOp) and op["address"].startswith("public:"):
            if _signature_changed(op):
                return "major"   # public signature changed — major
            bump = "patch"       # body-only change
        if isinstance(op, InsertOp) and op["address"].startswith("public:"):
            bump = max_bump(bump, "minor")   # new public symbol
    return bump
```

The `_signature_changed` function compares AST node types for parameter lists and return types — a structural comparison at the AST level. No byte count, no line count, no diff statistics.

This capability is stored in the `CommitDict` format:

```python
class CommitDict(TypedDict, total=False):
    sem_ver_bump: Literal["major", "minor", "patch", "none"]
    breaking_changes: list[str]   # human-readable breaking change descriptions
```

A `git log --format="%H %s"` cannot provide this. A `muse log --format=semver` can, because every commit carries machine-inferred semver impact derived from the typed operation algebra.

---

## 8. The Muse Architecture: A Constructive Counter-Proof

The five arguments above establish the impossibility of the flat-projection thesis by construction of counterexamples. Muse is the constructive counter-proof: a working system that implements correct version control for multidimensional state.

### 8.1 Design Principles

Three architectural principles distinguish Muse from byte-level VCS systems:

**Principle 1: Domain plugins own semantic interpretation.** The core engine is domain-agnostic. It never parses MIDI, reads ASTs, or interprets genomic formats. All semantic interpretation is delegated to domain plugins through the `MuseDomainPlugin` protocol.

**Principle 2: The diff output is typed operations, not bytes.** All change representation uses a typed operation algebra (`InsertOp`, `DeleteOp`, `MoveOp`, `ReplaceOp`, `MutateOp`, `PatchOp`) that carries semantic addressing, content IDs, and human-readable summaries. The byte representation of a change is never exposed at the diff layer.

**Principle 3: Merge is dimension-aware.** Conflict detection operates on the independence structure declared by the plugin's `schema()` method. Dimensions marked `independent_merge: True` are merged independently; dimensions marked `independent_merge: False` require human resolution if both branches touch them.

### 8.2 The Plugin-Core Separation

The architectural boundary between the core engine and domain plugins is enforced at the import level:

```
muse.core.*         ← domain-agnostic, never imports from muse.plugins.*
muse.plugins.*      ← domain-specific, implements MuseDomainPlugin
```

This boundary is the formal statement that **the core version control operations do not require semantic domain knowledge**. The core engine provides: content-addressed object storage, commit DAG management, BFS LCA for merge base computation, and the typed operation algebra. Domain plugins provide: parsing, diffing, merging, and schema declaration.

The boundary is enforced by `mypy` with strict typing — any import of a plugin from a core module would be a type error.

### 8.3 Evolution: From Music to Domain-Agnostic

The commit history of Muse reveals its research trajectory:

| Commit | Milestone |
|--------|-----------|
| `e1eb9cb` | "Initial extraction from tellurstori/maestro" — music-first origin |
| `7a6a60f` | "Introduce Muse v2 architecture: domain-agnostic VCS with plugin interface" |
| `98d6f7e` | "Remove all Maestro legacy code" — clean separation from music-specific predecessor |
| `1dd3b47` | "Typed delta algebra — replace DeltaManifest with StructuredDelta" — Phase 1 |
| `957ca8d` | "Domain schema declaration + diff algorithm library" — Phase 2 |
| `a1a57ce` | "Operation-level merge engine — OT-based auto-merge" — Phase 3 |
| `efd4bce` | "CRDT semantics for convergent multi-agent writes (691 tests green)" — Phase 4 |

The four-phase evolution represents a **layered theory of semantic richness**:
- Phase 1: What changed (typed operations)
- Phase 2: What structure the data has (domain schema)
- Phase 3: Can the changes commute (OT-based auto-merge)
- Phase 4: Can conflicts be eliminated mathematically (CRDT convergence)

---

## 9. The MuseDomainPlugin Protocol: Six Methods That Cannot Be Bytes

The `MuseDomainPlugin` protocol defines six methods. Each method's signature is a theorem about what information is required for correct version control of a domain — information that is unavailable from byte sequences alone.

```python
@runtime_checkable
class MuseDomainPlugin(Protocol):
    def snapshot(self, live_state: LiveState) -> StateSnapshot: ...
    def diff(self, base: StateSnapshot, target: StateSnapshot,
             *, repo_root: Path | None = None) -> StateDelta: ...
    def merge(self, base: StateSnapshot, left: StateSnapshot, right: StateSnapshot,
              *, repo_root: Path | None = None) -> MergeResult: ...
    def drift(self, committed: StateSnapshot, live: LiveState) -> DriftReport: ...
    def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState: ...
    def schema(self) -> DomainSchema: ...
```

### 9.1 `snapshot`: The Projection Decision

```python
def snapshot(self, live_state: LiveState) -> StateSnapshot:
```

`StateSnapshot = SnapshotManifest` — a `TypedDict` with `files: dict[str, str]` and `domain: str`.

The `files` dict maps workspace-relative paths to SHA-256 content digests. The key insight: **the granularity of change detection is determined by how the plugin slices the artifact into files**.

A music plugin that stores the entire MIDI file as a single `lead.mid → sha256` entry loses all sub-file structure. A more sophisticated plugin (like Muse's) could store dimension-sliced representations: `lead.mid/notes → sha256_of_notes`, `lead.mid/tempo_map → sha256_of_tempo_map`, enabling the manifest diff to detect dimension-level changes without parsing.

The plugin's `snapshot()` method is the *only* place where this architectural decision lives. The core engine never touches it.

### 9.2 `diff`: The Semantic Delta

```python
def diff(self, base: StateSnapshot, target: StateSnapshot,
         *, repo_root: Path | None = None) -> StateDelta:
```

`StateDelta = StructuredDelta` — contains `domain`, `ops: list[DomainOp]`, `summary`, `sem_ver_bump`, and `breaking_changes`.

The `ops` list is the formal statement that **version control change representation is a typed algebra**. The ops are not bytes, not lines, not hunks — they are domain-typed operations with semantic addresses.

A Git diff of the same change would produce: `@@ -14,7 +14,7 @@ ...`. The Muse diff produces:

```python
MutateOp(
    address="note:a3f8-...",
    entity_id="a3f8-...",
    fields={"velocity": FieldMutation(old="80", new="100")},
    old_summary="C4 vel=80 @beat=1.00",
    new_summary="C4 vel=100 @beat=1.00",
)
```

Both represent the same change. Only the second is inspectable, queryable, cherry-pickable, and auditable without re-parsing.

### 9.3 `merge`: The Dimension-Aware Combiner

```python
def merge(self, base: StateSnapshot, left: StateSnapshot, right: StateSnapshot,
          *, repo_root: Path | None = None) -> MergeResult:
```

`MergeResult` carries: `merged`, `conflicts`, `applied_strategies`, `dimension_reports`, `op_log`, and `conflict_records`.

The `dimension_reports` field is the evidence: it maps dimension names to per-dimension merge outcomes. This structure has no analogue in byte-level VCS because dimensions don't exist at the byte level.

### 9.4 `schema`: The Independence Declaration

```python
def schema(self) -> DomainSchema:
```

This is the method that makes machine-verifiable independence possible. The plugin returns a typed declaration of its structure:

```python
DomainSchema(
    top_level=SequenceSchema(kind="sequence", element="midi_file"),
    dimensions=[
        DimensionSpec(name="notes", schema=SequenceSchema(...), independent_merge=True),
        DimensionSpec(name="tempo_map", schema=SequenceSchema(...), independent_merge=False),
        # ... 19 more
    ],
    merge_mode="three_way",
)
```

The `DomainSchema` type system supports five structural types, each mapped to a domain-appropriate diff algorithm:

| Schema Type | Structural Class | Diff Algorithm | Example |
|-------------|-----------------|----------------|---------|
| `SequenceSchema` | Ordered list | LCS (Myers O(nd)) | MIDI notes, DNA |
| `TreeSchema` | Labeled tree | Zhang-Shasha edit distance | ASTs, scene graphs |
| `TensorSchema` | Numerical array | Sparse numerical diff | Simulation state |
| `SetSchema` | Unordered set | Set algebra | File collections |
| `MapSchema` | Key-value map | Key-based recursive | Annotation maps |

A `TensorSchema` carries:

```python
class TensorSchema(TypedDict):
    kind: Literal["tensor"]
    dtype: Literal["float32", "float64", "int8", ...]
    rank: int
    epsilon: float        # ← domain-aware floating-point equality
    diff_mode: Literal["sparse", "block", "full"]
```

The `epsilon` field is a formal statement that **domain equality for floating-point data is not bit equality**. A climate simulation parameter that changes by `1e-12` is numerically unchanged (floating-point rounding). A climate parameter that changes by `0.5` is meaningfully changed. The domain plugin declares which threshold separates noise from signal. No byte-level system can make this distinction.

---

## 10. The Typed Operation Algebra: The Language Bytes Don't Speak

The operation algebra is the formal language for expressing changes in multidimensional state. It has six primitive operations and one composite:

### 10.1 Operation Taxonomy

| Operation | Semantic | Byte-Level Equivalent |
|-----------|---------|----------------------|
| `InsertOp` | A new element was added at a specific position | Bytes added at offset N |
| `DeleteOp` | An existing element was removed | Bytes removed at offset N |
| `MoveOp` | An element changed position without content change | Bytes deleted and re-inserted |
| `ReplaceOp` | An entire element was atomically replaced | Bytes at offset N changed |
| `MutateOp` | Specific fields of an element changed (entity preserved) | (No byte-level equivalent) |
| `PatchOp` | A container was modified; the sub-operations describe the children | Bytes in range N–M changed |

The crucial entry is `MutateOp`: **there is no byte-level equivalent**. Bytes are replaced; they don't remember that the replaced bytes and the replacing bytes represent the same entity with a modified attribute.

### 10.2 The `MutateOp` Impossibility

`MutateOp` carries `fields: dict[str, FieldMutation]`, where `FieldMutation` is:

```python
class FieldMutation(TypedDict):
    old: str    # string representation of old value
    new: str    # string representation of new value
```

This allows the merge engine to detect: "both branch A and branch B mutated the velocity of the same note — this is a conflict." And also: "branch A mutated the velocity of this note, branch B mutated the timing of the same note — these are independent field-level changes, auto-mergeable."

Field-level merge at entity granularity requires:
1. Stable entity identity (the `entity_id`)
2. Typed field decomposition (the `fields` dict)
3. Domain knowledge of which fields are independent (a field-level `independent_merge` policy)

None of these are representable in a byte diff. The best a byte diff can do is flag "bytes in the same region changed on both branches" — a coarse over-approximation that produces many false conflicts.

### 10.3 The Semantic Cherry-Pick Capability

The typed operation algebra enables **semantic cherry-pick** — applying a specific logical change from one branch to another, independent of surrounding context:

```
$ muse semantic-cherry-pick feat/dynamics --range bar:3-7 --dimension notes
```

This command:
1. Computes the `StructuredDelta` for the specified commit range on `feat/dynamics`
2. Filters the `ops` list to `InsertOp` and `MutateOp` entries in the `notes` dimension at addresses between bar 3 and bar 7
3. Applies the filtered ops to the current branch's working state

This operation has no meaningful byte-level equivalent. A Git cherry-pick applies "the diff from this commit" — all byte changes, applied at the same byte offsets (with fuzzy matching for context). It cannot selectively apply "the note-level changes in bars 3–7 but not the dynamics changes or the tempo adjustments." That distinction requires understanding the typed structure of the delta.

---

## 11. The Merge Hierarchy: From File to Dimension to Operation

Muse implements a four-level merge hierarchy, each level adding semantic depth:

### Level 1: File-Level Content-Hash Merge

```python
def detect_conflicts(ours_changed: set[str], theirs_changed: set[str]) -> set[str]:
    return ours_changed & theirs_changed
```

This is what Git does. Two branches that both touch the same file SHA-256 are in conflict at this level. Muse escalates before accepting this verdict.

### Level 2: Dimension-Level Domain Merge

The MIDI plugin parses both conflicting files, extracts 21 dimension slices, computes per-dimension SHA-256 hashes, and checks for per-dimension changes:

```
change = classify_dimension_change(base_sha, left_sha, right_sha)
# → "unchanged", "left_only", "right_only", "both_same", "conflict"
```

Only `"conflict"` (both branches changed the same dimension to different values) triggers a real conflict. The `dimension_reports` output shows exactly which dimensions conflicted and which were auto-merged.

This level transforms the false positive rate from ~94% (file-level) to the true rate — only changes to the same dimension on both branches conflict.

### Level 3: Operation-Level OT Merge

When both branches produce `StructuredDelta` with typed operations, the `StructuredMergePlugin` extension applies Operational Transformation:

```python
def ops_commute(op1: DomainOp, op2: DomainOp) -> bool:
    match (op1["op"], op2["op"]):
        case ("insert", "insert"):
            if op1["address"] != op2["address"]:
                return True   # different containers, always commute
            return op1["position"] != op2["position"]
        case ("insert", "delete"):
            if op1["address"] != op2["address"]:
                return True
            return op1["position"] != op2["position"]
        case ("mutate", "mutate"):
            if op1["entity_id"] != op2["entity_id"]:
                return True   # different entities
            # same entity: check for field overlap
            return not (set(op1["fields"]) & set(op2["fields"]))
        # ... 22 more cases
```

The `("mutate", "mutate")` case is the key: two concurrent `MutateOp`s on the **same entity** are a conflict only if they touch the **same fields**. A velocity mutation and a timing mutation on the same note commute — they modify independent fields of the same entity.

This level is formally equivalent to the commutativity analysis in Operational Transformation theory (Ellis & Gibbs, 1989; Ressel et al., 1996), applied to the domain operation algebra rather than text character operations.

### Level 4: CRDT Convergent Join

For domains with appropriate mathematical structure, the `CRDTPlugin` protocol eliminates conflicts entirely:

```python
class CRDTPlugin(MuseDomainPlugin, Protocol):
    def join(self, a: CRDTSnapshotManifest, b: CRDTSnapshotManifest) -> CRDTSnapshotManifest: ...
```

The `join` operation must be commutative, associative, and idempotent — the properties of a mathematical **join-semilattice**. When the domain's state space forms a lattice under the `join` operation, concurrent edits by any number of agents always converge to the same final state, with no conflicts possible.

The five CRDT primitives implement the building blocks:

| Primitive | Mathematical Structure | Use Case |
|-----------|----------------------|---------|
| `RGA` (Replicated Growing Array) | Causal order on sequence | Collaborative note sequencing |
| `ORSet` (Observed-Remove Set) | Add-wins lattice | Annotation sets, reviewer lists |
| `LWWRegister` (Last-Write-Wins) | Timestamp-ordered lattice | Scalar parameters, metadata |
| `AWMap` (Add-Wins Map) | Component-wise lattice | Key-value annotation stores |
| `GCounter` (Grow-only Counter) | Integer maximum lattice | Monotone metrics |

The `RGA` is particularly relevant for music and genomics: it enables concurrent insertions into an ordered sequence with deterministic, conflict-free ordering, using `{timestamp}@{author}` element IDs as stable anchors.

---

## 12. CRDT Convergence: When Three-Way Merge Is the Wrong Model

The three-way merge model assumes a single merge base — a common ancestor from which two independent edit streams diverge. This assumption fails in several important scenarios:

### 12.1 The Concurrent Multi-Agent Problem

When $N$ AI agents concurrently edit the same multidimensional state, the merge DAG has $N$ concurrent tips, each with the same base. Three-way merge, applied sequentially, is $O(N)$ operations, each potentially producing conflicts that block subsequent merges.

For $N = 10$ concurrent agents, this produces up to 45 pairwise merges, each potentially conflicting. In a high-throughput AI-assisted composition environment, this is untenable.

### 12.2 The CRDT Solution

CRDTs replace the sequential merge pipeline with a single convergent `join` operation. The commit record's CRDT fields use vector clocks to establish causal ordering:

```python
class CRDTSnapshotManifest(TypedDict):
    files: dict[str, str]             # path → sha256 (inherited from StateSnapshot)
    domain: str
    vclock: dict[str, int]            # {agent_id: event_count}
    crdt_state: dict[str, str]        # per-path CRDT metadata → SHA-256
    schema_version: str
```

The vector clock `vclock` establishes causal precedence: agent $A$'s edit at `{A: 5, B: 3}` happened after agent $B$'s edit at `{A: 4, B: 3}` and is concurrent with agent $B$'s edit at `{A: 5, B: 4}`.

The `join` of two concurrent states is computed in constant time (amortized), regardless of how many concurrent edits exist:

```python
def join(a: CRDTSnapshotManifest, b: CRDTSnapshotManifest) -> CRDTSnapshotManifest:
    merged_vclock = {k: max(a.vclock.get(k, 0), b.vclock.get(k, 0)) for k in a.vclock | b.vclock}
    merged_state = rga_join(a.crdt_state, b.crdt_state)  # commutative, associative
    return CRDTSnapshotManifest(vclock=merged_vclock, crdt_state=merged_state, ...)
```

This replaces the entire conflict detection and resolution pipeline with a mathematical operation. The domain must choose: structure its state as a lattice (and get conflict-free convergence) or retain unrestricted structure (and use three-way merge with potential conflicts).

For music, the trade-off is: if we model the note sequence as an RGA (ordered, causal), concurrent note insertions are always conflict-free and converge deterministically. The trade-off is that RGA ordering (by insertion timestamp) may not match musical ordering (by beat position) — a UI concern, not a correctness concern.

---

## 13. Cross-Domain Generalization: Evidence Beyond Music

The Muse plugin architecture has been extended to three domains beyond music, each demonstrating a different facet of the multidimensional state thesis.

### 13.1 Source Code: Symbols as the Unit of Change

The code plugin uses tree-sitter to parse 15 programming languages into ASTs, then applies `symbol_diff.py` to produce symbol-level diffs. The claim: **source code is not a flat byte sequence — it has at minimum a symbol dimension and a dependency dimension**.

The code plugin's `DomainSchema` would declare:
- `SequenceSchema` for ordered statement sequences within function bodies
- `TreeSchema` for the AST of expression nodes within statements
- `SetSchema` for import declarations (order-independent)
- `MapSchema` for the symbol table (function name → function body)

The `muse coupling` command computes change coupling — which files change together across the commit history — from the typed operation log, enabling architectural insight that is structurally impossible from byte diffs.

### 13.2 Bitcoin: UTXO State as Multidimensional

The Bitcoin plugin tracks UTXO (Unspent Transaction Output) state. This demonstrates that **financial state is multidimensional** in a way that byte-level VCS cannot capture:

- The UTXO set is an `ORSet` (add-wins set): coins are created and spent, but the history of spent coins must be preserved for validation
- Transaction spending rules create causal dependencies: UTXO $U$ can only be spent if it was created in a previous block
- The block DAG creates a non-linear history structure that requires CRDT join semantics, not three-way merge

### 13.3 The Scaffold Plugin: Formal Interface for New Domains

The scaffold plugin at `muse/plugins/scaffold/plugin.py` provides a formally typed stub that new domain implementers must fill:

```python
class ScaffoldPlugin:
    """
    Scaffold plugin — copy this as muse/plugins/<domain>/plugin.py.

    Implementation effort by feature level:
    - Level 1 (30 min): snapshot, diff, merge, drift, apply — basic VCS operations
    - Level 2 (30 min): schema() — enables algorithm selection by structure  
    - Level 3 (1 hr): StructuredMergePlugin — OT-based sub-file auto-merge
    - Level 4 (1 hr): CRDTPlugin — convergent multi-agent writes, no conflicts possible
    """
```

The four levels of implementation effort are a formal hierarchy of semantic richness, each level requiring more domain knowledge than the previous:

- Level 1 requires knowing how to parse the format and extract changed elements
- Level 2 requires knowing the data's structural type (sequence, tree, tensor, set, map)
- Level 3 requires knowing which operations commute
- Level 4 requires knowing whether the state space forms a mathematical lattice

### 13.4 The Domain Concept Mapping

The `docs/protocol/muse-domain-concepts.md` establishes a cross-domain vocabulary that generalizes from music:

| Music Concept | Generic Name | Genomics | Climate Simulation |
|---------------|-------------|----------|-------------------|
| Track | Dimension/channel | Chromosome | Parameter set |
| Tempo | Rate metadata | Replication rate | Timestep |
| Key signature | Structural anchor | Reference genome | Baseline run |
| Region | Bounded segment | CRISPR edit window | Grid cell range |
| Variation | Preview-before-commit | Edit review | Parameter sweep eval |

The "Variation" concept is the most theoretically interesting: every domain needs a mechanism to **preview a proposed change in the domain's native modality before committing it to the DAG**. Music: listen. Genomics: simulate. Climate: evaluate against baseline. 3D design: render. Source code: run tests. The underlying abstraction is domain-agnostic; the modality is domain-specific.

---

## 14. Related Work

### 14.1 Git and the Byte-Level Baseline

Git (Torvalds, 2005) introduced content-addressed object storage and the commit DAG, establishing the foundation for modern VCS. Git's design is intentionally byte-agnostic: `git diff` for binary files produces `Binary files X and Y differ`, delegating all semantic interpretation to external tools.

Several Git extensions have addressed specific domain limitations:
- `git-annex`: manages large binary files via symbolic links, but provides no semantic diff
- `git-lfs`: large file storage via pointer files, same limitation
- `git-blame` for binary: unsupported
- Custom merge drivers (via `.gitattributes`): allow external programs to handle merge for specific file types, but require file-level invocation — no dimension-level independence, no typed operation output

### 14.2 Operational Transformation

The OT research program (Ellis & Gibbs, 1989; Ressel et al., 1996; Sun & Ellis, 1998) developed commutativity analysis for collaborative text editing. OT algorithms ensure that concurrent character-level operations converge to the same document regardless of application order, by transforming operations to account for concurrent insertions and deletions.

Muse's `op_transform.py` applies OT commutativity analysis to domain operations rather than character operations. The key extension: domain operations have **typed semantic content** (note velocity, AST node type, genome position) that makes commutativity decidable at a coarser granularity than character level.

The known correctness conditions for OT (the "TP2 property" — Oster et al., 2006) apply in full to the Muse operation algebra when restricted to a single sequence dimension.

### 14.3 CRDTs

The CRDT framework (Shapiro et al., 2011) provides convergence guarantees through mathematical structure rather than transformation. The five CRDT primitives in Muse implement the standard CRDT taxonomy from "A Comprehensive Study of Convergent and Commutative Replicated Data Types" (Shapiro et al., 2011):

- `GCounter` ≅ G-Counter
- `LWWRegister` ≅ LW-Register
- `ORSet` ≅ OR-Set
- `RGA` ≅ RGA (Roh et al., 2011)
- `AWMap` ≅ Add-Wins Map (Bieniusa et al., 2012)

Muse is, to the authors' knowledge, the first system to integrate CRDT primitives directly into a general-purpose VCS at the domain-plugin layer, with the core engine remaining agnostic to whether a domain uses CRDTs or three-way merge.

### 14.4 Music-Specific Systems

Several music notation systems have addressed version control for music:

- **LilyPond**: text-based music notation, enabling standard text VCS. Represents music as a textual DSL rather than structured events. Loses MIDI's temporal precision and performance data.
- **MuseScore's cloud**: proprietary, file-level, no semantic diff.
- **Splice (DAW plugin)**: session-level snapshots, no diff, no merge.
- **Git-based MusicXML workflows**: store MusicXML as text (XML), enabling line-level diffs. Loses MIDI's CC automation and event-level granularity. Merging is XML merge with XML-aware tools.

None of these systems implement: dimension-level independence, entity identity tracking across mutations, typed operation algebra, or CRDT convergence.

### 14.5 Scientific Data Versioning

The scientific data management community has developed domain-specific versioning:
- **DVC** (Data Version Control): content-addressed storage for data files, no semantic diff
- **Pachyderm**: data pipelines with lineage tracking, no domain-semantic diff
- **lakeFS**: Git-like operations on data lakes, file-level granularity

The gap: none of these systems declare the independence structure of scientific data dimensions, integrate domain-specific diff algorithms, or support CRDT convergence for high-concurrency simulation environments.

---

## 15. Discussion and Implications

### 15.1 The Cost of Domain Indifference

The fundamental cost of treating all artifacts as byte sequences is a systematic **loss of semantic information** that is irrecoverable at the VCS layer. Once stored as bytes, the dimensional structure of a MIDI file, the entity identity of a genomic sequence, the independence structure of a scene graph — all are lost. The VCS can be forced to produce a byte diff; it cannot be forced to produce a dimension-aware diff from stored bytes alone.

The consequence is that every higher-level capability — note blame, semantic cherry-pick, dimension-independent merge, entity lineage — must be rebuilt outside the VCS, from scratch, for every domain, without any principled framework.

### 15.2 The Proactive Archival Argument

One response to the flat-projection critique is: "just store more metadata alongside the artifact." Store the parsed note list as a JSON sidecar. Store the entity registry as a separate file. Store the dimension hashes in a `.museattributes` file.

This is correct but undermines the flat-projection thesis: the "metadata" is the semantic representation, and the byte stream is demoted to an archival format. The VCS is now operating on the metadata, not the bytes. This is exactly what Muse does — but doing it ad hoc, without a formal plugin protocol, produces fragile, domain-specific tooling rather than a reusable abstraction.

### 15.3 The AI-Native Dimension

Muse's commit record format includes AI-specific provenance fields:

```python
agent_id: str        # AI model or human agent identifier
model_id: str        # specific model version (e.g., "gpt-4o-2024-11-20")
toolchain_id: str    # orchestration framework
prompt_hash: str     # SHA-256 of the generating prompt
signature: str       # HMAC-SHA256(commit_id, agent_key)
```

This anticipates a future where AI agents are first-class collaborators in creative and scientific workflows. For AI-assisted music composition, genomic editing, and simulation parameter search, the rate of concurrent writes to multidimensional state will be orders of magnitude higher than human-only workflows. The CRDT layer is not a theoretical nicety — it is the only mathematically sound approach to convergent state for high-concurrency AI collaboration.

### 15.4 The Genomics Case: Why This Matters for Science

Consider a team of ten researchers collaborating on a reference genome annotation. Each researcher works on a different chromosome. Under byte-level VCS:
- Each `git push` to the main annotation file is a potential conflict
- Merging requires a human to compare two large binary files with no semantic diff
- No tool can answer "which commit introduced the annotation at position 14,523 on chromosome 3?"
- A researcher cannot cherry-pick "just the gene boundary corrections from branch genomics/chr7-boundary-fix" without taking the entire diff

Under the Muse model with a genomics plugin:
- Each chromosome is a declared dimension with `independent_merge: True`
- Concurrent edits to different chromosomes always merge automatically
- `muse blame genome.gff3 chr3:14523` answers "which commit, by which researcher, established this annotation"
- Semantic cherry-pick applies exactly the gene boundary corrections, filtered by chromosome and position range

The byte-level alternative is not just less convenient — it is structurally incapable of providing these capabilities.

---

## 16. Conclusion

We have made five formal arguments that the flat-projection thesis — the claim that multidimensional state can be adequately version-controlled as byte sequences — is incorrect in principle and in practice.

The arguments are:
1. **Byte equality ≠ domain equality**: non-canonical serializations make byte hashes unreliable as semantic identifiers.
2. **Independence is destroyed by projection**: the dimensional independence structure of multidimensional artifacts is a semantic property that survives only in the parsed domain model.
3. **Merge requires causal awareness**: global parameter dimensions (tempo, coordinate systems, timesteps) create causal dependencies that are invisible to byte-level diff but semantically essential.
4. **Entity identity requires domain knowledge**: stable entity tracking across mutations requires domain-specific identity functions that cannot be derived from bytes.
5. **Semantic versioning is structural**: whether a change is breaking, additive, or cosmetic is a property of the domain's symbol structure, not its byte count.

The Muse implementation is the constructive counter-proof: a working VCS for multidimensional state, with 419 commits, 691 passing tests, domain plugins for music, source code, and Bitcoin, and a formal plugin protocol that can be implemented for any structured domain. The MIDI plugin's 21-dimensional independence model, Myers-LCS note diffing, entity lineage tracking, dimension-aware merge, and optional CRDT convergence are the concrete demonstrations that the alternative is not only theoretically sound but practically achievable.

The central insight is not new — it is the fundamental principle of type theory applied to version control: **the right abstraction for managing change is the type of the thing that changed, not the bytes that represent it**. Muse is an implementation of this principle at the VCS layer, and the MIDI plugin — with its 21 independently mergeable dimensions — is its clearest proof.

The next domains are waiting: genomics, 3D spatial design, scientific simulation, spacetime modeling. The plugin protocol is ready. The core engine will not change. The flat-projection thesis will not hold for any of them.

---

## Appendix A: The 21 Dimensions of MIDI State

The MIDI plugin declares 21 semantic dimensions, each mapping to a subset of MIDI event types:

| # | Dimension Name | Event Types | `independent_merge` | Rationale |
|---|---------------|-------------|--------------------|-|
| 1 | `notes` | note_on, note_off | ✅ True | Core melodic/harmonic content |
| 2 | `pitch_bend` | pitchwheel | ✅ True | Continuous pitch expression |
| 3 | `channel_pressure` | aftertouch (mono) | ✅ True | Mono aftertouch expression |
| 4 | `poly_pressure` | polytouch | ✅ True | Per-note aftertouch |
| 5 | `cc_modulation` | CC 1 | ✅ True | Vibrato/modulation wheel |
| 6 | `cc_volume` | CC 7 | ✅ True | Channel volume fader |
| 7 | `cc_pan` | CC 10 | ✅ True | Stereo pan position |
| 8 | `cc_expression` | CC 11 | ✅ True | Expression pedal |
| 9 | `cc_sustain` | CC 64 | ✅ True | Sustain pedal |
| 10 | `cc_portamento` | CC 65 | ✅ True | Portamento on/off |
| 11 | `cc_sostenuto` | CC 66 | ✅ True | Sostenuto pedal |
| 12 | `cc_soft_pedal` | CC 67 | ✅ True | Soft pedal |
| 13 | `cc_reverb` | CC 91 | ✅ True | Reverb send level |
| 14 | `cc_chorus` | CC 93 | ✅ True | Chorus send level |
| 15 | `cc_other` | All other CCs | ✅ True | Remaining controller automation |
| 16 | `program_change` | program_change | ✅ True | Instrument/patch selection |
| 17 | `key_signatures` | key_signature meta | ✅ True | Key changes (display only) |
| 18 | `markers` | marker, text meta | ✅ True | Section labels, cue points |
| 19 | `tempo_map` | set_tempo meta | ❌ **False** | Shifts all subsequent tick positions |
| 20 | `time_signatures` | time_signature meta | ❌ **False** | Shifts bar/beat grid interpretation |
| 21 | `track_structure` | track_name, sysex | ❌ **False** | Structural/identity metadata |

**Independence rate**: 18/21 = **85.7%**. Under a file-level conflict detection system, any concurrent edit to any two of these 21 dimensions produces a conflict. Under Muse's dimension-level system, concurrent edits to any two of the 18 independent dimensions are auto-merged without conflict.

---

## Appendix B: The Operation Commutativity Table

The OT merge engine's commutativity oracle covers 25 operation-kind pairs:

| Op 1 \ Op 2 | insert | delete | move | replace | mutate | patch |
|-------------|--------|--------|------|---------|--------|-------|
| **insert** | iff different address or position | iff different address or position | generally no | generally no | iff different address | recurse |
| **delete** | iff different address or position | iff different address | generally no | iff different address | iff different address | recurse |
| **move** | generally no | generally no | iff non-overlapping position ranges | generally no | iff different address | generally no |
| **replace** | generally no | iff different address | generally no | iff different address | iff different address | recurse |
| **mutate** | iff different address | iff different address | iff different address | iff different address | iff different entity, or non-overlapping fields | recurse |
| **patch** | recurse | recurse | generally no | recurse | recurse | recurse into child_ops |

"Recurse" means: apply the commutativity check recursively to `child_ops` for `PatchOp`. The operation is commutative iff **all child pairs** commute.

The `("mutate", "mutate")` entry is the key: two `MutateOp`s commute iff they target different entities (`entity_id` differs) or target the same entity but modify non-overlapping fields (`set(op1.fields) ∩ set(op2.fields) == ∅`). This enables field-level independent merge within a single MIDI note entity.

---

## Appendix C: Formal Definitions

**Definition 1 (Domain State Space)**: A domain state space is a triple $(\mathcal{S}, =_{\mathcal{S}}, \{\pi_i\}_{i=1}^n)$ where $\mathcal{S}$ is the set of valid states, $=_{\mathcal{S}}$ is the domain equality relation, and $\pi_i: \mathcal{S} \to \mathcal{S}_i$ are projection functions onto dimension state spaces.

**Definition 2 (Dimensional Independence)**: Dimensions $i$ and $j$ are *independent* if there exists a merge function $m_{ij}: \mathcal{S}_i \times \mathcal{S}_i \times \mathcal{S}_i \to \mathcal{S}_i$ such that $m_{ij}$ operates correctly without knowledge of $\pi_j$.

**Definition 3 (Flat Projection)**: A flat projection of $\mathcal{S}$ is an injective function $\phi: \mathcal{S} \to \{0,1\}^*$ together with a byte-level equality relation $=_{\text{bytes}}$ such that $s_1 =_{\mathcal{S}} s_2 \Leftrightarrow \phi(s_1) =_{\text{bytes}} \phi(s_2)$.

**Theorem (Flat Projection Fails for Independent Dimensions)**: For any domain with $n \geq 2$ independent dimensions where the serialization format interleaves dimension data, no flat projection $\phi$ enables byte-level conflict detection with zero false positives and zero false negatives.

**Proof**: By the independence destruction problem (Section 4.3). $\square$

**Definition 4 (Typed Operation)**: A typed operation is an element of the operation algebra $\mathcal{O} = \text{InsertOp} \cup \text{DeleteOp} \cup \text{MoveOp} \cup \text{ReplaceOp} \cup \text{MutateOp} \cup \text{PatchOp}$, where each variant is a typed dict carrying a semantic `address`, content identifiers, and human-readable summaries.

**Definition 5 (Commutativity)**: Two operations $o_1, o_2 \in \mathcal{O}$ *commute* if for any state $s$: $\text{apply}(o_2, \text{apply}(o_1, s)) = \text{apply}(o_1, \text{apply}(o_2, s))$.

**Definition 6 (CRDT Join-Semilattice)**: A domain state space forms a *join-semilattice* if there exists a binary operation $\sqcup: \mathcal{S} \times \mathcal{S} \to \mathcal{S}$ that is commutative ($a \sqcup b = b \sqcup a$), associative ($a \sqcup (b \sqcup c) = (a \sqcup b) \sqcup c$), and idempotent ($a \sqcup a = a$). In a join-semilattice, the merge of any two states is unique and requires no conflict resolution.

---

*Muse is open source. The implementation discussed in this paper is at `/muse/`. The plugin protocol is in `muse/domain.py`. The MIDI plugin is in `muse/plugins/midi/plugin.py`. The test suite validating the claims in this paper is in `tests/`. All 691 tests pass.*

*Version: 0.1.4 · 419 commits · March 2026*
