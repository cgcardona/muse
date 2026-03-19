#!/usr/bin/env python3
"""MIDI Demo Page — Groove in Em × Muse VCS.

Outputs: artifacts/midi-demo.html

Demonstrates Muse's 21-dimensional MIDI version control using an original
funky-soul groove composition built across a 5-act VCS narrative:

  Instruments:
    - Drums       (kick/snare/hi-hat/ghost snares/crash)
    - Bass guitar (E minor pentatonic walking line)
    - Electric Piano (Em7→Am7→Bm7→Cmaj7 chord voicings)
    - Lead Synth  (E pentatonic melody with pitch bends)
    - Brass/Ensemble (stabs and pads — conflict & resolution)

  VCS Narrative:
    Act 1  — Foundation  (3 commits on main)
    Act 2  — Divergence  (feat/groove + feat/harmony branches)
    Act 3  — Clean Merge (feat/groove + feat/harmony → main)
    Act 4  — Conflict    (conflict/brass-a vs conflict/ensemble)
    Act 5  — Resolution  (resolved mix, v1.0 tag, 21 dimensions)
"""

import json
import logging
import math
import pathlib

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# MUSICAL CONSTANTS  (96 BPM, E minor)
# ─────────────────────────────────────────────────────────────────────────────
BPM: int = 96
BEAT: float = 60.0 / BPM   # 0.625 s / beat
BAR: float = 4 * BEAT       # 2.5 s / bar
S16: float = BEAT / 4       # 16th note  = 0.15625 s
E8: float = BEAT / 2        # 8th note   = 0.3125 s
Q4: float = BEAT            # quarter    = 0.625 s
H2: float = 2 * BEAT        # half       = 1.25 s
W1: float = 4 * BEAT        # whole      = 2.5 s
BARS: int = 8               # every commit plays 8 bars ≈ 20 s

# GM Drum pitches
KICK = 36; SNARE = 38; HAT_C = 42; HAT_O = 46; CRASH = 49; RIDE = 51

# E-minor chord voicings (mid register)
_EM7  = [52, 55, 59, 62]   # E3 G3 B3 D4
_AM9  = [57, 60, 64, 67]   # A3 C4 E4 G4
_BM7  = [59, 62, 66, 69]   # B3 D4 F#4 A4
_CMAJ = [60, 64, 67, 71]   # C4 E4 G4 B4
CHORDS: list[list[int]] = [_EM7, _AM9, _BM7, _CMAJ]

# E pentatonic (lead)
PENTA: list[int] = [64, 67, 69, 71, 74, 76]  # E4 G4 A4 B4 D5 E5


def _n(pitch: int, vel: int, t: float, dur: float, instr: str) -> list[object]:
    """Pack a single MIDI note: [pitch, vel, start_sec, dur_sec, instr]."""
    return [pitch, vel, round(t, 5), round(dur, 5), instr]


def _bs(bar: int) -> float:
    """Bar start time in seconds (0-indexed)."""
    return bar * BAR


# ─────────────────────────────────────────────────────────────────────────────
# DRUMS
# ─────────────────────────────────────────────────────────────────────────────

def gen_drums_basic(bars: range) -> list[list[object]]:
    """Kick + snare only — the skeleton groove."""
    notes: list[list[object]] = []
    for b in bars:
        t = _bs(b)
        if b == bars.start:
            notes.append(_n(CRASH, 100, t, 1.0, "crash"))
        # Kick beats 1 & 3
        notes.append(_n(KICK, 110, t,            0.08, "kick"))
        notes.append(_n(KICK, 100, t + 2 * Q4,   0.08, "kick"))
        # Snare beats 2 & 4
        notes.append(_n(SNARE, 95, t + Q4,       0.10, "snare"))
        notes.append(_n(SNARE, 90, t + 3 * Q4,   0.10, "snare"))
    return notes


def gen_drums_full(bars: range) -> list[list[object]]:
    """Full funk pattern: kick/snare + hi-hat 16ths + ghost snares."""
    notes = gen_drums_basic(bars)
    for b in bars:
        t = _bs(b)
        # Closed hi-hat every 16th (open on 14th 16th)
        for i in range(16):
            if i == 14:
                notes.append(_n(HAT_O, 72, t + i * S16, 0.20, "hat_o"))
            else:
                vel = 75 if i % 4 == 0 else (60 if i % 2 == 0 else 45)
                notes.append(_n(HAT_C, vel, t + i * S16, 0.06, "hat_c"))
        # Ghost snares (very soft, add texture)
        for ghost_16th in [2, 6, 10, 14]:
            notes.append(_n(SNARE, 22, t + ghost_16th * S16, 0.04, "ghost"))
        # Syncopated kick pickup on odd bars
        if b % 2 == 1:
            notes.append(_n(KICK, 78, t + 3 * Q4 + S16, 0.07, "kick"))
    return notes


# ─────────────────────────────────────────────────────────────────────────────
# BASS GUITAR  (E minor pentatonic walking line)
# ─────────────────────────────────────────────────────────────────────────────
# E2=40  G2=43  A2=45  B2=47  C3=48  D3=50  E3=52
_BASS_CELLS: list[list[tuple[float, int, float, int]]] = [
    # (beat_offset, pitch, dur_beats, vel)  — bar 0 mod 4 (Em root)
    [(0.0, 40, 1.00, 95), (1.00, 43, 0.50, 85), (1.50, 45, 0.25, 80), (1.75, 47, 2.25, 90)],
    # bar 1 mod 4 (Am flavor)
    [(0.0, 40, 1.25, 95), (1.25, 43, 0.50, 85), (1.75, 45, 0.75, 85),
     (2.50, 47, 0.50, 80), (3.00, 50, 1.00, 80)],
    # bar 2 mod 4 (Am → Bm)
    [(0.0, 45, 1.00, 90), (1.00, 48, 0.50, 80), (1.50, 47, 1.75, 85), (3.25, 45, 0.75, 75)],
    # bar 3 mod 4 (Bm → Em)
    [(0.0, 47, 1.00, 90), (1.00, 50, 0.50, 85), (1.50, 45, 1.00, 80), (2.50, 40, 1.50, 95)],
]


def gen_bass(bars: range) -> list[list[object]]:
    """E minor pentatonic walking bass line — 4-bar repeating cell."""
    notes: list[list[object]] = []
    for b in bars:
        t = _bs(b)
        for beat_off, pitch, dur_beats, vel in _BASS_CELLS[b % 4]:
            notes.append(_n(pitch, vel, t + beat_off * Q4, dur_beats * Q4, "bass"))
    return notes


# ─────────────────────────────────────────────────────────────────────────────
# ELECTRIC PIANO  (Em7 → Am9 → Bm7 → Cmaj7 comping)
# ─────────────────────────────────────────────────────────────────────────────
# Syncopated comping hits within each bar
_COMP_HITS: list[tuple[float, float, int]] = [
    # (beat_offset, dur_beats, base_vel)
    (0.00, 0.35, 85),   # beat 1  stab
    (1.50, 0.50, 70),   # beat 2+ upbeat
    (2.00, 0.35, 80),   # beat 3  stab
    (3.50, 1.00, 72),   # beat 4+ sustain into next bar
]


def gen_epiano(bars: range) -> list[list[object]]:
    """Funky electric piano comping — syncopated voicings."""
    notes: list[list[object]] = []
    for b in bars:
        t = _bs(b)
        chord = CHORDS[b % 4]
        for beat_off, dur_beats, base_vel in _COMP_HITS:
            for i, pitch in enumerate(chord):
                vel = min(127, base_vel + (3 - i) * 3)  # root loudest
                notes.append(_n(pitch, vel, t + beat_off * Q4, dur_beats * Q4, "epiano"))
    return notes


# ─────────────────────────────────────────────────────────────────────────────
# LEAD SYNTH  (E pentatonic melody, 4-bar cell)
# ─────────────────────────────────────────────────────────────────────────────
# (abs_beat_within_4_bars, pitch_idx, dur_beats, vel)
_LEAD_CELL: list[tuple[float, int, float, int]] = [
    # bar 0 — call phrase (ascending)
    (0.00, 2, 0.50, 85), (0.50, 3, 0.25, 80), (0.75, 4, 0.25, 82),
    (1.00, 4, 0.50, 88), (1.50, 3, 0.50, 78), (2.00, 3, 0.40, 80),
    (2.50, 2, 0.50, 75), (3.00, 1, 1.00, 82),
    # bar 1 — response (peak)
    (4.00, 0, 0.50, 75), (4.50, 1, 0.50, 78), (5.00, 2, 1.00, 88),
    (6.00, 3, 0.50, 82), (6.50, 4, 0.25, 80), (6.75, 5, 0.25, 85),
    (7.00, 5, 1.00, 92),
    # bar 2 — descent
    (8.00, 4, 0.50, 85), (8.50, 3, 0.50, 80), (9.00, 2, 0.50, 78),
    (9.50, 1, 0.50, 75), (10.00, 0, 1.00, 80), (11.00, 1, 1.00, 82),
    # bar 3 — resolution
    (12.00, 2, 0.50, 80), (12.50, 3, 0.50, 82), (13.00, 4, 1.00, 88),
    (14.00, 3, 0.50, 80), (14.50, 2, 0.50, 78), (15.00, 0, 1.50, 92),
]


