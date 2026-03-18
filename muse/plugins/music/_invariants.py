"""Musical invariants engine for the Muse music plugin.

Invariants are semantic rules that a MIDI track must satisfy.  They are
evaluated at commit time, merge time, or on-demand via ``muse music-check``.
Violations are reported with human-readable descriptions, severity levels,
and structured addresses for programmatic consumers.

Rule file format (TOML)
-----------------------
Rules are declared in ``.muse/music_invariants.toml`` (default path).
Example::

    [[rule]]
    name = "max_polyphony"
    severity = "error"
    scope = "track"
    rule_type = "max_polyphony"

    [rule.params]
    max_simultaneous = 6

    [[rule]]
    name = "keep_in_range"
    severity = "warning"
    scope = "track"
    rule_type = "pitch_range"

    [rule.params]
    min_pitch = 24
    max_pitch = 108

    [[rule]]
    name = "no_fifths"
    severity = "warning"
    scope = "voice_pair"
    rule_type = "no_parallel_fifths"

    [[rule]]
    name = "consistent_key"
    severity = "info"
    scope = "track"
    rule_type = "key_consistency"

    [rule.params]
    threshold = 0.15

Built-in rule types
-------------------

``max_polyphony``
    Detects bars where more than *max_simultaneous* notes overlap at any
    tick position.  Uses a sweep-line algorithm over start/end tick events.

``pitch_range``
    Detects any note with ``pitch < min_pitch`` or ``pitch > max_pitch``.

``key_consistency``
    Detects notes whose pitch class is highly inconsistent with the key
    estimated by the Krumhansl-Schmuckler algorithm.  Fires when the ratio
    of "foreign" pitch classes exceeds *threshold*.

``no_parallel_fifths``
    Detects consecutive bars where the lowest voice and the second-lowest
    voice both move by a perfect fifth in parallel (a classical counterpoint
    violation).  Best-effort heuristic — voice assignment is implicit.

Severity levels
---------------
- ``"error"`` — must be resolved before committing (when ``--strict`` is set).
- ``"warning"`` — reported but does not block commits.
- ``"info"`` — informational; surfaced in ``muse music-check`` output only.

Public API
----------
- :class:`InvariantRule`       — rule declaration TypedDict.
- :class:`InvariantViolation`  — single violation record TypedDict.
- :class:`InvariantReport`     — full report for one commit / track.
- :func:`load_invariant_rules` — load from TOML file with defaults fallback.
- :func:`run_invariants`       — evaluate all rules against a commit.
"""
from __future__ import annotations

import logging
import pathlib
from typing import Literal, TypedDict

from muse.core.object_store import read_object
from muse.core.store import get_commit_snapshot_manifest
from muse.plugins.music._query import NoteInfo, key_signature_guess, notes_by_bar
from muse.plugins.music.midi_diff import extract_notes

logger = logging.getLogger(__name__)

_DEFAULT_RULES_FILE = ".muse/music_invariants.toml"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class _InvariantRuleRequired(TypedDict):
    name: str
    severity: Literal["info", "warning", "error"]
    scope: Literal["track", "bar", "voice_pair", "global"]
    rule_type: str


class InvariantRule(_InvariantRuleRequired, total=False):
    """Declaration of one musical invariant rule.

    ``name``       Human-readable rule identifier (unique within a rule set).
    ``severity``   Violation severity: ``"info"``, ``"warning"``, or ``"error"``.
    ``scope``      Granularity: ``"track"``, ``"bar"``, ``"voice_pair"``, ``"global"``.
    ``rule_type``  Built-in type string: ``"max_polyphony"``, ``"pitch_range"``,
                   ``"key_consistency"``, ``"no_parallel_fifths"``.
    ``params``     Rule-specific parameter dict.
    """

    params: dict[str, str | int | float]


class InvariantViolation(TypedDict):
    """A single invariant violation record.

    ``rule_name``   The name of the rule that fired.
    ``severity``    Severity level from the rule declaration.
    ``track``       Workspace-relative MIDI file path.
    ``bar``         1-indexed bar number (0 for track-level violations).
    ``description`` Human-readable explanation of what was violated.
    ``addresses``   Note addresses or other domain addresses involved.
    """

    rule_name: str
    severity: Literal["info", "warning", "error"]
    track: str
    bar: int
    description: str
    addresses: list[str]


