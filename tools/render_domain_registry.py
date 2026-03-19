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
        from muse.core.crdts import AWMap, GCounter, LWWRegister, ORSet, RGA, VectorClock

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

        elem  = "annotation-GO:0001234"
        short = "GO:0001234"

        orset_html = f"""<div class="crdt-vis">
          <div class="crdt-concurrent">
            <div class="crdt-rep">
              <div class="crdt-rep-hdr">Replica A</div>
              <div class="crdt-op crdt-op-add">+ add("{short}")</div>
              <div class="crdt-rep-state">&rarr;&thinsp;{{{short}}}</div>
            </div>
            <div class="crdt-rep">
              <div class="crdt-rep-hdr">Replica B</div>
              <div class="crdt-op crdt-op-rm">&times; remove("{short}")</div>
              <div class="crdt-rep-state">&rarr;&thinsp;{{}}</div>
            </div>
          </div>
          <div class="crdt-join" style="--crdt-c:#bc8cff">
            <span class="crdt-join-label">join(A, B)</span>
            <span class="crdt-join-val" style="color:#bc8cff">{{{short}}}</span>
            <span class="crdt-join-rule">add-wins &mdash; A&rsquo;s new token survives</span>
          </div>
        </div>"""

        lww_html = f"""<div class="crdt-vis">
          <div class="crdt-writes">
            <div class="crdt-write">
              <span class="crdt-time">t=1.0</span>
              <span class="crdt-agent crdt-agent-a">A</span>
              <span class="crdt-wval">"{ra.read()}"</span>
            </div>
            <div class="crdt-write crdt-write-winner">
              <span class="crdt-time">t=2.0</span>
              <span class="crdt-agent crdt-agent-b">B</span>
              <span class="crdt-wval">"{rb.read()}"</span>
              <span class="crdt-latest">latest &uarr;</span>
            </div>
          </div>
          <div class="crdt-join">
            <span class="crdt-join-label">join(A,B) = join(B,A)</span>
            <span class="crdt-join-val" style="color:#58a6ff">"{rm.read()}"</span>
            <span class="crdt-join-rule">commutative &mdash; higher timestamp always wins</span>
          </div>
        </div>"""

        a_val = ca.value_for("agent-A")
        b_val = cb.value_for("agent-B")
        total = cm.value()
        a_pct = int(a_val / total * 100)
        b_pct = int(b_val / total * 100)
        gc_html = f"""<div class="crdt-vis">
          <div class="crdt-gcounter">
            <div class="crdt-gc-row">
              <span class="crdt-agent crdt-agent-a">A &times;{a_val}</span>
              <div class="crdt-bar"><div class="crdt-bar-fill crdt-bar-a" style="width:{a_pct}%">{a_val}</div></div>
            </div>
            <div class="crdt-gc-row">
              <span class="crdt-agent crdt-agent-b">B &times;{b_val}</span>
              <div class="crdt-bar"><div class="crdt-bar-fill crdt-bar-b" style="width:{b_pct}%">{b_val}</div></div>
            </div>
          </div>
          <div class="crdt-join">
            <span class="crdt-join-label">join(A, B) global</span>
            <span class="crdt-join-val" style="color:#3fb950">{total}</span>
            <span class="crdt-join-rule">component-wise max &mdash; monotonically non-decreasing</span>
          </div>
        </div>"""

        concurrent = va.concurrent_with(vb)
        merged_d   = vm.to_dict()
        vc_html = f"""<div class="crdt-vis">
          <div class="crdt-vclocks">
            <div class="crdt-vc">
              <div class="crdt-vc-hdr">Agent A</div>
              <div class="crdt-vc-cells">
                <div class="crdt-vc-cell crdt-vc-self">A:1</div>
                <div class="crdt-vc-cell crdt-vc-zero">B:0</div>
              </div>
            </div>
            <div class="crdt-vc-sep">&oplus;</div>
            <div class="crdt-vc">
              <div class="crdt-vc-hdr">Agent B</div>
              <div class="crdt-vc-cells">
                <div class="crdt-vc-cell crdt-vc-zero">A:0</div>
                <div class="crdt-vc-cell crdt-vc-self">B:1</div>
              </div>
            </div>
            <div class="crdt-vc-sep">=</div>
            <div class="crdt-vc">
              <div class="crdt-vc-hdr">merge</div>
              <div class="crdt-vc-cells">
                {"".join(f'<div class="crdt-vc-cell crdt-vc-max">{k.split("-")[1].upper()}:{v}</div>' for k, v in sorted(merged_d.items()))}
              </div>
            </div>
          </div>
          <div class="crdt-concurrent-badge">concurrent_with(A, B) = {concurrent}</div>
          <div class="crdt-join-rule" style="font-size:10.5px;color:var(--mute);font-style:italic">component-wise max &mdash; causal happens-before tracking</div>
        </div>"""

        # RGA — ordered note sequence: A inserts C4+E4, B inserts G4 concurrently
        rga_a = RGA()
        rga_a = rga_a.insert(None,    "hash-C4", element_id="1@agent-A")
        rga_a = rga_a.insert("1@agent-A", "hash-E4", element_id="2@agent-A")
        rga_b = RGA()
        rga_b = rga_b.insert(None,    "hash-C4", element_id="1@agent-A")  # same start
        rga_b = rga_b.insert("1@agent-A", "hash-G4", element_id="1@agent-B")  # concurrent
        rga_m = rga_a.join(rga_b)
        rga_seq_a = ["C4", "E4"]
        rga_seq_b = ["C4", "G4"]
        _rga_hash_to_note = {"hash-C4": "C4", "hash-E4": "E4", "hash-G4": "G4"}
        rga_seq_m = [_rga_hash_to_note.get(h, h) for h in rga_m.to_sequence()]
        rga_out = "\n".join([
            "RGA — ordered sequence (Google-Docs-style):",
            f"  Agent A sequence: {rga_seq_a}",
            f"  Agent B sequence: {rga_seq_b}",
            f"  join(A, B): {rga_seq_m}  [deterministic, ID-ordered]",
            "  Tombstones kept — deletions never lose causal history",
        ])
        rga_html = """<div class="crdt-vis">
          <div class="crdt-concurrent">
            <div class="crdt-rep">
              <div class="crdt-rep-hdr">Replica A</div>
              <div class="crdt-op crdt-op-add">+ insert("C4", id=1@A)</div>
              <div class="crdt-op crdt-op-add">+ insert("E4", id=2@A)</div>
              <div class="crdt-rep-state">&rarr;&thinsp;[C4, E4]</div>
            </div>
            <div class="crdt-rep">
              <div class="crdt-rep-hdr">Replica B</div>
              <div class="crdt-op crdt-op-add">+ insert("C4", id=1@A)</div>
              <div class="crdt-op crdt-op-add">+ insert("G4", id=1@B) <em>concurrent</em></div>
              <div class="crdt-rep-state">&rarr;&thinsp;[C4, G4]</div>
            </div>
          </div>
          <div class="crdt-join" style="--crdt-c:#f9a825">
            <span class="crdt-join-label">join(A, B)</span>
            <span class="crdt-join-val" style="color:#f9a825">[C4, E4, G4]</span>
            <span class="crdt-join-rule">ID-ordered &mdash; larger ID wins leftmost; all elements preserved</span>
          </div>
        </div>"""

        # AWMap — key-value map: A sets "tempo", B sets "key_sig", both survive
        aw_a = AWMap()
        aw_a = aw_a.set("tempo",   "120 BPM")
        aw_b = AWMap()
        aw_b = aw_b.set("key_sig", "C major")
        aw_m = aw_a.join(aw_b)
        aw_keys = sorted(aw_m.keys())
        awmap_out = "\n".join([
            "AWMap — add-wins key/value map:",
            f"  Agent A sets: {{tempo: '120 BPM'}}",
            f"  Agent B sets: {{key_sig: 'C major'}}",
            f"  join(A, B) keys: {aw_keys}",
            "  Both additions survive — concurrent removes cannot win",
        ])
        awmap_html = """<div class="crdt-vis">
          <div class="crdt-concurrent">
            <div class="crdt-rep">
              <div class="crdt-rep-hdr">Replica A</div>
              <div class="crdt-op crdt-op-add">+ set("tempo", "120 BPM")</div>
              <div class="crdt-rep-state">&rarr;&thinsp;{tempo}</div>
            </div>
            <div class="crdt-rep">
              <div class="crdt-rep-hdr">Replica B</div>
              <div class="crdt-op crdt-op-add">+ set("key_sig", "C major")</div>
              <div class="crdt-rep-state">&rarr;&thinsp;{key_sig}</div>
            </div>
          </div>
          <div class="crdt-join" style="--crdt-c:#3fb950">
            <span class="crdt-join-label">join(A, B)</span>
            <span class="crdt-join-val" style="color:#3fb950">{key_sig, tempo}</span>
            <span class="crdt-join-rule">add-wins &mdash; concurrent removes cannot evict new tokens</span>
          </div>
        </div>"""

        return [
            {"type": "ORSet",       "sub": "Observed-Remove Set",           "color": "#bc8cff", "icon": _ICONS["union"],      "output": orset_out,  "html_output": orset_html},
            {"type": "LWWRegister", "sub": "Last-Write-Wins Register",      "color": "#58a6ff", "icon": _ICONS["edit"],        "output": lww_out,    "html_output": lww_html},
            {"type": "GCounter",    "sub": "Grow-Only Distributed Counter",  "color": "#3fb950", "icon": _ICONS["arrow-up"],   "output": gc_out,     "html_output": gc_html},
            {"type": "VectorClock", "sub": "Causal Ordering",               "color": "#f9a825", "icon": _ICONS["git-branch"],  "output": vc_out,     "html_output": vc_html},
            {"type": "RGA",         "sub": "Replicated Growable Array",     "color": "#f9a825", "icon": _ICONS["layers"],      "output": rga_out,    "html_output": rga_html},
            {"type": "AWMap",       "sub": "Add-Wins Map",                  "color": "#3fb950", "icon": _ICONS["code"],        "output": awmap_out,  "html_output": awmap_html},
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
            "domain": "midi",
            "active": "true",
            "capabilities": ["Typed Deltas", "Domain Schema", "OT Merge", "CRDT Primitives", "MusicRGA"],
            "schema": {
                "schema_version": "1",
                "merge_mode": "three_way",
                "description": "MIDI file versioning with 21-dimension structured merge — notes, CC, pitch bend, tempo, time signatures, and more. Each dimension merges independently. MusicRGA provides voice-aware CRDT ordering (bass→tenor→alto→soprano) for concurrent note edits.",
                "dimensions": [
                    {"name": "notes",          "description": "Note-on/off events (pitch, velocity, channel, timing)"},
                    {"name": "control_change",  "description": "CC curves: modulation, sustain, expression, pan"},
                    {"name": "pitch_bend",      "description": "Pitch bend envelope per channel"},
                    {"name": "tempo",           "description": "Tempo map (BPM changes over time)"},
                    {"name": "time_signature",  "description": "Metre changes across the piece"},
                    {"name": "key_signature",   "description": "Key and mode declarations"},
                    {"name": "program_change",  "description": "Instrument (patch) assignments per channel"},
                    {"name": "aftertouch",      "description": "Channel and polyphonic pressure"},
                    {"name": "sysex",           "description": "System-exclusive device messages"},
                    {"name": "track_name",      "description": "Human-readable track labels"},
                ],
            },
        },
        {
            "domain": "code",
            "active": "true",
            "capabilities": ["Typed Deltas", "Domain Schema", "OT Merge", ".museattributes"],
            "schema": {
                "schema_version": "1",
                "merge_mode": "symbol_ot",
                "description": "Source-code versioning with symbol-level operational-transform merge. Tree-sitter parses 11 languages into ASTs; functions, classes, and imports merge independently. .museattributes gives per-path strategy control.",
                "dimensions": [
                    {"name": "functions",   "description": "Function and method definitions"},
                    {"name": "classes",     "description": "Class and struct declarations"},
                    {"name": "imports",     "description": "Import and module-level declarations"},
                    {"name": "variables",   "description": "Module-level variable and constant assignments"},
                    {"name": "expressions", "description": "Top-level expression statements"},
                ],
            },
        },
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


