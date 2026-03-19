# `.museattributes` Reference

> **Format:** TOML · **Location:** repository root (next to `.muse/`)
> **Loaded by:** `muse merge`, `muse cherry-pick`, `muse attributes`
> **Updated:** v0.1.2 — added `base` strategy, `comment` and `priority` fields,
> priority-based rule ordering, and full code-domain support.

`.museattributes` declares per-path, per-dimension merge strategy overrides for
a Muse repository. It uses TOML syntax for consistency with `.muse/config.toml`
and to allow richer structure (comments, typed values, named sections).

The file is domain-agnostic — the same format works for MIDI, code, genomics,
3D design, scientific simulation, or any future domain.

---

## File Structure

```toml
# .museattributes
# Merge strategy overrides for this repository.
# Documentation: docs/reference/muse-attributes.md

[meta]
domain = "midi"    # optional — validated against .muse/repo.json "domain"

[[rules]]
path      = "drums/*"        # fnmatch glob
dimension = "*"              # domain axis, or "*" for any
strategy  = "ours"           # resolution strategy
comment   = "Drums are always authored on this branch."
priority  = 20               # higher = evaluated first

[[rules]]
path      = "keys/*"
dimension = "pitch_bend"
strategy  = "theirs"
comment   = "Remote always has the better pitch-bend automation."
priority  = 15

[[rules]]
path      = "*"
dimension = "*"
strategy  = "auto"
```

---

## Sections

### `[meta]` (optional)

| Key | Type | Description |
|-----|------|-------------|
| `domain` | string | The domain this file targets. When present, validated against `.muse/repo.json "domain"`. A mismatch logs a warning but does not abort. |

`[meta]` has no effect on merge resolution. It provides a machine-readable
declaration of the intended domain, enabling tooling to warn when rules may be
targeting the wrong plugin.

---

### `[[rules]]` (array)

Each `[[rules]]` entry is a single merge strategy rule.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `path` | string | **yes** | — | `fnmatch` glob matched against workspace-relative POSIX paths (e.g. `"drums/*"`, `"src/**/*.py"`). |
| `dimension` | string | **yes** | — | Domain axis name (e.g. `"pitch_bend"`, `"symbols"`) or `"*"` to match any dimension. |
| `strategy` | string | **yes** | — | One of the six strategies (see below). |
| `comment` | string | no | `""` | Free-form documentation explaining *why* the rule exists. Ignored at runtime. |
| `priority` | integer | no | `0` | Ordering weight. Higher-priority rules are evaluated before lower-priority ones, regardless of their position in the file. Ties preserve declaration order. |

---

## Strategies

| Strategy | Behaviour |
|----------|-----------|
| `auto` | **Default.** Defer to the three-way merge engine. Unmatched paths always use this. |
| `ours` | Take the current-branch (left) version. Remove the path from the conflict list. |
| `theirs` | Take the incoming-branch (right) version. Remove the path from the conflict list. |
| `union` | Include **all** additions from both sides. Deletions are honoured only when both sides agree. Best for independent element sets (MIDI notes, symbol additions, import sets, genomic mutations). Falls back to `ours` for binary blobs where full unification is impossible. |
| `base` | Revert to the **common merge-base version** — discard changes from *both* branches. Use this for generated files, lock files, or any path that must stay at a known-good state during a merge. |
| `manual` | Force the path into the conflict list for human review, even when the engine would auto-resolve it. |

---

## Rule Evaluation Order

Rules are sorted by `priority` (descending) then by declaration order (ascending)
before evaluation. The **first matching rule wins**.

```
Rule evaluation order = sort by -priority, then by file position
```

This means:

- A `priority = 100` rule declared *anywhere* in the file always beats a
  `priority = 0` catch-all, no matter where either appears.
- Rules with equal `priority` preserve the order they were written in.
- When no rule matches, the strategy falls back to `"auto"`.

**Recommended pattern:** assign high `priority` values to narrow, safety-critical
rules (secrets, generated files, master tracks); assign `priority = 0` to your
broad catch-all rule.

---

## Matching Rules

- **Path matching** uses Python's `fnmatch.fnmatch()`. Patterns are matched
  against workspace-relative POSIX path strings (forward slashes, no leading `/`).
  `*` matches within a directory segment; `**` matches across segments.
- **Dimension matching**: `"*"` in the `dimension` field matches any dimension.
  A named dimension (e.g. `"pitch_bend"`) matches only that exact name.
- **When the caller passes `dimension="*"`**, any rule dimension matches — this is
  used when issuing a file-level strategy query that does not target a specific axis.

---

## Domain Integration

### MIDI domain

Rules are applied at two levels:

1. **File level** — strategy resolved against the full file path and `dimension="*"`.
   `ours` / `theirs` / `base` / `union` / `manual` all fire before any MIDI-specific
   processing.
2. **Dimension level** — for `.mid` files not resolved at file level,
   `merge_midi_dimensions` checks the named dimension (e.g. `"notes"`,
   `"pitch_bend"`) against the rule list. Dimension aliases (e.g. `"tempo"` for
   `"tempo_map"`) are also matched.

**MIDI dimensions** (usable in `dimension`):
`notes`, `pitch_bend`, `channel_pressure`, `poly_pressure`,
`cc_modulation`, `cc_volume`, `cc_pan`, `cc_expression`,
`cc_sustain`, `cc_portamento`, `cc_sostenuto`, `cc_soft_pedal`,
`cc_reverb`, `cc_chorus`, `cc_other`, `program_change`,
`tempo_map`, `time_signatures`, `key_signatures`, `markers`,
`track_structure`