class InvariantReport(TypedDict):
    """Full invariant check report for one commit.

    ``commit_id``     The commit that was checked.
    ``violations``    All violations found, sorted by track then bar.
    ``rules_checked`` Number of rules evaluated.
    ``has_errors``    True when any violation has severity ``"error"``.
    ``has_warnings``  True when any violation has severity ``"warning"``.
    """

    commit_id: str
    violations: list[InvariantViolation]
    rules_checked: int
    has_errors: bool
    has_warnings: bool


# ---------------------------------------------------------------------------
# Built-in rule implementations
# ---------------------------------------------------------------------------


def check_max_polyphony(
    notes: list[NoteInfo],
    track: str,
    rule_name: str,
    severity: Literal["info", "warning", "error"],
    *,
    max_simultaneous: int = 6,
) -> list[InvariantViolation]:
    """Find bars where simultaneous note count exceeds *max_simultaneous*.

    Uses a tick-based sweep-line over (start_tick, end_tick) intervals.
    Reports one violation per offending bar.

    Args:
        notes:           All notes in the track.
        track:           Track file path for violation records.
        rule_name:       Rule identifier string.
        severity:        Violation severity.
        max_simultaneous: Maximum allowed simultaneous notes.

    Returns:
        List of :class:`InvariantViolation` records.
    """
    violations: list[InvariantViolation] = []
    bars = notes_by_bar(notes)

    for bar_num, bar_notes in sorted(bars.items()):
        # Collect all tick events: +1 for note_on, -1 for note_off.
        events: list[tuple[int, int]] = []
        for n in bar_notes:
            events.append((n.start_tick, 1))
            events.append((n.start_tick + n.duration_ticks, -1))
        events.sort(key=lambda e: (e[0], e[1]))  # off before on at same tick

        current = 0
        peak = 0
        peak_tick = 0
        for tick, delta in events:
            current += delta
            if current > peak:
                peak = current
                peak_tick = tick

        if peak > max_simultaneous:
            violations.append(
                InvariantViolation(
                    rule_name=rule_name,
                    severity=severity,
                    track=track,
                    bar=bar_num,
                    description=(
                        f"Polyphony reached {peak} simultaneous notes at tick {peak_tick} "
                        f"(max allowed: {max_simultaneous})"
                    ),
                    addresses=[f"bar:{bar_num}:tick:{peak_tick}"],
                )
            )

    return violations


def check_pitch_range(
    notes: list[NoteInfo],
    track: str,
    rule_name: str,
    severity: Literal["info", "warning", "error"],
    *,
    min_pitch: int = 0,
    max_pitch: int = 127,
) -> list[InvariantViolation]:
    """Find notes outside the allowed MIDI pitch range.

    Args:
        notes:     All notes in the track.
        track:     Track file path.
        rule_name: Rule identifier.
        severity:  Violation severity.
        min_pitch: Lowest allowed MIDI pitch (inclusive).
        max_pitch: Highest allowed MIDI pitch (inclusive).

    Returns:
        One :class:`InvariantViolation` per out-of-range note.
    """
    violations: list[InvariantViolation] = []
    for note in notes:
        if note.pitch < min_pitch or note.pitch > max_pitch:
            violations.append(
                InvariantViolation(
                    rule_name=rule_name,
                    severity=severity,
                    track=track,
                    bar=note.bar,
                    description=(
                        f"Note {note.pitch_name} (MIDI {note.pitch}) is outside "
                        f"allowed range [{min_pitch}, {max_pitch}]"
                    ),
                    addresses=[f"bar:{note.bar}:pitch:{note.pitch}"],
                )
            )
    return violations