def gen_lead(bars: range) -> list[list[object]]:
    """E pentatonic melody — 4-bar repeating call-and-response phrase."""
    notes: list[list[object]] = []
    first = bars.start
    for b in bars:
        t = _bs(b)
        cell_bar = (b - first) % 4
        for abs_beat, pidx, dur_beats, vel in _LEAD_CELL:
            if int(abs_beat) // 4 == cell_bar:
                local_beat = abs_beat - cell_bar * 4
                notes.append(_n(PENTA[pidx], vel, t + local_beat * Q4, dur_beats * Q4, "lead"))
    return notes


# ─────────────────────────────────────────────────────────────────────────────
# BRASS / ENSEMBLE
# ─────────────────────────────────────────────────────────────────────────────

def gen_brass_a(bars: range) -> list[list[object]]:
    """Brass A: punchy staccato off-beat stabs.  G major triad."""
    STAB = [55, 59, 62]   # G3 B3 D4 (Em → G power)
    notes: list[list[object]] = []
    for b in bars:
        t = _bs(b)
        for beat_off in [0.5, 1.5, 2.5, 3.5]:
            for pitch in STAB:
                notes.append(_n(pitch, 95, t + beat_off * Q4, E8 * 0.55, "brass"))
    return notes


def gen_brass_b(bars: range) -> list[list[object]]:
    """Brass B / Ensemble: legato swell pads.  Em9 voicing."""
    PAD = [52, 55, 59, 64, 67]   # E3 G3 B3 E4 G4
    notes: list[list[object]] = []
    for b in bars:
        t = _bs(b)
        for pitch in PAD:
            notes.append(_n(pitch, 70, t, H2 * 1.8, "brassb"))
            notes.append(_n(pitch + 12, 55, t + H2, H2, "brassb"))  # octave upper bloom
    return notes


# ─────────────────────────────────────────────────────────────────────────────
# COMMIT DATA  (13 commits, 4 branches, 5 acts)
# ─────────────────────────────────────────────────────────────────────────────

def _all_notes(instrs: list[str], bars: range) -> list[list[object]]:
    """Gather notes for the given instruments over the bar range."""
    generators: dict[str, list[list[object]]] = {}
    if any(i in instrs for i in ["kick", "snare", "hat_c", "hat_o", "ghost", "crash"]):
        full = set(instrs) & {"hat_c", "hat_o", "ghost"}
        if full:
            generators.update({k: [] for k in ["kick","snare","hat_c","hat_o","ghost","crash"]})
            for nt in gen_drums_full(bars):
                if nt[4] in instrs:
                    generators[str(nt[4])].append(nt)
        else:
            for nt in gen_drums_basic(bars):
                if nt[4] in instrs:
                    generators.setdefault(str(nt[4]), []).append(nt)
    if "bass" in instrs:
        generators["bass"] = gen_bass(bars)
    if "epiano" in instrs:
        generators["epiano"] = gen_epiano(bars)
    if "lead" in instrs:
        generators["lead"] = gen_lead(bars)
    if "brass" in instrs:
        generators["brass"] = gen_brass_a(bars)
    if "brassb" in instrs:
        generators["brassb"] = gen_brass_b(bars)

    all_notes: list[list[object]] = []
    for lst in generators.values():
        all_notes.extend(lst)
    return all_notes


_R = range(0, BARS)    # all 8 bars
_DK = ["kick", "snare"]
_DF = ["kick", "snare", "hat_c", "hat_o", "ghost", "crash"]


def _build_commits() -> list[dict[str, object]]:
    """Return the full ordered commit list with note payloads."""

    def mk(
        sha: str,
        branch: str,
        label: str,
        cmd: str,
        output: str,
        act: int,
        instrs: list[str],
        dim_act: dict[str, int],
        parents: list[str] | None = None,
        conflict: bool = False,
        resolved: bool = False,
    ) -> dict[str, object]:
        notes = _all_notes(instrs, _R)
        return {
            "sha": sha,
            "branch": branch,
            "label": label,
            "cmd": cmd,
            "output": output,
            "act": act,
            "notes": notes,
            "dimAct": dim_act,
            "parents": parents or [],
            "conflict": conflict,
            "resolved": resolved,
        }

    # Dimension shorthand
    _META  = {"time_signatures": 2, "key_signatures": 2, "tempo_map": 2, "markers": 2, "track_structure": 1}
    _VOL   = {"cc_volume": 2, "cc_pan": 1}
    _BASS_D = {"cc_portamento": 2, "cc_reverb": 1, "cc_expression": 1, "cc_other": 1}
    _PIANO = {"cc_sustain": 2, "cc_chorus": 1, "cc_soft_pedal": 1, "cc_sostenuto": 1}
    _LEAD_D = {"pitch_bend": 3, "cc_modulation": 2, "channel_pressure": 2, "poly_pressure": 1}
    _BRASS_D = {"cc_expression": 3}
    _ENS_D  = {"cc_reverb": 3, "cc_chorus": 2}  # CONFLICT source

    c: list[dict[str, object]] = []

    c.append(mk("a0f4d2e1", "main",
        "muse init\\n--domain midi",
        "muse init --domain midi",
        "✓ Initialized Muse repository\n  domain: midi  |  .muse/ created",
        0, [],
        {**_META},
    ))

    c.append(mk("1b3c8f02", "main",
        "Foundation\\n4/4 · 96 BPM · Em",
        "muse commit -m 'Foundation: 4/4, 96 BPM, Em key'",
        "✓ [main 1b3c8f02] Foundation: 4/4, 96 BPM, Em key\n"
        "  1 file changed — .museattributes, time_sig, key_sig, markers",
        1, [],
        {**_META, "program_change": 1},
        ["a0f4d2e1"],
    ))

    c.append(mk("2d9e1a47", "main",
        "Foundation\\nkick + snare groove",
        "muse commit -m 'Foundation: kick+snare groove pattern'",
        "✓ [main 2d9e1a47] Foundation: kick+snare groove pattern\n"
        "  notes dim active  |  cc_volume",
        1, _DK,
        {**_META, "notes": 2, **_VOL},
        ["1b3c8f02"],
    ))

    # ── Act 2: Divergence ─────────────────────────────────────────────────────

    c.append(mk("3f0b5c8d", "feat/groove",
        "Groove\\nfull drum kit + bass",
        "muse commit -m 'Groove: hi-hat 16ths, ghost snares, bass root motion'",
        "✓ [feat/groove 3f0b5c8d] Groove: hi-hat 16ths, ghost snares, bass root motion\n"
        "  notes, program_change, cc_portamento, cc_pan",
        2, [*_DF, "bass"],
        {**_META, "notes": 3, **_VOL, "program_change": 2, **_BASS_D},
        ["2d9e1a47"],
    ))

    c.append(mk("4a2c7e91", "feat/groove",
        "Groove\\nbass expression + reverb",
        "muse commit -m 'Groove: bass portamento slides, CC reverb tail'",
        "✓ [feat/groove 4a2c7e91] Groove: bass portamento slides, CC reverb tail\n"
        "  cc_portamento, cc_reverb, cc_expression active",
        2, [*_DF, "bass"],
        {**_META, "notes": 3, **_VOL, "program_change": 2, **_BASS_D},
        ["3f0b5c8d"],
    ))

    c.append(mk("5e8d3b14", "feat/harmony",
        "Harmony\\nEm7→Am9→Bm7→Cmaj7",
        "muse commit -m 'Harmony: Em7 chord voicings, CC sustain + chorus'",
        "✓ [feat/harmony 5e8d3b14] Harmony: Em7 chord voicings, CC sustain + chorus\n"
        "  notes, cc_sustain, cc_chorus, cc_soft_pedal",
        2, [*_DF, "epiano"],
        {**_META, "notes": 3, **_VOL, "program_change": 2, **_PIANO},
        ["2d9e1a47"],
    ))

    c.append(mk("6c1f9a52", "feat/harmony",
        "Melody\\nE pentatonic + pitch bends",
        "muse commit -m 'Melody: E pentatonic lead, pitch_bend, channel_pressure'",
        "✓ [feat/harmony 6c1f9a52] Melody: E pentatonic lead, pitch_bend, channel_pressure\n"
        "  pitch_bend, cc_modulation, channel_pressure, poly_pressure",
        2, [*_DF, "epiano", "lead"],
        {**_META, "notes": 3, **_VOL, "program_change": 2, **_PIANO, **_LEAD_D},
        ["5e8d3b14"],
    ))

    # ── Act 3: Clean Merge ────────────────────────────────────────────────────

    c.append(mk("7b4e2d85", "main",
        "MERGE\\nfeat/groove + feat/harmony",
        "muse merge feat/groove feat/harmony",
        "✓ Merged 'feat/groove' into 'main' — 0 conflicts\n"
        "✓ Merged 'feat/harmony' into 'main' — 0 conflicts\n"
        "  Full rhythm + harmony stack active",
        3, [*_DF, "bass", "epiano", "lead"],
        {**_META, "notes": 4, **_VOL, "program_change": 3,
         **_BASS_D, **_PIANO, **_LEAD_D},
        ["4a2c7e91", "6c1f9a52"],
    ))

    # ── Act 4: Conflict ───────────────────────────────────────────────────────

    c.append(mk("8d7f1c36", "conflict/brass-a",
        "Brass A\\nstaccato stabs",
        "muse commit -m 'Brass A: punchy stabs, CC expression bus'",
        "✓ [conflict/brass-a 8d7f1c36] Brass A: punchy stabs, CC expression bus\n"
        "  brass track  |  cc_expression elevated",
        4, [*_DF, "bass", "epiano", "lead", "brass"],
        {**_META, "notes": 4, **_VOL, "program_change": 3,
         **_BASS_D, **_PIANO, **_LEAD_D, **_BRASS_D},
        ["7b4e2d85"],
    ))

    c.append(mk("9e0a4b27", "conflict/ensemble",
        "Ensemble\\nlegato pads",
        "muse commit -m 'Ensemble: legato pads, CC reverb swell'",
        "✓ [conflict/ensemble 9e0a4b27] Ensemble: legato pads, CC reverb swell\n"
        "  brassb track  |  cc_reverb elevated (CONFLICT INCOMING)",
        4, [*_DF, "bass", "epiano", "lead", "brassb"],
        {**_META, "notes": 4, **_VOL, "program_change": 3,
         **_BASS_D, **_PIANO, **_LEAD_D, **_ENS_D},
        ["7b4e2d85"],
    ))

    c.append(mk("a1b5c8d9", "main",
        "MERGE\\nconflict/brass-a → main",
        "muse merge conflict/brass-a",
        "✓ Merged 'conflict/brass-a' into 'main' — 0 conflicts\n"
        "  stab brass layer integrated",
        4, [*_DF, "bass", "epiano", "lead", "brass"],
        {**_META, "notes": 4, **_VOL, "program_change": 3,
         **_BASS_D, **_PIANO, **_LEAD_D, **_BRASS_D},
        ["7b4e2d85", "8d7f1c36"],
    ))

    c.append(mk("b2c6d9e0", "main",
        "⚠ CONFLICT\\ncc_reverb dimension",
        "muse merge conflict/ensemble",
        "⚠  CONFLICT detected in dimension: cc_reverb\n"
        "  conflict/brass-a:  cc_reverb = 45\n"
        "  conflict/ensemble: cc_reverb = 82\n"
        "  → muse resolve --strategy=auto cc_reverb",
        4, [*_DF, "bass", "epiano", "lead", "brass", "brassb"],
        {**_META, "notes": 5, **_VOL, "program_change": 4,
         **_BASS_D, **_PIANO, **_LEAD_D, **_BRASS_D, **_ENS_D},
        ["a1b5c8d9", "9e0a4b27"],
        conflict=True,
    ))

    # ── Act 5: Resolution ─────────────────────────────────────────────────────

    c.append(mk("c3d7e0f1", "main",
        "RESOLVED · v1.0\\n21 dimensions active",
        "muse resolve --strategy=auto cc_reverb && muse tag add v1.0",
        "✓ Resolved cc_reverb — took max(45, 82) = 82\n"
        "✓ All 21 MIDI dimensions active\n"
        "✓ Tag 'v1.0' created → [main c3d7e0f1]",
        5, [*_DF, "bass", "epiano", "lead", "brass", "brassb"],
        {**_META, "notes": 5, **_VOL, "program_change": 4,
         **_BASS_D, **_PIANO, **_LEAD_D, **_BRASS_D, **_ENS_D},
        ["b2c6d9e0"],
        resolved=True,
    ))

    return c