**Aliases**: `aftertouch`, `poly_aftertouch`, `modulation`, `volume`, `pan`,
`expression`, `sustain`, `portamento`, `sostenuto`, `soft_pedal`, `reverb`,
`chorus`, `automation`, `program`, `tempo`, `time_sig`, `key_sig`

**Non-independent dimensions** (`tempo_map`, `time_signatures`, `track_structure`):
a conflict in any of these blocks the entire file merge, because they are
structurally coupled.

### Code domain

Rules are applied at two levels:

1. **File level** — inside `CodePlugin.merge()`, strategy resolved against each
   file path and `dimension="*"`. All six strategies are fully implemented.
   `manual` also fires on one-sided auto-resolved paths (i.e. a path where only
   one branch changed — `manual` forces it into the conflict list anyway).
2. **Symbol level** — inside `CodePlugin.merge_ops()`, symbol-level conflict
   addresses (`"src/utils.py::calculate_total"`) are checked by extracting the
   file path and calling `resolve_strategy`. A `path = "src/**/*.py"` /
   `strategy = "ours"` rule suppresses symbol-level conflicts inside those files,
   not just file-level manifest conflicts.

**Code dimensions** (usable in `dimension`):
`structure`, `symbols`, `imports`, `variables`, `metadata`

---

## Examples

### MIDI — drums always ours, pitch-bend from remote, union on stems

```toml
[meta]
domain = "midi"

[[rules]]
path      = "master.mid"
dimension = "*"
strategy  = "manual"
comment   = "Master track must always be reviewed by a human."
priority  = 100

[[rules]]
path      = "drums/*"
dimension = "*"
strategy  = "ours"
comment   = "Drum tracks are always authored on this branch."
priority  = 20

[[rules]]
path      = "keys/*.mid"
dimension = "pitch_bend"
strategy  = "theirs"
comment   = "Remote always has better pitch-bend automation."
priority  = 15

[[rules]]
path      = "stems/*"
dimension = "notes"
strategy  = "union"
comment   = "Combine note additions from both arrangers."

[[rules]]
path      = "mixdown.mid"
dimension = "*"
strategy  = "base"
comment   = "Mixdown is generated — revert to ancestor during merge."

[[rules]]
path      = "*"
dimension = "*"
strategy  = "auto"
```

### Code — generated files reverted, test additions unioned, core reviewed

```toml
[meta]
domain = "code"

[[rules]]
path      = "config/secrets.*"
dimension = "*"
strategy  = "manual"
comment   = "Secrets require human review — never auto-merge."
priority  = 100

[[rules]]
path      = "src/generated/**"
dimension = "*"
strategy  = "base"
comment   = "Generated — always revert to ancestor; re-run codegen after merge."
priority  = 30

[[rules]]
path      = "src/core/**"
dimension = "*"
strategy  = "manual"
comment   = "Core changes need human review on every merge."
priority  = 25

[[rules]]
path      = "tests/**"
dimension = "symbols"
strategy  = "union"
comment   = "Test additions from both branches are always safe to combine."

[[rules]]
path      = "src/**/*.py"
dimension = "imports"
strategy  = "union"
comment   = "Import sets are independent; accumulate additions from both sides."

[[rules]]
path      = "package-lock.json"
dimension = "*"
strategy  = "ours"
comment   = "Lock file is managed by this branch's CI."

[[rules]]
path      = "*"
dimension = "*"
strategy  = "auto"
```

### Genomics — reference always ours, mutation sets unioned

```toml
[meta]
domain = "genomics"

[[rules]]
path      = "reference/*"
dimension = "*"
strategy  = "ours"
comment   = "Reference sequence is always maintained on main."
priority  = 50

[[rules]]
path      = "mutations/*"
dimension = "*"
strategy  = "union"
comment   = "Accumulate mutations from both experimental branches."

[[rules]]
path      = "*"
dimension = "*"
strategy  = "auto"
```

### Force manual review on all structural changes (any domain)

```toml
[[rules]]
path      = "*"
dimension = "track_structure"
strategy  = "manual"
priority  = 50

[[rules]]
path      = "*"
dimension = "*"
strategy  = "auto"
```

---

## `applied_strategies` in MergeResult

After a merge, `MergeResult.applied_strategies` is a `dict[str, str]` mapping
each path (or symbol address for the code domain) where a `.museattributes` rule
fired to the strategy that was applied. The `muse merge` CLI prints this as:

```
✔ [ours]    drums/kick.mid
✔ [base]    mixdown.mid
✔ [union]   stems/bass.mid
✔ [manual]  master.mid
```

Paths resolved by the default `"auto"` strategy are not included — only explicit
overrides appear in the map.

---

## Generated Template

`muse init --domain <name>` writes a fully-commented template to the repository
root. The template documents all six strategies, all five rule fields, and
includes annotated examples for MIDI, code, and generic repositories.

---

## Related

- `.muse/config.toml` — per-repository user, auth, remote, and domain configuration
- `.museignore` — snapshot exclusion rules (TOML; `[global]` + `[domain.<name>]` sections; paths excluded from `muse commit`)
- `muse attributes` — CLI command to display current rules and `[meta]` domain
- `docs/reference/type-contracts.md` — `AttributeRule`, `MuseAttributesFile`, `MergeResult` TypedDict definitions
- `docs/reference/code-domain.md` — code domain schema and dimensions