def check_key_consistency(
    notes: list[NoteInfo],
    track: str,
    rule_name: str,
    severity: Literal["info", "warning", "error"],
    *,
    threshold: float = 0.15,
) -> list[InvariantViolation]:
    """Detect notes whose pitch class is inconsistent with the guessed key.

    Estimates the key using the Krumhansl-Schmuckler algorithm, then counts
    the fraction of notes that use a pitch class not diatonic to that key.
    Fires when the foreign-note ratio exceeds *threshold*.

    Args:
        notes:     All notes in the track.
        track:     Track file path.
        rule_name: Rule identifier.
        severity:  Violation severity.
        threshold: Maximum allowed ratio of foreign pitch classes (0.0–1.0).

    Returns:
        Zero or one :class:`InvariantViolation` for the track.
    """
    if not notes:
        return []

    key_guess = key_signature_guess(notes)
    # Parse key guess string e.g. "G major" or "D minor".
    parts = key_guess.split()
    if len(parts) < 2:
        return []

    root_name = parts[0]
    mode = parts[1]

    pitch_classes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    root_idx = pitch_classes.index(root_name) if root_name in pitch_classes else -1
    if root_idx < 0:
        return []

    # Diatonic pitch classes for major and natural minor scales.
    major_steps = [0, 2, 4, 5, 7, 9, 11]
    minor_steps = [0, 2, 3, 5, 7, 8, 10]
    steps = major_steps if mode == "major" else minor_steps
    diatonic_pcs = frozenset((root_idx + s) % 12 for s in steps)

    foreign = sum(1 for n in notes if n.pitch_class not in diatonic_pcs)
    ratio = foreign / len(notes)

    if ratio > threshold:
        return [
            InvariantViolation(
                rule_name=rule_name,
                severity=severity,
                track=track,
                bar=0,
                description=(
                    f"{foreign}/{len(notes)} notes ({ratio:.0%}) use pitch classes "
                    f"foreign to estimated key {key_guess} "
                    f"(threshold: {threshold:.0%})"
                ),
                addresses=[track],
            )
        ]
    return []


