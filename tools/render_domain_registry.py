#!/usr/bin/env python3
"""Muse Domain Registry — standalone HTML generator.

Produces a self-contained, shareable page that explains the MuseDomainPlugin
protocol, shows the registered plugin ecosystem, and guides developers through
scaffolding and publishing their own domain plugin.

Stand-alone usage
-----------------
    python tools/render_domain_registry.py
    python tools/render_domain_registry.py --out artifacts/domain_registry.html
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Live domain data from the CLI
# ---------------------------------------------------------------------------


def _compute_crdt_demos() -> list[dict]:
    """Run the four CRDT primitives live and return formatted demo output."""
    sys.path.insert(0, str(_ROOT))
    try:
        from muse.core.crdts import GCounter, LWWRegister, ORSet, VectorClock

        # ORSet
        base, _ = ORSet().add("annotation-GO:0001234")
        a, _    = base.add("annotation-GO:0001234")
        b       = base.remove("annotation-GO:0001234", base.tokens_for("annotation-GO:0001234"))
        merged  = a.join(b)
        orset_out = "\n".join([
            "ORSet — add-wins concurrent merge:",
            f"  base  elements: {sorted(base.elements())}",
            f"  A re-adds  →  elements: {sorted(a.elements())}",
            f"  B removes  →  elements: {sorted(b.elements())}",
            f"  join(A, B) →  elements: {sorted(merged.elements())}",
            "  [A's new token is not tombstoned — add always wins]",
        ])

        # LWWRegister
        ra = LWWRegister.from_dict({"value": "80 BPM",  "timestamp": 1.0, "author": "agent-A"})
        rb = LWWRegister.from_dict({"value": "120 BPM", "timestamp": 2.0, "author": "agent-B"})
        rm = ra.join(rb)
        lww_out = "\n".join([
            "LWWRegister — last-write-wins scalar:",
            f"  Agent A writes: '{ra.read()}' at t=1.0",
            f"  Agent B writes: '{rb.read()}' at t=2.0  (later)",
            f"  join(A, B) → '{rm.read()}'  [higher timestamp wins]",
            "  join(B, A) → same result  [commutativity]",
        ])

        # GCounter
        ca = GCounter().increment("agent-A").increment("agent-A")
        cb = GCounter().increment("agent-B").increment("agent-B").increment("agent-B")
        cm = ca.join(cb)
        gc_out = "\n".join([
            "GCounter — grow-only distributed counter:",
            f"  Agent A x2  →  A slot: {ca.value_for('agent-A')}",
            f"  Agent B x3  →  B slot: {cb.value_for('agent-B')}",
            f"  join(A, B) global value: {cm.value()}",
            "  [monotonically non-decreasing — joins never lose counts]",
        ])

        # VectorClock
        va = VectorClock().increment("agent-A")
        vb = VectorClock().increment("agent-B")
        vm = va.merge(vb)
        vc_out = "\n".join([
            "VectorClock — causal ordering:",
            f"  Agent A: {va.to_dict()}",
            f"  Agent B: {vb.to_dict()}",
            f"  concurrent_with(A, B): {va.concurrent_with(vb)}",
            f"  merge(A, B): {vm.to_dict()}  [component-wise max]",
        ])

        return [
            {"type": "ORSet",       "sub": "Observed-Remove Set",          "color": "#bc8cff", "icon": "∪", "output": orset_out},
            {"type": "LWWRegister", "sub": "Last-Write-Wins Register",     "color": "#58a6ff", "icon": "✎", "output": lww_out},
            {"type": "GCounter",    "sub": "Grow-Only Distributed Counter", "color": "#3fb950", "icon": "↑", "output": gc_out},
            {"type": "VectorClock", "sub": "Causal Ordering",              "color": "#f9a825", "icon": "⊕", "output": vc_out},
        ]
    except Exception as exc:
        print(f"  ⚠ CRDT demo failed ({exc}); using static fallback")
        return []


def _load_domains() -> list[dict]:
    """Run `muse domains --json` and return parsed output."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "muse", "domains", "--json"],
            capture_output=True,
            text=True,
            cwd=str(_ROOT),
            timeout=15,
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            data: list[dict] = json.loads(raw)
            return data
    except Exception:
        pass

    # Fallback: static reference data
    return [
        {
            "domain": "music",
            "active": "true",
            "capabilities": ["Typed Deltas", "Domain Schema", "OT Merge"],
            "schema": {
                "schema_version": "1",
                "merge_mode": "three_way",
                "description": "MIDI and audio file versioning with note-level diff and semantic merge",
                "dimensions": [
                    {"name": "melodic",    "description": "Note pitches and durations over time"},
                    {"name": "harmonic",   "description": "Chord progressions and key signatures"},
                    {"name": "dynamic",    "description": "Velocity and expression curves"},
                    {"name": "structural", "description": "Track layout, time signatures, tempo map"},
                ],
            },
        }
    ]


# ---------------------------------------------------------------------------
# Scaffold template (shown in the "Build in 3 steps" section)
# ---------------------------------------------------------------------------

_TYPED_DELTA_EXAMPLE = """\
# muse show --json  (any commit, any domain)
{
  "commit_id": "b26f3c99",
  "message": "Resolve: integrate shared-state (A+B reconciled)",
  "operations": [
    {
      "op_type": "ReplaceOp",
      "address": "shared-state.mid",
      "before_hash": "a1b2c3d4",
      "after_hash":  "e5f6g7h8",
      "dimensions":  ["structural"]
    },
    {
      "op_type": "InsertOp",
      "address": "beta-a.mid",
      "after_hash": "09ab1234",
      "dimensions":  ["rhythmic", "dynamic"]
    }
  ],
  "summary": {
    "inserted": 1,
    "replaced": 1,
    "deleted":  0
  }
}"""

