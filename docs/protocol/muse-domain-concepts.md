# Muse Domain Concepts & Terminology

> Muse is domain-agnostic at the core. This document clarifies which terms
> are universal VCS primitives, which are cross-domain patterns, and which
> belong only to the music plugin.

---

## Universal Terms (all domains, all plugins)

These terms are part of the core Muse engine. Their definitions contain no
domain-specific meaning.

| Term | Definition |
|---|---|
| **Commit** | A named snapshot in the DAG, with one or more parent commits |
| **Snapshot** | A serializable, content-addressed capture of current state |
| **Branch** | A named, divergent line of intent forked from a shared ancestor |
| **Merge** | Three-way reconciliation of two divergent state lines against a common base |
| **Merge base** | The lowest common ancestor commit of two branches |
| **Conflict** | A path that was modified on both sides of a merge without consensus |
| **Drift** | The delta between the last committed snapshot and the current live state |
| **Checkout** | Deterministic reconstruction of any historical state from the DAG |
| **Lineage** | The causal chain of commits from root to any HEAD |
| **Revert** | A new commit whose snapshot is identical to a prior commit's parent |
| **Cherry-pick** | Applying one commit's delta on top of a different HEAD |
| **Tag** | A named, human-readable reference attached to a specific commit |
| **Stash** | A temporary shelving of uncommitted live-state changes |
| **Reset** | Moving a branch pointer backward (soft: pointer only; hard: also restores working state) |
| **Delta** | The minimal set of additions, removals, and modifications between two snapshots |
| **Object** | A content-addressed binary blob, identified by its SHA-256 digest |
| **Working tree** | The live, uncommitted state the user is currently editing |

---

## The Term "Variation"

**"Variation" is currently a music-domain concept.** It is not part of the core
Muse engine in v0.1.1. This section explains its current meaning and how it might
generalize.

### Current meaning (music domain)

In the music plugin context — specifically the Stori DAW integration —
a *Variation* is a **proposed change set awaiting human review** before being
committed. The lifecycle is:

```
Propose → Stream → Review → Accept (commit) | Discard
```

A Variation maps onto standard VCS concepts as:

| Music (Stori) | Standard VCS |
|---|---|
| Variation | A staged diff |
| Phrase | A hunk (contiguous group of changes) |
| Accept Variation | `muse commit` |
| Discard Variation | Discard working-tree changes |
| Undo Variation | `muse revert` |

The key distinction: a Variation is *auditioned before commit* — the human
listens to the proposed change before deciding to accept it. This is a
domain-specific UX pattern layered on top of VCS primitives, not a VCS
primitive itself.

### Does it generalize?

The *propose → review → commit or discard* pattern is not music-specific. It
appears in many domains:

| Domain | Equivalent of a Variation |
|---|---|
| Music | A proposed MIDI change set, auditioned before commit |
| Genomics | A proposed edit sequence, reviewed before applying to the canonical genome |
| Climate simulation | A proposed parameter change, evaluated against a baseline run |
| 3D spatial design | A proposed layout modification, previewed in the viewport |
| Code review | A pull request diff, reviewed before merging |

The common pattern: **a domain-aware proposal that can be previewed in the
domain's native modality before being committed to the DAG.**

Muse could adopt "Variation" as a first-class VCS primitive — a content-
addressed, reviewable proposal that lives between `snapshot()` and `commit()`.
This is reserved for a future version. For now, the concept belongs to each
domain's plugin and UX layer.

---

## Cross-Domain Term Mapping

When building a new domain plugin, these music-domain terms have natural
analogues:

| Music term | Generic concept | Example (Genomics) | Example (Climate) |
|---|---|---|---|
| **Track** | A named dimension or channel of state | Gene sequence | Model parameter set |
| **Region** | A bounded segment within a track | CRISPR edit window | Grid cell range |
| **Phrase** | A grouped set of changes within a region | Edit block | Parameter sweep |
| **Section** | A high-level structural division | Chromosome arm | Simulation epoch |
| **Emotion** | A semantic label on a commit | Functional annotation | Confidence tier |
| **Tempo** | A rate or throughput metadata field | Replication rate | Timestep |
| **Key** | A tonal or structural anchor | Reference genome | Baseline run |

These are metadata conventions for `commit --<field> <value>` and
`log --<field> <value>`. The core engine stores them in `CommitRecord.metadata`
as `dict[str, str]` — no music-specific meaning is enforced.

---

## What Is and Is Not Music-Specific

### Music-specific (stay in music plugin only)

- MIDI, notes, velocities, controller events (CC), pitch bends, aftertouch
- DAW (Digital Audio Workstation) integration
- Beat-based time (all time in the music plugin is measured in beats, not seconds)
- Groove analysis, swing, harmonic analysis, chord maps
- The `muse groove-check`, `muse emotion-diff`, `muse harmony`, `muse dynamics`
  commands
- `.museattributes` merge strategies keyed on track names and musical dimensions
  (harmonic, rhythmic, melodic, structural, dynamic) — though the file format
  itself could generalize to any domain

### Potentially cross-domain (implemented for music, could generalize)

- **Variation** — the propose-review-commit pattern (see above)
- **Section / Track / Region / Phrase** — structural metadata concepts
- **Emotion / Tempo / Key** — semantic commit labels (already stored generically
  in `metadata`)
- **`.museattributes`** — per-path merge strategy overrides (format is generic;
  content is currently music-specific)

### Definitely universal (core engine, all domains)

Everything in the [Universal Terms](#universal-terms-all-domains-all-plugins)
table above.

---

## Guidance for New Domain Authors

When documenting a new domain plugin, use the universal terms from this document
for shared concepts, and define your own domain vocabulary for concepts that have
no clean analogue.

A good domain glossary entry answers:
1. What is this concept in the domain's own language?
2. Which Muse primitive does it map to?
3. Is it a snapshot dimension, a metadata field, or a behavioral policy?

For example, a genomics plugin might define:

> **Edit Session** — analogous to a Muse *branch*. An edit session is a divergent
> line of CRISPR interventions forked from a reference genome commit. An edit
> session is committed when the intervention set is finalized for review.

That glossary entry is domain-owned, not part of Muse core. The Muse core
only cares that it is a branch.