_SCAFFOLD_SNIPPET = """\
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
# SVG icon library — Lucide/Feather style, stroke="currentColor", no fixed size
# ---------------------------------------------------------------------------

def _icon(paths: str) -> str:
    """Wrap SVG paths in a standard icon shell."""
    return (
        '<svg class="icon" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.75" '
        'stroke-linecap="round" stroke-linejoin="round">'
        + paths
        + "</svg>"
    )


_ICONS: dict[str, str] = {
    # Domains
    "music":     _icon('<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>'),
    "genomics":  _icon('<path d="M2 15c6.667-6 13.333 0 20-6"/><path d="M2 9c6.667 6 13.333 0 20 6"/><line x1="5.5" y1="11" x2="5.5" y2="13"/><line x1="18.5" y1="11" x2="18.5" y2="13"/>'),
    "cube":      _icon('<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>'),
    "trending":  _icon('<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>'),
    "atom":      _icon('<circle cx="12" cy="12" r="1"/><path d="M20.2 20.2c2.04-2.03.02-7.36-4.5-11.9-4.54-4.52-9.87-6.54-11.9-4.5-2.04 2.03-.02 7.36 4.5 11.9 4.54 4.52 9.87 6.54 11.9 4.5z"/><path d="M15.7 15.7c4.52-4.54 6.54-9.87 4.5-11.9-2.03-2.04-7.36-.02-11.9 4.5-4.52 4.54-6.54 9.87-4.5 11.9 2.03 2.04 7.36.02 11.9-4.5z"/>'),
    "plus":      _icon('<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/>'),
    "activity":  _icon('<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>'),
    "pen-tool":  _icon('<path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/><circle cx="11" cy="11" r="2"/>'),
    # Distribution
    "terminal":  _icon('<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>'),
    "package":   _icon('<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>'),
    "globe":     _icon('<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'),
    # Engine capabilities
    "code":      _icon('<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>'),
    "layers":    _icon('<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>'),
    "git-merge": _icon('<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/>'),
    "zap":       _icon('<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>'),
    # MuseHub features
    "search":    _icon('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'),
    "lock":      _icon('<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>'),
    # CRDT primitives
    "union":       _icon('<path d="M5 5v8a7 7 0 0 0 14 0V5"/><line x1="3" y1="19" x2="21" y2="19"/>'),
    "edit":        _icon('<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>'),
    "arrow-up":    _icon('<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>'),
    "git-branch":  _icon('<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>'),
    # OT scenario outcome badges
    "check-circle":_icon('<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>'),
    "x-circle":    _icon('<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>'),
}


# ---------------------------------------------------------------------------
# Planned / aspirational domains
# ---------------------------------------------------------------------------

_PLANNED_DOMAINS = [
    {
        "name": "Genomics",
        "icon": _ICONS["genomics"],
        "status": "planned",
        "tagline": "Version sequences, variants, and annotations",
        "dimensions": ["sequence", "variants", "annotations", "metadata"],
        "color": "#3fb950",
    },
    {
        "name": "3D / Spatial",
        "icon": _ICONS["cube"],
        "status": "planned",
        "tagline": "Merge spatial fields, meshes, and simulation frames",
        "dimensions": ["geometry", "materials", "physics", "temporal"],
        "color": "#58a6ff",
    },
    {
        "name": "Financial",
        "icon": _ICONS["trending"],
        "status": "planned",
        "tagline": "Track model versions, alpha signals, and risk state",
        "dimensions": ["signals", "positions", "risk", "parameters"],
        "color": "#f9a825",
    },
    {
        "name": "Scientific Simulation",
        "icon": _ICONS["atom"],
        "status": "planned",
        "tagline": "Snapshot simulation state across timesteps and parameter spaces",
        "dimensions": ["state", "parameters", "observables", "checkpoints"],
        "color": "#ab47bc",
    },
    {
        "name": "Your Domain",
        "icon": _ICONS["plus"],
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
        "icon": _ICONS["terminal"],
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
        "icon": _ICONS["package"],
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
        "icon": _ICONS["globe"],
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
    body = (
        cap["html_output"]
        if "html_output" in cap
        else f'<pre class="cap-showcase-output">{cap["output"]}</pre>'
    )
    return f"""
      <div class="cap-showcase-card" style="--cap-color:{color}">
        <div class="cap-showcase-header">
          <span class="cap-showcase-badge" style="color:{color};background:{color}15;border-color:{color}40">
            {cap['icon']} {cap['type']}
          </span>
          <span class="cap-showcase-sub">{cap['sub']}</span>
        </div>
        <div class="cap-showcase-body">
          {body}
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
    html = html.replace("{{CRDT_CARDS}}", crdt_cards_html)
    html = html.replace("{{DIFF_ALGEBRA}}", _DIFF_ALGEBRA_HTML)

    # Inject SVG icons into template placeholders
    _ICON_SLOTS: dict[str, str] = {
        "MUSIC":     _ICONS["music"],
        "GENOMICS":  _ICONS["genomics"],
        "CUBE":      _ICONS["cube"],
        "TRENDING":  _ICONS["trending"],
        "ATOM":      _ICONS["atom"],
        "PLUS":      _ICONS["plus"],
        "ACTIVITY":  _ICONS["activity"],
        "PEN_TOOL":  _ICONS["pen-tool"],
        "CODE":      _ICONS["code"],
        "LAYERS":    _ICONS["layers"],
        "GIT_MERGE": _ICONS["git-merge"],
        "ZAP":       _ICONS["zap"],
        "GLOBE":     _ICONS["globe"],
        "SEARCH":    _ICONS["search"],
        "PACKAGE":   _ICONS["package"],
        "LOCK":         _ICONS["lock"],
        "CHECK_CIRCLE": _ICONS["check-circle"],
        "X_CIRCLE":     _ICONS["x-circle"],
    }
    for slot, svg in _ICON_SLOTS.items():
        html = html.replace(f"{{{{ICON_{slot}}}}}", svg)

    output_path.write_text(html, encoding="utf-8")
    size_kb = output_path.stat().st_size // 1024
    print(f"  HTML written ({size_kb}KB) → {output_path}")

    # Also write as index.html so the domain registry IS the landing page.
    index_path = output_path.parent / "index.html"
    index_path.write_text(html, encoding="utf-8")
    print(f"  Landing page mirrored → {index_path}")