_OT_MERGE_EXAMPLE = """\
# Scenario A — independent InsertOps at different addresses → commute → clean merge
  left:  InsertOp("ot-notes-a.mid")   # tick=0,   C4 E4 G4
  right: InsertOp("ot-notes-b.mid")   # tick=480, D4 F4 A4

  transform(left, right) → no overlap → both applied
  result: both files present, zero conflicts  ✓

# Scenario B — same address, different content → genuine conflict
  base:  shared-melody.mid  # C4 G4
  left:  ReplaceOp("shared-melody.mid")  # C4 E4 G4  (major triad)
  right: ReplaceOp("shared-melody.mid")  # C4 Eb4 G4 (minor triad)

  transform(left, right) → same address, non-commuting content
  result: ❌ Merge conflict in 1 file(s):
    CONFLICT (both modified): shared-melody.mid
  [musical intent differs — human must choose major or minor]"""

_SCAFFOLD_SNIPPET = """\
from __future__ import annotations
from muse.domain import (
    MuseDomainPlugin, LiveState, StateSnapshot,
    StateDelta, DriftReport, MergeResult, DomainSchema,
)

class GenomicsPlugin(MuseDomainPlugin):
    \"\"\"Version control for genomic sequences.\"\"\"

    def snapshot(self, live_state: LiveState) -> StateSnapshot:
        # Serialize current genome state to a content-addressable blob
        raise NotImplementedError

    def diff(self, base: StateSnapshot,
             target: StateSnapshot) -> StateDelta:
        # Compute minimal delta between two snapshots
        raise NotImplementedError

    def merge(self, base: StateSnapshot,
              left: StateSnapshot,
              right: StateSnapshot) -> MergeResult:
        # Three-way merge — surface conflicts per dimension
        raise NotImplementedError

    def drift(self, committed: StateSnapshot,
              live: LiveState) -> DriftReport:
        # Detect uncommitted changes in the working state
        raise NotImplementedError

    def apply(self, delta: StateDelta,
              live_state: LiveState) -> LiveState:
        # Reconstruct historical state from a delta
        raise NotImplementedError

    def schema(self) -> DomainSchema:
        # Declare dimensions — drives diff algorithm selection
        raise NotImplementedError
"""

# ---------------------------------------------------------------------------
# Planned / aspirational domains
# ---------------------------------------------------------------------------

_PLANNED_DOMAINS = [
    {
        "name": "Genomics",
        "icon": "🧬",
        "status": "planned",
        "tagline": "Version sequences, variants, and annotations",
        "dimensions": ["sequence", "variants", "annotations", "metadata"],
        "color": "#3fb950",
    },
    {
        "name": "3D / Spatial",
        "icon": "🌐",
        "status": "planned",
        "tagline": "Merge spatial fields, meshes, and simulation frames",
        "dimensions": ["geometry", "materials", "physics", "temporal"],
        "color": "#58a6ff",
    },
    {
        "name": "Financial",
        "icon": "📈",
        "status": "planned",
        "tagline": "Track model versions, alpha signals, and risk state",
        "dimensions": ["signals", "positions", "risk", "parameters"],
        "color": "#f9a825",
    },
    {
        "name": "Scientific Simulation",
        "icon": "⚛️",
        "status": "planned",
        "tagline": "Snapshot simulation state across timesteps and parameter spaces",
        "dimensions": ["state", "parameters", "observables", "checkpoints"],
        "color": "#ab47bc",
    },
    {
        "name": "Your Domain",
        "icon": "✦",
        "status": "yours",
        "tagline": "Six methods. Any multidimensional state. Full VCS for free.",
        "dimensions": ["your_dim_1", "your_dim_2", "..."],
        "color": "#4f8ef7",
    },
]

# ---------------------------------------------------------------------------
# Distribution model description
# ---------------------------------------------------------------------------

_DISTRIBUTION_LEVELS = [
    {
        "tier": "Local",
        "icon": "💻",
        "title": "Local plugin (right now)",
        "color": "#3fb950",
        "steps": [
            "muse domains --new &lt;name&gt;",
            "Implement 6 methods in muse/plugins/&lt;name&gt;/plugin.py",
            "Register in muse/plugins/registry.py",
            "muse init --domain &lt;name&gt;",
        ],
        "desc": "Works today. Scaffold → implement → register. "
                "Your plugin lives alongside the core.",
    },
    {
        "tier": "Shareable",
        "icon": "📦",
        "title": "pip-installable package (right now)",
        "color": "#58a6ff",
        "steps": [
            "Package your plugin as a Python module",
            "pip install git+https://github.com/you/muse-plugin-genomics",
            "Register the entry-point in pyproject.toml",
            "muse init --domain genomics",
        ],
        "desc": "Share your plugin as a standard Python package. "
                "Anyone with pip can install and use it.",
    },
    {
        "tier": "MuseHub",
        "icon": "🌐",
        "title": "Centralized registry (coming — MuseHub)",
        "color": "#bc8cff",
        "steps": [
            "musehub publish muse-plugin-genomics",
            "musehub search genomics",
            "muse init --domain @musehub/genomics",
            "Browse plugins at musehub.io",
        ],
        "desc": "MuseHub is a planned centralized registry — npm for Muse plugins. "
                "Versioned, searchable, one-command install.",
    },
]


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

def _render_capability_card(cap: dict) -> str:
    color = cap["color"]
    return f"""
      <div class="cap-showcase-card" style="--cap-color:{color}">
        <div class="cap-showcase-header">
          <span class="cap-showcase-badge" style="color:{color};background:{color}15;border-color:{color}40">
            {cap['icon']} {cap['type']}
          </span>
          <span class="cap-showcase-sub">{cap['sub']}</span>
        </div>
        <div class="cap-showcase-body">
          <pre class="cap-showcase-output">{cap['output']}</pre>
        </div>
      </div>"""


def _render_domain_card(d: dict) -> str:
    domain  = d.get("domain", "unknown")
    active  = d.get("active") == "true"
    schema  = d.get("schema", {})
    desc    = schema.get("description", "")
    dims    = schema.get("dimensions", [])
    caps    = d.get("capabilities", [])

    cap_html = " ".join(
        f'<span class="cap-pill cap-{c.lower().replace(" ","-")}">{c}</span>'
        for c in caps
    )
    dim_html = " · ".join(
        f'<span class="dim-tag">{dim["name"]}</span>' for dim in dims
    )

    status_cls  = "active-badge" if active else "reg-badge"
    status_text = "● active" if active else "○ registered"
    dot         = '<span class="active-dot"></span>' if active else ""

    short_desc = desc[:150] + ("…" if len(desc) > 150 else "")

    return f"""
      <div class="domain-card{' active-domain' if active else ''}">
        <div class="domain-card-hdr">
          <span class="{status_cls}">{status_text}</span>
          <span class="domain-name-lg">{domain}</span>
          {dot}
        </div>
        <div class="domain-card-body">
          <p class="domain-desc">{short_desc}</p>
          <div class="cap-row">{cap_html}</div>
          <div class="dim-row"><strong>Dimensions:</strong> {dim_html}</div>
        </div>
      </div>"""