# ─────────────────────────────────────────────────────────────────────────────
# HTML TEMPLATE
# ─────────────────────────────────────────────────────────────────────────────

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Muse · MIDI Demo — Groove in Em</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/tone@14.7.77/build/Tone.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#07090f;--surface:#0d1118;--panel:#111724;--border:rgba(255,255,255,0.07);
  --text:#e8eaf0;--muted:rgba(255,255,255,0.38);--accent:#33ddff;
  --pink:#ff6b9d;--purple:#a855f7;--gold:#f59e0b;--green:#34d399;
  --kick:#ef4444;--snare:#fb923c;--hat:#facc15;--crash:#fef9c3;
  --bass:#a855f7;--epiano:#22d3ee;--lead:#f472b6;--brass:#34d399;--brassb:#86efac;
  --main:#4f8ef7;--groove:#a855f7;--harmony:#22d3ee;--bra:#ef4444;--ens:#f59e0b;
}
html{font-size:14px;scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;min-height:100vh;overflow-x:hidden}

/* ── NAV ── */
nav{display:flex;align-items:center;justify-content:space-between;padding:0 20px;height:48px;
    background:rgba(13,17,24,0.92);border-bottom:1px solid var(--border);
    position:sticky;top:0;z-index:50;backdrop-filter:blur(8px)}
.nav-logo{font-size:13px;font-family:'JetBrains Mono',monospace;color:var(--accent);letter-spacing:.05em}
.nav-links{display:flex;gap:18px}
.nav-links a{font-size:12px;color:var(--muted);text-decoration:none;transition:color .2s}
.nav-links a:hover,.nav-links a.active{color:var(--text)}
.nav-badge{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--accent);
           background:rgba(51,221,255,.1);border:1px solid rgba(51,221,255,.2);
           padding:2px 8px;border-radius:20px}

/* ── HERO ── */
.hero{padding:28px 24px 18px;text-align:center}
.hero h1{font-size:clamp(22px,3.5vw,36px);font-weight:700;letter-spacing:-.02em;
         background:linear-gradient(135deg,#fff 30%,var(--accent) 100%);
         -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hero-sub{font-size:13px;color:var(--muted);margin-top:6px}
.hero-tags{display:flex;justify-content:center;flex-wrap:wrap;gap:8px;margin-top:12px}
.hero-tag{font-size:10px;font-family:'JetBrains Mono',monospace;padding:2px 8px;
          border-radius:20px;background:rgba(255,255,255,0.06);border:1px solid var(--border);color:var(--muted)}
.hero-tag.on{color:var(--accent);background:rgba(51,221,255,.08);border-color:rgba(51,221,255,.2)}

/* ── MAIN GRID ── */
.main-grid{display:grid;grid-template-columns:320px 1fr;gap:12px;padding:0 14px 14px;
           max-width:1400px;margin:0 auto}
@media(max-width:900px){.main-grid{grid-template-columns:1fr}}

/* ── PANELS ── */
.panel{background:var(--panel);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.panel-hd{display:flex;align-items:center;justify-content:space-between;
          padding:9px 14px;border-bottom:1px solid var(--border);
          font-size:11px;font-family:'JetBrains Mono',monospace;color:var(--muted);letter-spacing:.05em}
.panel-hd span{color:var(--text);font-size:12px}

/* ── DAG ── */
#dag-wrap{padding:10px 0 6px}
#dag-svg{display:block;width:100%;overflow:visible}
#dag-branch-badge{font-size:10px;font-family:'JetBrains Mono',monospace;color:var(--accent);
                  background:rgba(51,221,255,.1);border:1px solid rgba(51,221,255,.15);
                  padding:1px 6px;border-radius:12px}

/* ── ACT BADGE ── */
.act-badge{display:inline-flex;align-items:center;gap:5px;font-size:10px;
           font-family:'JetBrains Mono',monospace;color:var(--muted)}
.act-dot{width:6px;height:6px;border-radius:50%;background:currentColor}

/* ── COMMAND LOG ── */
#cmd-terminal{margin:10px;background:#060a12;border:1px solid var(--border);
              border-radius:6px;padding:10px;min-height:100px;max-height:140px;overflow:hidden}
.term-dots{display:flex;gap:4px;margin-bottom:8px}
.term-dot{width:9px;height:9px;border-radius:50%}
.t-red{background:#ff5f57}.t-yel{background:#febc2e}.t-grn{background:#28c840}
#cmd-prompt{font-family:'JetBrains Mono',monospace;font-size:11px;line-height:1.6;color:#c4c9d4}
.cmd-line{color:var(--accent)}
.cmd-ok{color:var(--green)}
.cmd-warn{color:var(--gold)}
.cmd-err{color:var(--kick)}
.cmd-cursor{display:inline-block;width:6px;height:13px;background:var(--accent);
            animation:blink .9s step-end infinite;vertical-align:middle}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}

/* ── DAW TRACK VIEW ── */
.daw-wrap{position:relative;overflow-x:auto;overflow-y:hidden}
#daw-svg{display:block}
.daw-time-label{font-family:'JetBrains Mono',monospace;font-size:9px;fill:var(--muted)}
.daw-track-label{font-family:'JetBrains Mono',monospace;font-size:9px;fill:var(--muted);text-anchor:end}
.playhead-line{stroke:rgba(255,255,255,0.8);stroke-width:1.5;pointer-events:none}

/* ── CONTROLS ── */
.ctrl-bar{display:flex;align-items:center;gap:10px;padding:10px 14px;
          background:var(--surface);border-top:1px solid var(--border);
          border-bottom:1px solid var(--border);flex-wrap:wrap}
.ctrl-group{display:flex;align-items:center;gap:6px}
.ctrl-btn{width:36px;height:36px;border-radius:50%;border:1px solid var(--border);
          background:rgba(255,255,255,.05);color:var(--text);cursor:pointer;
          display:flex;align-items:center;justify-content:center;font-size:13px;
          transition:all .15s}
.ctrl-btn:hover{background:rgba(255,255,255,.1);border-color:var(--accent)}
.ctrl-btn:disabled{opacity:.3;cursor:not-allowed}
.ctrl-play{width:44px;height:44px;border-radius:50%;border:none;
           background:var(--accent);color:#000;cursor:pointer;font-size:15px;
           display:flex;align-items:center;justify-content:center;
           transition:all .15s;box-shadow:0 0 16px rgba(51,221,255,.3)}
.ctrl-play:hover{transform:scale(1.08)}
.ctrl-play.playing{background:var(--pink);box-shadow:0 0 20px rgba(255,107,157,.4)}
.ctrl-info{font-family:'JetBrains Mono',monospace;font-size:11px}
.ctrl-time{color:var(--accent);min-width:40px}
.ctrl-sha{color:var(--muted);font-size:10px}
.ctrl-msg{font-size:11px;color:var(--text);flex:1;min-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.audio-status{font-size:10px;color:var(--muted);font-family:'JetBrains Mono',monospace}
.audio-status.ready{color:var(--green)}
.audio-status.loading{color:var(--gold)}

/* ── 21-DIM PANEL ── */
.dim-grid{display:grid;grid-template-columns:1fr 1fr;gap:3px;padding:10px 12px;max-height:280px;overflow-y:auto}
.dim-row{display:flex;align-items:center;gap:5px;padding:3px 5px;border-radius:4px;
         transition:background .2s;cursor:default;min-width:0}
.dim-row:hover{background:rgba(255,255,255,.04)}
.dim-row.active{background:rgba(255,255,255,.02)}
.dim-dot{width:7px;height:7px;border-radius:50%;background:rgba(255,255,255,.15);flex-shrink:0;transition:all .3s}
.dim-name{font-size:9.5px;font-family:'JetBrains Mono',monospace;color:var(--muted);
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;transition:color .3s}
.dim-row.active .dim-name{color:var(--text)}
.dim-bar-wrap{width:32px;height:4px;background:rgba(255,255,255,.07);border-radius:2px;flex-shrink:0}
.dim-bar{height:100%;width:0;border-radius:2px;transition:width .4s,background .3s}
.dim-group-label{grid-column:1/-1;font-size:9px;font-family:'JetBrains Mono',monospace;
                 color:rgba(255,255,255,.2);text-transform:uppercase;letter-spacing:.08em;
                 padding:5px 5px 2px;border-top:1px solid var(--border);margin-top:4px}
.dim-group-label:first-child{border-top:none;margin-top:0}

/* ── HEATMAP ── */
#heatmap-wrap{padding:12px 14px;overflow-x:auto}
#heatmap-svg{display:block}

/* ── CLI REFERENCE ── */
.cli-section{max-width:1400px;margin:0 auto;padding:0 14px 40px}
.cli-section h2{font-size:16px;font-weight:600;margin-bottom:14px;color:var(--accent)}
.cli-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px}
.cli-card{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.cli-cmd{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--accent);margin-bottom:4px}
.cli-desc{font-size:12px;color:var(--muted);margin-bottom:6px}
.cli-flags{display:flex;flex-direction:column;gap:2px}
.cli-flag{font-family:'JetBrains Mono',monospace;font-size:10px;color:rgba(255,255,255,.4)}
.cli-flag span{color:var(--text)}

/* ── INIT OVERLAY ── */
#init-overlay{position:fixed;inset:0;background:rgba(7,9,15,.88);
              display:flex;flex-direction:column;align-items:center;justify-content:center;
              z-index:100;backdrop-filter:blur(6px);gap:16px;text-align:center}
