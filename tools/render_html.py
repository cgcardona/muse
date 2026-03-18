#!/usr/bin/env python3
"""Muse Tour de Force — HTML renderer.

Takes the structured TourData dict produced by tour_de_force.py and renders
a self-contained, shareable HTML file with an interactive D3 commit DAG,
operation log, architecture diagram, and animated replay.

Stand-alone usage
-----------------
    python tools/render_html.py artifacts/tour_de_force.json
    python tools/render_html.py artifacts/tour_de_force.json --out custom.html
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.request


# ---------------------------------------------------------------------------
# D3.js fetcher
# ---------------------------------------------------------------------------

_D3_CDN = "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"
_D3_FALLBACK = f'<script src="{_D3_CDN}"></script>'


def _fetch_d3() -> str:
    """Download D3.js v7 minified. Returns the source or a CDN script tag."""
    try:
        with urllib.request.urlopen(_D3_CDN, timeout=15) as resp:
            src = resp.read().decode("utf-8")
        print(f"  ↓ D3.js fetched ({len(src)//1024}KB)")
        return f"<script>\n{src}\n</script>"
    except Exception as exc:
        print(f"  ⚠ Could not fetch D3 ({exc}); using CDN link in HTML")
        return _D3_FALLBACK


# ---------------------------------------------------------------------------
# Architecture SVG
# ---------------------------------------------------------------------------

_ARCH_HTML = """\
<div class="arch-flow">
  <div class="arch-row">
    <div class="arch-box cli">
      <div class="box-title">muse CLI</div>
      <div class="box-sub">14 commands</div>
      <div class="box-detail">init · commit · log · diff · show · branch<br>
        checkout · merge · reset · revert · cherry-pick<br>
        stash · tag · status</div>
    </div>
  </div>
  <div class="arch-connector"><div class="connector-line"></div><div class="connector-arrow">▼</div></div>
  <div class="arch-row">
    <div class="arch-box registry">
      <div class="box-title">Plugin Registry</div>
      <div class="box-sub">resolve_plugin(root)</div>
    </div>
  </div>
  <div class="arch-connector"><div class="connector-line"></div><div class="connector-arrow">▼</div></div>
  <div class="arch-row">
    <div class="arch-box core">
      <div class="box-title">Core Engine</div>
      <div class="box-sub">DAG · Content-addressed Objects · Branches · Store · Log Graph · Merge Base</div>
    </div>
  </div>
  <div class="arch-connector"><div class="connector-line"></div><div class="connector-arrow">▼</div></div>
  <div class="arch-row">
    <div class="arch-box protocol">
      <div class="box-title">MuseDomainPlugin Protocol</div>
      <div class="box-sub">Implement 6 methods → get the full VCS for free</div>
    </div>
  </div>
  <div class="arch-connector"><div class="connector-line"></div><div class="connector-arrow">▼</div></div>
  <div class="arch-row plugins-row">
    <div class="arch-box plugin active">
      <div class="box-title">MusicPlugin</div>
      <div class="box-sub">reference impl<br>MIDI · notes · CC · pitch</div>
    </div>
    <div class="arch-box plugin planned">
      <div class="box-title">GenomicsPlugin</div>
      <div class="box-sub">planned<br>sequences · variants</div>
    </div>
    <div class="arch-box plugin planned">
      <div class="box-title">SpacetimePlugin</div>
      <div class="box-sub">planned<br>3D fields · time-slices</div>
    </div>
    <div class="arch-box plugin planned">
      <div class="box-title">YourPlugin</div>
      <div class="box-sub">implement 6 methods<br>get VCS for free</div>
    </div>
  </div>
</div>

<div class="protocol-table">
  <div class="proto-row header">
    <div class="proto-method">Method</div>
    <div class="proto-sig">Signature</div>
    <div class="proto-desc">Purpose</div>
  </div>
  <div class="proto-row">
    <div class="proto-method">snapshot</div>
    <div class="proto-sig">snapshot(live_state) → StateSnapshot</div>
    <div class="proto-desc">Capture current state as a content-addressable JSON blob</div>
  </div>
  <div class="proto-row">
    <div class="proto-method">diff</div>
    <div class="proto-sig">diff(base, target) → StateDelta</div>
    <div class="proto-desc">Compute minimal change between two snapshots (added · removed · modified)</div>
  </div>
  <div class="proto-row">
    <div class="proto-method">merge</div>
    <div class="proto-sig">merge(base, left, right) → MergeResult</div>
    <div class="proto-desc">Three-way reconcile divergent state lines; surface conflicts</div>
  </div>
  <div class="proto-row">
    <div class="proto-method">drift</div>
    <div class="proto-sig">drift(committed, live) → DriftReport</div>
    <div class="proto-desc">Detect uncommitted changes between HEAD and working state</div>
  </div>
  <div class="proto-row">
    <div class="proto-method">apply</div>
    <div class="proto-sig">apply(delta, live_state) → LiveState</div>
    <div class="proto-desc">Apply a delta during checkout to reconstruct historical state</div>
  </div>
  <div class="proto-row">
    <div class="proto-method">schema</div>
    <div class="proto-sig">schema() → DomainSchema</div>
    <div class="proto-desc">Declare data structure — drives diff algorithm selection per dimension</div>
  </div>