def _render_planned_card(p: dict) -> str:
    dims = " · ".join(f'<span class="dim-tag">{d}</span>' for d in p["dimensions"])
    cls  = "planned-card yours" if p["status"] == "yours" else "planned-card"
    return f"""
      <div class="{cls}" style="--card-accent:{p['color']}">
        <div class="planned-icon">{p['icon']}</div>
        <div class="planned-name">{p['name']}</div>
        <div class="planned-tag">{p['tagline']}</div>
        <div class="planned-dims">{dims}</div>
        {'<a class="cta-btn" href="#build">Build it →</a>' if p["status"] == "yours" else '<span class="coming-soon">coming soon</span>'}
      </div>"""


def _render_dist_card(d: dict) -> str:
    steps = "".join(
        f'<li><code>{s}</code></li>' for s in d["steps"]
    )
    return f"""
      <div class="dist-card" style="--dist-color:{d['color']}">
        <div class="dist-header">
          <span class="dist-icon">{d['icon']}</span>
          <div>
            <div class="dist-tier">{d['tier']}</div>
            <div class="dist-title">{d['title']}</div>
          </div>
        </div>
        <p class="dist-desc">{d['desc']}</p>
        <ol class="dist-steps">{steps}</ol>
      </div>"""


def render(output_path: pathlib.Path) -> None:
    """Generate the domain registry HTML page."""
    print("  Loading live domain data...")
    domains = _load_domains()
    print(f"  Found {len(domains)} registered domain(s)")

    print("  Computing live CRDT demos...")
    crdt_demos = _compute_crdt_demos()

    active_domains_html = "\n".join(_render_domain_card(d) for d in domains)
    planned_html        = "\n".join(_render_planned_card(p) for p in _PLANNED_DOMAINS)
    dist_html           = "\n".join(_render_dist_card(d) for d in _DISTRIBUTION_LEVELS)
    crdt_cards_html     = "\n".join(_render_capability_card(c) for c in crdt_demos)

    html = _HTML_TEMPLATE.replace("{{ACTIVE_DOMAINS}}", active_domains_html)
    html = html.replace("{{PLANNED_DOMAINS}}", planned_html)
    html = html.replace("{{DIST_CARDS}}", dist_html)
    html = html.replace("{{SCAFFOLD_SNIPPET}}", _SCAFFOLD_SNIPPET)
    html = html.replace("{{TYPED_DELTA_EXAMPLE}}", _TYPED_DELTA_EXAMPLE)
    html = html.replace("{{OT_MERGE_EXAMPLE}}", _OT_MERGE_EXAMPLE)
    html = html.replace("{{CRDT_CARDS}}", crdt_cards_html)

    output_path.write_text(html, encoding="utf-8")
    size_kb = output_path.stat().st_size // 1024
    print(f"  HTML written ({size_kb}KB) → {output_path}")

    # Also write as index.html so the domain registry IS the landing page.
    index_path = output_path.parent / "index.html"
    index_path.write_text(html, encoding="utf-8")
    print(f"  Landing page mirrored → {index_path}")


