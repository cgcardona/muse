#!/usr/bin/env python3
"""Muse — landing page generator.

Produces artifacts/index.html: a single entry point that presents both
the Tour de Force demo and the Domain Registry to visitors.

Stand-alone usage
-----------------
    python tools/render_index.py
    python tools/render_index.py --out artifacts/index.html
"""
from __future__ import annotations

import pathlib

_ROOT    = pathlib.Path(__file__).resolve().parent.parent
_DEFAULT = _ROOT / "artifacts" / "index.html"

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Muse — Version Anything</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg:     #0d1117;
      --bg2:    #161b22;
      --bg3:    #21262d;
      --border: #30363d;
      --text:   #e6edf3;
      --mute:   #8b949e;
      --dim:    #484f58;
      --accent: #4f8ef7;
      --a2:     #58a6ff;
      --green:  #3fb950;
      --purple: #bc8cff;
      --mono:   'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
      --ui:     -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
      --r:      10px;
    }
    html { scroll-behavior: smooth; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--ui);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    a { color: var(--a2); text-decoration: none; }
    a:hover { text-decoration: underline; }

    /* ---- Nav ---- */
    nav {
      display: flex;
      align-items: center;
      padding: 0 40px;
      height: 52px;
      border-bottom: 1px solid var(--border);
      background: var(--bg2);
      gap: 24px;
    }
    .nav-logo {
      font-family: var(--mono);
      font-size: 17px;
      font-weight: 700;
      color: var(--a2);
      letter-spacing: -0.5px;
    }
    .nav-link { font-size: 13px; color: var(--mute); transition: color 0.15s; }
    .nav-link:hover { color: var(--text); text-decoration: none; }
    .nav-spacer { flex: 1; }
    .nav-badge {
      font-family: var(--mono);
      font-size: 11px;
      background: rgba(79,142,247,0.1);
      border: 1px solid rgba(79,142,247,0.3);
      color: var(--a2);
      border-radius: 4px;
      padding: 2px 8px;
    }

    /* ---- Hero ---- */
    .hero {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 80px 40px 60px;
      position: relative;
      overflow: hidden;
    }
    .hero::before {
      content: '';
      position: absolute;
      inset: 0;
      background:
        radial-gradient(ellipse 55% 45% at 25% 55%, rgba(79,142,247,0.08) 0%, transparent 70%),
        radial-gradient(ellipse 45% 40% at 75% 45%, rgba(188,140,255,0.07) 0%, transparent 70%);
      pointer-events: none;
    }
    .hero-eyebrow {
      font-family: var(--mono);
      font-size: 11px;
      color: var(--a2);
      letter-spacing: 2.5px;
      text-transform: uppercase;
      margin-bottom: 22px;
      opacity: 0.85;
    }
    .hero h1 {
      font-size: clamp(52px, 8vw, 88px);
      font-weight: 800;
      letter-spacing: -3px;
      line-height: 1;
      margin-bottom: 20px;
      font-family: var(--mono);
    }
    .hero h1 .grad {
      background: linear-gradient(135deg, #4f8ef7 0%, #bc8cff 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .hero-sub {
      font-size: clamp(16px, 2.5vw, 20px);
      color: var(--mute);
      max-width: 560px;
      line-height: 1.65;
      margin-bottom: 48px;
    }
    .hero-sub strong { color: var(--text); }

    /* ---- Cards ---- */
    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 24px;
      max-width: 800px;
      width: 100%;
      position: relative;
    }
    .card {
      border: 1px solid var(--border);
      border-radius: var(--r);
      background: var(--bg2);
      padding: 32px 28px;
      text-align: left;
      display: flex;
      flex-direction: column;
      gap: 12px;
      transition: border-color 0.2s, transform 0.15s, box-shadow 0.2s;
      text-decoration: none;
      color: inherit;
    }
    .card:hover {
      border-color: var(--accent);
      transform: translateY(-3px);
      box-shadow: 0 8px 32px rgba(79,142,247,0.12);
      text-decoration: none;
    }
    .card.registry:hover {
      border-color: var(--purple);
      box-shadow: 0 8px 32px rgba(188,140,255,0.12);
    }
    .card-icon { font-size: 36px; line-height: 1; }
    .card-eyebrow {
      font-family: var(--mono);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 1.5px;
      text-transform: uppercase;
      color: var(--accent);
    }
    .card.registry .card-eyebrow { color: var(--purple); }
    .card-title {
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.4px;
      color: var(--text);
    }
    .card-desc { font-size: 14px; color: var(--mute); line-height: 1.65; }
    .card-desc strong { color: var(--text); }
    .card-cta {
      margin-top: auto;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      font-weight: 600;
      color: var(--accent);
      padding-top: 8px;
      border-top: 1px solid var(--border);
    }
    .card.registry .card-cta { color: var(--purple); }
    .card-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .pill {
      font-size: 10px;
      padding: 2px 8px;
      border-radius: 12px;
      border: 1px solid var(--border);
      color: var(--mute);
      background: var(--bg3);
    }
    .pill.blue   { border-color: rgba(79,142,247,0.3);  color: var(--a2);    background: rgba(79,142,247,0.07); }
    .pill.green  { border-color: rgba(63,185,80,0.3);   color: var(--green); background: rgba(63,185,80,0.07); }
    .pill.purple { border-color: rgba(188,140,255,0.3); color: var(--purple);background: rgba(188,140,255,0.07); }

    /* ---- Feature strip ---- */
    .features {
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      gap: 32px;
      padding: 40px;
      border-top: 1px solid var(--border);
      background: var(--bg2);
    }
    .feature {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 13px;
      color: var(--mute);
    }
    .feature-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--accent);
      flex-shrink: 0;
    }

    /* ---- Footer ---- */
    footer {
      background: var(--bg2);
      border-top: 1px solid var(--border);
      padding: 20px 40px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      font-size: 12px;
      color: var(--dim);
    }
    footer a { color: var(--a2); }
  </style>