# ---------------------------------------------------------------------------
# Diff Algebra section — five algorithm visualizations + StructuredDelta flow
# ---------------------------------------------------------------------------

_DIFF_ALGEBRA_HTML = """
<section id="diff-algebra" style="background:var(--bg)">
  <div class="inner">
    <div class="section-eyebrow">Diff Algebra</div>
    <h2>Five Algebras. One Typed Result.</h2>
    <p class="section-lead">
      The engine selects the algorithm per dimension from your plugin&rsquo;s
      <code>schema()</code>. You declare the shape &mdash; the engine handles identity,
      diffing, and merge selection automatically.
    </p>

    <div class="da-grid">

      <!-- 1. SEQUENCE — spans full width -->
      <div class="da-card da-seq-card">
        <div class="da-card-hdr">
          <span class="da-chip da-chip-seq">Sequence</span>
          <span class="da-algo-name">Myers / LCS on SHA-256 IDs</span>
        </div>
        <div class="da-domains-row">notes &middot; nucleotides &middot; animation frames &middot; git objects</div>
        <div class="da-visual" style="flex-direction:column;align-items:flex-start;gap:8px">
          <div class="seq-vis">
            <div class="seq-row-lbl">before</div>
            <div class="seq-row">
              <div class="seq-blk seq-match"><div class="seq-hash">a1b2</div><div class="seq-name">C4</div></div>
              <div class="seq-blk seq-del"><div class="seq-hash">c3d4</div><div class="seq-name">E4</div></div>
              <div class="seq-blk seq-match"><div class="seq-hash">e5f6</div><div class="seq-name">G4</div></div>
              <div class="seq-blk seq-moved-from"><div class="seq-hash">g7h8</div><div class="seq-name">B&flat;4</div></div>
            </div>
            <div class="seq-ops-row">
              <div class="seq-op-cell"><span class="da-op da-op-match">= match</span></div>
              <div class="seq-op-cell"><span class="da-op da-op-delete">&times; DeleteOp</span></div>
              <div class="seq-op-cell"><span class="da-op da-op-match">= match</span></div>
              <div class="seq-op-cell"><span class="da-op da-op-move">&darr; MoveOp</span></div>
            </div>
            <div class="seq-row">
              <div class="seq-blk seq-match"><div class="seq-hash">a1b2</div><div class="seq-name">C4</div></div>
              <div class="seq-blk seq-ins"><div class="seq-hash">k1l2</div><div class="seq-name">F4</div></div>
              <div class="seq-blk seq-match"><div class="seq-hash">e5f6</div><div class="seq-name">G4</div></div>
              <div class="seq-blk seq-ins"><div class="seq-hash">n5o6</div><div class="seq-name">A4</div></div>
              <div class="seq-blk seq-moved-to"><div class="seq-hash">g7h8</div><div class="seq-name">B&flat;4</div></div>
            </div>
            <div class="seq-ops-row">
              <div class="seq-op-cell"></div>
              <div class="seq-op-cell"><span class="da-op da-op-insert">+ InsertOp</span></div>
              <div class="seq-op-cell"></div>
              <div class="seq-op-cell"><span class="da-op da-op-insert">+ InsertOp</span></div>
              <div class="seq-op-cell"><span class="da-op da-op-move">&uarr; arrived</span></div>
            </div>
            <div class="seq-row-lbl">after</div>
          </div>
        </div>
        <div class="da-note">Identity is hash-based: two elements are equal iff their SHA-256 hashes match &mdash; content is never inspected by the core. Delete&thinsp;+&thinsp;insert pairs sharing the same hash are collapsed into MoveOps in a post-pass.</div>
      </div>

      <!-- 2. TREE -->
      <div class="da-card">
        <div class="da-card-hdr">
          <span class="da-chip da-chip-tree">Tree</span>
          <span class="da-algo-name">Zhang-Shasha / GumTree</span>
        </div>
        <div class="da-domains-row">scene graphs &middot; ASTs &middot; track hierarchies</div>
        <div class="da-visual" style="padding:10px 8px">
          <svg class="tree-vis" viewBox="0 0 290 128" xmlns="http://www.w3.org/2000/svg">
            <!-- BEFORE -->
            <text x="52" y="9" text-anchor="middle" font-size="7" fill="#484f58" font-family="monospace" font-weight="700">BEFORE</text>
            <line x1="52" y1="27" x2="24" y2="57" stroke="#30363d" stroke-width="1.5"/>
            <line x1="52" y1="27" x2="80" y2="57" stroke="#30363d" stroke-width="1.5"/>
            <line x1="24" y1="73" x2="11" y2="100" stroke="#30363d" stroke-width="1.5"/>
            <line x1="24" y1="73" x2="37" y2="100" stroke="#30363d" stroke-width="1.5"/>
            <line x1="80" y1="73" x2="80" y2="100" stroke="#bc8cff" stroke-width="1.5" stroke-dasharray="3,2"/>
            <rect x="31" y="14" width="42" height="16" rx="4" fill="#161b22" stroke="#58a6ff" stroke-width="1.5"/>
            <text x="52" y="25" text-anchor="middle" font-size="8" fill="#58a6ff" font-family="monospace">session</text>
            <rect x="7" y="59" width="34" height="16" rx="3" fill="#161b22" stroke="#484f58"/>
            <text x="24" y="70" text-anchor="middle" font-size="8" fill="#8b949e" font-family="monospace">intro</text>
            <rect x="63" y="59" width="34" height="16" rx="3" fill="#161b22" stroke="#484f58"/>
            <text x="80" y="70" text-anchor="middle" font-size="8" fill="#8b949e" font-family="monospace">verse</text>
            <rect x="4" y="98" width="15" height="13" rx="3" fill="#161b22" stroke="#484f58"/>
            <text x="11" y="108" text-anchor="middle" font-size="7.5" fill="#8b949e" font-family="monospace">C4</text>
            <rect x="28" y="98" width="15" height="13" rx="3" fill="#161b22" stroke="#484f58"/>
            <text x="35" y="108" text-anchor="middle" font-size="7.5" fill="#8b949e" font-family="monospace">E4</text>
            <rect x="72" y="98" width="15" height="13" rx="3" fill="rgba(188,140,255,0.1)" stroke="#bc8cff" stroke-width="1.5"/>
            <text x="79" y="108" text-anchor="middle" font-size="7.5" fill="#bc8cff" font-family="monospace">G4</text>
            <!-- divider -->
            <line x1="143" y1="8" x2="143" y2="118" stroke="#30363d" stroke-width="1" stroke-dasharray="3,3"/>
            <text x="143" y="126" text-anchor="middle" font-size="6.5" fill="#484f58" font-family="monospace">MoveOp + InsertOp</text>
            <!-- AFTER -->
            <text x="216" y="9" text-anchor="middle" font-size="7" fill="#484f58" font-family="monospace" font-weight="700">AFTER</text>
            <line x1="216" y1="27" x2="183" y2="57" stroke="#30363d" stroke-width="1.5"/>
            <line x1="216" y1="27" x2="249" y2="57" stroke="#30363d" stroke-width="1.5"/>
            <line x1="183" y1="73" x2="162" y2="100" stroke="#30363d" stroke-width="1.5"/>
            <line x1="183" y1="73" x2="178" y2="100" stroke="#30363d" stroke-width="1.5"/>
            <line x1="183" y1="73" x2="195" y2="100" stroke="#bc8cff" stroke-width="1.5" stroke-dasharray="3,2"/>
            <line x1="249" y1="73" x2="249" y2="100" stroke="#3fb950" stroke-width="1.5"/>
            <rect x="195" y="14" width="42" height="16" rx="4" fill="#161b22" stroke="#58a6ff" stroke-width="1.5"/>
            <text x="216" y="25" text-anchor="middle" font-size="8" fill="#58a6ff" font-family="monospace">session</text>
            <rect x="166" y="59" width="34" height="16" rx="3" fill="#161b22" stroke="#484f58"/>
            <text x="183" y="70" text-anchor="middle" font-size="8" fill="#8b949e" font-family="monospace">intro</text>
            <rect x="232" y="59" width="34" height="16" rx="3" fill="#161b22" stroke="#484f58"/>
            <text x="249" y="70" text-anchor="middle" font-size="8" fill="#8b949e" font-family="monospace">verse</text>
            <rect x="155" y="98" width="15" height="13" rx="3" fill="#161b22" stroke="#484f58"/>
            <text x="162" y="108" text-anchor="middle" font-size="7.5" fill="#8b949e" font-family="monospace">C4</text>
            <rect x="171" y="98" width="15" height="13" rx="3" fill="#161b22" stroke="#484f58"/>
            <text x="178" y="108" text-anchor="middle" font-size="7.5" fill="#8b949e" font-family="monospace">E4</text>
            <rect x="188" y="98" width="15" height="13" rx="3" fill="rgba(188,140,255,0.12)" stroke="#bc8cff" stroke-width="1.5"/>
            <text x="195" y="108" text-anchor="middle" font-size="7.5" fill="#bc8cff" font-family="monospace">G4</text>
            <rect x="241" y="98" width="15" height="13" rx="3" fill="rgba(63,185,80,0.12)" stroke="#3fb950" stroke-width="1.5"/>
            <text x="249" y="108" text-anchor="middle" font-size="7.5" fill="#3fb950" font-family="monospace">A4</text>
            <rect x="156" y="116" width="8" height="8" rx="1" fill="rgba(188,140,255,0.1)" stroke="#bc8cff"/>
            <text x="166" y="122" font-size="6" fill="#bc8cff" font-family="monospace">moved</text>
            <rect x="204" y="116" width="8" height="8" rx="1" fill="rgba(63,185,80,0.1)" stroke="#3fb950"/>
            <text x="214" y="122" font-size="6" fill="#3fb950" font-family="monospace">inserted</text>
          </svg>
        </div>
        <div class="da-note">Edit distance over node hierarchy &mdash; moves preserve subtree identity across parent changes.</div>
      </div>

      <!-- 3. TENSOR -->
      <div class="da-card">
        <div class="da-card-hdr">
          <span class="da-chip da-chip-tensor">Tensor</span>
          <span class="da-algo-name">Sparse / block numerical diff</span>
        </div>
        <div class="da-domains-row">sim state &middot; voxel grids &middot; weight matrices</div>
        <div class="da-visual" style="flex-direction:column;gap:12px">
          <div class="tensor-wrap">
            <div class="tensor-panel">
              <div class="tensor-label">t&thinsp;=&thinsp;0</div>
              <div class="tensor-grid">
                <div class="tc tc-2"></div><div class="tc tc-0"></div><div class="tc tc-1"></div><div class="tc tc-0"></div>
                <div class="tc tc-0"></div><div class="tc tc-3"></div><div class="tc tc-0"></div><div class="tc tc-1"></div>
                <div class="tc tc-1"></div><div class="tc tc-0"></div><div class="tc tc-2"></div><div class="tc tc-0"></div>
                <div class="tc tc-0"></div><div class="tc tc-1"></div><div class="tc tc-0"></div><div class="tc tc-3"></div>
              </div>
            </div>
            <div class="tensor-arrow">&rarr;</div>
            <div class="tensor-panel">
              <div class="tensor-label">t&thinsp;=&thinsp;1&thinsp;(&Delta;)</div>
              <div class="tensor-grid">
                <div class="tc tc-2"></div><div class="tc tc-0"></div><div class="tc tc-1"></div><div class="tc tc-0"></div>
                <div class="tc tc-0"></div><div class="tc tc-hot3"></div><div class="tc tc-0"></div><div class="tc tc-warm1"></div>
                <div class="tc tc-1"></div><div class="tc tc-0"></div><div class="tc tc-hot2"></div><div class="tc tc-0"></div>
                <div class="tc tc-0"></div><div class="tc tc-1"></div><div class="tc tc-0"></div><div class="tc tc-3"></div>
              </div>
            </div>
          </div>
          <div class="tensor-legend">
            <span class="tl-item"><span class="tl-swatch tl-unchanged"></span>|&Delta;| = 0</span>
            <span class="tl-item"><span class="tl-swatch tl-warm"></span>|&Delta;| &le; &epsilon; (within threshold)</span>
            <span class="tl-item"><span class="tl-swatch tl-hot"></span>|&Delta;| &gt; &epsilon; &rarr; PatchOp</span>
          </div>
        </div>
        <div class="da-note">Configurable &epsilon; threshold. Sparse mode records only changed blocks &mdash; efficient for large tensors.</div>
      </div>

      <!-- 4. SET -->
      <div class="da-card">
        <div class="da-card-hdr">
          <span class="da-chip da-chip-set">Set</span>
          <span class="da-algo-name">Set algebra &middot; add / remove</span>
        </div>
        <div class="da-domains-row">annotations &middot; tags &middot; gene ontology terms</div>
        <div class="da-visual">
          <div class="set-vis">
            <div class="set-col">
              <div class="set-header">before</div>
              <div class="set-members">
                <span class="set-member set-kept">GO:0001234</span>
                <span class="set-member set-kept">GO:0005634</span>
                <span class="set-member set-removed">GO:0006915</span>
                <span class="set-member set-kept">GO:0016020</span>
              </div>
            </div>
            <div class="set-ops-col">
              <div class="set-op-line set-op-keep">&mdash;</div>
              <div class="set-op-line set-op-keep">&mdash;</div>
              <div class="set-op-line set-op-rm">&times; del</div>
              <div class="set-op-line set-op-keep">&mdash;</div>
              <div class="set-op-line set-op-add">+ ins</div>
            </div>
            <div class="set-col">
              <div class="set-header">after</div>
              <div class="set-members">
                <span class="set-member set-kept">GO:0001234</span>
                <span class="set-member set-kept">GO:0005634</span>
                <span class="set-member set-kept">GO:0016020</span>
                <span class="set-member set-added">GO:0042592</span>
              </div>
            </div>
          </div>
        </div>
        <div class="da-note">Unordered &mdash; no position tracking. Pure membership delta: {removed} and {added}.</div>
      </div>

      <!-- 5. MAP -->
      <div class="da-card">
        <div class="da-card-hdr">
          <span class="da-chip da-chip-map">Map</span>
          <span class="da-algo-name">Recursive key-by-key delegation</span>
        </div>
        <div class="da-domains-row">metadata &middot; configs &middot; nested structures</div>
        <div class="da-visual" style="align-items:flex-start">
          <div class="map-vis">
            <div class="map-entry map-entry-changed">
              <span class="map-key">tempo</span>
              <span class="map-val-before">120</span><span class="map-delta">&nbsp;&rarr;&nbsp;</span><span class="map-val-after">140</span>
              <span class="map-sub-algo da-chip-tensor">scalar &rarr; PatchOp</span>
            </div>
            <div class="map-entry map-entry-changed">
              <span class="map-key">notes</span>
              <span class="map-val-before">[&hellip;]</span><span class="map-delta">&nbsp;&rarr;&nbsp;</span><span class="map-val-after">[&hellip;&prime;]</span>
              <span class="map-sub-algo da-chip-seq">sequence &rarr; LCS</span>
            </div>
            <div class="map-entry map-entry-changed">
              <span class="map-key">tags</span>
              <span class="map-val-before">{&hellip;}</span><span class="map-delta">&nbsp;&rarr;&nbsp;</span><span class="map-val-after">{&hellip;&prime;}</span>
              <span class="map-sub-algo da-chip-set">set &rarr; algebra</span>
            </div>
            <div class="map-entry map-entry-unchanged">
              <span class="map-key">author</span>
              <span class="map-val-before">"Bach"</span><span class="map-delta">&nbsp;=&nbsp;</span><span class="map-val-after">"Bach"</span>
              <span style="margin-left:auto;font-size:9px;color:var(--dim);font-family:var(--mono)">unchanged</span>
            </div>
          </div>
        </div>
        <div class="da-note">Each key is diffed by whichever algorithm matches its declared type &mdash; recursively, to arbitrary depth.</div>
      </div>

    </div><!-- .da-grid -->

    <!-- StructuredDelta taxonomy -->
    <div class="da-delta-flow">
      <div class="da-delta-top">
        <span class="da-delta-label">diff() &rarr; StructuredDelta</span>
        <span class="da-delta-sub">all five algorithms produce the same typed operation list</span>
      </div>
      <div class="da-delta-ops">
        <span class="da-dop da-dop-ins">InsertOp</span>
        <span class="da-dop da-dop-del">DeleteOp</span>
        <span class="da-dop da-dop-mov">MoveOp</span>
        <span class="da-dop da-dop-rep">ReplaceOp</span>
        <span class="da-dop da-dop-pat">PatchOp</span>
      </div>
      <div class="da-delta-merge">
        <div class="da-merge-branch da-merge-ot">
          <div class="da-merge-mode-label">merge_mode: &ldquo;three_way&rdquo;</div>
          <div class="da-merge-desc">Operational Transformation &mdash; independent ops commute automatically; conflicting ops surface for human resolution</div>
        </div>
        <div class="da-merge-divider">or</div>
        <div class="da-merge-branch da-merge-crdt">
          <div class="da-merge-mode-label">merge_mode: &ldquo;crdt&rdquo;</div>
          <div class="da-merge-desc">CRDT join() &mdash; convergent, no coordination required; any two replicas always reach the same state</div>
        </div>
      </div>
    </div>

  </div>
</section>
"""

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

    /* ---- Base icon ---- */
    .icon {
      display: inline-block;
      vertical-align: -0.15em;
      flex-shrink: 0;
    }
    .ticker-item .icon   { width: 13px; height: 13px; vertical-align: -0.1em; }
    .cap-showcase-badge .icon { width: 13px; height: 13px; vertical-align: -0.1em; }

    /* ---- Protocol two-col layout ---- */
    .proto-layout {
      display: grid;
      grid-template-columns: 148px 1fr;
      gap: 0;
      border: 1px solid var(--border);
      border-radius: var(--r);
      overflow: hidden;
      margin-bottom: 40px;
      align-items: stretch;
    }
    @media (max-width: 640px) {
      .proto-layout { grid-template-columns: 1fr; }
      .stat-strip { border-right: none; border-bottom: 1px solid var(--border); }
    }

    /* ---- Stat strip (left column) ---- */
    .stat-strip {
      display: flex;
      flex-direction: column;
      border-right: 1px solid var(--border);
    }
    .stat-cell {
      flex: 1;
      padding: 18px 20px;
      border-bottom: 1px solid var(--border);
      text-align: center;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
    }
    .stat-cell:last-child { border-bottom: none; }
    .stat-num {
      font-family: var(--mono);
      font-size: 26px;
      font-weight: 700;
      color: var(--accent2);
      display: block;
      line-height: 1.1;
    }
    .stat-lbl { font-size: 11px; color: var(--mute); margin-top: 4px; line-height: 1.3; }

    /* ---- Protocol table (right column) ---- */
    .proto-table {
      overflow: hidden;
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
    /* ---- Diff Algebra section ---- */
    .da-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 20px;
      margin-bottom: 36px;
    }
    .da-seq-card { grid-column: span 2; }
    @media (max-width: 720px) {
      .da-grid { grid-template-columns: 1fr; }
      .da-seq-card { grid-column: span 1; }
    }
    .da-card {
      background: var(--bg2);
      border: 1px solid var(--border);
      border-radius: var(--r);
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .da-card-hdr { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .da-chip {
      font-family: var(--mono);
      font-size: 10.5px;
      font-weight: 700;
      padding: 3px 10px;
      border-radius: 4px;
      border: 1px solid;
    }
    .da-chip-seq    { color:#58a6ff; background:rgba(88,166,255,0.1);  border-color:rgba(88,166,255,0.3);  }
    .da-chip-tree   { color:#f9a825; background:rgba(249,168,37,0.1);  border-color:rgba(249,168,37,0.3);  }
    .da-chip-tensor { color:#ab47bc; background:rgba(171,71,188,0.1);  border-color:rgba(171,71,188,0.3);  }
    .da-chip-set    { color:#3fb950; background:rgba(63,185,80,0.1);   border-color:rgba(63,185,80,0.3);   }
    .da-chip-map    { color:#ef5350; background:rgba(239,83,80,0.1);   border-color:rgba(239,83,80,0.3);   }
    .da-algo-name   { font-size:12px; color:var(--mute); font-family:var(--mono); }
    .da-domains-row { font-size:11px; color:var(--dim); }
    .da-visual {
      background: #0a0e14;
      border: 1px solid var(--border);
      border-radius: 5px;
      padding: 14px;
      flex: 1;
      min-height: 130px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .da-note { font-size:11.5px; color:var(--mute); line-height:1.5; }

    /* Sequence LCS */
    .seq-vis { display:flex; flex-direction:column; gap:6px; width:100%; }
    .seq-row-lbl { font-family:var(--mono); font-size:9px; color:var(--dim); text-transform:uppercase; letter-spacing:.6px; }
    .seq-row { display:flex; gap:6px; flex-wrap:wrap; }
    .seq-blk {
      display:flex; flex-direction:column; align-items:center;
      padding:5px 9px; border-radius:5px; border:1.5px solid;
      min-width:46px; gap:2px; transition:transform .15s;
    }
    .seq-blk:hover { transform:translateY(-2px); }
    .seq-hash { font-family:var(--mono); font-size:7.5px; opacity:.55; }
    .seq-name { font-family:var(--mono); font-size:14px; font-weight:700; }
    .seq-match     { background:rgba(88,166,255,.07);  border-color:rgba(88,166,255,.25); color:#58a6ff; }
    .seq-del       { background:rgba(239,83,80,.1);    border-color:rgba(239,83,80,.35);  color:#ef5350; text-decoration:line-through; }
    .seq-ins       { background:rgba(63,185,80,.1);    border-color:rgba(63,185,80,.35);  color:#3fb950; }
    .seq-moved-from{ background:rgba(188,140,255,.06); border-color:rgba(188,140,255,.25);color:#bc8cff; opacity:.55; }
    .seq-moved-to  { background:rgba(188,140,255,.12); border-color:rgba(188,140,255,.5); color:#bc8cff; }
    .seq-ops-row { display:flex; gap:6px; padding:2px 0; flex-wrap:wrap; }
    .seq-op-cell { min-width:46px; display:flex; justify-content:center; }
    .da-op { font-size:9px; font-weight:700; letter-spacing:.3px; padding:1px 5px; border-radius:3px; white-space:nowrap; }
    .da-op-match  { color:var(--dim); }
    .da-op-delete { color:#ef5350; background:rgba(239,83,80,.1); }
    .da-op-insert { color:#3fb950; background:rgba(63,185,80,.1); }
    .da-op-move   { color:#bc8cff; background:rgba(188,140,255,.1); }

    /* Tree SVG */
    .tree-vis { width:100%; height:130px; overflow:visible; }

    /* Tensor */
    .tensor-wrap { display:flex; align-items:center; gap:14px; }
    .tensor-panel { display:flex; flex-direction:column; align-items:center; gap:5px; }
    .tensor-label { font-family:var(--mono); font-size:9px; color:var(--dim); }
    .tensor-grid { display:grid; grid-template-columns:repeat(4,26px); gap:3px; }
    .tc { width:26px; height:26px; border-radius:3px; }
    .tc-0     { background:#141c28; }
    .tc-1     { background:#1a2538; }
    .tc-2     { background:#1d2f50; }
    .tc-3     { background:#1a3060; }
    .tc-warm1 { background:rgba(249,168,37,.28); border:1px solid rgba(249,168,37,.4); }
    .tc-hot2  { background:rgba(239,83,80,.45);  border:1px solid rgba(239,83,80,.65); }
    .tc-hot3  { background:rgba(239,83,80,.65);  border:1px solid rgba(239,83,80,.9); }
    .tensor-arrow { font-size:22px; color:var(--dim); }
    .tensor-legend { display:flex; flex-direction:column; gap:5px; margin-top:4px; }
    .tl-item { display:flex; align-items:center; gap:5px; font-size:9px; color:var(--mute); }
    .tl-swatch { width:12px; height:12px; border-radius:2px; flex-shrink:0; }
    .tl-unchanged { background:#141c28; }
    .tl-warm { background:rgba(249,168,37,.28); border:1px solid rgba(249,168,37,.4); }
    .tl-hot  { background:rgba(239,83,80,.65);  border:1px solid rgba(239,83,80,.9); }

    /* Set algebra */
    .set-vis { display:grid; grid-template-columns:1fr auto 1fr; gap:8px; align-items:start; width:100%; }
    .set-col .set-header { font-family:var(--mono); font-size:9px; color:var(--dim); text-transform:uppercase; letter-spacing:.6px; margin-bottom:6px; }
    .set-members { display:flex; flex-direction:column; gap:4px; }
    .set-member { font-family:var(--mono); font-size:10px; padding:3px 8px; border-radius:3px; border:1px solid; }
    .set-kept    { color:var(--mute); border-color:var(--border); background:var(--bg3); }
    .set-removed { color:#ef5350; border-color:rgba(239,83,80,.35); background:rgba(239,83,80,.07); text-decoration:line-through; }
    .set-added   { color:#3fb950; border-color:rgba(63,185,80,.35); background:rgba(63,185,80,.07); }
    .set-ops-col { display:flex; flex-direction:column; gap:4px; padding-top:21px; }
    .set-op-line { font-size:10px; font-weight:700; white-space:nowrap; height:26px; display:flex; align-items:center; }
    .set-op-keep { color:var(--dim); }
    .set-op-rm   { color:#ef5350; }
    .set-op-add  { color:#3fb950; }

    /* Map recursive */
    .map-vis { display:flex; flex-direction:column; gap:5px; width:100%; font-family:var(--mono); }
    .map-entry { display:flex; align-items:center; gap:6px; padding:5px 8px; border-radius:4px; font-size:11px; flex-wrap:wrap; }
    .map-entry-changed   { background:rgba(88,166,255,.04); border:1px solid rgba(88,166,255,.15); }
    .map-entry-unchanged { background:var(--bg3); border:1px solid var(--border); opacity:.65; }
    .map-key   { color:#61afef; font-weight:700; min-width:52px; }
    .map-delta { color:var(--dim); }
    .map-val-before { color:var(--dim); font-size:10px; }
    .map-val-after  { color:var(--text); font-size:10px; }
    .map-sub-algo { margin-left:auto; font-size:9px; font-weight:700; padding:1px 6px; border-radius:3px; border:1px solid; }

    /* StructuredDelta flow */
    .da-delta-flow {
      background: var(--bg2);
      border: 1px solid var(--border);
      border-radius: var(--r);
      padding: 24px;
    }
    .da-delta-top { margin-bottom:14px; display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
    .da-delta-label { font-family:var(--mono); font-size:15px; font-weight:700; color:var(--text); }
    .da-delta-sub   { font-size:12px; color:var(--mute); }
    .da-delta-ops   { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:18px; }
    .da-dop { font-family:var(--mono); font-size:12px; font-weight:700; padding:5px 14px; border-radius:5px; border:1px solid; }
    .da-dop-ins { color:#3fb950; background:rgba(63,185,80,.1);  border-color:rgba(63,185,80,.3);  }
    .da-dop-del { color:#ef5350; background:rgba(239,83,80,.1);  border-color:rgba(239,83,80,.3);  }
    .da-dop-mov { color:#bc8cff; background:rgba(188,140,255,.1);border-color:rgba(188,140,255,.3); }
    .da-dop-rep { color:#f9a825; background:rgba(249,168,37,.1); border-color:rgba(249,168,37,.3); }
    .da-dop-pat { color:#58a6ff; background:rgba(88,166,255,.1); border-color:rgba(88,166,255,.3); }
    .da-delta-merge { display:grid; grid-template-columns:1fr auto 1fr; gap:14px; align-items:center; }
    @media (max-width: 640px) { .da-delta-merge { grid-template-columns:1fr; } .da-merge-divider { text-align:center; } }
    .da-merge-branch { padding:14px 16px; border-radius:6px; border:1px solid; }
    .da-merge-ot   { border-color:rgba(239,83,80,.3);   background:rgba(239,83,80,.04); }
    .da-merge-crdt { border-color:rgba(188,140,255,.3); background:rgba(188,140,255,.04); }
    .da-merge-mode-label { font-family:var(--mono); font-size:11px; font-weight:700; margin-bottom:5px; }
    .da-merge-ot   .da-merge-mode-label { color:#ef5350; }
    .da-merge-crdt .da-merge-mode-label { color:#bc8cff; }
    .da-merge-desc { font-size:11.5px; color:var(--mute); line-height:1.5; }
    .da-merge-divider { color:var(--dim); font-size:13px; font-weight:700; }

    /* ---- CRDT visualizations ---- */
    .crdt-vis { display:flex; flex-direction:column; gap:10px; width:100%; }
    /* concurrent replicas row */
    .crdt-concurrent { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .crdt-rep {
      background:var(--bg3); border:1px solid var(--border);
      border-radius:5px; padding:9px 10px;
      display:flex; flex-direction:column; gap:4px;
      min-width:0; overflow:hidden;
    }
    .crdt-rep-hdr { font-family:var(--mono); font-size:9px; font-weight:700; color:var(--dim); text-transform:uppercase; letter-spacing:.6px; }
    .crdt-op { font-family:var(--mono); font-size:10px; padding:2px 6px; border-radius:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%; }
    .crdt-op-add { color:#3fb950; background:rgba(63,185,80,.1); }
    .crdt-op-rm  { color:#ef5350; background:rgba(239,83,80,.1); }
    .crdt-rep-state { font-family:var(--mono); font-size:11px; color:var(--mute); margin-top:2px; }
    /* join result row */
    .crdt-join { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:8px 10px; background:var(--bg3); border-radius:5px; border:1px solid var(--border); }
    .crdt-join-label { font-family:var(--mono); font-size:10px; color:var(--dim); white-space:nowrap; }
    .crdt-join-val { font-family:var(--mono); font-size:13px; font-weight:700; }
    .crdt-join-rule { font-size:10px; color:var(--mute); font-style:italic; margin-left:auto; }
    /* LWW timeline */
    .crdt-writes { display:flex; flex-direction:column; gap:5px; }
    .crdt-write { display:flex; align-items:center; gap:8px; padding:7px 10px; border-radius:5px; border:1px solid var(--border); background:var(--bg3); font-family:var(--mono); font-size:11px; }
    .crdt-write-winner { border-color:rgba(88,166,255,.4); background:rgba(88,166,255,.05); }
    .crdt-time { font-size:9px; color:var(--dim); min-width:32px; }
    .crdt-agent { font-size:9px; font-weight:700; padding:1px 6px; border-radius:3px; min-width:18px; text-align:center; }
    .crdt-agent-a { background:rgba(249,168,37,.15); color:#f9a825; }
    .crdt-agent-b { background:rgba(88,166,255,.15); color:#58a6ff; }
    .crdt-wval { color:var(--text); flex:1; }
    .crdt-latest { font-size:9px; font-weight:700; color:#58a6ff; background:rgba(88,166,255,.12); padding:1px 6px; border-radius:10px; white-space:nowrap; }
    /* GCounter bars */
    .crdt-gcounter { display:flex; flex-direction:column; gap:7px; }
    .crdt-gc-row { display:flex; align-items:center; gap:8px; }
    .crdt-bar { flex:1; height:22px; background:var(--bg3); border-radius:4px; overflow:hidden; }
    .crdt-bar-fill { height:100%; display:flex; align-items:center; justify-content:flex-end; padding-right:7px; font-family:var(--mono); font-size:11px; font-weight:700; border-radius:4px; transition:width .4s; }
    .crdt-bar-a { background:rgba(249,168,37,.35); color:#f9a825; }
    .crdt-bar-b { background:rgba(88,166,255,.35);  color:#58a6ff; }
    /* VectorClock grid */
    .crdt-vclocks { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
    .crdt-vc { display:flex; flex-direction:column; gap:4px; }
    .crdt-vc-hdr { font-family:var(--mono); font-size:9px; color:var(--dim); text-transform:uppercase; letter-spacing:.5px; }
    .crdt-vc-cells { display:flex; gap:4px; }
    .crdt-vc-cell { font-family:var(--mono); font-size:10.5px; font-weight:700; padding:4px 8px; border-radius:4px; border:1px solid; }
    .crdt-vc-self { color:#f9a825; background:rgba(249,168,37,.1); border-color:rgba(249,168,37,.35); }
    .crdt-vc-zero { color:var(--dim); background:var(--bg3); border-color:var(--border); }
    .crdt-vc-max  { color:#3fb950; background:rgba(63,185,80,.1); border-color:rgba(63,185,80,.35); }
    .crdt-vc-sep  { font-size:16px; color:var(--dim); padding:0 2px; align-self:center; margin-top:14px; }
    .crdt-concurrent-badge { font-family:var(--mono); font-size:10px; color:#f9a825; background:rgba(249,168,37,.08); border:1px solid rgba(249,168,37,.25); border-radius:4px; padding:3px 10px; align-self:flex-start; }

    /* ---- OT Merge scenario cards ---- */
    .ot-scenarios { display: flex; flex-direction: column; gap: 10px; }
    .ot-scenario {
      background: var(--bg);
      border: 1px solid var(--border);
      border-left: 3px solid transparent;
      border-radius: 6px;
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 9px;
    }
    .ot-clean    { border-left-color: #3fb950; }
    .ot-conflict { border-left-color: #ef5350; }
    .ot-scenario-hdr { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
    .ot-scenario-label {
      font-family: var(--mono);
      font-size: 9.5px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--dim);
    }
    .ot-scenario-title { font-size: 11.5px; color: var(--mute); }
    .ot-ops { display: flex; flex-direction: column; gap: 5px; }
    .ot-op {
      display: flex;
      align-items: center;
      gap: 7px;
      font-family: var(--mono);
      font-size: 11.5px;
      flex-wrap: wrap;
    }
    .ot-op-side {
      font-size: 9px;
      font-weight: 700;
      color: var(--dim);
      background: var(--bg3);
      padding: 1px 6px;
      border-radius: 3px;
      min-width: 34px;
      text-align: center;
    }
    .ot-op-type { font-weight: 700; padding: 1px 7px; border-radius: 3px; font-size: 10.5px; }
    .ot-insert  { background: rgba(63,185,80,0.13); color: #3fb950; }
    .ot-replace { background: rgba(249,168,37,0.13); color: #f9a825; }
    .ot-op-addr { color: #98c379; }
    .ot-op-meta { color: var(--dim); font-size: 10.5px; }
    .ot-result {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 8px;
      padding-top: 9px;
      border-top: 1px solid var(--border);
    }
    .ot-reason { font-family: var(--mono); font-size: 11px; color: var(--mute); }
    .ot-badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 11px;
      font-weight: 700;
      padding: 3px 10px;
      border-radius: 12px;
      white-space: nowrap;
    }
    .ot-badge .icon { width: 11px; height: 11px; vertical-align: -0.05em; }
    .ot-badge-clean    { background: rgba(63,185,80,0.1); color: #3fb950; border: 1px solid rgba(63,185,80,0.3); }
    .ot-badge-conflict { background: rgba(239,83,80,0.1); color: #ef5350; border: 1px solid rgba(239,83,80,0.3); }

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
    .planned-icon  { line-height: 0; }
    .planned-icon .icon { width: 28px; height: 28px; }
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
    .dist-icon   { line-height: 0; flex-shrink: 0; }
    .dist-icon .icon { width: 26px; height: 26px; }
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
      margin-bottom: 20px;
      line-height: 0;
    }
    .musehub-logo .icon { width: 48px; height: 48px; stroke: #bc8cff; }
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
    .mh-feature-icon { margin-bottom: 10px; line-height: 0; }
    .mh-feature-icon .icon { width: 22px; height: 22px; stroke: #bc8cff; }
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
  <a class="nav-link" href="demo.html">Demo</a>
  <a class="nav-link" href="https://github.com/cgcardona/muse/blob/main/docs/guide/plugin-authoring-guide.md">Plugin Guide</a>
  <div class="nav-spacer"></div>
  <span class="nav-badge">v0.1.2</span>
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
    <a class="btn-outline" href="demo.html">Watch the Demo →</a>
  </div>
  <div class="domain-ticker">
    <div class="ticker-track">
      <span class="ticker-item active">{{ICON_MUSIC}} midi</span>
      <span class="ticker-item active">{{ICON_CODE}} code</span>
      <span class="ticker-item">{{ICON_GENOMICS}} genomics</span>
      <span class="ticker-item">{{ICON_CUBE}} 3d-spatial</span>
      <span class="ticker-item">{{ICON_TRENDING}} financial</span>
      <span class="ticker-item">{{ICON_ATOM}} simulation</span>
      <span class="ticker-item">{{ICON_ACTIVITY}} proteomics</span>
      <span class="ticker-item">{{ICON_PEN_TOOL}} cad</span>
      <span class="ticker-item">{{ICON_ZAP}} game-state</span>
      <span class="ticker-item">{{ICON_PLUS}} your-domain</span>
      <!-- duplicate for seamless loop -->
      <span class="ticker-item active">{{ICON_MUSIC}} midi</span>
      <span class="ticker-item active">{{ICON_CODE}} code</span>
      <span class="ticker-item">{{ICON_GENOMICS}} genomics</span>
      <span class="ticker-item">{{ICON_CUBE}} 3d-spatial</span>
      <span class="ticker-item">{{ICON_TRENDING}} financial</span>
      <span class="ticker-item">{{ICON_ATOM}} simulation</span>
      <span class="ticker-item">{{ICON_ACTIVITY}} proteomics</span>
      <span class="ticker-item">{{ICON_PEN_TOOL}} cad</span>
      <span class="ticker-item">{{ICON_ZAP}} game-state</span>
      <span class="ticker-item">{{ICON_PLUS}} your-domain</span>
    </div>
  </div>
</div>

<!-- =================== PROTOCOL =================== -->
<section id="protocol">
  <div class="inner">
    <div class="section-eyebrow">The Contract</div>
    <h2>The MuseDomainPlugin Protocol</h2>
    <p class="section-lead">
      Every domain — MIDI, source code, genomics, 3D spatial, financial models —
      implements the same <strong>six-method protocol</strong>. The core engine
      handles everything else: content-addressed storage, DAG, branches, log,
      merge base, cherry-pick, revert, stash, tags.
    </p>

    <div class="proto-layout">
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
      MIDI and code are the two shipped domains — both fully active with typed
      deltas, structured merge, and <code>.museattributes</code> rule control.
      These are the domains planned next — and the slot waiting for yours.
    </p>
    <div class="planned-grid">
      {{PLANNED_DOMAINS}}
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
            {{ICON_CODE}} Typed Delta Algebra
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
          <pre class="cap-showcase-output" data-lang="json">{{TYPED_DELTA_EXAMPLE}}</pre>
        </div>
      </div>

      <div class="cap-showcase-card" style="--cap-color:#58a6ff">
        <div class="cap-showcase-header">
          <span class="cap-showcase-badge" style="color:#58a6ff;background:#58a6ff15;border-color:#58a6ff40">
            {{ICON_LAYERS}} Domain Schema
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
            {{ICON_GIT_MERGE}} OT Merge
          </span>
          <span class="cap-showcase-sub">Operational transformation — independent ops commute automatically</span>
        </div>
        <div class="cap-showcase-body">
          <p class="cap-showcase-desc">
            Plugins implementing <strong>StructuredMergePlugin</strong> get operational
            transformation. Operations at different addresses commute automatically —
            only operations on the same address with incompatible intent surface a conflict.
          </p>
          <div class="ot-scenarios">

            <div class="ot-scenario ot-clean">
              <div class="ot-scenario-hdr">
                <span class="ot-scenario-label">Scenario A</span>
                <span class="ot-scenario-title">Independent ops at different addresses</span>
              </div>
              <div class="ot-ops">
                <div class="ot-op">
                  <span class="ot-op-side">left</span>
                  <span class="ot-op-type ot-insert">InsertOp</span>
                  <span class="ot-op-addr">"ot-notes-a.mid"</span>
                  <span class="ot-op-meta">tick=0 · C4 E4 G4</span>
                </div>
                <div class="ot-op">
                  <span class="ot-op-side">right</span>
                  <span class="ot-op-type ot-insert">InsertOp</span>
                  <span class="ot-op-addr">"ot-notes-b.mid"</span>
                  <span class="ot-op-meta">tick=480 · D4 F4 A4</span>
                </div>
              </div>
              <div class="ot-result">
                <span class="ot-reason">transform → no overlap → ops commute</span>
                <span class="ot-badge ot-badge-clean">{{ICON_CHECK_CIRCLE}} Clean merge · both files applied</span>
              </div>
            </div>

            <div class="ot-scenario ot-conflict">
              <div class="ot-scenario-hdr">
                <span class="ot-scenario-label">Scenario B</span>
                <span class="ot-scenario-title">Same address, conflicting intent — conflict surfaced</span>
              </div>
              <div class="ot-ops">
                <div class="ot-op">
                  <span class="ot-op-side">left</span>
                  <span class="ot-op-type ot-replace">ReplaceOp</span>
                  <span class="ot-op-addr">"shared-melody.mid"</span>
                  <span class="ot-op-meta">C4 E4 G4 · major triad</span>
                </div>
                <div class="ot-op">
                  <span class="ot-op-side">right</span>
                  <span class="ot-op-type ot-replace">ReplaceOp</span>
                  <span class="ot-op-addr">"shared-melody.mid"</span>
                  <span class="ot-op-meta">C4 Eb4 G4 · minor triad</span>
                </div>
              </div>
              <div class="ot-result">
                <span class="ot-reason">transform → same address · non-commuting content</span>
                <span class="ot-badge ot-badge-conflict">{{ICON_X_CIRCLE}} Conflict · human resolves</span>
              </div>
            </div>

          </div>
        </div>
      </div>

      <div class="cap-showcase-card" style="--cap-color:#bc8cff">
        <div class="cap-showcase-header">
          <span class="cap-showcase-badge" style="color:#bc8cff;background:#bc8cff15;border-color:#bc8cff40">
            {{ICON_ZAP}} CRDT Primitives
          </span>
          <span class="cap-showcase-sub">Convergent merge — any two replicas always reach the same state</span>
        </div>
        <div class="cap-showcase-body">
          <p class="cap-showcase-desc">
            Plugins implementing <strong>CRDTPlugin</strong> get six battle-tested
            convergent data structures. No coordination required between replicas.
            The MIDI plugin extends RGA into <strong>MusicRGA</strong> &mdash; a
            voice-aware variant that orders concurrent note insertions by voice lane
            (bass &rarr; tenor &rarr; alto &rarr; soprano) before falling back to
            op-id, preventing voice crossings without human intervention.
          </p>
          <div class="crdt-mini-grid">
            {{CRDT_CARDS}}
          </div>
        </div>
      </div>

    </div>
  </div>
</section>

{{DIFF_ALGEBRA}}

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
      <a href="https://github.com/cgcardona/muse/blob/main/docs/guide/plugin-authoring-guide.md">docs/guide/plugin-authoring-guide.md</a>
      · CRDT extension →
      <a href="https://github.com/cgcardona/muse/blob/main/docs/guide/crdt-reference.md">docs/guide/crdt-reference.md</a>
    </p>
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
  <div class="musehub-logo">{{ICON_GLOBE}}</div>
  <h2><span>MuseHub</span> is coming</h2>
  <p class="musehub-desc">
    A <strong>centralized, searchable registry</strong> for Muse domain plugins —
    think npm or crates.io, but for any multidimensional versioned state.
    One command to publish. One command to install.
  </p>
  <div class="musehub-features">
    <div class="mh-feature">
      <div class="mh-feature-icon">{{ICON_SEARCH}}</div>
      <div class="mh-feature-title">Searchable</div>
      <div class="mh-feature-desc">Find plugins by domain, capability, or keyword</div>
    </div>
    <div class="mh-feature">
      <div class="mh-feature-icon">{{ICON_PACKAGE}}</div>
      <div class="mh-feature-title">Versioned</div>
      <div class="mh-feature-desc">Semantic versioning, pinned installs, changelogs</div>
    </div>
    <div class="mh-feature">
      <div class="mh-feature-icon">{{ICON_LOCK}}</div>
      <div class="mh-feature-title">Private registries</div>
      <div class="mh-feature-desc">Self-host for enterprise or research teams</div>
    </div>
    <div class="mh-feature">
      <div class="mh-feature-icon">{{ICON_ZAP}}</div>
      <div class="mh-feature-title">One command</div>
      <div class="mh-feature-desc"><code>muse init --domain @musehub/genomics</code></div>
    </div>
  </div>
  <div class="musehub-status">
    <div class="mh-dot"></div>
    MuseHub — planned · building in public at <a href="https://github.com/cgcardona/musehub" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:underline;text-underline-offset:3px;">github.com/cgcardona/musehub</a>
  </div>
</div>

<footer>
  <span>Muse v0.1.2 · domain-agnostic version control for multidimensional state · Python 3.14</span>
  <span>
    <a href="demo.html">Demo</a> ·
    <a href="https://github.com/cgcardona/muse">GitHub</a> ·
    <a href="https://github.com/cgcardona/muse/blob/main/docs/guide/plugin-authoring-guide.md">Plugin Guide</a>
  </span>
</footer>

<script>
(function () {
  function esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function tokenizeJSON(raw) {
    let html = '';
    let i = 0;
    while (i < raw.length) {
      // Comment line starting with #
      if (raw[i] === '#') {
        const end = raw.indexOf('\n', i);
        const line = end === -1 ? raw.slice(i) : raw.slice(i, end);
        html += '<span style="color:#5c6370;font-style:italic">' + esc(line) + '</span>';
        i += line.length;
        continue;
      }
      // String literal
      if (raw[i] === '"') {
        let j = i + 1;
        while (j < raw.length && raw[j] !== '"') {
          if (raw[j] === '\\') j++;
          j++;
        }
        j++;
        const str = raw.slice(i, j);
        // Peek past whitespace — key if followed by ':'
        let k = j;
        while (k < raw.length && (raw[k] === ' ' || raw[k] === '\t')) k++;
        const color = raw[k] === ':' ? '#61afef' : '#98c379';
        html += '<span style="color:' + color + '">' + esc(str) + '</span>';
        i = j;
        continue;
      }
      // Number (including negative)
      if (/[0-9]/.test(raw[i]) || (raw[i] === '-' && /[0-9]/.test(raw[i + 1] || ''))) {
        let j = i;
        if (raw[j] === '-') j++;
        while (j < raw.length && /[0-9.eE+-]/.test(raw[j])) j++;
        html += '<span style="color:#d19a66">' + esc(raw.slice(i, j)) + '</span>';
        i = j;
        continue;
      }
      // Keywords: true / false / null
      const kws = [['true', '#c678dd'], ['false', '#c678dd'], ['null', '#c678dd']];
      let matched = false;
      for (const [kw, col] of kws) {
        if (raw.slice(i, i + kw.length) === kw) {
          html += '<span style="color:' + col + '">' + kw + '</span>';
          i += kw.length;
          matched = true;
          break;
        }
      }
      if (matched) continue;
      // Default character (punctuation / whitespace)
      html += esc(raw[i]);
      i++;
    }
    return html;
  }

  document.querySelectorAll('pre[data-lang="json"]').forEach(function (pre) {
    pre.innerHTML = tokenizeJSON(pre.textContent);
  });
})();
</script>

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
