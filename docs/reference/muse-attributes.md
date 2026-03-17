# `.museattributes` Reference

`.museattributes` is a per-repository configuration file that declares merge
strategies for specific paths and dimensions. It lives in the repository root,
alongside `muse-work/`.

---

## Purpose

Without `.museattributes`, every conflict in a three-way merge requires manual
resolution. `.museattributes` lets you encode domain knowledge once so
`muse merge` can skip conflict detection for well-understood cases.

For example: "this team's changes to the structural layer always win" can be
expressed as a single rule rather than being resolved manually on every merge.

---

## File Format

```
<path-pattern>  <dimension>  <strategy>
```

- **path-pattern** — an `fnmatch` glob matched against workspace-relative paths
  (e.g. `drums/*`, `src/models/**, `*`)
- **dimension** — a domain-defined dimension name, or `*` to match all dimensions
- **strategy** — `ours | theirs | union | auto | manual`

Lines beginning with `#` and blank lines are ignored. **First matching rule wins.**

---

## Strategies

| Strategy | Behavior |
|---|---|
| `ours` | Take the current branch's version unconditionally. Skip conflict detection. |
| `theirs` | Take the incoming branch's version unconditionally. Skip conflict detection. |
| `union` | Include both sides' changes; fall through to three-way merge. |
| `auto` | Let the merge engine decide (default when no rule matches). |
| `manual` | Flag this path/dimension for mandatory human resolution. |

---

## Music Domain Examples

```
# Drums are always authoritative — take ours on every dimension:
drums/*     *          ours

# Accept a collaborator's harmonic changes on key instruments:
keys/*      harmonic   theirs
bass/*      harmonic   theirs

# Require manual review for all structural changes:
*           structural manual

# Default for everything else:
*           *          auto
```

### Music Dimensions

| Dimension | What it covers |
|---|---|
| `melodic` | Note pitch and pitch-class resolution |
| `rhythmic` | Note start-beat and duration resolution |
| `harmonic` | Pitch-bend event resolution |
| `dynamic` | CC and aftertouch event resolution |
| `structural` | Section and region-level structure |

> **Implementation status:** Dimension names are parsed and validated. Wiring
> into the merge engine's three-way reconciliation is reserved for a future
> release. Writing dimension-specific rules now is safe — they will take effect
> automatically once the merge engine is updated.

---

## Generic Domain Example

The `.museattributes` format is not music-specific. Any domain plugin can define
its own dimension names and path patterns. For a hypothetical genomics plugin:

```
# Reference sequence is always canonical:
reference/*   *           ours

# Accept collaborator's annotations:
annotations/* semantic     theirs

# All structural edits require manual review:
*             structural   manual

# Default:
*             *            auto
```

The path-pattern and strategy syntax is identical. Only the dimension names
and path conventions are domain-specific.

---

## CLI

```bash
muse attributes [--json]
```

Reads and displays the `.museattributes` rules from the current repository.

---

## Behavior During `muse merge`

1. `load_attributes(repo_path)` reads the file (if present).
2. `resolve_strategy(attributes, path, dimension)` returns the first matching rule.
3. `ours` → take the left (current HEAD) snapshot for this path.
4. `theirs` → take the right (incoming) snapshot for this path.
5. All other strategies → fall through to three-way merge.

If `.museattributes` is absent, `muse merge` behaves as if all paths use `auto`.

---

## Resolution Precedence

Rules are evaluated top-to-bottom. The first rule where **both** `path-pattern`
and `dimension` match (via `fnmatch`) wins.

If no rule matches, `auto` is used.

---

## Notes

- `ours` and `theirs` are positional: `ours` = the branch merging INTO (current HEAD),
  `theirs` = the branch merging FROM (incoming).
- Path patterns follow POSIX conventions (forward slashes).
- The file is optional. Its absence has no effect on merge correctness.
