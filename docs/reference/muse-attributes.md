# `.museattributes` Reference

> **Format:** TOML · **Location:** repository root (next to `.muse/`)
> **Loaded by:** `muse merge`, `muse cherry-pick`, `muse attributes`

`.museattributes` declares per-path, per-dimension merge strategy overrides for
a Muse repository. It uses TOML syntax for consistency with `.muse/config.toml`
and to allow richer structure (comments, typed values, named sections).

---

## File Structure

```toml
# .museattributes
# Merge strategy overrides for this repository.
# Documentation: docs/reference/muse-attributes.md

[meta]
domain = "midi"    # must match .muse/repo.json "domain" field (optional but recommended)

[[rules]]
path = "drums/*"
dimension = "*"
strategy = "ours"

[[rules]]
path = "keys/*"
dimension = "pitch_bend"
strategy = "theirs"

[[rules]]
path = "*"
dimension = "*"
strategy = "auto"
```

---

## Sections

### `[meta]` (optional)

| Key | Type | Description |
|-----|------|-------------|
| `domain` | string | The domain this file targets. When present, must match `.muse/repo.json "domain"`. If they differ, `muse merge` logs a warning but proceeds. |

`[meta]` has no effect on merge resolution. It provides a machine-readable
declaration of the intended domain and enables validation tooling to warn when
rules may be targeting the wrong plugin.

---

### `[[rules]]` (array)

Each `[[rules]]` entry is a single merge strategy rule. Rules are evaluated
**top-to-bottom**; the **first matching rule wins**.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | yes | An `fnmatch` glob matched against workspace-relative POSIX paths (e.g. `"drums/*"`, `"src/**/*.mid"`). |
| `dimension` | string | yes | A domain axis name (e.g. `"pitch_bend"`, `"notes"`) or `"*"` to match any dimension. |
| `strategy` | string | yes | One of the five strategies below. |

---

## Strategies

| Strategy | Behaviour |
|----------|-----------|
| `auto` | Use the merge engine's automatic algorithm. This is the default when no rule matches. |
| `ours` | Always take the current branch's version. The incoming branch's changes to this path are discarded. |
| `theirs` | Always take the incoming branch's version. The current branch's changes to this path are discarded. |
| `union` | Combine both versions (union semantics). Applicable to set-like dimensions. |
| `manual` | Report this path as a conflict regardless of whether the engine could auto-resolve it. Forces human review. |

---

## Matching Rules

- **Path matching** uses Python's `fnmatch.fnmatch()`. Patterns are matched
  against workspace-relative POSIX path strings (forward slashes, no leading `/`).
- **Dimension matching**: `"*"` in the `dimension` field matches any dimension.
  A named dimension (e.g. `"pitch_bend"`) matches only that dimension.
- **First match wins.** Order your rules from most-specific to least-specific.

---

## Multi-Domain Repositories

If a repository has multiple domain plugins active (multi-domain mode), the
`[meta] domain` field scopes the rules to a specific plugin. Each domain's
`.museattributes` rules should live in a separate file or be clearly delimited.

For single-domain repositories (the common case), `[meta] domain` ensures the
rules are validated against the active plugin — a useful guard when copying
`.museattributes` between repositories.

---

## Examples

### Music repository — drums always ours, keys harmonic auto

```toml
[meta]
domain = "midi"

[[rules]]
path = "drums/*"
dimension = "*"
strategy = "ours"

[[rules]]
path = "keys/*"
dimension = "pitch_bend"
strategy = "auto"

[[rules]]
path = "*"
dimension = "*"
strategy = "auto"
```

### Force manual review for all structural changes

```toml
[meta]
domain = "midi"

[[rules]]
path = "*"
dimension = "track_structure"
strategy = "manual"

[[rules]]
path = "*"
dimension = "*"
strategy = "auto"
```

### Genomics repository — reference sequence is always ours

```toml
[meta]
domain = "genomics"

[[rules]]
path = "reference/*"
dimension = "*"
strategy = "ours"

[[rules]]
path = "edits/*"
dimension = "*"
strategy = "auto"
```

---

## Generated Template

`muse init --domain <name>` writes the following template to the repository root:

```toml
# .museattributes — merge strategy overrides for this repository.
# Documentation: docs/reference/muse-attributes.md
#
# Format: TOML. [[rules]] entries are matched top-to-bottom; first match wins.
# Strategies: ours | theirs | union | auto | manual

[meta]
domain = "<name>"    # must match .muse/repo.json "domain" field

# Add [[rules]] entries below. Examples:
#
# [[rules]]
# path = "tracks/*"
# dimension = "*"
# strategy = "auto"
#
# [[rules]]
# path = "*"
# dimension = "*"
# strategy = "auto"
```

---

## Related

- `.muse/config.toml` — per-repository user, auth, remote, and domain configuration
- `.museignore` — snapshot exclusion list (paths excluded from `muse commit`)
- `muse attributes` — CLI command to display current rules and `[meta]` domain
- `docs/reference/type-contracts.md` — `MuseAttributesFile` TypedDict definition