</div>
"""


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Muse — Demo</title>
  <style>
    /* ---- Reset & base ---- */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg:        #0d1117;
      --bg2:       #161b22;
      --bg3:       #21262d;
      --border:    #30363d;
      --text:      #e6edf3;
      --text-mute: #8b949e;
      --text-dim:  #484f58;
      --accent:    #4f8ef7;
      --accent2:   #58a6ff;
      --green:     #3fb950;
      --red:       #f85149;
      --yellow:    #d29922;
      --purple:    #bc8cff;
      --font-mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
      --font-ui:   -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
      --radius:    8px;
    }
    html { scroll-behavior: smooth; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--font-ui);
      font-size: 14px;
      line-height: 1.6;
      min-height: 100vh;
    }

    /* ---- Header ---- */
    header {
      background: var(--bg2);
      border-bottom: 1px solid var(--border);
      padding: 24px 40px;
    }
    .header-top {
      display: flex;
      align-items: baseline;
      gap: 16px;
      flex-wrap: wrap;
    }
    header h1 {
      font-size: 28px;
      font-weight: 700;
      letter-spacing: -0.5px;
      color: var(--accent2);
      font-family: var(--font-mono);
    }
    .tagline {
      color: var(--text-mute);
      font-size: 14px;
    }
    .stats-bar {
      display: flex;
      gap: 24px;
      margin-top: 14px;
      flex-wrap: wrap;
    }
    .stat {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 2px;
    }
    .stat-num {
      font-size: 22px;
      font-weight: 700;
      font-family: var(--font-mono);
      color: var(--accent2);
    }
    .stat-label {
      font-size: 11px;
      color: var(--text-mute);
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }
    .stat-sep { color: var(--border); font-size: 22px; align-self: center; }
    .header-nav-link {
      margin-left: auto;
      font-size: 12px;
      color: var(--accent2);
      text-decoration: none;
      border: 1px solid rgba(88,166,255,0.3);
      border-radius: 4px;
      padding: 4px 12px;
      transition: background 0.15s;
    }
    .header-nav-link:hover { background: rgba(88,166,255,0.08); }
    .version-badge {
      margin-left: auto;
      padding: 4px 10px;
      border: 1px solid var(--border);
      border-radius: 20px;
      font-size: 12px;
      font-family: var(--font-mono);
      color: var(--text-mute);
    }

    /* ---- Main layout ---- */
    .main-container {
      display: grid;
      grid-template-columns: 1fr 380px;
      gap: 0;
      height: calc(100vh - 130px);
      min-height: 600px;
    }

    /* ---- DAG panel ---- */
    .dag-panel {
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .dag-header {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 12px 20px;
      border-bottom: 1px solid var(--border);
      background: var(--bg2);
      flex-shrink: 0;
    }
    .dag-header h2 {
      font-size: 13px;
      font-weight: 600;
      color: var(--text-mute);
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }
    .controls { display: flex; gap: 8px; margin-left: auto; align-items: center; }
    .btn {
      padding: 6px 14px;
      border-radius: var(--radius);
      border: 1px solid var(--border);
      background: var(--bg3);
      color: var(--text);
      cursor: pointer;
      font-size: 12px;
      font-family: var(--font-ui);
      transition: all 0.15s;
    }
    .btn:hover { background: var(--border); }
    .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .btn.primary:hover { background: var(--accent2); }
    .btn:disabled { opacity: 0.35; cursor: not-allowed; }
    .btn:disabled:hover { background: var(--bg3); }
    .step-counter {
      font-size: 11px;
      font-family: var(--font-mono);
      color: var(--text-mute);
      min-width: 80px;
      text-align: right;
    }
    .dag-scroll {
      flex: 1;
      overflow: auto;
      padding: 20px;
    }
    #dag-svg { display: block; }
    .branch-legend {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      padding: 8px 20px;
      border-top: 1px solid var(--border);
      background: var(--bg2);
      flex-shrink: 0;
    }
    .legend-item {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      color: var(--text-mute);
    }
    .legend-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      flex-shrink: 0;
    }

    /* ---- Log panel ---- */
    .log-panel {
      display: flex;
      flex-direction: column;
      overflow: hidden;
      background: var(--bg);
    }
    .log-header {
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      background: var(--bg2);
      flex-shrink: 0;
    }
    .log-header h2 {
      font-size: 13px;
      font-weight: 600;
      color: var(--text-mute);
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }
    .log-scroll {
      flex: 1;
      overflow-y: auto;
      padding: 0;
    }
    .act-header {
      padding: 10px 16px 6px;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--text-dim);
      border-top: 1px solid var(--border);
      margin-top: 4px;
      position: sticky;
      top: 0;
      background: var(--bg);
      z-index: 1;
    }
    .act-header:first-child { border-top: none; margin-top: 0; }
    .event-item {
      padding: 8px 16px;
      border-bottom: 1px solid #1a1f26;
      opacity: 0.3;
      transition: opacity 0.3s, background 0.2s;
      cursor: default;
    }
    .event-item.revealed { opacity: 1; }
    .event-item.active { background: rgba(79,142,247,0.08); border-left: 2px solid var(--accent); }
    .event-item.failed { border-left: 2px solid var(--red); }
    .event-cmd {
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--text);
      margin-bottom: 3px;
    }
    .event-cmd .cmd-prefix { color: var(--text-dim); }
    .event-cmd .cmd-name { color: var(--accent2); font-weight: 600; }
    .event-cmd .cmd-args { color: var(--text); }
    .event-output {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--text-mute);
      white-space: pre-wrap;
      word-break: break-all;
      max-height: 80px;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .event-output.conflict { color: var(--red); }
    .event-output.success { color: var(--green); }
    .event-item.rich-act .event-output { max-height: 220px; }

    /* ---- Act jump bar ---- */
    .act-jump-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      padding: 6px 12px;
      border-bottom: 1px solid var(--border);
      background: var(--bg2);
      flex-shrink: 0;
    }
    .act-jump-bar span {
      font-size: 10px;
      color: var(--text-dim);
      align-self: center;
      margin-right: 4px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.6px;
    }
    .act-jump-btn {
      font-size: 10px;
      padding: 2px 8px;
      border-radius: 4px;
      background: var(--bg3);
      border: 1px solid var(--border);
      color: var(--text-mute);
      cursor: pointer;
      font-family: var(--font-mono);
      transition: background 0.15s, color 0.15s;
    }
    .act-jump-btn:hover { background: var(--bg); color: var(--accent); border-color: var(--accent); }
    .act-jump-btn.reveal-all { border-color: var(--green); color: var(--green); }
    .act-jump-btn.reveal-all:hover { background: rgba(63,185,80,0.08); }

    .event-meta {
      display: flex;
      gap: 8px;
      margin-top: 3px;
      font-size: 10px;
      color: var(--text-dim);
    }
    .tag-commit { background: rgba(79,142,247,0.15); color: var(--accent2); padding: 1px 5px; border-radius: 3px; font-family: var(--font-mono); }
    .tag-time { color: var(--text-dim); }

    /* ---- DAG SVG styles ---- */
    .commit-node { cursor: pointer; }
    .commit-node:hover circle { filter: brightness(1.3); }
    .commit-node.highlighted circle { filter: brightness(1.5) drop-shadow(0 0 6px currentColor); }
    .commit-label { font-size: 10px; fill: var(--text-mute); font-family: var(--font-mono); }
    .commit-msg { font-size: 10px; fill: var(--text-mute); }
    .commit-node.highlighted .commit-label,
    .commit-node.highlighted .commit-msg { fill: var(--text); }
    text { font-family: -apple-system, system-ui, sans-serif; }

    /* ---- Registry callout ---- */
    .registry-callout {
      background: var(--bg2);
      border-top: 1px solid var(--border);
      padding: 40px;
    }
    .registry-callout-inner {
      max-width: 1100px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      gap: 32px;
      flex-wrap: wrap;
    }
    .registry-callout-text { flex: 1; min-width: 200px; }
    .registry-callout-title {
      font-size: 16px;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 6px;
    }
    .registry-callout-sub {
      font-size: 13px;
      color: var(--text-mute);
      line-height: 1.6;
    }
    .registry-callout-btn {
      flex-shrink: 0;
      display: inline-block;
      padding: 10px 22px;
      background: var(--accent);
      color: #fff;
      font-size: 13px;
      font-weight: 600;
      border-radius: var(--radius);
      text-decoration: none;
      transition: opacity 0.15s;
    }
    .registry-callout-btn:hover { opacity: 0.85; }

    /* ---- Domain Dashboard section ---- */
    .domain-section {
      background: var(--bg);
      border-top: 1px solid var(--border);
      padding: 60px 40px;
    }
    .domain-inner { max-width: 1100px; margin: 0 auto; }
    .domain-section h2, .crdt-section h2 {
      font-size: 22px;
      font-weight: 700;
      margin-bottom: 8px;
      color: var(--text);
    }
    .domain-section .section-intro, .crdt-section .section-intro {
      color: var(--text-mute);
      max-width: 680px;
      margin-bottom: 36px;
      line-height: 1.7;
    }
    .domain-section .section-intro strong, .crdt-section .section-intro strong { color: var(--text); }
    .domain-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 20px;
    }
    .domain-card {
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--bg2);
      overflow: hidden;
      transition: border-color 0.2s;
    }
    .domain-card:hover { border-color: var(--accent); }
    .domain-card.active-domain { border-color: rgba(249,168,37,0.5); }
    .domain-card.scaffold-domain { border-style: dashed; opacity: 0.85; }
    .domain-card-header {
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 10px;
      background: var(--bg3);
    }
    .domain-badge {
      font-family: var(--font-mono);
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 4px;
      background: rgba(79,142,247,0.12);
      border: 1px solid rgba(79,142,247,0.3);
      color: var(--accent2);
    }
    .domain-badge.active { background: rgba(249,168,37,0.12); border-color: rgba(249,168,37,0.4); color: #f9a825; }
    .domain-name {
      font-weight: 700;
      font-size: 15px;
      font-family: var(--font-mono);
      color: var(--text);
    }
    .domain-active-dot {
      margin-left: auto;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--green);
    }
    .domain-card-body { padding: 14px 16px; }
    .domain-desc {
      font-size: 13px;
      color: var(--text-mute);
      margin-bottom: 12px;
      line-height: 1.5;
    }
    .domain-caps {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 12px;
    }
    .cap-pill {
      font-size: 10px;
      padding: 2px 8px;
      border-radius: 12px;
      border: 1px solid var(--border);
      color: var(--text-mute);
      background: var(--bg3);
    }
    .cap-pill.cap-crdt { border-color: rgba(188,140,255,0.4); color: var(--purple); background: rgba(188,140,255,0.08); }
    .cap-pill.cap-ot { border-color: rgba(88,166,255,0.4); color: var(--accent2); background: rgba(88,166,255,0.08); }
    .cap-pill.cap-schema { border-color: rgba(63,185,80,0.4); color: var(--green); background: rgba(63,185,80,0.08); }
    .cap-pill.cap-delta { border-color: rgba(249,168,37,0.4); color: #f9a825; background: rgba(249,168,37,0.08); }
    .domain-dims {
      font-size: 11px;
      color: var(--text-dim);
    }
    .domain-dims strong { color: var(--text-mute); }
    .domain-new-card {
      border: 2px dashed var(--border);
      border-radius: var(--radius);
      background: transparent;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 32px 20px;
      text-align: center;
      gap: 12px;
      transition: border-color 0.2s;
      cursor: default;
    }
    .domain-new-card:hover { border-color: var(--accent); }
    .domain-new-icon { font-size: 28px; color: var(--text-dim); }
    .domain-new-title { font-size: 14px; font-weight: 600; color: var(--text-mute); }
    .domain-new-cmd {
      font-family: var(--font-mono);
      font-size: 12px;
      background: var(--bg3);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 6px 12px;
      color: var(--accent2);
    }
    .domain-new-link {
      font-size: 11px;
      color: var(--text-dim);
    }
    .domain-new-link a { color: var(--accent); text-decoration: none; }
    .domain-new-link a:hover { text-decoration: underline; }

    /* ---- CRDT Primitives section ---- */
    .crdt-section {
      background: var(--bg2);
      border-top: 1px solid var(--border);
      padding: 60px 40px;
    }
    .crdt-inner { max-width: 1100px; margin: 0 auto; }
    .crdt-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 20px;
    }
    .crdt-card {
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--bg);
      overflow: hidden;
      transition: border-color 0.2s;
    }
    .crdt-card:hover { border-color: var(--purple); }
    .crdt-card-header {
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      background: rgba(188,140,255,0.06);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .crdt-type-badge {
      font-family: var(--font-mono);
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 4px;
      background: rgba(188,140,255,0.12);
      border: 1px solid rgba(188,140,255,0.3);
      color: var(--purple);
    }
    .crdt-card-title { font-weight: 700; font-size: 14px; color: var(--text); }
    .crdt-card-sub { font-size: 11px; color: var(--text-mute); }
    .crdt-card-body { padding: 14px 16px; }
    .crdt-output {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--text-mute);
      white-space: pre-wrap;
      line-height: 1.6;
      background: var(--bg3);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 10px 12px;
    }
    .crdt-output .out-win { color: var(--green); }
    .crdt-output .out-key { color: var(--accent2); }

    /* ---- Architecture section ---- */
    .arch-section {
      background: var(--bg2);
      border-top: 1px solid var(--border);
      padding: 48px 40px;
    }
    .arch-inner { max-width: 1100px; margin: 0 auto; }
    .arch-section h2 {
      font-size: 22px;
      font-weight: 700;
      margin-bottom: 8px;
      color: var(--text);
    }
    .arch-section .section-intro {
      color: var(--text-mute);
      max-width: 680px;
      margin-bottom: 40px;
      line-height: 1.7;
    }
    .arch-section .section-intro strong { color: var(--text); }
    .arch-content {
      display: grid;
      grid-template-columns: 380px 1fr;
      gap: 48px;
      align-items: start;
    }

    /* Architecture flow diagram */
    .arch-flow {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0;
    }
    .arch-row { width: 100%; display: flex; justify-content: center; }
    .plugins-row { gap: 8px; flex-wrap: wrap; }
    .arch-box {
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 12px 16px;
      background: var(--bg3);
      width: 100%;
      max-width: 340px;
      transition: border-color 0.2s;
    }
    .arch-box:hover { border-color: var(--accent); }
    .arch-box.cli { border-color: rgba(79,142,247,0.4); }
    .arch-box.registry { border-color: rgba(188,140,255,0.3); }
    .arch-box.core { border-color: rgba(63,185,80,0.3); background: rgba(63,185,80,0.05); }
    .arch-box.protocol { border-color: rgba(79,142,247,0.5); background: rgba(79,142,247,0.05); }
    .arch-box.plugin { max-width: 160px; width: auto; flex: 1; }
    .arch-box.plugin.active { border-color: rgba(249,168,37,0.5); background: rgba(249,168,37,0.05); }
    .arch-box.plugin.planned { opacity: 0.6; border-style: dashed; }
    .box-title { font-weight: 600; font-size: 13px; color: var(--text); }
    .box-sub { font-size: 11px; color: var(--text-mute); margin-top: 3px; }
    .box-detail { font-size: 10px; color: var(--text-dim); margin-top: 4px; line-height: 1.5; }
    .arch-connector {
      display: flex;
      flex-direction: column;
      align-items: center;
      height: 24px;
      color: var(--border);
    }
    .connector-line { width: 1px; flex: 1; background: var(--border); }
    .connector-arrow { font-size: 10px; }

    /* Protocol table */
    .protocol-table { border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
    .proto-row {
      display: grid;
      grid-template-columns: 80px 220px 1fr;
      gap: 0;
      border-bottom: 1px solid var(--border);
    }
    .proto-row:last-child { border-bottom: none; }
    .proto-row.header { background: var(--bg3); }
    .proto-row > div { padding: 10px 14px; }
    .proto-method {
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--accent2);
      font-weight: 600;
      border-right: 1px solid var(--border);
    }
    .proto-sig {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--text-mute);
      border-right: 1px solid var(--border);
      word-break: break-all;
    }
    .proto-desc { font-size: 12px; color: var(--text-mute); }
    .proto-row.header .proto-method,
    .proto-row.header .proto-sig,
    .proto-row.header .proto-desc {
      font-family: var(--font-ui);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: var(--text-dim);
    }

    /* ---- Footer ---- */
    footer {
      background: var(--bg);
      border-top: 1px solid var(--border);
      padding: 16px 40px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      color: var(--text-dim);
    }
    footer a { color: var(--accent2); text-decoration: none; }
    footer a:hover { text-decoration: underline; }

    /* ---- Scrollbar ---- */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: var(--bg); }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--text-dim); }

    /* ---- Tooltip ---- */
    .tooltip {
      position: fixed;
      background: var(--bg2);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 10px 14px;
      font-size: 12px;
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.15s;
      z-index: 100;
      max-width: 280px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.4);
    }
    .tooltip.visible { opacity: 1; }
    .tip-id { font-family: var(--font-mono); font-size: 11px; color: var(--accent2); margin-bottom: 4px; }
    .tip-msg { color: var(--text); margin-bottom: 4px; }
    .tip-branch { font-size: 11px; margin-bottom: 4px; }
    .tip-files { font-size: 11px; color: var(--text-mute); font-family: var(--font-mono); }

    /* ---- Dimension dots on DAG nodes ---- */
    .dim-dots { pointer-events: none; }

    /* ---- Dimension State Matrix section ---- */
    .dim-section {
      background: var(--bg);
      border-top: 2px solid var(--border);
      padding: 28px 40px 32px;
    }
    .dim-inner { max-width: 1200px; margin: 0 auto; }
    .dim-section-header { display:flex; align-items:baseline; gap:14px; margin-bottom:6px; }
    .dim-section h2 { font-size:16px; font-weight:700; color:var(--text); }
    .dim-section .dim-tagline { font-size:12px; color:var(--text-mute); }
    .dim-matrix-wrap { overflow-x:auto; margin-top:18px; padding-bottom:4px; }
    .dim-matrix { display:table; border-collapse:separate; border-spacing:0; min-width:100%; }
    .dim-matrix-row { display:table-row; }
    .dim-label-cell {
      display:table-cell; padding:6px 14px 6px 0;
      font-size:11px; font-weight:600; color:var(--text-mute);
      text-transform:uppercase; letter-spacing:0.6px;
      white-space:nowrap; vertical-align:middle; min-width:100px;
    }
    .dim-label-dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; vertical-align:middle; }
    .dim-cell { display:table-cell; padding:4px 3px; vertical-align:middle; text-align:center; min-width:46px; }
    .dim-cell-inner {
      width:38px; height:28px; border-radius:5px; margin:0 auto;
      display:flex; align-items:center; justify-content:center;
      font-size:11px; font-weight:700;
      transition:transform 0.2s, box-shadow 0.2s;
      cursor:default;
      background:var(--bg3); border:1px solid transparent; color:transparent;
    }
    .dim-cell-inner.active { border-color:currentColor; }
    .dim-cell-inner.conflict-dim { box-shadow:0 0 0 2px #f85149; }
    .dim-cell-inner.col-highlight { transform:scaleY(1.12); box-shadow:0 0 14px 2px rgba(255,255,255,0.12); }
    .dim-commit-cell {
      display:table-cell; padding:8px 3px 0; text-align:center;
      font-size:9px; font-family:var(--font-mono); color:var(--text-dim);
      vertical-align:top; transition:color 0.2s;
    }
    .dim-commit-cell.col-highlight { color:var(--accent2); font-weight:700; }
    .dim-commit-label { display:table-cell; padding-top:10px; vertical-align:top; }
    .dim-legend { display:flex; gap:18px; margin-top:18px; flex-wrap:wrap; font-size:11px; color:var(--text-mute); }
    .dim-legend-item { display:flex; align-items:center; gap:6px; }
    .dim-legend-swatch { width:22px; height:14px; border-radius:3px; border:1px solid currentColor; display:inline-block; }
    .dim-conflict-note {
      margin-top:16px; padding:12px 16px;
      background:rgba(248,81,73,0.08); border:1px solid rgba(248,81,73,0.25);
      border-radius:6px; font-size:12px; color:var(--text-mute);
    }
    .dim-conflict-note strong { color:var(--red); }
    .dim-conflict-note em { color:var(--green); font-style:normal; }

    /* ---- Dimension pills in the operation log ---- */
    .dim-pills { display:flex; flex-wrap:wrap; gap:3px; margin-top:4px; }
    .dim-pill {
      display:inline-block; padding:1px 6px; border-radius:10px;
      font-size:9px; font-weight:700; letter-spacing:0.4px; text-transform:uppercase;
      border:1px solid currentColor; opacity:0.85;
    }
    .dim-pill.conflict-pill { background:rgba(248,81,73,0.2); color:var(--red) !important; }
  </style>
</head>
<body>

<header>
  <div class="header-top">
    <h1>muse</h1>
    <span class="tagline">Demo · domain-agnostic version control for multidimensional state</span>
    <span class="version-badge">v{{VERSION}} · {{DOMAIN}} domain · {{ELAPSED}}s</span>
    <a class="header-nav-link" href="domain_registry.html">Domain Registry →</a>
  </div>
  <div class="stats-bar">
    <div class="stat"><span class="stat-num">{{COMMITS}}</span><span class="stat-label">Commits</span></div>
    <div class="stat-sep">·</div>
    <div class="stat"><span class="stat-num">{{BRANCHES}}</span><span class="stat-label">Branches</span></div>
    <div class="stat-sep">·</div>
    <div class="stat"><span class="stat-num">{{MERGES}}</span><span class="stat-label">Merges</span></div>
    <div class="stat-sep">·</div>
    <div class="stat"><span class="stat-num">{{CONFLICTS}}</span><span class="stat-label">Conflicts Resolved</span></div>
    <div class="stat-sep">·</div>
    <div class="stat"><span class="stat-num">{{OPS}}</span><span class="stat-label">Operations</span></div>
  </div>
</header>

<div class="main-container">
  <div class="dag-panel">
    <div class="dag-header">
      <h2>Commit Graph</h2>
      <div class="controls">
        <button class="btn primary" id="btn-play">&#9654; Play Tour</button>
        <button class="btn" id="btn-prev" title="Previous step (←)">&#9664;</button>
        <button class="btn" id="btn-next" title="Next step (→)">&#9654;</button>
        <button class="btn" id="btn-reset">&#8635; Reset</button>
        <span class="step-counter" id="step-counter"></span>
      </div>
    </div>
    <div class="dag-scroll" id="dag-scroll">
      <svg id="dag-svg"></svg>
    </div>
    <div class="branch-legend" id="branch-legend"></div>
  </div>

  <div class="log-panel">
    <div class="log-header"><h2>Operation Log</h2></div>
    <div class="act-jump-bar" id="act-jump-bar"></div>
    <div class="log-scroll" id="log-scroll">
      <div id="event-list"></div>
    </div>
  </div>
</div>


<div class="dim-section">
  <div class="dim-inner">
    <div class="dim-section-header">
      <h2>Dimension State Matrix</h2>
      <span class="dim-tagline">
        Unlike Git (binary file conflicts), Muse merges each orthogonal dimension independently —
        only conflicting dimensions require human resolution.
      </span>
    </div>
    <div class="dim-matrix-wrap">
      <div class="dim-matrix" id="dim-matrix"></div>
    </div>
    <div class="dim-legend">
      <div class="dim-legend-item"><span class="dim-legend-swatch" style="background:rgba(188,140,255,0.35);color:#bc8cff"></span> Melodic</div>
      <div class="dim-legend-item"><span class="dim-legend-swatch" style="background:rgba(63,185,80,0.35);color:#3fb950"></span> Rhythmic</div>
      <div class="dim-legend-item"><span class="dim-legend-swatch" style="background:rgba(88,166,255,0.35);color:#58a6ff"></span> Harmonic</div>
      <div class="dim-legend-item"><span class="dim-legend-swatch" style="background:rgba(249,168,37,0.35);color:#f9a825"></span> Dynamic</div>
      <div class="dim-legend-item"><span class="dim-legend-swatch" style="background:rgba(239,83,80,0.35);color:#ef5350"></span> Structural</div>
      <div class="dim-legend-item" style="margin-left:8px"><span style="display:inline-block;width:22px;height:14px;border-radius:3px;border:2px solid #f85149;vertical-align:middle;margin-right:6px"></span> Conflict (required resolution)</div>
      <div class="dim-legend-item"><span style="display:inline-block;width:22px;height:14px;border-radius:3px;background:var(--bg3);border:1px solid var(--border);vertical-align:middle;margin-right:6px"></span> Unchanged</div>
    </div>
    <div class="dim-conflict-note">
      <strong>⚡ Merge conflict (shared-state.mid)</strong> — shared-state.mid had both-sides changes in
      <strong style="color:#ef5350">structural</strong> (manual resolution required).
      <em>✓ melodic auto-merged from left</em> · <em>✓ harmonic auto-merged from right</em> —
      only 1 of 5 dimensions conflicted. Git would have flagged the entire file as a conflict.
    </div>
  </div>
</div>

<div class="arch-section">
  <div class="arch-inner">
    <h2>How Muse Works</h2>
    <p class="section-intro">
      Muse is a version control system for <strong>state</strong> — any multidimensional
      state that can be snapshotted, diffed, and merged. The core engine provides
      the DAG, content-addressed storage, branching, merging, time-travel, and
      conflict resolution. A domain plugin implements <strong>6 methods</strong> and
      gets everything else for free.
      <br><br>
      Music is the reference implementation. Genomics sequences, scientific simulation
      frames, 3D spatial fields, and financial time-series are all the same pattern.
    </p>
    <div class="arch-content">
      {{ARCH_HTML}}
    </div>
  </div>
</div>

<div class="registry-callout">
  <div class="registry-callout-inner">
    <div class="registry-callout-text">
      <div class="registry-callout-title">Want to version something else?</div>
      <div class="registry-callout-sub">
        Music is the reference implementation. The same engine works for genomics,
        3D spatial fields, financial models, and any multidimensional state —
        six methods between you and a complete VCS.
      </div>
    </div>
    <a class="registry-callout-btn" href="domain_registry.html">
      Domain Registry &amp; Plugin Guide →
    </a>
  </div>
</div>

<footer>
  <span>Generated {{GENERATED_AT}} · {{ELAPSED}}s · {{OPS}} operations</span>
  <span><a href="https://github.com/cgcardona/muse">github.com/cgcardona/muse</a></span>
</footer>

<div class="tooltip" id="tooltip">
  <div class="tip-id" id="tip-id"></div>
  <div class="tip-msg" id="tip-msg"></div>
  <div class="tip-branch" id="tip-branch"></div>
  <div class="tip-files" id="tip-files"></div>
  <div id="tip-dims" style="margin-top:6px;font-size:10px;line-height:1.8"></div>
</div>

{{D3_SCRIPT}}

<script>
/* ===== Embedded tour data ===== */
const DATA = {{DATA_JSON}};

/* ===== Constants ===== */
const ROW_H   = 62;
const COL_W   = 90;
const PAD     = { top: 30, left: 55, right: 160 };
const R_NODE  = 11;
const BRANCH_ORDER = ['main','alpha','beta','gamma','conflict/left','conflict/right'];
const PLAY_INTERVAL_MS = 1200;

/* ===== Dimension data ===== */
const DIM_COLORS = {
  melodic:    '#bc8cff',
  rhythmic:   '#3fb950',
  harmonic:   '#58a6ff',
  dynamic:    '#f9a825',
  structural: '#ef5350',
};
const DIMS = ['melodic','rhythmic','harmonic','dynamic','structural'];

// Commit message → dimension mapping (stable across re-runs, independent of hash)
function getDims(commit) {
  const m = (commit.message || '').toLowerCase();
  if (m.includes('root') || m.includes('initial state'))
    return ['melodic','rhythmic','harmonic','dynamic','structural'];
  if (m.includes('layer 1') || m.includes('rhythmic dimension'))
    return ['rhythmic','structural'];
  if (m.includes('layer 2') || m.includes('harmonic dimension'))
    return ['harmonic','structural'];
  if (m.includes('texture pattern a') || m.includes('sparse'))
    return ['melodic','rhythmic'];
  if (m.includes('texture pattern b') || m.includes('dense'))
    return ['melodic','dynamic'];
  if (m.includes('syncopated'))
    return ['rhythmic','dynamic'];
  if (m.includes('descending'))
    return ['melodic','harmonic'];
  if (m.includes('ascending'))
    return ['melodic'];
  if (m.includes("merge branch 'beta'"))
    return ['rhythmic','dynamic'];
  if (m.includes('left:') || m.includes('version a'))
    return ['melodic','structural'];
  if (m.includes('right:') || m.includes('version b'))
    return ['harmonic','structural'];
  if (m.includes('resolve') || m.includes('reconciled'))
    return ['structural'];
  if (m.includes('cherry-pick') || m.includes('cherry pick'))
    return ['melodic'];
  if (m.includes('revert'))
    return ['melodic'];
  return [];
}

function getConflicts(commit) {
  const m = (commit.message || '').toLowerCase();
  if (m.includes('resolve') && m.includes('reconciled')) return ['structural'];
  return [];
}

// Build per-short-ID lookup tables once the DATA is available (populated at init)
const DIM_DATA = {};
const DIM_CONFLICTS = {};
function _initDimMaps() {
  DATA.dag.commits.forEach(c => {
    DIM_DATA[c.short]     = getDims(c);
    DIM_CONFLICTS[c.short] = getConflicts(c);
  });
  // Also key by the short prefix used in events (some may be truncated)
  DATA.events.forEach(ev => {
    if (ev.commit_id && !DIM_DATA[ev.commit_id]) {
      const full = DATA.dag.commits.find(c => c.short.startsWith(ev.commit_id) || ev.commit_id.startsWith(c.short));
      if (full) {
        DIM_DATA[ev.commit_id]     = getDims(full);
        DIM_CONFLICTS[ev.commit_id] = getConflicts(full);
      }
    }
  });
}


/* ===== State ===== */
let currentStep = -1;
let isPlaying   = false;
let playTimer   = null;

/* ===== Utilities ===== */
function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

/* ===== Topological sort ===== */
function topoSort(commits) {
  const map = new Map(commits.map(c => [c.id, c]));
  const visited = new Set();
  const result = [];
  function visit(id) {
    if (visited.has(id)) return;
    visited.add(id);
    const c = map.get(id);
    if (!c) return;
    (c.parents || []).forEach(pid => visit(pid));
    result.push(c);
  }
  commits.forEach(c => visit(c.id));
  // Oldest commit at row 0 (top of DAG); newest at the bottom so the DAG
  // scrolls down in sync with the operation log during playback.
  return result;
}

/* ===== Layout ===== */
function computeLayout(commits) {
  const sorted = topoSort(commits);
  const branchCols = {};
  let nextCol = 0;
  // Assign columns in BRANCH_ORDER first, then any extras
  BRANCH_ORDER.forEach(b => { branchCols[b] = nextCol++; });
  commits.forEach(c => {
    if (!(c.branch in branchCols)) branchCols[c.branch] = nextCol++;
  });
  const numCols = nextCol;
  const positions = new Map();
  sorted.forEach((c, i) => {
    positions.set(c.id, {
      x: PAD.left + (branchCols[c.branch] || 0) * COL_W,
      y: PAD.top  + i * ROW_H,
      row: i,
      col: branchCols[c.branch] || 0,
    });
  });
  const svgW = PAD.left + numCols * COL_W + PAD.right;
  const svgH = PAD.top  + sorted.length * ROW_H + PAD.top;
  return { sorted, positions, branchCols, svgW, svgH };
}

/* ===== Draw DAG ===== */
function drawDAG() {
  const { dag, dag: { commits, branches } } = DATA;
  if (!commits.length) return;

  const layout = computeLayout(commits);
  const { sorted, positions, svgW, svgH } = layout;
  const branchColor = new Map(branches.map(b => [b.name, b.color]));
  const commitMap   = new Map(commits.map(c => [c.id, c]));

  const svg = d3.select('#dag-svg')
    .attr('width', svgW)
    .attr('height', svgH);

  // ---- Edges ----
  const edgeG = svg.append('g').attr('class', 'edges');
  sorted.forEach(commit => {
    const pos = positions.get(commit.id);
    (commit.parents || []).forEach((pid, pIdx) => {
      const ppos = positions.get(pid);
      if (!pos || !ppos) return;
      const color = pIdx === 0
        ? (branchColor.get(commit.branch) || '#555')
        : (branchColor.get(commitMap.get(pid)?.branch || '') || '#555');

      let pathStr;
      if (Math.abs(pos.x - ppos.x) < 4) {
        // Same column → straight line
        pathStr = `M${pos.x},${pos.y} L${ppos.x},${ppos.y}`;
      } else {
        // Different columns → S-curve bezier
        const mid = (pos.y + ppos.y) / 2;
        pathStr = `M${pos.x},${pos.y} C${pos.x},${mid} ${ppos.x},${mid} ${ppos.x},${ppos.y}`;
      }
      edgeG.append('path')
        .attr('d', pathStr)
        .attr('stroke', color)
        .attr('stroke-width', 1.8)
        .attr('fill', 'none')
        .attr('opacity', 0.45)
        .attr('class', `edge-from-${commit.id.slice(0,8)}`);
    });
  });

  // ---- Nodes ----
  const nodeG = svg.append('g').attr('class', 'nodes');
  const tooltip = document.getElementById('tooltip');

  sorted.forEach(commit => {
    const pos = positions.get(commit.id);
    if (!pos) return;
    const color   = branchColor.get(commit.branch) || '#78909c';
    const isMerge = (commit.parents || []).length >= 2;

    const g = nodeG.append('g')
      .attr('class', 'commit-node')
      .attr('data-id', commit.id)
      .attr('data-short', commit.short)
      .attr('transform', `translate(${pos.x},${pos.y})`);

    if (isMerge) {
      g.append('circle')
        .attr('r', R_NODE + 6)
        .attr('fill', 'none')
        .attr('stroke', color)
        .attr('stroke-width', 1.5)
        .attr('opacity', 0.35);
    }

    g.append('circle')
      .attr('r', R_NODE)
      .attr('fill', color)
      .attr('stroke', '#0d1117')
      .attr('stroke-width', 2);

    // Short ID
    g.append('text')
      .attr('x', R_NODE + 7)
      .attr('y', 0)
      .attr('dy', '0.35em')
      .attr('class', 'commit-label')
      .text(commit.short);

    // Message (truncated)
    const maxLen = 38;
    const msg = commit.message.length > maxLen
      ? commit.message.slice(0, maxLen) + '…'
      : commit.message;
    g.append('text')
      .attr('x', R_NODE + 7)
      .attr('y', 13)
      .attr('class', 'commit-msg')
      .text(msg);


    // Dimension dots below node
    const dims = DIM_DATA[commit.short] || [];
    if (dims.length > 0) {
      const dotR = 4, dotSp = 11;
      const totalW = (DIMS.length - 1) * dotSp;
      const dotsG = g.append('g')
        .attr('class', 'dim-dots')
        .attr('transform', `translate(${-totalW/2},${R_NODE + 9})`);
      DIMS.forEach((dim, di) => {
        const active = dims.includes(dim);
        const isConf = (DIM_CONFLICTS[commit.short] || []).includes(dim);
        dotsG.append('circle')
          .attr('cx', di * dotSp).attr('cy', 0).attr('r', dotR)
          .attr('fill', active ? DIM_COLORS[dim] : '#21262d')
          .attr('stroke', isConf ? '#f85149' : (active ? DIM_COLORS[dim] : '#30363d'))
          .attr('stroke-width', isConf ? 1.5 : 0.8)
          .attr('opacity', active ? 1 : 0.35);
      });
    }

    // Hover tooltip
    g.on('mousemove', (event) => {
      tooltip.classList.add('visible');
      document.getElementById('tip-id').textContent    = commit.id;
      document.getElementById('tip-msg').textContent   = commit.message;
      document.getElementById('tip-branch').innerHTML  =
        `<span style="color:${color}">⬤</span> ${commit.branch}`;
      document.getElementById('tip-files').textContent =
        commit.files.length
          ? commit.files.join('\\n')
          : '(empty snapshot)';
      const tipDims = DIM_DATA[commit.short] || [];
      const tipConf = DIM_CONFLICTS[commit.short] || [];
      const tipDimEl = document.getElementById('tip-dims');
      if (tipDimEl) {
        tipDimEl.innerHTML = tipDims.length
          ? tipDims.map(d => {
              const c = tipConf.includes(d);
              return `<span style="color:${DIM_COLORS[d]};margin-right:6px">● ${d}${c?' ⚡':''}</span>`;
            }).join('')
          : '';
      }
      tooltip.style.left = (event.clientX + 12) + 'px';
      tooltip.style.top  = (event.clientY - 10) + 'px';
    }).on('mouseleave', () => {
      tooltip.classList.remove('visible');
    });
  });

  // ---- Branch legend ----
  const legend = document.getElementById('branch-legend');
  DATA.dag.branches.forEach(b => {
    const item = document.createElement('div');
    item.className = 'legend-item';
    item.innerHTML =
      `<span class="legend-dot" style="background:${b.color}"></span>` +
      `<span>${escHtml(b.name)}</span>`;
    legend.appendChild(item);
  });
}

/* ===== Act metadata ===== */
const ACT_ICONS = {
  1:'🎵', 2:'🌿', 3:'⚡', 4:'🔀', 5:'⏪',
};
const ACT_COLORS = {
  1:'#4f8ef7', 2:'#3fb950', 3:'#f85149', 4:'#ab47bc', 5:'#f9a825',
};

/* ===== Act jump navigation ===== */
function buildActJumpBar() {
  const bar = document.getElementById('act-jump-bar');
  if (!bar) return;

  const lbl = document.createElement('span');
  lbl.textContent = 'Jump:';
  bar.appendChild(lbl);

  // Collect unique acts
  const acts = [];
  let last = -1;
  DATA.events.forEach(ev => {
    if (ev.act !== last) { acts.push({ num: ev.act, title: ev.act_title }); last = ev.act; }
  });

  acts.forEach(a => {
    const btn = document.createElement('button');
    btn.className = 'act-jump-btn';
    btn.title = `Jump to Act ${a.num}: ${a.title}`;
    const icon = ACT_ICONS[a.num] || '';
    btn.innerHTML = `${icon} ${a.num}`;
    if (a.num >= 6) btn.style.borderColor = ACT_COLORS[a.num] + '66';
    btn.addEventListener('click', () => {
      pauseTour();
      // Find first event index for this act
      const idx = DATA.events.findIndex(ev => ev.act === a.num);
      if (idx >= 0) {
        // Reveal up to this point
        revealStep(idx);
        // Scroll the act header into view
        const hdr = document.getElementById(`act-hdr-${a.num}`);
        if (hdr) hdr.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
    bar.appendChild(btn);
  });

  // Reveal All button
  const allBtn = document.createElement('button');
  allBtn.className = 'act-jump-btn reveal-all';
  allBtn.textContent = '✦ Reveal All';
  allBtn.title = 'Reveal all 69 events at once';
  allBtn.addEventListener('click', () => {
    pauseTour();
    revealStep(DATA.events.length - 1);
  });
  bar.appendChild(allBtn);
}

/* ===== Event log ===== */
function buildEventLog() {
  const list = document.getElementById('event-list');
  let lastAct = -1;

  DATA.events.forEach((ev, idx) => {
    if (ev.act !== lastAct) {
      lastAct = ev.act;

      // Act header — always visible (no opacity fade)
      const hdr = document.createElement('div');
      hdr.className = 'act-header';
      hdr.id = `act-hdr-${ev.act}`;
      const icon = ACT_ICONS[ev.act] || '';
      const col  = ACT_COLORS[ev.act] || 'var(--text-dim)';
      hdr.innerHTML =
        `<span style="color:${col};margin-right:6px">${icon}</span>` +
        `Act ${ev.act} <span style="opacity:0.6">—</span> ${ev.act_title}`;
      if (ev.act >= 6) {
        hdr.style.color = col;
        hdr.style.borderTop = `1px solid ${col}33`;
      }
      list.appendChild(hdr);
    }

    const isCliCmd = ev.cmd.startsWith('muse ') || ev.cmd.startsWith('git ');

    const item = document.createElement('div');
    item.className = 'event-item';
    item.id = `ev-${idx}`;

    if (ev.exit_code !== 0 && ev.output.toLowerCase().includes('conflict')) {
      item.classList.add('failed');
    }

    // Parse cmd
    const parts   = ev.cmd.split(' ');
    const cmdName = parts.slice(0, 2).join(' ');
    const cmdArgs = parts.slice(2).join(' ');

    // Output class
    let outClass = '';
    if (ev.output.toLowerCase().includes('conflict')) outClass = 'conflict';
    else if (ev.exit_code === 0 && ev.commit_id) outClass = 'success';

    const outLines = ev.output.split('\\n').slice(0, 6).join('\\n');

    const cmdLine =
      `<div class="event-cmd">` +
        `<span class="cmd-prefix">$ </span>` +
        `<span class="cmd-name">${escHtml(cmdName)}</span>` +
        (cmdArgs
          ? ` <span class="cmd-args">${escHtml(cmdArgs.slice(0, 80))}${cmdArgs.length > 80 ? '…' : ''}</span>`
          : '') +
      `</div>`;

    item.innerHTML =
      cmdLine +
      (outLines
        ? `<div class="event-output ${outClass}">${escHtml(outLines)}</div>`
        : '') +
      (() => {
        if (!ev.commit_id) return '';
        const dims = DIM_DATA[ev.commit_id] || [];
        const conf = DIM_CONFLICTS[ev.commit_id] || [];
        if (!dims.length) return '';
        return '<div class="dim-pills">' + dims.map(d => {
          const isc = conf.includes(d);
          const col = DIM_COLORS[d];
          const cls = isc ? 'dim-pill conflict-pill' : 'dim-pill';
          const sty = isc ? '' : `color:${col};border-color:${col};background:${col}22`;
          return `<span class="${cls}" style="${sty}">${isc ? '⚡ ' : ''}${d}</span>`;
        }).join('') + '</div>';
      })() +
      `<div class="event-meta">` +
        (ev.commit_id ? `<span class="tag-commit">${escHtml(ev.commit_id)}</span>` : '') +
        `<span class="tag-time">${ev.duration_ms}ms</span>` +
      `</div>`;

    list.appendChild(item);
  });
}



/* ===== Dimension Timeline ===== */
function buildDimTimeline() {
  const matrix = document.getElementById('dim-matrix');
  if (!matrix) return;
  const sorted = topoSort(DATA.dag.commits);

  // Commit ID header row
  const hrow = document.createElement('div');
  hrow.className = 'dim-matrix-row';
  const sp = document.createElement('div');
  sp.className = 'dim-label-cell';
  hrow.appendChild(sp);
  sorted.forEach(c => {
    const cell = document.createElement('div');
    cell.className = 'dim-commit-cell';
    cell.id = `dim-col-label-${c.short}`;
    cell.title = c.message;
    cell.textContent = c.short.slice(0,6);
    hrow.appendChild(cell);
  });
  matrix.appendChild(hrow);

  // One row per dimension
  DIMS.forEach(dim => {
    const row = document.createElement('div');
    row.className = 'dim-matrix-row';
    const lbl = document.createElement('div');
    lbl.className = 'dim-label-cell';
    const dot = document.createElement('span');
    dot.className = 'dim-label-dot';
    dot.style.background = DIM_COLORS[dim];
    lbl.appendChild(dot);
    lbl.appendChild(document.createTextNode(dim.charAt(0).toUpperCase() + dim.slice(1)));
    row.appendChild(lbl);

    sorted.forEach(c => {
      const dims = DIM_DATA[c.short] || [];
      const conf = DIM_CONFLICTS[c.short] || [];
      const active = dims.includes(dim);
      const isConf = conf.includes(dim);
      const col = DIM_COLORS[dim];
      const cell = document.createElement('div');
      cell.className = 'dim-cell';
      const inner = document.createElement('div');
      inner.className = 'dim-cell-inner' + (active ? ' active' : '') + (isConf ? ' conflict-dim' : '');
      inner.id = `dim-cell-${dim}-${c.short}`;
      if (active) {
        inner.style.background = col + '33';
        inner.style.color = col;
        inner.textContent = isConf ? '⚡' : '●';
      }
      cell.appendChild(inner);
      row.appendChild(cell);
    });
    matrix.appendChild(row);
  });
}

function highlightDimColumn(shortId) {
  document.querySelectorAll('.dim-commit-cell.col-highlight, .dim-cell-inner.col-highlight')
    .forEach(el => el.classList.remove('col-highlight'));
  if (!shortId) return;
  const lbl = document.getElementById(`dim-col-label-${shortId}`);
  if (lbl) {
    lbl.classList.add('col-highlight');
    lbl.scrollIntoView({ behavior:'smooth', block:'nearest', inline:'center' });
  }
  DIMS.forEach(dim => {
    const cell = document.getElementById(`dim-cell-${dim}-${shortId}`);
    if (cell) cell.classList.add('col-highlight');
  });
}

/* ===== Replay animation ===== */
function revealStep(stepIdx) {
  if (stepIdx < 0 || stepIdx >= DATA.events.length) return;

  const ev = DATA.events[stepIdx];

  // Reveal all events up to this step
  for (let i = 0; i <= stepIdx; i++) {
    const el = document.getElementById(`ev-${i}`);
    if (el) el.classList.add('revealed');
  }

  // Mark current as active (remove previous)
  document.querySelectorAll('.event-item.active').forEach(el => el.classList.remove('active'));
  const cur = document.getElementById(`ev-${stepIdx}`);
  if (cur) {
    cur.classList.add('active');
    cur.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // Highlight commit node
  document.querySelectorAll('.commit-node.highlighted').forEach(el => el.classList.remove('highlighted'));
  if (ev.commit_id) {
    const node = document.querySelector(`.commit-node[data-short="${ev.commit_id}"]`);
    if (node) {
      node.classList.add('highlighted');
      // Scroll DAG to show the node
      const transform = node.getAttribute('transform');
      if (transform) {
        const m = transform.match(/translate\\(([\\d.]+),([\\d.]+)\\)/);
        if (m) {
          const scroll = document.getElementById('dag-scroll');
          const y = parseFloat(m[2]);
          scroll.scrollTo({ top: Math.max(0, y - 200), behavior: 'smooth' });
        }
      }
    }
  }

  // Highlight dimension matrix column
  highlightDimColumn(ev.commit_id || null);

  // Update counter and step button states
  document.getElementById('step-counter').textContent =
    `Step ${stepIdx + 1} / ${DATA.events.length}`;
  document.getElementById('btn-prev').disabled = (stepIdx === 0);
  document.getElementById('btn-next').disabled = (stepIdx === DATA.events.length - 1);

  currentStep = stepIdx;
}

function playTour() {
  if (isPlaying) return;
  isPlaying = true;
  document.getElementById('btn-play').textContent = '⏸ Pause';

  function advance() {
    if (!isPlaying) return;
    const next = currentStep + 1;
    if (next >= DATA.events.length) {
      pauseTour();
      document.getElementById('btn-play').textContent = '✓ Done';
      return;
    }
    revealStep(next);
    playTimer = setTimeout(advance, PLAY_INTERVAL_MS);
  }
  advance();
}

function pauseTour() {
  isPlaying = false;
  clearTimeout(playTimer);
  document.getElementById('btn-play').textContent = '▶ Play Tour';
  highlightDimColumn(null);
}

function resetTour() {
  pauseTour();
  currentStep = -1;
  document.querySelectorAll('.event-item').forEach(el => {
    el.classList.remove('revealed','active');
  });
  document.querySelectorAll('.commit-node.highlighted').forEach(el => {
    el.classList.remove('highlighted');
  });
  document.getElementById('step-counter').textContent = '';
  document.getElementById('log-scroll').scrollTop = 0;
  document.getElementById('dag-scroll').scrollTop = 0;
  document.getElementById('btn-play').textContent = '▶ Play Tour';
  document.getElementById('btn-prev').disabled = true;
  document.getElementById('btn-next').disabled = false;
  highlightDimColumn(null);
}

/* ===== Init ===== */
document.addEventListener('DOMContentLoaded', () => {
  _initDimMaps();
  drawDAG();
  buildEventLog();
  buildActJumpBar();
  buildDimTimeline();

  document.getElementById('btn-prev').disabled = true;  // nothing to go back to yet

  document.getElementById('btn-play').addEventListener('click', () => {
    if (isPlaying) pauseTour(); else playTour();
  });
  document.getElementById('btn-prev').addEventListener('click', () => {
    pauseTour();
    if (currentStep > 0) revealStep(currentStep - 1);
  });
  document.getElementById('btn-next').addEventListener('click', () => {
    pauseTour();
    if (currentStep < DATA.events.length - 1) revealStep(currentStep + 1);
  });
  document.getElementById('btn-reset').addEventListener('click', resetTour);

  // Keyboard shortcuts: ← → for step, Space for play/pause
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      pauseTour();
      if (currentStep > 0) revealStep(currentStep - 1);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      pauseTour();
      if (currentStep < DATA.events.length - 1) revealStep(currentStep + 1);
    } else if (e.key === ' ') {
      e.preventDefault();
      if (isPlaying) pauseTour(); else playTour();
    }
  });
});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------


def render(tour: dict, output_path: pathlib.Path) -> None:
    """Render the tour data into a self-contained HTML file."""
    print("  Rendering HTML visualization...")
    d3_script = _fetch_d3()

    meta   = tour.get("meta", {})
    stats  = tour.get("stats", {})

    # Format generated_at nicely
    gen_raw = meta.get("generated_at", "")
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(gen_raw).astimezone(timezone.utc)
        gen_str = dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        gen_str = gen_raw[:19]

    html = _HTML_TEMPLATE
    html = html.replace("{{VERSION}}",      str(meta.get("muse_version", "0.1.1")))
    html = html.replace("{{DOMAIN}}",       str(meta.get("domain", "music")))
    html = html.replace("{{ELAPSED}}",      str(meta.get("elapsed_s", "?")))
    html = html.replace("{{GENERATED_AT}}", gen_str)
    html = html.replace("{{COMMITS}}",      str(stats.get("commits", 0)))
    html = html.replace("{{BRANCHES}}",     str(stats.get("branches", 0)))
    html = html.replace("{{MERGES}}",       str(stats.get("merges", 0)))
    html = html.replace("{{CONFLICTS}}",    str(stats.get("conflicts_resolved", 0)))
    html = html.replace("{{OPS}}",          str(stats.get("operations", 0)))
    html = html.replace("{{ARCH_HTML}}",    _ARCH_HTML)
    html = html.replace("{{D3_SCRIPT}}",    d3_script)
    html = html.replace("{{DATA_JSON}}",    json.dumps(tour, separators=(",", ":")))

    output_path.write_text(html, encoding="utf-8")
    size_kb = output_path.stat().st_size // 1024
    print(f"  HTML written ({size_kb}KB) → {output_path}")


# ---------------------------------------------------------------------------
# Stand-alone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Render tour_de_force.json → HTML")
    parser.add_argument("json_file", help="Path to tour_de_force.json")
    parser.add_argument("--out", default=None, help="Output HTML path")
    args = parser.parse_args()

    json_path = pathlib.Path(args.json_file)
    if not json_path.exists():
        print(f"❌ File not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(json_path.read_text())
    out_path = pathlib.Path(args.out) if args.out else json_path.with_suffix(".html")
    render(data, out_path)
    print(f"Open: file://{out_path.resolve()}")