#init-overlay h2{font-size:24px;font-weight:700;color:var(--text)}
#init-overlay p{font-size:14px;color:var(--muted);max-width:400px}
.btn-init{padding:12px 28px;border-radius:8px;border:none;background:var(--accent);
          color:#000;font-size:15px;font-weight:600;cursor:pointer;transition:all .2s}
.btn-init:hover{transform:scale(1.05);box-shadow:0 0 20px rgba(51,221,255,.4)}

/* ── BRANCH LEGEND ── */
.branch-legend{display:flex;flex-wrap:wrap;gap:10px;padding:6px 14px 10px}
.bl-item{display:flex;align-items:center;gap:5px;font-size:10px;
         font-family:'JetBrains Mono',monospace;color:var(--muted)}
.bl-dot{width:9px;height:9px;border-radius:50%}
.bl-item.active .bl-dot{box-shadow:0 0 6px currentColor}
.bl-item.active span{color:var(--text)}

/* ── SCROLLBAR ── */
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:3px}
</style>
</head>
<body>

<div id="init-overlay">
  <h2>Muse MIDI Demo</h2>
  <p>Groove in Em — 5-act VCS narrative · 5 instruments · 21 dimensions<br>Click to initialize audio engine.</p>
  <button class="btn-init" id="btn-init-audio">Initialize Audio ▶</button>
</div>

<nav>
  <span class="nav-logo">muse / midi-demo</span>
  <div class="nav-links">
    <a href="index.html">Docs</a>
    <a href="demo.html">Demo</a>
    <a href="midi-demo.html" class="active">MIDI</a>
  </div>
  <span class="nav-badge">v0.1.2</span>
</nav>

<div class="hero">
  <h1>Groove in Em · Muse VCS</h1>
  <div class="hero-sub">5-act VCS narrative · 5 instruments · 13 commits · 4 branches · 21 MIDI dimensions</div>
  <div class="hero-tags">
    <span class="hero-tag on">drums</span>
    <span class="hero-tag on">bass</span>
    <span class="hero-tag on">electric piano</span>
    <span class="hero-tag on">lead synth</span>
    <span class="hero-tag on">brass</span>
    <span class="hero-tag">96 BPM</span>
    <span class="hero-tag">E minor</span>
  </div>
</div>

<!-- CONTROLS -->
<div class="ctrl-bar">
  <div class="ctrl-group">
    <button class="ctrl-btn" id="btn-first" title="First commit">⏮</button>
    <button class="ctrl-btn" id="btn-prev"  title="Previous commit">◀</button>
    <button class="ctrl-play" id="btn-play" disabled title="Play / Pause">▶</button>
    <button class="ctrl-btn" id="btn-next"  title="Next commit">▶</button>
    <button class="ctrl-btn" id="btn-last"  title="Last commit">⏭</button>
  </div>
  <div class="ctrl-group ctrl-info">
    <span class="ctrl-time" id="time-display">0:00</span>
    <span class="ctrl-sha"  id="sha-display">a0f4d2e1</span>
  </div>
  <div class="ctrl-msg" id="msg-display">muse init --domain midi</div>
  <span class="audio-status" id="audio-status">○ click ▶ to load audio</span>
</div>

<!-- MAIN GRID -->
<div class="main-grid">

  <!-- LEFT COLUMN -->
  <div style="display:flex;flex-direction:column;gap:12px">

    <!-- DAG -->
    <div class="panel">
      <div class="panel-hd">
        <span>COMMIT DAG</span>
        <span id="dag-branch-badge" class="nav-badge" style="border-color:rgba(79,142,247,.3);color:#4f8ef7">main</span>
      </div>
      <div class="branch-legend" id="branch-legend"></div>
      <div id="dag-wrap"><svg id="dag-svg"></svg></div>
    </div>

    <!-- CMD LOG -->
    <div class="panel">
      <div class="panel-hd"><span>COMMAND LOG</span><span id="act-label">Act 0</span></div>
      <div id="cmd-terminal">
        <div class="term-dots">
          <div class="term-dot t-red"></div>
          <div class="term-dot t-yel"></div>
          <div class="term-dot t-grn"></div>
        </div>
        <div id="cmd-prompt"><span class="cmd-cursor"></span></div>
      </div>
    </div>

    <!-- 21-DIM PANEL -->
    <div class="panel">
      <div class="panel-hd"><span>21 MIDI DIMENSIONS</span><span id="dim-active-count">0 active</span></div>
      <div class="dim-grid" id="dim-list"></div>
    </div>

  </div><!-- /left -->

  <!-- RIGHT COLUMN -->
  <div style="display:flex;flex-direction:column;gap:12px">

    <!-- DAW TRACK VIEW -->
    <div class="panel">
      <div class="panel-hd"><span>DAW TRACK VIEW</span><span id="daw-commit-label">commit 0/12</span></div>
      <div class="daw-wrap">
        <svg id="daw-svg"></svg>
      </div>
    </div>

    <!-- HEATMAP -->
    <div class="panel">
      <div class="panel-hd"><span>DIMENSION ACTIVITY HEATMAP</span><span style="color:var(--muted);font-size:10px">commits × 21 dimensions</span></div>
      <div id="heatmap-wrap"><svg id="heatmap-svg"></svg></div>
    </div>

  </div><!-- /right -->
</div><!-- /main-grid -->

<!-- CLI REFERENCE -->
<div class="cli-section">
  <h2>MIDI Plugin — Command Reference</h2>
  <div class="cli-grid" id="cli-grid"></div>
</div>

<script>
// ═══════════════════════════════════════════════════════════════
// DATA
// ═══════════════════════════════════════════════════════════════
const BPM = __BPM__;
const BEAT = 60 / BPM;
const BAR  = 4 * BEAT;
const TOTAL_SECS = 8 * BAR;

const COMMITS = __COMMITS__;

