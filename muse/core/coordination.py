"""Multi-agent coordination layer for the Muse VCS.

Coordination data lives under ``.muse/coordination/``.  It is purely advisory —
the VCS engine never reads it for correctness decisions.  Its purpose is to
enable agents working in parallel to announce their intentions, detect likely
conflicts *before* they happen, and plan merges without writing to the repo.

Layout::

    .muse/coordination/
      reservations/<uuid>.json   advisory symbol lease
      intents/<uuid>.json        declared operation before an edit

Reservation schema::

    {
      "schema_version": "<muse package version>",
      "reservation_id": "<uuid>",
      "run_id": "<agent-supplied ID>",
      "branch": "<branch name>",
      "addresses": ["src/billing.py::compute_total", ...],
      "created_at": "2026-03-18T12:00:00+00:00",
      "expires_at": "2026-03-18T13:00:00+00:00",
      "operation": null | "rename" | "move" | "extract" | "modify" | "delete"
    }

Intent schema::

    {
      "schema_version": "<muse package version>",
      "intent_id": "<uuid>",
      "reservation_id": "<uuid>",
      "run_id": "<agent-supplied ID>",
      "branch": "<branch name>",
      "addresses": ["src/billing.py::compute_total"],
      "operation": "rename",
      "created_at": "2026-03-18T12:00:00+00:00",
      "detail": "rename to compute_invoice_total"
    }

All coordination records are write-once, never mutated.  Expiry is enforced
by ``is_active()`` — expired records are ignored but not deleted (they provide
a historical audit trail for the coordination session).
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
import uuid as _uuid_mod

from muse._version import __version__ as _SCHEMA_VERSION

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------


def _coord_dir(root: pathlib.Path) -> pathlib.Path:
    return root / ".muse" / "coordination"


def _reservations_dir(root: pathlib.Path) -> pathlib.Path:
    return _coord_dir(root) / "reservations"


def _intents_dir(root: pathlib.Path) -> pathlib.Path:
    return _coord_dir(root) / "intents"


def _ensure_coord_dirs(root: pathlib.Path) -> None:
    _reservations_dir(root).mkdir(parents=True, exist_ok=True)
    _intents_dir(root).mkdir(parents=True, exist_ok=True)


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_dt(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------


class Reservation:
    """An advisory lock on a set of symbol addresses."""

    def __init__(
        self,
        reservation_id: str,
        run_id: str,
        branch: str,
        addresses: list[str],
        created_at: datetime.datetime,
        expires_at: datetime.datetime,
        operation: str | None,
    ) -> None:
        self.reservation_id = reservation_id
        self.run_id = run_id
        self.branch = branch
        self.addresses = addresses
        self.created_at = created_at
        self.expires_at = expires_at
        self.operation = operation

    def is_active(self) -> bool:
        """Return True if this reservation has not yet expired."""
        return _now_utc() < self.expires_at

    def to_dict(self) -> dict[str, str | int | list[str] | None]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "reservation_id": self.reservation_id,
            "run_id": self.run_id,
            "branch": self.branch,
            "addresses": self.addresses,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "operation": self.operation,
        }

    @classmethod
    def from_dict(cls, d: dict[str, str | int | list[str] | None]) -> "Reservation":
        expires_at_raw = d.get("expires_at")
        created_at_raw = d.get("created_at")
        expires_at = _parse_dt(str(expires_at_raw)) if expires_at_raw else _now_utc()
        created_at = _parse_dt(str(created_at_raw)) if created_at_raw else _now_utc()
        addrs_raw = d.get("addresses", [])
        addrs = list(addrs_raw) if isinstance(addrs_raw, list) else []
        op_raw = d.get("operation")
        return cls(
            reservation_id=str(d.get("reservation_id", "")),
            run_id=str(d.get("run_id", "")),
            branch=str(d.get("branch", "")),
            addresses=addrs,
            created_at=created_at,
            expires_at=expires_at,
            operation=str(op_raw) if op_raw is not None else None,
        )


def create_reservation(
    root: pathlib.Path,
    run_id: str,
    branch: str,
    addresses: list[str],
    ttl_seconds: int = 3600,
    operation: str | None = None,
) -> Reservation:
    """Write and return a new reservation for *addresses*."""
    _ensure_coord_dirs(root)
    now = _now_utc()
    res = Reservation(
        reservation_id=str(_uuid_mod.uuid4()),
        run_id=run_id,
        branch=branch,
        addresses=addresses,
        created_at=now,
        expires_at=now + datetime.timedelta(seconds=ttl_seconds),
        operation=operation,
    )
    path = _reservations_dir(root) / f"{res.reservation_id}.json"
    path.write_text(json.dumps(res.to_dict(), indent=2) + "\n")
    logger.debug("✅ Created reservation %s for %d addresses", res.reservation_id[:8], len(addresses))
    return res


def load_all_reservations(root: pathlib.Path) -> list[Reservation]:
    """Load all reservation files (including expired ones)."""
    rdir = _reservations_dir(root)
    if not rdir.exists():
        return []
    reservations: list[Reservation] = []
    for path in rdir.glob("*.json"):
        try:
            raw = json.loads(path.read_text())
            reservations.append(Reservation.from_dict(raw))
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("⚠️ Corrupt reservation %s: %s", path.name, exc)
    return reservations


def active_reservations(root: pathlib.Path) -> list[Reservation]:
    """Return only non-expired reservations."""
    return [r for r in load_all_reservations(root) if r.is_active()]


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------


class Intent:
    """A declared operational intent extending a reservation."""

    def __init__(
        self,
        intent_id: str,
        reservation_id: str,
        run_id: str,
        branch: str,
        addresses: list[str],
        operation: str,
        created_at: datetime.datetime,
        detail: str,
    ) -> None:
        self.intent_id = intent_id
        self.reservation_id = reservation_id
        self.run_id = run_id
        self.branch = branch
        self.addresses = addresses
        self.operation = operation
        self.created_at = created_at
        self.detail = detail

    def to_dict(self) -> dict[str, str | int | list[str]]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "intent_id": self.intent_id,
            "reservation_id": self.reservation_id,
            "run_id": self.run_id,
            "branch": self.branch,
            "addresses": self.addresses,
            "operation": self.operation,
            "created_at": self.created_at.isoformat(),
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, d: dict[str, str | int | list[str]]) -> "Intent":
        created_raw = d.get("created_at")
        created_at = _parse_dt(str(created_raw)) if created_raw else _now_utc()
        addrs_raw = d.get("addresses", [])
        addrs = list(addrs_raw) if isinstance(addrs_raw, list) else []
        return cls(
            intent_id=str(d.get("intent_id", "")),
            reservation_id=str(d.get("reservation_id", "")),
            run_id=str(d.get("run_id", "")),
            branch=str(d.get("branch", "")),
            addresses=addrs,
            operation=str(d.get("operation", "")),
            created_at=created_at,
            detail=str(d.get("detail", "")),
        )


def create_intent(
    root: pathlib.Path,
    reservation_id: str,
    run_id: str,
    branch: str,
    addresses: list[str],
    operation: str,
    detail: str = "",
) -> Intent:
    """Write and return a new intent record."""
    _ensure_coord_dirs(root)
    now = _now_utc()
    intent = Intent(
        intent_id=str(_uuid_mod.uuid4()),
        reservation_id=reservation_id,
        run_id=run_id,
        branch=branch,
        addresses=addresses,
        operation=operation,
        created_at=now,
        detail=detail,
    )
    path = _intents_dir(root) / f"{intent.intent_id}.json"
    path.write_text(json.dumps(intent.to_dict(), indent=2) + "\n")
    logger.debug("✅ Created intent %s (%s)", intent.intent_id[:8], operation)
    return intent


def load_all_intents(root: pathlib.Path) -> list[Intent]:
    """Load all intent files."""
    idir = _intents_dir(root)
    if not idir.exists():
        return []
    intents: list[Intent] = []
    for path in idir.glob("*.json"):
        try:
            raw = json.loads(path.read_text())
            intents.append(Intent.from_dict(raw))
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("⚠️ Corrupt intent %s: %s", path.name, exc)
    return intents
