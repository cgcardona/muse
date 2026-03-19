"""Muse Tier 1 plumbing commands — machine-readable, pipeable, stable primitives.

Every command in this package:

- Emits JSON by default (machine-stable schema with ``format_version``).
- Accepts ``--format text`` for human-readable output where meaningful.
- Never prompts for input or confirmation.
- Uses strict exit codes: 0 success, 1 user error, 3 internal error.
- Is pipeable — stdin/stdout friendly, no interactive elements.

These are the atoms from which Tier 2 porcelain commands are composed, and the
surface that MuseHub, agent orchestrators, and shell scripts can rely on to
remain stable across Muse versions.
"""