const DIMS_21 = [
  {id:'notes',         label:'notes',          group:'core', color:'#33ddff', desc:'Note-on/off events'},
  {id:'pitch_bend',    label:'pitch_bend',     group:'expr', color:'#f472b6', desc:'Pitch wheel automation'},
  {id:'channel_pressure',label:'channel_pressure',group:'expr',color:'#fb923c',desc:'Channel aftertouch'},
  {id:'poly_pressure', label:'poly_pressure',  group:'expr', color:'#f97316', desc:'Per-note aftertouch'},
  {id:'cc_modulation', label:'cc_modulation',  group:'cc',   color:'#a78bfa', desc:'CC 1 — vibrato/LFO'},
  {id:'cc_volume',     label:'cc_volume',      group:'cc',   color:'#60a5fa', desc:'CC 7 — channel volume'},
  {id:'cc_pan',        label:'cc_pan',         group:'cc',   color:'#34d399', desc:'CC 10 — stereo pan'},
  {id:'cc_expression', label:'cc_expression',  group:'cc',   color:'#f59e0b', desc:'CC 11 — expression'},
  {id:'cc_sustain',    label:'cc_sustain',     group:'cc',   color:'#22d3ee', desc:'CC 64 — sustain pedal'},
  {id:'cc_portamento', label:'cc_portamento',  group:'cc',   color:'#a855f7', desc:'CC 65 — portamento on/off'},
  {id:'cc_sostenuto',  label:'cc_sostenuto',   group:'cc',   color:'#818cf8', desc:'CC 66 — sostenuto pedal'},
  {id:'cc_soft_pedal', label:'cc_soft_pedal',  group:'cc',   color:'#6ee7b7', desc:'CC 67 — soft pedal'},
  {id:'cc_reverb',     label:'cc_reverb',      group:'fx',   color:'#c4b5fd', desc:'CC 91 — reverb send'},
  {id:'cc_chorus',     label:'cc_chorus',      group:'fx',   color:'#93c5fd', desc:'CC 93 — chorus send'},
  {id:'cc_other',      label:'cc_other',       group:'fx',   color:'#6b7280', desc:'Other CC controllers'},
  {id:'program_change',label:'program_change', group:'meta', color:'#f9a825', desc:'Instrument program selection'},
  {id:'tempo_map',     label:'tempo_map',      group:'meta', color:'#ef4444', desc:'BPM automation'},
  {id:'time_signatures',label:'time_signatures',group:'meta',color:'#ec4899', desc:'Meter changes'},
  {id:'key_signatures',label:'key_signatures', group:'meta', color:'#d946ef', desc:'Key / mode changes'},
  {id:'markers',       label:'markers',        group:'meta', color:'#8b5cf6', desc:'Named timeline markers'},
  {id:'track_structure',label:'track_structure',group:'meta',color:'#64748b', desc:'Track count & arrangement'},
];

const BRANCH_COLOR = {
  'main':'#4f8ef7', 'feat/groove':'#a855f7',
  'feat/harmony':'#22d3ee', 'conflict/brass-a':'#ef4444', 'conflict/ensemble':'#f59e0b'
};

const INSTR_COLOR = {
  kick:'#ef4444', snare:'#fb923c', hat_c:'#facc15', hat_o:'#86efac',
  ghost:'rgba(251,146,60,0.35)', crash:'#fef3c7',
  bass:'#a855f7', epiano:'#22d3ee', lead:'#f472b6', brass:'#34d399', brassb:'#86efac'
};

const INSTR_LABEL = {
  kick:'KICK', snare:'SNARE', hat_c:'HAT', hat_o:'HAT',
  ghost:'GHOST', crash:'CRASH', bass:'BASS', epiano:'E.PIANO', lead:'LEAD',
  brass:'BRASS A', brassb:'BRASS B'
};

const ACT_LABELS = ['Init', 'Foundation', 'Divergence', 'Clean Merge', 'Conflict', 'Resolution'];

// ═══════════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════════
const state = {
  cur: 0,
  isPlaying: false,
  audioReady: false,
  pausedAt: null,        // null = not paused, number = paused at this second
  playStartWallClock: 0,
  playStartAudioSec: 0,
  rafId: null,
};

let instruments = {};
let masterBus = null;

// ═══════════════════════════════════════════════════════════════
// AUDIO ENGINE  (Tone.js, multi-instrument)
// ═══════════════════════════════════════════════════════════════
async function initAudio() {
  const overlay = document.getElementById('init-overlay');
  const statusEl = document.getElementById('audio-status');
  const btn = document.getElementById('btn-play');

  if (overlay) overlay.style.display = 'none';
  statusEl.textContent = '◌ loading…';
  statusEl.className = 'audio-status loading';

  await Tone.start();

  // Master chain: Compressor → Limiter → Destination
  const limiter    = new Tone.Limiter(-1).toDestination();
  const masterComp = new Tone.Compressor({threshold:-18, ratio:4, attack:0.003, release:0.25}).connect(limiter);
  masterBus = masterComp;

  // Per-instrument reverb sends
  const roomRev  = new Tone.Reverb({decay:1.8, wet:0.18}).connect(masterBus);
  const hallRev  = new Tone.Reverb({decay:3.5, wet:0.28}).connect(masterBus);

  // 808-style kick
  const kick = new Tone.MembraneSynth({
    pitchDecay:0.08, octaves:8,
    envelope:{attack:0.001, decay:0.28, sustain:0, release:0.12},
    volume:2
  }).connect(masterBus);

  // Snare
  const snare = new Tone.NoiseSynth({
    noise:{type:'white'},
    envelope:{attack:0.001, decay:0.14, sustain:0, release:0.06},
    volume:-4
  }).connect(masterBus);

  // Closed hi-hat
  const hat_c = new Tone.MetalSynth({
    frequency:600, harmonicity:5.1, modulationIndex:32,
    resonance:4000, octaves:1.5,
    envelope:{attack:0.001, decay:0.028, release:0.01},
    volume:-16
  }).connect(masterBus);

  // Open hi-hat
  const hat_o = new Tone.MetalSynth({
    frequency:600, harmonicity:5.1, modulationIndex:32,
    resonance:4000, octaves:1.5,
    envelope:{attack:0.001, decay:0.22, release:0.08},
    volume:-13
  }).connect(masterBus);

  // Ghost snare (quieter)
  const ghost = new Tone.NoiseSynth({
    noise:{type:'white'},
    envelope:{attack:0.001, decay:0.04, sustain:0, release:0.01},
    volume:-20
  }).connect(masterBus);

  // Crash cymbal
  const crash = new Tone.MetalSynth({
    frequency:300, harmonicity:5.1, modulationIndex:64,
    resonance:4000, octaves:2.5,
    envelope:{attack:0.001, decay:1.6, release:0.8},
    volume:-10
  }).connect(masterBus);

  // Bass guitar (fat mono saw + resonant filter)
  const bass = new Tone.MonoSynth({
    oscillator:{type:'sawtooth'},
    filter:{Q:3, type:'lowpass', rolloff:-24},
    filterEnvelope:{attack:0.002, decay:0.15, sustain:0.5, release:0.4, baseFrequency:260, octaves:3},
    envelope:{attack:0.004, decay:0.12, sustain:0.85, release:0.35},
    volume:-2
  }).connect(masterBus);
  bass.connect(roomRev);

  // Electric piano (FM — warm Rhodes-ish)
  const epiano = new Tone.PolySynth(Tone.FMSynth, {
    harmonicity:3.01, modulationIndex:14,
    oscillator:{type:'triangle'},
    envelope:{attack:0.01, decay:1.1, sustain:0.5, release:0.6},
    modulation:{type:'square'},
    modulationEnvelope:{attack:0.002, decay:0.12, sustain:0.2, release:0.01},
    volume:-10
  }).connect(masterBus);
  epiano.connect(roomRev);

  // Lead synth (fat detune sawtooth)
  const lead = new Tone.PolySynth(Tone.Synth, {
    oscillator:{type:'fatsawtooth', spread:28, count:3},
    envelope:{attack:0.025, decay:0.18, sustain:0.65, release:0.45},
    volume:-9
  }).connect(masterBus);
  lead.connect(hallRev);

  // Brass A (punchy staccato)
  const brass = new Tone.PolySynth(Tone.Synth, {
    oscillator:{type:'sawtooth'},
    envelope:{attack:0.008, decay:0.25, sustain:0.75, release:0.18},
    volume:-8
  }).connect(masterBus);
  brass.connect(roomRev);

  // Brass B / Ensemble (legato lush pads)
  const brassb = new Tone.PolySynth(Tone.Synth, {
    oscillator:{type:'triangle'},
    envelope:{attack:0.32, decay:0.6, sustain:0.82, release:0.9},
    volume:-12
  }).connect(masterBus);
  brassb.connect(hallRev);

  instruments = { kick, snare, hat_c, hat_o, ghost, crash, bass, epiano, lead, brass, brassb };

  state.audioReady = true;
  btn.disabled = false;
  statusEl.textContent = '● audio ready';
  statusEl.className = 'audio-status ready';
}

// ── Play helpers ────────────────────────────────────────────────