</head>
<body>

<nav>
  <span class="nav-logo">muse</span>
  <a class="nav-link" href="tour_de_force.html">Tour de Force</a>
  <a class="nav-link" href="domain_registry.html">Domain Registry</a>
  <div class="nav-spacer"></div>
  <a class="nav-link" href="https://github.com/cgcardona/muse">GitHub</a>
  <span class="nav-badge">v0.1.1</span>
</nav>

<div class="hero">
  <div class="hero-eyebrow">Domain-agnostic version control</div>
  <h1><span class="grad">muse</span></h1>
  <p class="hero-sub">
    Version control for <strong>any multidimensional state</strong>.
    The same DAG, branching, merging, and conflict resolution that powers
    music — applied to genomics, 3D spatial fields, scientific simulation,
    or whatever you build next.
  </p>

  <div class="cards">
    <a class="card" href="tour_de_force.html">
      <div class="card-icon">🎵</div>
      <div class="card-eyebrow">Interactive Demo</div>
      <div class="card-title">Tour de Force</div>
      <div class="card-desc">
        Watch Muse version a real music project — five acts covering
        <strong>commits, branches, merges, conflict resolution</strong>,
        cherry-pick, stash, revert, and tags. Every operation is live,
        every commit real.
      </div>
      <div class="card-pills">
        <span class="pill blue">Animated DAG</span>
        <span class="pill blue">5 Acts</span>
        <span class="pill blue">41 Operations</span>
        <span class="pill blue">Dimension Matrix</span>
      </div>
      <div class="card-cta">Open Tour de Force →</div>
    </a>

    <a class="card registry" href="domain_registry.html">
      <div class="card-icon">🌐</div>
      <div class="card-eyebrow">Plugin Ecosystem</div>
      <div class="card-title">Domain Registry</div>
      <div class="card-desc">
        Build your own domain plugin. The
        <strong>six-method MuseDomainPlugin protocol</strong> gives you
        the full VCS for free — typed deltas, OT merge, CRDT primitives,
        domain schema. One command to scaffold.
      </div>
      <div class="card-pills">
        <span class="pill purple">6-Method Protocol</span>
        <span class="pill purple">OT Merge</span>
        <span class="pill purple">CRDT Primitives</span>
        <span class="pill purple">MuseHub Roadmap</span>
      </div>
      <div class="card-cta">Open Domain Registry →</div>
    </a>
  </div>
</div>

<div class="features">
  <div class="feature"><div class="feature-dot"></div>Content-addressed object store</div>
  <div class="feature"><div class="feature-dot"></div>Commit DAG with full history</div>
  <div class="feature"><div class="feature-dot" style="background:var(--green)"></div>Typed delta algebra</div>
  <div class="feature"><div class="feature-dot" style="background:var(--green)"></div>Per-dimension merge & conflict</div>
  <div class="feature"><div class="feature-dot" style="background:var(--purple)"></div>OT merge (StructuredMergePlugin)</div>
  <div class="feature"><div class="feature-dot" style="background:var(--purple)"></div>CRDT primitives (CRDTPlugin)</div>
  <div class="feature"><div class="feature-dot"></div>14 CLI commands · zero dependencies</div>
</div>

<footer>
  <span>Muse v0.1.1 · Python 3.12 · MIT License</span>
  <span>
    <a href="https://github.com/cgcardona/muse">github.com/cgcardona/muse</a>
    &nbsp;·&nbsp;
    <a href="https://github.com/cgcardona/muse/blob/main/docs/guide/plugin-authoring-guide.md">Plugin Guide</a>
  </span>
</footer>

</body>
</html>
"""


def render(output_path: pathlib.Path) -> None:
    """Write the landing page."""
    output_path.write_text(_HTML, encoding="utf-8")
    size_kb = output_path.stat().st_size // 1024
    print(f"  Landing page written ({size_kb}KB) → {output_path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Muse landing index.html")
    parser.add_argument("--out", default=str(_DEFAULT), help="Output path")
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    print("Generating index.html...")
    render(out)
    print(f"Open: file://{out.resolve()}")