# ---------------------------------------------------------------------------
# Large HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Muse — Version Anything</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg:        #0d1117;
      --bg2:       #161b22;
      --bg3:       #21262d;
      --border:    #30363d;
      --text:      #e6edf3;
      --mute:      #8b949e;
      --dim:       #484f58;
      --accent:    #4f8ef7;
      --accent2:   #58a6ff;
      --green:     #3fb950;
      --red:       #f85149;
      --yellow:    #d29922;
      --purple:    #bc8cff;
      --mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
      --ui:   -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
      --r:    8px;
    }
    html { scroll-behavior: smooth; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--ui);
      font-size: 15px;
      line-height: 1.7;
    }
    a { color: var(--accent2); text-decoration: none; }
    a:hover { text-decoration: underline; }
    code {
      font-family: var(--mono);
      font-size: 0.88em;
      background: var(--bg3);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 1px 6px;
    }

    /* ---- Hero ---- */
    .hero {
      background: linear-gradient(160deg, #0d1117 0%, #161b22 50%, #0d1117 100%);
      border-bottom: 1px solid var(--border);
      padding: 80px 40px 100px;
      text-align: center;
      position: relative;
      overflow: hidden;
    }
    .hero::before {
      content: '';
      position: absolute;
      inset: 0;
      background:
        radial-gradient(ellipse 60% 40% at 20% 50%, rgba(79,142,247,0.07) 0%, transparent 70%),
        radial-gradient(ellipse 50% 40% at 80% 50%, rgba(188,140,255,0.06) 0%, transparent 70%);
      pointer-events: none;
    }
    .hero-wordmark {
      font-family: var(--ui);
      font-size: clamp(72px, 11vw, 130px);
      font-weight: 800;
      letter-spacing: -5px;
      line-height: 1;
      margin-bottom: 12px;
      background: linear-gradient(90deg, #6ea8fe 0%, #a78bfa 50%, #c084fc 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .hero-version-any {
      font-size: clamp(18px, 2.8vw, 26px);
      font-weight: 700;
      color: #ffffff;
      letter-spacing: 6px;
      text-transform: uppercase;
      margin-bottom: 32px;
    }
    .hero-sub {
      font-size: 18px;
      color: var(--mute);
      max-width: 600px;
      margin: 0 auto 40px;
      line-height: 1.6;
    }
    .hero-sub strong { color: var(--text); }
    .hero-cta-row {
      display: flex;
      gap: 12px;
      justify-content: center;
      flex-wrap: wrap;
    }
    .btn-primary {
      background: var(--accent);
      color: #fff;
      font-weight: 600;
      padding: 12px 28px;
      border-radius: var(--r);
      font-size: 15px;
      border: none;
      cursor: pointer;
      text-decoration: none;
      transition: opacity 0.15s, transform 0.1s;
      display: inline-block;
    }
    .btn-primary:hover { opacity: 0.88; transform: translateY(-1px); text-decoration: none; }
    .btn-outline {
      background: transparent;
      color: var(--text);
      font-weight: 500;
      padding: 12px 28px;
      border-radius: var(--r);
      font-size: 15px;
      border: 1px solid var(--border);
      cursor: pointer;
      text-decoration: none;
      display: inline-block;
      transition: border-color 0.15s, color 0.15s;
    }
    .btn-outline:hover { border-color: var(--accent); color: var(--accent); text-decoration: none; }

    /* ---- Domain ticker ---- */
    .domain-ticker {
      margin: 32px auto 0;
      max-width: 700px;
      overflow: hidden;
      position: relative;
      height: 34px;
    }
    .domain-ticker::before,
    .domain-ticker::after {
      content: '';
      position: absolute;
      top: 0; bottom: 0;
      width: 60px;
      z-index: 2;
    }
    .domain-ticker::before { left: 0; background: linear-gradient(90deg, var(--bg), transparent); }
    .domain-ticker::after  { right: 0; background: linear-gradient(-90deg, var(--bg), transparent); }
    .ticker-track {
      display: flex;
      gap: 10px;
      animation: ticker-scroll 18s linear infinite;
      width: max-content;
    }
    @keyframes ticker-scroll {
      0%   { transform: translateX(0); }
      100% { transform: translateX(-50%); }
    }
    .ticker-item {
      font-family: var(--mono);
      font-size: 13px;
      padding: 4px 14px;
      border-radius: 20px;
      border: 1px solid var(--border);
      white-space: nowrap;
      color: var(--mute);
    }
    .ticker-item.active { border-color: rgba(79,142,247,0.5); color: var(--accent2); background: rgba(79,142,247,0.08); }

    /* ---- Sections ---- */
    section { padding: 72px 40px; border-top: 1px solid var(--border); }
    .inner { max-width: 1100px; margin: 0 auto; }
    .section-eyebrow {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--accent2);
      letter-spacing: 2px;
      text-transform: uppercase;
      margin-bottom: 10px;
    }
    section h2 {
      font-size: 32px;
      font-weight: 700;
      letter-spacing: -0.5px;
      margin-bottom: 12px;
    }
    .section-lead {
      font-size: 16px;
      color: var(--mute);
      max-width: 620px;
      margin-bottom: 48px;
      line-height: 1.7;
    }
    .section-lead strong { color: var(--text); }

    /* ---- Stat strip ---- */
    .stat-strip {
      display: flex;
      gap: 0;
      border: 1px solid var(--border);
      border-radius: var(--r);
      overflow: hidden;
      margin-bottom: 48px;
    }
    .stat-cell {
      flex: 1;
      padding: 20px 24px;
      border-right: 1px solid var(--border);
      text-align: center;
    }
    .stat-cell:last-child { border-right: none; }
    .stat-num {
      font-family: var(--mono);
      font-size: 28px;
      font-weight: 700;
      color: var(--accent2);
      display: block;
    }
    .stat-lbl { font-size: 12px; color: var(--mute); }

    /* ---- Protocol table ---- */
    .proto-table {
      border: 1px solid var(--border);
      border-radius: var(--r);
      overflow: hidden;
      margin-bottom: 40px;
    }
    .proto-row {
      display: grid;
      grid-template-columns: 90px 240px 1fr;
      border-bottom: 1px solid var(--border);
    }
    .proto-row:last-child { border-bottom: none; }
    .proto-row.hdr { background: var(--bg3); }
    .proto-row > div { padding: 11px 16px; }
    .proto-method { font-family: var(--mono); font-size: 13px; color: var(--accent2); font-weight: 600; }
    .proto-sig    { font-family: var(--mono); font-size: 12px; color: var(--mute); }
    .proto-desc   { font-size: 13px; color: var(--mute); }
    .proto-row.hdr .proto-method,
    .proto-row.hdr .proto-sig,
    .proto-row.hdr .proto-desc { font-family: var(--ui); font-size: 11px; font-weight: 600; color: var(--dim); text-transform: uppercase; letter-spacing: 0.6px; }

    /* ---- Engine capability showcase ---- */
    .cap-showcase-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(480px, 1fr));
      gap: 24px;
    }
    @media (max-width: 600px) { .cap-showcase-grid { grid-template-columns: 1fr; } }
    .cap-showcase-card {
      border: 1px solid var(--border);
      border-top: 3px solid var(--cap-color, var(--accent));
      border-radius: var(--r);
      background: var(--bg);
      overflow: hidden;
      transition: transform 0.15s;
    }
    .cap-showcase-card:hover { transform: translateY(-2px); }
    .cap-showcase-header {
      padding: 14px 18px;
      border-bottom: 1px solid var(--border);
      background: var(--bg2);
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .cap-showcase-badge {
      font-size: 12px;
      font-family: var(--mono);
      padding: 3px 10px;
      border-radius: 4px;
      border: 1px solid;
      white-space: nowrap;
    }
    .cap-showcase-sub {
      font-size: 12px;
      color: var(--mute);
      font-style: italic;
    }
    .cap-showcase-body { padding: 16px 18px; }
    .cap-showcase-desc {
      font-size: 13px;
      color: var(--mute);
      margin-bottom: 14px;
      line-height: 1.6;
    }
    .cap-showcase-desc strong { color: var(--text); }
    .cap-showcase-output {
      background: #0a0e14;
      border: 1px solid var(--border);
      border-radius: 5px;
      padding: 12px 14px;
      font-family: var(--mono);
      font-size: 11.5px;
      color: #abb2bf;
      white-space: pre;
      overflow-x: auto;
      line-height: 1.65;
    }
    .cap-showcase-domain-grid {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .crdt-mini-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    @media (max-width: 700px) { .crdt-mini-grid { grid-template-columns: 1fr; } }

    /* ---- Three steps ---- */
    .steps-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 24px;
    }
    @media (max-width: 800px) { .steps-grid { grid-template-columns: 1fr; } }
    .step-card {
      border: 1px solid var(--border);
      border-radius: var(--r);
      background: var(--bg2);
      padding: 24px;
      position: relative;
    }
    .step-num {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--accent);
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-bottom: 10px;
    }
    .step-title { font-size: 17px; font-weight: 700; margin-bottom: 8px; }
    .step-desc  { font-size: 13px; color: var(--mute); line-height: 1.6; margin-bottom: 16px; }
    .step-cmd {
      font-family: var(--mono);
      font-size: 12px;
      background: var(--bg3);
      border: 1px solid var(--border);
      border-radius: 5px;
      padding: 10px 14px;
      color: var(--accent2);
    }

    /* ---- Code block ---- */
    .code-wrap {
      border: 1px solid var(--border);
      border-radius: var(--r);
      overflow: hidden;
    }
    .code-bar {
      background: var(--bg3);
      border-bottom: 1px solid var(--border);
      padding: 8px 16px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .code-bar-dot {
      width: 10px; height: 10px; border-radius: 50%;
    }
    .code-bar-title {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--mute);
      margin-left: 6px;
    }
    .code-body {
      background: #0a0e14;
      padding: 20px 24px;
      font-family: var(--mono);
      font-size: 12.5px;
      line-height: 1.7;
      color: #abb2bf;
      white-space: pre;
      overflow-x: auto;
    }
    /* Simple syntax highlights */
    .kw  { color: #c678dd; }
    .kw2 { color: #e06c75; }
    .fn  { color: #61afef; }
    .str { color: #98c379; }
    .cmt { color: #5c6370; font-style: italic; }
    .cls { color: #e5c07b; }
    .typ { color: #56b6c2; }

    /* ---- Active domains grid ---- */
    .domain-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 20px;
    }
    .domain-card {
      border: 1px solid var(--border);
      border-radius: var(--r);
      background: var(--bg2);
      overflow: hidden;
      transition: border-color 0.2s, transform 0.15s;
    }
    .domain-card:hover { border-color: var(--accent); transform: translateY(-2px); }
    .domain-card.active-domain { border-color: rgba(63,185,80,0.4); }
    .domain-card-hdr {
      background: var(--bg3);
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .active-badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; background: rgba(63,185,80,0.12); border: 1px solid rgba(63,185,80,0.3); color: var(--green); font-family: var(--mono); }
    .reg-badge    { font-size: 11px; padding: 2px 8px; border-radius: 4px; background: var(--bg); border: 1px solid var(--border); color: var(--mute); font-family: var(--mono); }
    .active-dot   { width: 8px; height: 8px; border-radius: 50%; background: var(--green); margin-left: auto; }
    .domain-name-lg { font-family: var(--mono); font-size: 16px; font-weight: 700; color: var(--text); }
    .domain-card-body { padding: 16px; }
    .domain-desc  { font-size: 13px; color: var(--mute); margin-bottom: 12px; }
    .cap-row      { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
    .cap-pill     { font-size: 10px; padding: 2px 8px; border-radius: 12px; border: 1px solid var(--border); color: var(--mute); background: var(--bg3); }
    .cap-pill.cap-crdt          { border-color: rgba(188,140,255,0.4); color: var(--purple); background: rgba(188,140,255,0.08); }
    .cap-pill.cap-ot-merge      { border-color: rgba(88,166,255,0.4);  color: var(--accent2); background: rgba(88,166,255,0.08); }
    .cap-pill.cap-domain-schema { border-color: rgba(63,185,80,0.4);   color: var(--green);   background: rgba(63,185,80,0.08); }
    .cap-pill.cap-typed-deltas  { border-color: rgba(249,168,37,0.4);  color: #f9a825;        background: rgba(249,168,37,0.08); }
    .dim-row  { font-size: 11px; color: var(--dim); }
    .dim-tag  { color: var(--mute); }

    /* ---- Planned domains ---- */
    .planned-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 16px;
    }
    .planned-card {
      border: 1px solid var(--border);
      border-radius: var(--r);
      background: var(--bg2);
      padding: 20px 16px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      transition: border-color 0.2s, transform 0.15s;
    }
    .planned-card:hover { border-color: var(--card-accent,var(--accent)); transform: translateY(-2px); }
    .planned-card.yours { border: 2px dashed var(--accent); background: rgba(79,142,247,0.04); }
    .planned-icon  { font-size: 28px; }
    .planned-name  { font-size: 15px; font-weight: 700; color: var(--text); }
    .planned-tag   { font-size: 12px; color: var(--mute); line-height: 1.5; }
    .planned-dims  { font-size: 10px; color: var(--dim); }
    .coming-soon   { font-size: 10px; color: var(--dim); border: 1px solid var(--border); border-radius: 12px; padding: 2px 8px; display: inline-block; margin-top: 4px; }
    .cta-btn {
      display: inline-block;
      margin-top: 6px;
      font-size: 12px;
      font-weight: 600;
      color: var(--accent2);
      border: 1px solid rgba(88,166,255,0.4);
      border-radius: 4px;
      padding: 4px 12px;
      text-decoration: none;
      transition: background 0.15s;
    }
    .cta-btn:hover { background: rgba(88,166,255,0.1); text-decoration: none; }

    /* ---- Distribution tiers ---- */
    .dist-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 24px;
    }
    .dist-card {
      border: 1px solid var(--border);
      border-top: 3px solid var(--dist-color, var(--accent));
      border-radius: var(--r);
      background: var(--bg2);
      padding: 24px;
      transition: transform 0.15s;
    }
    .dist-card:hover { transform: translateY(-2px); }
    .dist-header { display: flex; align-items: flex-start; gap: 14px; margin-bottom: 14px; }
    .dist-icon   { font-size: 26px; line-height: 1; }
    .dist-tier   { font-family: var(--mono); font-size: 11px; color: var(--dist-color,var(--accent)); letter-spacing: 1px; text-transform: uppercase; font-weight: 700; }
    .dist-title  { font-size: 14px; font-weight: 600; color: var(--text); margin-top: 2px; }
    .dist-desc   { font-size: 13px; color: var(--mute); margin-bottom: 16px; line-height: 1.6; }
    .dist-steps  { list-style: none; counter-reset: step; display: flex; flex-direction: column; gap: 6px; }
    .dist-steps li { counter-increment: step; display: flex; align-items: flex-start; gap: 8px; font-size: 12px; color: var(--mute); }
    .dist-steps li::before { content: counter(step); min-width: 18px; height: 18px; background: var(--dist-color,var(--accent)); color: #000; border-radius: 50%; font-size: 10px; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0; margin-top: 1px; }
    .dist-steps code { background: var(--bg3); border: 1px solid var(--border); border-radius: 4px; padding: 1px 6px; font-size: 11px; }

    /* ---- MuseHub teaser ---- */
    .musehub-section {
      background: linear-gradient(135deg, #0d1117 0%, #1a0d2e 50%, #0d1117 100%);
      padding: 80px 40px;
      text-align: center;
      border-top: 1px solid var(--border);
    }
    .musehub-logo {
      font-size: 48px;
      margin-bottom: 20px;
    }
    .musehub-section h2 {
      font-size: 36px;
      font-weight: 800;
      letter-spacing: -1px;
      margin-bottom: 12px;
    }
    .musehub-section h2 span {
      background: linear-gradient(135deg, #bc8cff, #4f8ef7);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .musehub-desc {
      font-size: 16px;
      color: var(--mute);
      max-width: 560px;
      margin: 0 auto 36px;
    }
    .musehub-desc strong { color: var(--text); }
    .musehub-features {
      display: flex;
      gap: 24px;
      justify-content: center;
      flex-wrap: wrap;
      margin-bottom: 40px;
    }
    .mh-feature {
      background: var(--bg2);
      border: 1px solid rgba(188,140,255,0.2);
      border-radius: var(--r);
      padding: 16px 20px;
      text-align: left;
      min-width: 180px;
    }
    .mh-feature-icon { font-size: 20px; margin-bottom: 8px; }
    .mh-feature-title { font-size: 13px; font-weight: 600; color: var(--text); margin-bottom: 4px; }
    .mh-feature-desc  { font-size: 12px; color: var(--mute); }
    .musehub-status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: rgba(188,140,255,0.1);
      border: 1px solid rgba(188,140,255,0.3);
      border-radius: 20px;
      padding: 8px 20px;
      font-size: 13px;
      color: var(--purple);
    }
    .mh-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--purple); animation: pulse 2s ease-in-out infinite; }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }

    /* ---- Footer ---- */
    footer {
      background: var(--bg2);
      border-top: 1px solid var(--border);
      padding: 24px 40px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      font-size: 13px;
      color: var(--mute);
    }
    footer a { color: var(--accent2); }

    /* ---- Nav ---- */
    nav {
      background: var(--bg2);
      border-bottom: 1px solid var(--border);
      padding: 0 40px;
      display: flex;
      align-items: center;
      gap: 0;
      height: 52px;
    }
    .nav-logo {
      font-family: var(--mono);
      font-size: 16px;
      font-weight: 700;
      color: var(--accent2);
      margin-right: 32px;
      text-decoration: none;
    }
    .nav-logo:hover { text-decoration: none; }
    .nav-link {
      font-size: 13px;
      color: var(--mute);
      padding: 0 14px;
      height: 100%;
      display: flex;
      align-items: center;
      border-bottom: 2px solid transparent;
      text-decoration: none;
      transition: color 0.15s, border-color 0.15s;
    }
    .nav-link:hover { color: var(--text); text-decoration: none; }
    .nav-link.current { color: var(--text); border-bottom-color: var(--accent); }
    .nav-spacer { flex: 1; }
    .nav-badge {
      font-size: 11px;
      background: rgba(79,142,247,0.12);
      border: 1px solid rgba(79,142,247,0.3);
      color: var(--accent2);
      border-radius: 4px;
      padding: 2px 8px;
      font-family: var(--mono);
    }
  </style>
</head>
<body>

<nav>
  <a class="nav-logo" href="#">muse</a>
  <a class="nav-link" href="tour_de_force.html">Tour de Force</a>
  <a class="nav-link current" href="index.html">Domain Registry</a>
  <a class="nav-link" href="../docs/guide/plugin-authoring-guide.md">Plugin Guide</a>
  <div class="nav-spacer"></div>
  <span class="nav-badge">v0.1.1</span>
</nav>

<!-- =================== HERO =================== -->
<div class="hero">
  <h1 class="hero-wordmark">muse</h1>
  <div class="hero-version-any">Version Anything</div>
  <p class="hero-sub">
    One protocol. Any domain. <strong>Six methods</strong> between you and a
    complete version control system — branching, merging, conflict resolution,
    time-travel, and typed diffs — for free.
  </p>
  <div class="hero-cta-row">
    <a class="btn-primary" href="#build">Build a Domain Plugin</a>
    <a class="btn-outline" href="tour_de_force.html">Watch the Demo →</a>
  </div>
  <div class="domain-ticker">
    <div class="ticker-track">
      <span class="ticker-item active">🎵 music</span>
      <span class="ticker-item">🧬 genomics</span>
      <span class="ticker-item">🌐 3d-spatial</span>
      <span class="ticker-item">📈 financial</span>
      <span class="ticker-item">⚛️ simulation</span>
      <span class="ticker-item">🔬 proteomics</span>
      <span class="ticker-item">🏗️ cad</span>
      <span class="ticker-item">🎮 game-state</span>
      <span class="ticker-item">✦ your-domain</span>
      <!-- duplicate for seamless loop -->
      <span class="ticker-item active">🎵 music</span>
      <span class="ticker-item">🧬 genomics</span>
      <span class="ticker-item">🌐 3d-spatial</span>
      <span class="ticker-item">📈 financial</span>
      <span class="ticker-item">⚛️ simulation</span>
      <span class="ticker-item">🔬 proteomics</span>
      <span class="ticker-item">🏗️ cad</span>
      <span class="ticker-item">🎮 game-state</span>
      <span class="ticker-item">✦ your-domain</span>
    </div>
  </div>
</div>

<!-- =================== PROTOCOL =================== -->
<section id="protocol">
  <div class="inner">
    <div class="section-eyebrow">The Contract</div>
    <h2>The MuseDomainPlugin Protocol</h2>
    <p class="section-lead">
      Every domain — music, genomics, 3D spatial, financial models — implements
      the same <strong>six-method protocol</strong>. The core engine handles
      everything else: content-addressed storage, DAG, branches, log, merge base,
      cherry-pick, revert, stash, tags.
    </p>

    <div class="stat-strip">
      <div class="stat-cell"><span class="stat-num">6</span><span class="stat-lbl">methods to implement</span></div>
      <div class="stat-cell"><span class="stat-num">14</span><span class="stat-lbl">CLI commands, free</span></div>
      <div class="stat-cell"><span class="stat-num">∞</span><span class="stat-lbl">domains possible</span></div>
      <div class="stat-cell"><span class="stat-num">0</span><span class="stat-lbl">core changes needed</span></div>
    </div>

    <div class="proto-table">
      <div class="proto-row hdr">
        <div class="proto-method">Method</div>
        <div class="proto-sig">Signature</div>
        <div class="proto-desc">Purpose</div>
      </div>
      <div class="proto-row">
        <div class="proto-method">snapshot</div>
        <div class="proto-sig">snapshot(live) → StateSnapshot</div>
        <div class="proto-desc">Capture current state as a content-addressable blob</div>
      </div>
      <div class="proto-row">
        <div class="proto-method">diff</div>
        <div class="proto-sig">diff(base, target) → StateDelta</div>
        <div class="proto-desc">Compute minimal change between two snapshots (added · removed · modified)</div>
      </div>
      <div class="proto-row">
        <div class="proto-method">merge</div>
        <div class="proto-sig">merge(base, left, right) → MergeResult</div>
        <div class="proto-desc">Three-way reconcile divergent state lines; surface conflicts per dimension</div>
      </div>
      <div class="proto-row">
        <div class="proto-method">drift</div>
        <div class="proto-sig">drift(committed, live) → DriftReport</div>
        <div class="proto-desc">Detect uncommitted changes between HEAD and working state</div>
      </div>
      <div class="proto-row">
        <div class="proto-method">apply</div>
        <div class="proto-sig">apply(delta, live) → LiveState</div>
        <div class="proto-desc">Apply a delta during checkout to reconstruct historical state</div>
      </div>
      <div class="proto-row">
        <div class="proto-method">schema</div>
        <div class="proto-sig">schema() → DomainSchema</div>
        <div class="proto-desc">Declare data structure — drives diff algorithm selection per dimension</div>
      </div>
    </div>
  </div>
</section>

<!-- =================== ENGINE CAPABILITIES =================== -->
<section id="capabilities" style="background:var(--bg2)">
  <div class="inner">
    <div class="section-eyebrow">Engine Capabilities</div>
    <h2>What Every Plugin Gets for Free</h2>
    <p class="section-lead">
      The core engine provides four advanced capabilities that any domain plugin
      can opt into. Implement the protocol — the engine does the rest.
    </p>

    <div class="cap-showcase-grid">

      <div class="cap-showcase-card" style="--cap-color:#f9a825">
        <div class="cap-showcase-header">
          <span class="cap-showcase-badge" style="color:#f9a825;background:#f9a82515;border-color:#f9a82540">
            🔬 Typed Delta Algebra
          </span>
          <span class="cap-showcase-sub">StructuredDelta — every change is a typed operation</span>
        </div>
        <div class="cap-showcase-body">
          <p class="cap-showcase-desc">
            Unlike Git's blob diffs, Muse deltas are <strong>typed objects</strong>:
            <code>InsertOp</code>, <code>ReplaceOp</code>, <code>DeleteOp</code> — each
            carrying the address, before/after hashes, and affected dimensions.
            Machine-readable with <code>muse show --json</code>.
          </p>
          <pre class="cap-showcase-output">{{TYPED_DELTA_EXAMPLE}}</pre>
        </div>
      </div>

      <div class="cap-showcase-card" style="--cap-color:#58a6ff">
        <div class="cap-showcase-header">
          <span class="cap-showcase-badge" style="color:#58a6ff;background:#58a6ff15;border-color:#58a6ff40">
            🗂️ Domain Schema
          </span>
          <span class="cap-showcase-sub">Per-domain dimensions drive diff algorithm selection</span>
        </div>
        <div class="cap-showcase-body">
          <p class="cap-showcase-desc">
            Each plugin's <code>schema()</code> method declares its dimensions and merge mode.
            The engine uses this to select the right diff algorithm per dimension and to
            surface only the dimensions that actually conflict.
          </p>
          <div class="cap-showcase-domain-grid" id="schema-domain-grid">
            {{ACTIVE_DOMAINS}}
          </div>
        </div>
      </div>

      <div class="cap-showcase-card" style="--cap-color:#ef5350">
        <div class="cap-showcase-header">
          <span class="cap-showcase-badge" style="color:#ef5350;background:#ef535015;border-color:#ef535040">
            ⚙️ OT Merge
          </span>
          <span class="cap-showcase-sub">Operational transformation — independent ops commute automatically</span>
        </div>
        <div class="cap-showcase-body">
          <p class="cap-showcase-desc">
            Plugins implementing <strong>StructuredMergePlugin</strong> get operational
            transformation. Operations at different addresses commute automatically —
            only operations on the same address with incompatible intent surface a conflict.
          </p>
          <pre class="cap-showcase-output">{{OT_MERGE_EXAMPLE}}</pre>
        </div>
      </div>

      <div class="cap-showcase-card" style="--cap-color:#bc8cff">
        <div class="cap-showcase-header">
          <span class="cap-showcase-badge" style="color:#bc8cff;background:#bc8cff15;border-color:#bc8cff40">
            🔮 CRDT Primitives
          </span>
          <span class="cap-showcase-sub">Convergent merge — any two replicas always reach the same state</span>
        </div>
        <div class="cap-showcase-body">
          <p class="cap-showcase-desc">
            Plugins implementing <strong>CRDTPlugin</strong> get four battle-tested
            convergent data structures. No coordination required between replicas.
          </p>
          <div class="crdt-mini-grid">
            {{CRDT_CARDS}}
          </div>
        </div>
      </div>

    </div>
  </div>
</section>

<!-- =================== BUILD =================== -->
<section id="build" style="background:var(--bg)">
  <div class="inner">
    <div class="section-eyebrow">Build</div>
    <h2>Build in Three Steps</h2>
    <p class="section-lead">
      One command scaffolds the entire plugin skeleton. You fill in six methods.
      The full VCS follows.
    </p>

    <div class="steps-grid">
      <div class="step-card">
        <div class="step-num">Step 1 · Scaffold</div>
        <div class="step-title">Generate the skeleton</div>
        <div class="step-desc">
          One command creates the plugin directory, class, and all six method stubs
          with full type annotations.
        </div>
        <div class="step-cmd">muse domains --new genomics</div>
      </div>
      <div class="step-card">
        <div class="step-num">Step 2 · Implement</div>
        <div class="step-title">Fill in the six methods</div>
        <div class="step-desc">
          Replace each <code>raise NotImplementedError</code> with your domain's
          snapshot, diff, merge, drift, apply, and schema logic.
        </div>
        <div class="step-cmd">vim muse/plugins/genomics/plugin.py</div>
      </div>
      <div class="step-card">
        <div class="step-num">Step 3 · Use</div>
        <div class="step-title">Full VCS, instantly</div>
        <div class="step-desc">
          Register in <code>registry.py</code>, then every Muse command works
          for your domain out of the box.
        </div>
        <div class="step-cmd">muse init --domain genomics</div>
      </div>
    </div>
  </div>
</section>

<!-- =================== CODE =================== -->
<section id="code">
  <div class="inner">
    <div class="section-eyebrow">The Scaffold</div>
    <h2>What <code>muse domains --new genomics</code> produces</h2>
    <p class="section-lead">
      A fully typed, immediately runnable plugin skeleton. Every method has the
      correct signature. You replace the stubs — the protocol does the rest.
    </p>
    <div class="code-wrap">
      <div class="code-bar">
        <div class="code-bar-dot" style="background:#ff5f57"></div>
        <div class="code-bar-dot" style="background:#febc2e"></div>
        <div class="code-bar-dot" style="background:#28c840"></div>
        <span class="code-bar-title">muse/plugins/genomics/plugin.py</span>
      </div>
      <div class="code-body">{{SCAFFOLD_SNIPPET}}</div>
    </div>
    <p style="margin-top:16px;font-size:13px;color:var(--mute)">
      Full walkthrough →
      <a href="../docs/guide/plugin-authoring-guide.md">docs/guide/plugin-authoring-guide.md</a>
      · CRDT extension →
      <a href="../docs/guide/crdt-reference.md">docs/guide/crdt-reference.md</a>
    </p>
  </div>
</section>

<!-- =================== ACTIVE DOMAINS =================== -->
<section id="registry" style="background:var(--bg2)">
  <div class="inner">
    <div class="section-eyebrow">Registry</div>
    <h2>Registered Domains</h2>
    <p class="section-lead">
      Domains currently registered in this Muse instance. The active domain
      is the one used when you run <code>muse commit</code>, <code>muse diff</code>,
      and all other commands.
    </p>
    <div class="domain-grid">
      {{ACTIVE_DOMAINS}}
    </div>
  </div>
</section>

<!-- =================== PLANNED ECOSYSTEM =================== -->
<section id="ecosystem">
  <div class="inner">
    <div class="section-eyebrow">Ecosystem</div>
    <h2>The Plugin Ecosystem</h2>
    <p class="section-lead">
      Music is the reference implementation. These are the domains planned
      next — and the slot waiting for yours.
    </p>
    <div class="planned-grid">
      {{PLANNED_DOMAINS}}
    </div>
  </div>
</section>

<!-- =================== DISTRIBUTION =================== -->
<section id="distribute" style="background:var(--bg2)">
  <div class="inner">
    <div class="section-eyebrow">Distribution</div>
    <h2>How to Share Your Plugin</h2>
    <p class="section-lead">
      Three tiers of distribution — from local prototype to globally searchable
      registry. Start local, publish when ready.
    </p>
    <div class="dist-grid">
      {{DIST_CARDS}}
    </div>
  </div>
</section>

<!-- =================== MUSEHUB TEASER =================== -->
<div class="musehub-section">
  <div class="musehub-logo">🌐</div>
  <h2><span>MuseHub</span> is coming</h2>
  <p class="musehub-desc">
    A <strong>centralized, searchable registry</strong> for Muse domain plugins —
    think npm or crates.io, but for any multidimensional versioned state.
    One command to publish. One command to install.
  </p>
  <div class="musehub-features">
    <div class="mh-feature">
      <div class="mh-feature-icon">🔍</div>
      <div class="mh-feature-title">Searchable</div>
      <div class="mh-feature-desc">Find plugins by domain, capability, or keyword</div>
    </div>
    <div class="mh-feature">
      <div class="mh-feature-icon">📦</div>
      <div class="mh-feature-title">Versioned</div>
      <div class="mh-feature-desc">Semantic versioning, pinned installs, changelogs</div>
    </div>
    <div class="mh-feature">
      <div class="mh-feature-icon">🔒</div>
      <div class="mh-feature-title">Private registries</div>
      <div class="mh-feature-desc">Self-host for enterprise or research teams</div>
    </div>
    <div class="mh-feature">
      <div class="mh-feature-icon">⚡</div>
      <div class="mh-feature-title">One command</div>
      <div class="mh-feature-desc"><code>muse init --domain @musehub/genomics</code></div>
    </div>
  </div>
  <div class="musehub-status">
    <div class="mh-dot"></div>
    MuseHub — planned · building in public at github.com/cgcardona/muse
  </div>
</div>

<footer>
  <span>Muse v0.1.1 · domain-agnostic version control for multidimensional state</span>
  <span>
    <a href="tour_de_force.html">Tour de Force</a> ·
    <a href="https://github.com/cgcardona/muse">GitHub</a> ·
    <a href="../docs/guide/plugin-authoring-guide.md">Plugin Guide</a>
  </span>
</footer>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate the Muse domain registry HTML page"
    )
    parser.add_argument(
        "--out",
        default=str(_ROOT / "artifacts" / "domain_registry.html"),
        help="Output HTML path",
    )
    args = parser.parse_args()

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("Generating domain_registry.html...")
    render(out_path)
    print(f"Open: file://{out_path.resolve()}")