function fmtTime(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2,'0')}`;
}

function _scheduleNotes(notes, offsetSec) {
  // Group by (instr, start_sec, dur_sec) for polyphonic batching
  const groups = {};
  for (const [pitch, vel, startSec, durSec, instr] of notes) {
    if (startSec < offsetSec - 0.01) continue;  // skip already-played
    const key = `${instr}__${startSec.toFixed(4)}__${durSec.toFixed(4)}`;
    if (!groups[key]) groups[key] = {instr, startSec, durSec, velMax:0, pitches:[]};
    groups[key].pitches.push(pitch);
    groups[key].velMax = Math.max(groups[key].velMax, vel);
  }

  const origin = Tone.now() + 0.15 - offsetSec;

  for (const grp of Object.values(groups)) {
    const syn = instruments[grp.instr];
    if (!syn) continue;
    const when = origin + grp.startSec;
    if (when < Tone.now()) continue;
    const velN = grp.velMax / 127;
    const dur  = Math.max(0.02, grp.durSec);

    try {
      if (grp.instr === 'kick')              syn.triggerAttackRelease('C2', dur, when, velN);
      else if (['snare','ghost'].includes(grp.instr)) syn.triggerAttackRelease(dur, when, velN);
      else if (['hat_c','hat_o','crash'].includes(grp.instr)) syn.triggerAttackRelease(dur, when, velN);
      else {
        const freqs = grp.pitches.map(p => Tone.Frequency(p,'midi').toNote());
        syn.triggerAttackRelease(freqs.length === 1 ? freqs[0] : freqs, dur, when, velN);
      }
    } catch(e) { /* ignore scheduling errors */ }
  }
}

function stopPlayback() {
  state.isPlaying = false;
  state.pausedAt  = null;
  if (state.rafId) { cancelAnimationFrame(state.rafId); state.rafId = null; }
  try { Tone.getTransport().stop(); Tone.getTransport().cancel(); } catch(e) {}
  document.getElementById('time-display').textContent = '0:00';
  document.getElementById('btn-play').className = 'ctrl-play';
  document.getElementById('btn-play').textContent = '▶';
  DAW.setPlayhead(0);
}

function pausePlayback() {
  const elapsed = (performance.now() - state.playStartWallClock) / 1000;
  state.pausedAt = state.playStartAudioSec + elapsed;
  state.isPlaying = false;
  if (state.rafId) { cancelAnimationFrame(state.rafId); state.rafId = null; }
  try { Tone.getTransport().stop(); Tone.getTransport().cancel(); } catch(e) {}
  document.getElementById('btn-play').className = 'ctrl-play';
  document.getElementById('btn-play').textContent = '▶';
}

function playNotes(notes, fromSec) {
  const startAt = fromSec ?? 0;
  state.isPlaying = true;
  state.pausedAt  = null;
  state.playStartWallClock = performance.now() - startAt * 1000;
  state.playStartAudioSec  = startAt;

  _scheduleNotes(notes, startAt);

  document.getElementById('btn-play').className = 'ctrl-play playing';
  document.getElementById('btn-play').textContent = '⏸';

  // Animation loop
  const animate = () => {
    const elapsed = (performance.now() - state.playStartWallClock) / 1000;
    const sec = state.playStartAudioSec + elapsed;
    document.getElementById('time-display').textContent = fmtTime(elapsed);
    DAW.setPlayhead(sec);

    if (elapsed >= TOTAL_SECS + 0.5) {
      stopPlayback();
      return;
    }
    state.rafId = requestAnimationFrame(animate);
  };
  state.rafId = requestAnimationFrame(animate);
}

// ═══════════════════════════════════════════════════════════════
// COMMIT NAVIGATION
// ═══════════════════════════════════════════════════════════════
function selectCommit(idx) {
  const wasPlaying = state.isPlaying;
  if (state.isPlaying) stopPlayback();

  state.cur = Math.max(0, Math.min(COMMITS.length - 1, idx));
  const commit = COMMITS[state.cur];

  // Update UI elements
  document.getElementById('sha-display').textContent = commit.sha.slice(0,8);
  document.getElementById('msg-display').textContent = commit.cmd;
  document.getElementById('act-label').textContent = `Act ${commit.act} · ${ACT_LABELS[commit.act] || ''}`;
  document.getElementById('daw-commit-label').textContent = `commit ${state.cur + 1}/${COMMITS.length}`;

  const bColor = BRANCH_COLOR[commit.branch] || '#fff';
  const badge = document.getElementById('dag-branch-badge');
  badge.textContent = commit.branch;
  badge.style.color = bColor;
  badge.style.borderColor = bColor + '40';
  badge.style.background = bColor + '14';

  DAG.select(state.cur);
  DAW.render(commit);
  DimPanel.update(commit);
  CmdLog.show(commit);

  if (wasPlaying && commit.notes.length) {
    playNotes(commit.notes, 0);
  }
}

// ═══════════════════════════════════════════════════════════════
// DAG RENDERER
// ═══════════════════════════════════════════════════════════════
const DAG = (() => {
  const W = 300, PADX = 30, PADY = 22, NODE_R = 11;

  // Assign column per branch
  const BRANCH_COL = {
    'main':0, 'feat/groove':1, 'feat/harmony':2,
    'conflict/brass-a':1, 'conflict/ensemble':2
  };

  const positions = COMMITS.map((c, i) => {
    const col = BRANCH_COL[c.branch] ?? 0;
    const ncols = 3;
    const xStep = (W - 2*PADX) / (ncols - 0.5);
    return { x: PADX + col * xStep, y: PADY + i * 34, c };
  });

  const H = PADY + (COMMITS.length - 1) * 34 + PADY + 10;
  const svg = d3.select('#dag-svg').attr('width', W).attr('height', H);

  // Gradient defs
  const defs = svg.append('defs');
  Object.entries(BRANCH_COLOR).forEach(([branch, color]) => {
    const g = defs.append('radialGradient').attr('id', `glow-${branch.replace(/\\W/g,'_')}`);
    g.append('stop').attr('offset','0%').attr('stop-color', color).attr('stop-opacity', 0.4);
    g.append('stop').attr('offset','100%').attr('stop-color', color).attr('stop-opacity', 0);
  });

  // Edges
  COMMITS.forEach((c, i) => {
    const p2 = positions[i];
    (c.parents || []).forEach(psha => {
      const pi = COMMITS.findIndex(x => x.sha === psha);
      if (pi < 0) return;
      const p1 = positions[pi];
      if (p1.x === p2.x) {
        svg.append('line')
          .attr('x1', p1.x).attr('y1', p1.y)
          .attr('x2', p2.x).attr('y2', p2.y - NODE_R - 1)
          .attr('stroke', BRANCH_COLOR[c.branch] || '#666')
          .attr('stroke-width', 1.5).attr('stroke-opacity', 0.4);
      } else {
        const my = (p1.y + p2.y) / 2;
        const path = `M${p1.x},${p1.y} C${p1.x},${my} ${p2.x},${my} ${p2.x},${p2.y - NODE_R - 1}`;
        svg.append('path').attr('d', path).attr('fill','none')
          .attr('stroke', BRANCH_COLOR[c.branch] || '#666')
          .attr('stroke-width', 1.5).attr('stroke-opacity', 0.3)
          .attr('stroke-dasharray', '4,2');
      }
    });
  });

  // Nodes
  const nodeGs = svg.selectAll('.dag-node').data(COMMITS).join('g')
    .attr('class','dag-node')
    .attr('transform',(_,i) => `translate(${positions[i].x},${positions[i].y})`)
    .attr('cursor','pointer')
    .on('click',(_,d) => selectCommit(COMMITS.indexOf(d)));

  // Glow
  nodeGs.append('circle').attr('r', NODE_R+7).attr('class','node-glow')
    .attr('fill', d => `url(#glow-${d.branch.replace(/\\W/g,'_')})`)
    .attr('opacity', 0);

  // Ring
  nodeGs.append('circle').attr('r', NODE_R+3).attr('class','node-ring')
    .attr('fill','none').attr('stroke', d => BRANCH_COLOR[d.branch]||'#fff')
    .attr('stroke-width', 1.5).attr('opacity', 0);

  // Main circle
  nodeGs.append('circle').attr('r', NODE_R)
    .attr('fill', d => d.conflict ? '#1a0505' : d.resolved ? '#011a0d' : '#0d1118')
    .attr('stroke', d => BRANCH_COLOR[d.branch]||'#fff').attr('stroke-width', 1.8);

  // Icon
  nodeGs.append('text').attr('text-anchor','middle').attr('dy','0.38em')
    .attr('font-size', 9).attr('fill', d => BRANCH_COLOR[d.branch]||'#fff')
    .attr('font-family','JetBrains Mono, monospace')
    .text(d => d.conflict ? '⚠' : d.resolved ? '✓' : d.sha.slice(0,4));

  // Label
  nodeGs.each(function(d, i) {
    const g = d3.select(this);
    const lines = d.label.split('\\n');
    lines.forEach((line, li) => {
      g.append('text').attr('text-anchor','start')
        .attr('x', NODE_R + 5).attr('y', (li - (lines.length-1)/2) * 11 + 1)
        .attr('font-size', 8.5).attr('fill','rgba(255,255,255,0.45)')
        .attr('font-family','JetBrains Mono, monospace').text(line);
    });
  });

  function select(idx) {
    svg.selectAll('.node-ring').attr('opacity', 0);
    svg.selectAll('.node-glow').attr('opacity', 0);
    const c = COMMITS[idx];
    svg.selectAll('.dag-node').filter(d => d.sha === c.sha)
      .select('.node-ring').attr('opacity', 1);
    svg.selectAll('.dag-node').filter(d => d.sha === c.sha)
      .select('.node-glow').attr('opacity', 1);
  }

  return { select };
})();

