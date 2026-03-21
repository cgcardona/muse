# `muse domains` — Domain Plugin Dashboard & Marketplace Publisher

Domain plugins are the extensibility engine of Muse.  Every new type of
multidimensional state — MIDI, source code, genomic sequences, spatial
scenes — is a new domain plugin.  The `muse domains` command surfaces
the plugin registry, guides scaffold creation, and publishes plugins to
the [MuseHub marketplace](https://musehub.ai/domains).

---

## Table of Contents

1. [Overview](#overview)
2. [Commands](#commands)
   - [muse domains (dashboard)](#muse-domains-dashboard)
   - [muse domains --new](#muse-domains---new)
   - [muse domains publish](#muse-domains-publish)
3. [Capability Levels](#capability-levels)
4. [Publishing to MuseHub](#publishing-to-musehub)
   - [Capabilities manifest](#capabilities-manifest)
   - [Authentication](#authentication)
   - [HTTP semantics](#http-semantics)
   - [Agent usage (--json)](#agent-usage---json)
5. [Plugin Architecture](#plugin-architecture)
6. [Examples](#examples)

---

## Overview

```
muse domains              # human-readable dashboard
muse domains --json       # machine-readable registry dump
muse domains --new <name> # scaffold a new plugin directory
muse domains publish      # register a plugin on MuseHub
```

All four modes operate without an active Muse repository (though `publish`
benefits from having one — it auto-derives capability metadata from the
repo's active domain plugin).

---

## Commands

### `muse domains` (dashboard)

Print a human-readable table of every registered domain plugin.

```
╔══════════════════════════════════════════════════════════════╗
║               Muse Domain Plugin Dashboard                   ║
╚══════════════════════════════════════════════════════════════╝

Registered domains: 2
──────────────────────────────────────────────────────────────

  ●  midi  ← active repo domain
     Module:        plugins/midi/plugin.py
     Capabilities:  Typed Deltas · Domain Schema · OT Merge
     Schema:        v0.1.3 · top_level: set · merge_mode: three_way
     Dimensions:    notes, pitch_bend, tempo_map, … (21 total)

  ○  scaffold
     Module:        plugins/scaffold/plugin.py
     Capabilities:  Typed Deltas · Domain Schema · OT Merge · CRDT
     Schema:        v0.1.3 · top_level: set · merge_mode: three_way
     Dimensions:    primary, metadata
──────────────────────────────────────────────────────────────
```

The `●` marker identifies the active domain of the current repository.
`○` means registered but not active in this repo.

**Options:**

| Flag | Description |
|------|-------------|
| `--json` | Emit registry as machine-readable JSON (see [Agent usage](#agent-usage---json)) |

---

### `muse domains --new`

Scaffold a new domain plugin directory from the built-in scaffold template.

```
muse domains --new genomics
```

Creates `muse/plugins/genomics/` by copying `muse/plugins/scaffold/` and
renaming `ScaffoldPlugin` → `GenomicsPlugin` throughout.  The scaffold
implements the minimal `MuseDomainPlugin` protocol so `muse domains` shows
it immediately.

**What gets created:**

```
muse/plugins/genomics/
  __init__.py
  plugin.py      # GenomicsPlugin implements MuseDomainPlugin
```

After scaffolding, implement the six required methods in `plugin.py`:

| Method | Required | Description |
|--------|----------|-------------|
| `name()` | yes | Canonical domain name string |
| `diff(base, head)` | yes | Compute semantic delta |
| `apply(state, delta)` | yes | Apply a delta to a state |
| `merge(base, ours, theirs)` | yes | Three-way merge |
| `schema()` | recommended | Return `DomainSchema` for rich tooling |
| `serialize(state)` / `deserialize(blob)` | yes | Round-trip to bytes |

---

### `muse domains publish`

Register a Muse domain plugin on the [MuseHub marketplace](https://musehub.ai/domains)
so agents and users can discover and install it.

```
muse domains publish \
  --author <slug> \
  --slug   <slug> \
  --name   <display-name> \
  --description <text> \
  --viewer-type <type>
```

**Required options:**

| Flag | Description |
|------|-------------|
| `--author SLUG` | Your MuseHub username (owner of the domain) |
| `--slug SLUG` | URL-safe domain identifier (e.g. `genomics`, `spatial-3d`) |
| `--name NAME` | Human-readable marketplace display name |
| `--description TEXT` | What this domain models and why it benefits from semantic VCS |
| `--viewer-type TYPE` | Primary viewer identifier (`midi`, `code`, `spatial`, `genome`, …) |

**Optional options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--version SEMVER` | `0.1.0` | Semantic version string |
| `--capabilities JSON` | _(auto-derived)_ | Full capabilities manifest as JSON (see below) |
| `--hub URL` | `.muse/config.toml` | Override MuseHub base URL |
| `--json` | off | Emit server response as machine-readable JSON |

---

## Capability Levels

Every domain plugin is classified by the capabilities it implements:

| Capability | Protocol | Description |
|------------|----------|-------------|
| **Typed Deltas** | `MuseDomainPlugin` | Core — every domain gets this |
| **Domain Schema** | `schema()` method returns `DomainSchema` | Declares dimensions, merge mode, version |
| **OT Merge** | `StructuredMergePlugin` | Operational-transform three-way merge |
| **CRDT** | `CRDTPlugin` | Convergent replicated data type merge |

The dashboard shows `Typed Deltas · Domain Schema · OT Merge · CRDT` in
ascending capability order.  These are also reflected in the `capabilities`
field of the published marketplace manifest.

---

## Publishing to MuseHub

### Capabilities manifest

When `--capabilities` is **omitted**, the command reads the active repo's
domain plugin schema and derives the manifest automatically:

```python
schema = plugin.schema()
capabilities = {
    "dimensions": [{"name": d["name"], "description": d["description"]} for d in schema["dimensions"]],
    "merge_semantics": schema["merge_mode"],   # "three_way" or "crdt"
    "supported_commands": ["commit", "diff", "merge", "log", "status"],
}
```

When `--capabilities` is provided, it must be a JSON object with any
subset of these keys:

```json
{
  "dimensions": [
    {"name": "geometry", "description": "3-D mesh vertex and face data"},
    {"name": "materials", "description": "PBR material assignments"}
  ],
  "artifact_types": ["glb", "usdz", "obj"],
  "merge_semantics": "three_way",
  "supported_commands": ["commit", "diff", "merge", "log", "status"]
}
```

### Authentication

`muse domains publish` uses the same bearer token as `muse push`:

```
muse auth login          # stores token in ~/.muse/identity.toml
muse hub connect <url>   # sets hub URL in .muse/config.toml
```

The token is read by `get_auth_token()` from the hub URL configured in
`.muse/config.toml`.  If no token is found, the command exits `1` with
instructions for `muse auth login`.

### HTTP semantics

The command POSTs to `POST /api/v1/domains` on the configured MuseHub
instance.  Possible responses:

| HTTP status | Meaning | CLI behaviour |
|-------------|---------|---------------|
| `200 OK` | Domain registered | Prints `✅ Domain published: @author/slug` |
| `409 Conflict` | Slug already registered | Exits `1` with "already registered" hint |
| `401 Unauthorized` | Invalid or expired token | Exits `1` with re-login instructions |
| `5xx` | Server error | Exits `1` with raw HTTP status |
| Network error | DNS / connection failure | Exits `1` with "Could not reach" message |

### Agent usage (`--json`)

```bash
muse domains publish \
  --author alice --slug spatial \
  --name "Spatial 3D" \
  --description "Version 3-D scenes as structured state" \
  --viewer-type spatial \
  --json
```

Output (JSON, stdout):

```json
{
  "domain_id": "dom-abc123",
  "scoped_id": "@alice/spatial",
  "manifest_hash": "sha256:def456..."
}
```

Non-zero exit codes are always accompanied by a human-readable error on
stderr.  The `--json` flag affects stdout only.

---

## Plugin Architecture

Every domain plugin lives under `muse/plugins/<name>/plugin.py` and
implements the `MuseDomainPlugin` protocol defined in `muse/domain.py`.

```
muse/
  domain.py               ← MuseDomainPlugin protocol + DomainSchema type
  plugins/
    registry.py           ← _REGISTRY: dict[str, MuseDomainPlugin]
    midi/
      plugin.py           ← MidiPlugin (reference implementation)
    scaffold/
      plugin.py           ← ScaffoldPlugin (copy template for new domains)
    <your-domain>/
      plugin.py           ← YourPlugin implements MuseDomainPlugin
```

The core engine in `muse/core/` **never imports** from `muse/plugins/`.
Domain dispatch is achieved entirely through the `MuseDomainPlugin`
protocol — the engine calls the six methods; it does not know or care
about MIDI, DNA, or spatial geometry.

**Registering a new plugin:**

```python
# muse/plugins/registry.py
from muse.plugins.genomics.plugin import GenomicsPlugin

_REGISTRY: dict[str, MuseDomainPlugin] = {
    "midi": MidiPlugin(),
    "scaffold": ScaffoldPlugin(),
    "genomics": GenomicsPlugin(),   # add your plugin here
}
```

---

## Examples

### List all registered domains (machine-readable)

```bash
muse domains --json | jq '.[].name'
```

### Scaffold and immediately publish a new domain

```bash
# 1. Scaffold
muse domains --new genomics

# 2. Implement plugin.py
# ... implement MuseDomainPlugin methods ...

# 3. Register in muse/plugins/registry.py
# ... add GenomicsPlugin() to _REGISTRY ...

# 4. Publish to MuseHub
muse domains publish \
  --author alice \
  --slug genomics \
  --name "Genomics" \
  --description "Version DNA sequences as multidimensional state" \
  --viewer-type genome \
  --version 0.1.0
```

### Override capabilities for an out-of-repo publish

```bash
muse domains publish \
  --author alice \
  --slug spatial-3d \
  --name "Spatial 3D" \
  --description "3-D scene version control" \
  --viewer-type spatial \
  --capabilities '{
    "dimensions": [
      {"name": "geometry", "description": "Mesh data"},
      {"name": "materials", "description": "PBR material assignments"},
      {"name": "lights", "description": "Light rig"}
    ],
    "artifact_types": ["glb", "usdz"],
    "merge_semantics": "three_way",
    "supported_commands": ["commit", "diff", "merge", "log"]
  }' \
  --json
```

### Use via agent (`musehub_publish_domain` MCP tool)

Agents do not need the CLI.  MuseHub exposes `musehub_publish_domain`
as a first-class MCP tool:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "musehub_publish_domain",
    "arguments": {
      "author_slug": "alice",
      "slug": "genomics",
      "display_name": "Genomics",
      "description": "Version DNA sequences",
      "viewer_type": "genome",
      "version": "0.1.0"
    }
  }
}
```

See [`musehub_publish_domain`](https://musehub.ai/mcp/docs#musehub_publish_domain)
in the MuseHub MCP reference for the full schema.