def check_no_parallel_fifths(
    notes: list[NoteInfo],
    track: str,
    rule_name: str,
    severity: Literal["info", "warning", "error"],
) -> list[InvariantViolation]:
    """Detect consecutive bars with parallel perfect fifth motion.

    Heuristic: for each pair of consecutive bars, find the two lowest-pitched
    notes (approximating bass and tenor voices) and check whether both voices
    move by a perfect fifth (7 semitones) in the same direction.

    This is a best-effort approximation — accurate voice separation would
    require dedicated voice-leading analysis beyond this scope.

    Args:
        notes:     All notes in the track.
        track:     Track file path.
        rule_name: Rule identifier.
        severity:  Violation severity.

    Returns:
        One :class:`InvariantViolation` per detected parallel-fifth bar pair.
    """
    violations: list[InvariantViolation] = []
    bars = notes_by_bar(notes)
    sorted_bars = sorted(bars.keys())

    for i in range(len(sorted_bars) - 1):
        bar_a = sorted_bars[i]
        bar_b = sorted_bars[i + 1]
        notes_a = sorted(bars[bar_a], key=lambda n: n.pitch)
        notes_b = sorted(bars[bar_b], key=lambda n: n.pitch)

        if len(notes_a) < 2 or len(notes_b) < 2:
            continue

        # Take two lowest pitches as approximated bass + tenor voices.
        v1_a, v2_a = notes_a[0].pitch, notes_a[1].pitch
        v1_b, v2_b = notes_b[0].pitch, notes_b[1].pitch

        # Interval between voices in each bar.
        interval_a = abs(v2_a - v1_a) % 12
        interval_b = abs(v2_b - v1_b) % 12

        # Both form a perfect fifth (7 semitones modulo octave)?
        if interval_a == 7 and interval_b == 7:
            # Both voices moved in the same direction?
            motion_v1 = v1_b - v1_a
            motion_v2 = v2_b - v2_a
            if (motion_v1 > 0 and motion_v2 > 0) or (motion_v1 < 0 and motion_v2 < 0):
                violations.append(
                    InvariantViolation(
                        rule_name=rule_name,
                        severity=severity,
                        track=track,
                        bar=bar_b,
                        description=(
                            f"Parallel fifths between bars {bar_a} and {bar_b}: "
                            f"lower voice {notes_a[0].pitch_name}→{notes_b[0].pitch_name}, "
                            f"upper voice {notes_a[1].pitch_name}→{notes_b[1].pitch_name}"
                        ),
                        addresses=[f"bar:{bar_a}", f"bar:{bar_b}"],
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------

_DEFAULT_RULE_SET: list[InvariantRule] = [
    InvariantRule(
        name="max_polyphony",
        severity="warning",
        scope="track",
        rule_type="max_polyphony",
        params={"max_simultaneous": 8},
    ),
    InvariantRule(
        name="pitch_range",
        severity="warning",
        scope="track",
        rule_type="pitch_range",
        params={"min_pitch": 0, "max_pitch": 127},
    ),
]


def load_invariant_rules(rules_file: pathlib.Path | None = None) -> list[InvariantRule]:
    """Load invariant rules from a TOML file, falling back to defaults.

    Requires ``tomllib`` (Python 3.11+) for TOML parsing.  If the file does
    not exist or cannot be parsed, the default rule set is returned.

    Args:
        rules_file: Path to the TOML rule file.  ``None`` means use defaults.

    Returns:
        List of :class:`InvariantRule` dicts.
    """
    if rules_file is None or not rules_file.exists():
        return list(_DEFAULT_RULE_SET)

    try:
        import tomllib

        with rules_file.open("rb") as fh:
            data = tomllib.load(fh)

        rules: list[InvariantRule] = []
        for raw in data.get("rule", []):
            _valid_severities: dict[str, Literal["info", "warning", "error"]] = {
                "info": "info", "warning": "warning", "error": "error",
            }
            _valid_scopes: dict[str, Literal["track", "bar", "voice_pair", "global"]] = {
                "track": "track", "bar": "bar", "voice_pair": "voice_pair", "global": "global",
            }
            sev = _valid_severities.get(str(raw.get("severity", "")), "warning")
            scope = _valid_scopes.get(str(raw.get("scope", "")), "track")
            rule = InvariantRule(
                name=str(raw.get("name", "unnamed")),
                severity=sev,
                scope=scope,
                rule_type=str(raw.get("rule_type", "")),
            )
            if "params" in raw:
                rule["params"] = raw["params"]
            rules.append(rule)
        return rules if rules else list(_DEFAULT_RULE_SET)

    except Exception as exc:
        logger.warning("⚠️ Could not load invariant rules from %s: %s", rules_file, exc)
        return list(_DEFAULT_RULE_SET)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_invariants(
    root: "pathlib.Path",
    commit_id: str,
    rules: list[InvariantRule],
    *,
    track_filter: str | None = None,
) -> InvariantReport:
    """Evaluate all *rules* against every MIDI track in *commit_id*.

    Args:
        root:          Repository root.
        commit_id:     Commit to check.
        rules:         List of :class:`InvariantRule` declarations.
        track_filter:  Restrict check to a single MIDI file path.

    Returns:
        An :class:`InvariantReport` with all violations found.
    """
    import pathlib as _pathlib

    all_violations: list[InvariantViolation] = []
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}

    midi_paths = [
        p for p in manifest
        if p.lower().endswith(".mid")
        and (track_filter is None or p == track_filter)
    ]

    for track_path in sorted(midi_paths):
        obj_hash = manifest.get(track_path)
        if obj_hash is None:
            continue
        raw = read_object(root, obj_hash)
        if raw is None:
            continue
        try:
            keys, tpb = extract_notes(raw)
        except ValueError as exc:
            logger.debug("Cannot parse MIDI %r: %s", track_path, exc)
            continue

        notes = [NoteInfo.from_note_key(k, tpb) for k in keys]

        for rule in rules:
            rt = rule["rule_type"]
            sev = rule["severity"]
            params = rule.get("params", {})
            name = rule["name"]

            if rt == "max_polyphony":
                max_sim = int(params.get("max_simultaneous", 8))
                all_violations.extend(
                    check_max_polyphony(notes, track_path, name, sev, max_simultaneous=max_sim)
                )
            elif rt == "pitch_range":
                min_p = int(params.get("min_pitch", 0))
                max_p = int(params.get("max_pitch", 127))
                all_violations.extend(
                    check_pitch_range(notes, track_path, name, sev, min_pitch=min_p, max_pitch=max_p)
                )
            elif rt == "key_consistency":
                thresh = float(params.get("threshold", 0.15))
                all_violations.extend(
                    check_key_consistency(notes, track_path, name, sev, threshold=thresh)
                )
            elif rt == "no_parallel_fifths":
                all_violations.extend(
                    check_no_parallel_fifths(notes, track_path, name, sev)
                )
            else:
                logger.debug("Unknown rule_type %r in rule %r — skipped", rt, name)

    all_violations.sort(key=lambda v: (v["track"], v["bar"]))
    has_errors = any(v["severity"] == "error" for v in all_violations)
    has_warnings = any(v["severity"] == "warning" for v in all_violations)

    return InvariantReport(
        commit_id=commit_id,
        violations=all_violations,
        rules_checked=len(rules) * len(midi_paths),
        has_errors=has_errors,
        has_warnings=has_warnings,
    )