// ═══════════════════════════════════════════════════════════════
// DAW TRACK VIEW
// ═══════════════════════════════════════════════════════════════
const DAW = (() => {
  const LABEL_W = 64;
  const DRUM_TYPES = { crash:0, hat_o:1, hat_c:2, ghost:3, snare:4, kick:5 };

  const TRACKS = [
    { key:'drums',  label:'DRUMS',    instrs:['kick','snare','hat_c','hat_o','ghost','crash'], color:'#ef4444', h:62 },
    { key:'bass',   label:'BASS',     instrs:['bass'],   color:'#a855f7', h:44, pMin:36, pMax:60 },
    { key:'epiano', label:'E.PIANO',  instrs:['epiano'], color:'#22d3ee', h:52, pMin:50, pMax:74 },
    { key:'lead',   label:'LEAD',     instrs:['lead'],   color:'#f472b6', h:44, pMin:62, pMax:78 },
    { key:'brass',  label:'BRASS',    instrs:['brass','brassb'], color:'#34d399', h:44, pMin:50, pMax:78 },
  ];

  const GAP = 5;
  const totalH = TRACKS.reduce((a,t) => a + t.h + GAP, 0) + 30; // +30 for time axis
  const svgW = 720;

  const svg = d3.select('#daw-svg').attr('width', svgW).attr('height', totalH);
  d3.select('#daw-svg').style('min-width', `${svgW}px`);

  const contentW = svgW - LABEL_W;
  const xScale = d3.scaleLinear().domain([0, TOTAL_SECS]).range([LABEL_W, svgW - 8]);

  // Time axis
  const timeG = svg.append('g').attr('transform', `translate(0,${totalH - 24})`);
  timeG.append('line').attr('x1', LABEL_W).attr('x2', svgW-8).attr('y1',0).attr('y2',0)
    .attr('stroke','rgba(255,255,255,0.1)');
  d3.range(0, TOTAL_SECS+1, BAR).forEach(sec => {
    const x = xScale(sec);
    timeG.append('line').attr('x1',x).attr('x2',x).attr('y1',0).attr('y2',5)
      .attr('stroke','rgba(255,255,255,0.2)');
    timeG.append('text').attr('x',x).attr('y',15)
      .attr('class','daw-time-label').attr('text-anchor','middle')
      .text(`${Math.round(sec)}s`);
  });

  // Bar lines (every beat)
  d3.range(0, TOTAL_SECS, BEAT).forEach(sec => {
    svg.append('line')
      .attr('x1', xScale(sec)).attr('x2', xScale(sec))
      .attr('y1', 0).attr('y2', totalH-24)
      .attr('stroke', sec % BAR < 0.01 ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.025)')
      .attr('stroke-width', sec % BAR < 0.01 ? 1 : 0.5);
  });

  // Track backgrounds
  let yOff = 0;
  TRACKS.forEach(track => {
    svg.append('rect').attr('x', LABEL_W).attr('y', yOff).attr('width', contentW)
      .attr('height', track.h).attr('fill','rgba(255,255,255,0.015)').attr('rx', 3);
    svg.append('text').attr('x', LABEL_W - 6).attr('y', yOff + track.h/2 + 1)
      .attr('class','daw-track-label').attr('dy','0.35em').text(track.label)
      .attr('fill', track.color + '88');
    // Separator
    svg.append('line').attr('x1', 0).attr('x2', svgW)
      .attr('y1', yOff + track.h + GAP/2).attr('y2', yOff + track.h + GAP/2)
      .attr('stroke','rgba(255,255,255,0.04)');
    yOff += track.h + GAP;
  });

  // Note groups (cleared on each render)
  const notesG = svg.append('g').attr('class','notes-g');

  // Playhead
  const playheadG = svg.append('g');
  const playheadLine = playheadG.append('line').attr('class','playhead-line')
    .attr('x1', xScale(0)).attr('x2', xScale(0))
    .attr('y1', 0).attr('y2', totalH - 26).attr('opacity', 0);

  function setPlayhead(sec) {
    const x = xScale(Math.min(sec, TOTAL_SECS));
    playheadLine.attr('x1', x).attr('x2', x).attr('opacity', sec > 0 ? 0.8 : 0);
  }

  function render(commit) {
    notesG.selectAll('*').remove();
    const notes = commit.notes || [];
    if (!notes.length) return;

    const byInstr = {};
    for (const [pitch, vel, startSec, durSec, instr] of notes) {
      (byInstr[instr] = byInstr[instr] || []).push([pitch, vel, startSec, durSec]);
    }

    let yOff = 0;
    TRACKS.forEach(track => {
      const trackNotes = track.instrs.flatMap(k => (byInstr[k] || []).map(n => ({...n, instr:k})));
      if (!trackNotes.length) { yOff += track.h + GAP; return; }

      if (track.key === 'drums') {
        const nRows = 6;
        const rowH  = (track.h - 4) / nRows;
        for (const nt of trackNotes) {
          const row = DRUM_TYPES[nt.instr] ?? 2;
          const y = yOff + 2 + row * rowH;
          const x = xScale(nt[2]);
          const w = Math.max(2, (xScale(nt[2] + nt[3]) - x) * 0.9);
          notesG.append('rect').attr('x', x).attr('y', y).attr('width', w)
            .attr('height', rowH - 1).attr('rx', 1)
            .attr('fill', INSTR_COLOR[nt.instr] || '#fff')
            .attr('opacity', nt.instr === 'ghost' ? 0.4 : 0.85);
        }
      } else {
        const pMin = track.pMin || 36;
        const pMax = track.pMax || 80;
        for (const nt of trackNotes) {
          const pitch = nt[0]; const vel = nt[1];
          const frac  = (pitch - pMin) / (pMax - pMin);
          const y     = yOff + track.h - 4 - frac * (track.h - 8);
          const x     = xScale(nt[2]);
          const w     = Math.max(3, (xScale(nt[2] + nt[3]) - x) * 0.9);
          const alpha = 0.5 + (vel / 127) * 0.5;
          notesG.append('rect').attr('x', x).attr('y', y - 3)
            .attr('width', w).attr('height', 6).attr('rx', 2)
            .attr('fill', INSTR_COLOR[nt.instr] || track.color)
            .attr('opacity', alpha);
        }
      }

      yOff += track.h + GAP;
    });
  }

  return { render, setPlayhead };
})();

// ═══════════════════════════════════════════════════════════════
// 21-DIMENSION PANEL
// ═══════════════════════════════════════════════════════════════
const DimPanel = (() => {
  const container = document.getElementById('dim-list');
  const groups = ['core','expr','cc','fx','meta'];
  const GL = {core:'Core',expr:'Expression',cc:'Controllers (CC)',fx:'Effects',meta:'Meta / Structure'};

  groups.forEach(grp => {
    const dims = DIMS_21.filter(d => d.group === grp);
    if (!dims.length) return;
    const lbl = document.createElement('div');
    lbl.className = 'dim-group-label'; lbl.textContent = GL[grp];
    container.appendChild(lbl);
    dims.forEach(dim => {
      const row = document.createElement('div');
      row.className = 'dim-row'; row.id = `dr-${dim.id}`; row.title = dim.desc;
      row.innerHTML = `<div class="dim-dot" id="dd-${dim.id}"></div>
                       <div class="dim-name">${dim.label}</div>
                       <div class="dim-bar-wrap"><div class="dim-bar" id="db-${dim.id}"></div></div>`;
      container.appendChild(row);
    });
  });

  function update(commit) {
    const act = commit.dimAct || {};
    let cnt = 0;
    DIMS_21.forEach(dim => {
      const level = act[dim.id] || 0;
      const row = document.getElementById(`dr-${dim.id}`);
      const dot = document.getElementById(`dd-${dim.id}`);
      const bar = document.getElementById(`db-${dim.id}`);
      if (!row) return;
      if (level > 0) {
        cnt++;
        row.classList.add('active');
        dot.style.background = dim.color; dot.style.boxShadow = `0 0 5px ${dim.color}`;
        bar.style.background = dim.color; bar.style.width = `${Math.min(level*25,100)}%`;
      } else {
        row.classList.remove('active');
        dot.style.background = 'rgba(255,255,255,0.12)'; dot.style.boxShadow = '';
        bar.style.width = '0'; bar.style.background = '';
      }
    });
    document.getElementById('dim-active-count').textContent = `${cnt} active`;
  }

  return { update };
})();

// ═══════════════════════════════════════════════════════════════
// COMMAND LOG
// ═══════════════════════════════════════════════════════════════
const CmdLog = (() => {
  const prompt = document.getElementById('cmd-prompt');
  let timer = null;

  function show(commit) {
    if (timer) clearTimeout(timer);
    const lines = commit.output.split('\\n');
    const isWarn = commit.conflict;
    const isOk   = commit.resolved;

    let html = `<div class="cmd-line">$ ${commit.cmd}</div>`;
    lines.forEach(line => {
      const cls = line.startsWith('⚠') || line.includes('CONFLICT') ? 'cmd-warn'
                : line.startsWith('✓') ? 'cmd-ok'
                : line.startsWith('✗') ? 'cmd-err'
                : '';
      html += `<div class="${cls}">${line}</div>`;
    });
    prompt.innerHTML = html;
  }

  return { show };
})();

// ═══════════════════════════════════════════════════════════════
// HEATMAP
// ═══════════════════════════════════════════════════════════════
(function buildHeatmap() {
  const cellW = 32, cellH = 10, padL = 88, padT = 10;
  const nCols = COMMITS.length;
  const nRows = DIMS_21.length;
  const W = padL + nCols * cellW + 10;
  const H = padT + nRows * cellH + 22;

  const svg = d3.select('#heatmap-svg').attr('width', W).attr('height', H);
  d3.select('#heatmap-svg').style('min-width', `${W}px`);

  // Row labels
  DIMS_21.forEach((dim, ri) => {
    svg.append('text').attr('x', padL - 4).attr('y', padT + ri * cellH + cellH/2 + 1)
      .attr('text-anchor','end').attr('dy','0.35em')
      .attr('font-family','JetBrains Mono,monospace').attr('font-size', 7.5)
      .attr('fill', dim.color + 'aa').text(dim.label);
  });

  // Col labels (sha)
  COMMITS.forEach((c, ci) => {
    svg.append('text').attr('x', padL + ci * cellW + cellW/2)
      .attr('y', H - 6).attr('text-anchor','middle')
      .attr('font-family','JetBrains Mono,monospace').attr('font-size', 7)
      .attr('fill', BRANCH_COLOR[c.branch] + 'aa').text(c.sha.slice(0,4));
  });

  // Cells
  const cells = svg.selectAll('.hm-cell')
    .data(COMMITS.flatMap((c,ci) => DIMS_21.map((dim,ri) => ({ci,ri,dim,c,level:c.dimAct[dim.id]||0}))))
    .join('rect').attr('class','hm-cell')
    .attr('x', d => padL + d.ci * cellW + 1)
    .attr('y', d => padT + d.ri * cellH + 1)
    .attr('width', cellW - 2).attr('height', cellH - 2).attr('rx', 1)
    .attr('fill', d => d.level > 0 ? d.dim.color : 'rgba(255,255,255,0.04)')
    .attr('opacity', d => d.level > 0 ? Math.min(0.9, 0.25 + d.level * 0.22) : 1)
    .attr('cursor','pointer')
    .on('mouseover', function(evt, d) {
      d3.select(this).attr('stroke', d.dim.color).attr('stroke-width', 1);
    })
    .on('mouseout', function() { d3.select(this).attr('stroke','none'); });

  // Highlight column on commit select
  window._heatmapSelectCol = function(idx) {
    cells.attr('opacity', d => {
      const base = d.level > 0 ? Math.min(0.9, 0.25 + d.level * 0.22) : 1;
      if (d.ci !== idx) return d.level > 0 ? base * 0.4 : 0.3;
      return base;
    });
    svg.selectAll('.hm-col-hl').remove();
    svg.append('rect').attr('class','hm-col-hl')
      .attr('x', padL + idx * cellW).attr('y', padT - 2)
      .attr('width', cellW).attr('height', nRows * cellH + 4)
      .attr('fill','none').attr('stroke', BRANCH_COLOR[COMMITS[idx].branch]||'#fff')
      .attr('stroke-width', 1).attr('rx', 2).attr('opacity', 0.45);
  };
})();

// ═══════════════════════════════════════════════════════════════
// BRANCH LEGEND
// ═══════════════════════════════════════════════════════════════
(function buildLegend() {
  const el = document.getElementById('branch-legend');
  Object.entries(BRANCH_COLOR).forEach(([branch, color]) => {
    const item = document.createElement('div');
    item.className = 'bl-item';
    item.innerHTML = `<div class="bl-dot" style="background:${color};box-shadow:0 0 5px ${color}"></div>
                      <span>${branch}</span>`;
    el.appendChild(item);
  });
})();

// ═══════════════════════════════════════════════════════════════
// CLI REFERENCE
// ═══════════════════════════════════════════════════════════════
(function buildCLI() {
  const commands = [
    { cmd:'muse init --domain midi',
      desc:'Initialize a Muse repository with the MIDI domain plugin.',
      flags:['--domain <name>   specify domain plugin (midi, code, …)',
             '--bare            create a bare repository'],
      ret:'✓ .muse/ directory created with domain config' },
    { cmd:'muse commit -m <msg>',
      desc:'Snapshot current MIDI state and create a new commit.',
      flags:['-m <message>      commit message',
             '--domain <name>   override domain for this commit',
             '--no-verify       skip pre-commit hooks'],
      ret:'[<branch> <sha8>] <message>' },
    { cmd:'muse status',
      desc:'Show working directory status vs HEAD snapshot.',
      flags:['--short           machine-readable one-line output',
             '--porcelain       stable scripting format'],
      ret:'Added/modified/removed files; clean or dirty state' },
    { cmd:'muse diff [<sha>]',
      desc:'Show 21-dimensional delta between working dir and a commit.',
      flags:['--stat            summary only (file counts + dim counts)',
             '--dim <name>      filter to one MIDI dimension',
             '--commit <sha>    compare two commits'],
      ret:'StructuredDelta per file: notes±, CC changes, bend curves' },
    { cmd:'muse log [--oneline] [--stat]',
      desc:'Show commit history with branch topology.',
      flags:['--oneline         compact one-line format',
             '--stat            include files + dimension summary',
             '--graph           ASCII branch graph'],
      ret:'Ordered commit list with SHA, message, branch, timestamp' },
    { cmd:'muse branch -b <name>',
      desc:'Create a new branch at the current HEAD.',
      flags:['-b <name>         name of the new branch',
             '--list            list all branches',
             '-d <name>         delete a branch'],
      ret:'✓ Branch <name> created at <sha8>' },
    { cmd:'muse checkout <branch>',
      desc:'Switch to a branch or restore a commit.',
      flags:['<branch>          branch name or commit SHA',
             '-b <name>         create and switch in one step'],
      ret:'Switched to branch <name>; working dir restored' },
    { cmd:'muse merge <branch> [<branch2>]',
      desc:'Three-way MIDI merge using the 21-dim engine.',
      flags:['<branch>          branch to merge into current',
             '--strategy ours|theirs|auto   conflict resolution',
             '--no-ff           always create a merge commit'],
      ret:'✓ 0 conflicts — or — ⚠ CONFLICT in <dim> on <file>' },
    { cmd:'muse resolve --strategy <s> <dim>',
      desc:'Resolve a dimension conflict after a failed merge.',
      flags:['--strategy ours|theirs|auto|manual   merge strategy',
             '<dim>             MIDI dimension to resolve (e.g. cc_reverb)'],
      ret:'✓ Resolved <dim> using strategy <s>' },
    { cmd:'muse stash / stash pop',
      desc:'Park uncommitted changes and restore later.',
      flags:['stash             save working dir to stash',
             'stash pop         restore last stash',
             'stash list        list all stash entries'],
      ret:'✓ Stashed <N> changes / ✓ Popped stash@{0}' },
    { cmd:'muse cherry-pick <sha>',
      desc:'Apply a single commit from any branch.',
      flags:['<sha>             commit ID to cherry-pick (full or short)',
             '--no-commit       apply changes without committing'],
      ret:'[<branch> <sha8>] cherry-pick of <src-sha>' },
    { cmd:'muse tag add <name>',
      desc:'Create a lightweight tag at the current HEAD.',
      flags:['add <name>        create tag',
             'list              list all tags',
             'delete <name>     delete a tag'],
      ret:'✓ Tag <name> → <sha8>' },
  ];

  const grid = document.getElementById('cli-grid');
  commands.forEach(c => {
    const card = document.createElement('div');
    card.className = 'cli-card';
    card.innerHTML = `<div class="cli-cmd">$ ${c.cmd}</div>
      <div class="cli-desc">${c.desc}</div>
      <div class="cli-flags">${c.flags.map(f => `<div class="cli-flag">${f.replace(/^(--?\\S+)/,'<span>$1</span>')}</div>`).join('')}</div>
      <div style="margin-top:6px;font-size:10px;color:rgba(255,255,255,0.3);font-family:'JetBrains Mono',monospace">→ ${c.ret}</div>`;
    grid.appendChild(card);
  });
})();

// ═══════════════════════════════════════════════════════════════
// EVENT WIRING
// ═══════════════════════════════════════════════════════════════
document.getElementById('btn-init-audio').addEventListener('click', initAudio);

document.getElementById('btn-play').addEventListener('click', () => {
  if (!state.audioReady) { initAudio(); return; }
  const commit = COMMITS[state.cur];
  if (!commit.notes.length) return;

  if (state.isPlaying) {
    pausePlayback();
  } else if (state.pausedAt !== null) {
    playNotes(commit.notes, state.pausedAt);
  } else {
    playNotes(commit.notes, 0);
  }
});

document.getElementById('btn-prev').addEventListener('click', () => selectCommit(state.cur - 1));
document.getElementById('btn-next').addEventListener('click', () => selectCommit(state.cur + 1));
document.getElementById('btn-first').addEventListener('click', () => selectCommit(0));
document.getElementById('btn-last').addEventListener('click', () => selectCommit(COMMITS.length - 1));

document.addEventListener('keydown', e => {
  if (e.key === ' ')          { e.preventDefault(); document.getElementById('btn-play').click(); }
  else if (e.key === 'ArrowRight') selectCommit(state.cur + 1);
  else if (e.key === 'ArrowLeft')  selectCommit(state.cur - 1);
});

// ═══════════════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════════════
document.getElementById('btn-play').disabled = true;
selectCommit(0);
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# RENDERER
# ─────────────────────────────────────────────────────────────────────────────

def render_midi_demo() -> str:
    """Build and return the complete HTML string."""
    commits = _build_commits()

    # Serialize commits (notes lists contain mixed-type elements)
    commits_json = json.dumps(commits, separators=(",", ":"))

    html = _HTML
    html = html.replace("__BPM__", str(BPM))
    html = html.replace("__COMMITS__", commits_json)
    return html


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate artifacts/midi-demo.html")
    parser.add_argument("--output-dir", default="artifacts", help="Output directory")
    args = parser.parse_args()

    out_dir = pathlib.Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    html = render_midi_demo()
    out_path = out_dir / "midi-demo.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info("Written: %s (%d bytes)", out_path, len(html))
    print(f"✓ MIDI demo → {out_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
