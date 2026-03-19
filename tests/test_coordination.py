"""Tests for muse/core/coordination.py — multi-agent coordination layer.

Coverage
--------
Directory helpers
    - _ensure_coord_dirs creates .muse/coordination/reservations/ and intents/.

Reservation
    - create_reservation writes a valid JSON file.
    - Reservation.from_dict / to_dict round-trip.
    - Reservation.is_active() returns True for non-expired, False for expired.
    - load_all_reservations loads all files including expired.
    - active_reservations filters out expired reservations.
    - Corrupt reservation file is skipped with a warning.
    - Multiple reservations can coexist for the same address.

Intent
    - create_intent writes a valid JSON file.
    - Intent.from_dict / to_dict round-trip.
    - load_all_intents loads all files.
    - Corrupt intent file is skipped.

Schema
    - All records have schema_version == 1.
    - created_at and expires_at are ISO 8601 strings.
    - operation field is None-able for reservations.
"""

import datetime
import json
import pathlib

import pytest

from muse.core.coordination import (
    Intent,
    Reservation,
    active_reservations,
    create_intent,
    create_reservation,
    load_all_intents,
    load_all_reservations,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _future(seconds: int = 3600) -> datetime.datetime:
    return _now() + datetime.timedelta(seconds=seconds)


def _past(seconds: int = 60) -> datetime.datetime:
    return _now() - datetime.timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Reservation — create and load
# ---------------------------------------------------------------------------


class TestCreateReservation:
    def test_creates_json_file(self, tmp_path: pathlib.Path) -> None:
        res = create_reservation(
            tmp_path,
            run_id="agent-1",
            branch="feature-x",
            addresses=["src/billing.py::compute_total"],
        )
        rdir = tmp_path / ".muse" / "coordination" / "reservations"
        assert rdir.exists()
        files = list(rdir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["reservation_id"] == res.reservation_id
        assert data["run_id"] == "agent-1"
        assert data["branch"] == "feature-x"
        assert data["addresses"] == ["src/billing.py::compute_total"]
        assert data["schema_version"] == 1

    def test_default_ttl_sets_future_expiry(self, tmp_path: pathlib.Path) -> None:
        res = create_reservation(tmp_path, run_id="r", branch="main", addresses=[])
        assert res.expires_at > _now()

    def test_custom_ttl(self, tmp_path: pathlib.Path) -> None:
        res = create_reservation(
            tmp_path, run_id="r", branch="main", addresses=[], ttl_seconds=7200
        )
        delta = res.expires_at - res.created_at
        assert abs(delta.total_seconds() - 7200) < 5

    def test_operation_stored(self, tmp_path: pathlib.Path) -> None:
        res = create_reservation(
            tmp_path, run_id="r", branch="main",
            addresses=["src/a.py::f"], operation="rename"
        )
        assert res.operation == "rename"
        rdir = tmp_path / ".muse" / "coordination" / "reservations"
        data = json.loads(list(rdir.glob("*.json"))[0].read_text())
        assert data["operation"] == "rename"

    def test_none_operation(self, tmp_path: pathlib.Path) -> None:
        res = create_reservation(tmp_path, run_id="r", branch="main", addresses=[])
        assert res.operation is None

    def test_multiple_addresses(self, tmp_path: pathlib.Path) -> None:
        addrs = ["src/a.py::f", "src/b.py::g", "src/c.py::h"]
        res = create_reservation(tmp_path, run_id="r", branch="main", addresses=addrs)
        assert res.addresses == addrs

    def test_multiple_reservations_coexist(self, tmp_path: pathlib.Path) -> None:
        create_reservation(tmp_path, run_id="a1", branch="main", addresses=["src/a.py::f"])
        create_reservation(tmp_path, run_id="a2", branch="main", addresses=["src/a.py::f"])
        rdir = tmp_path / ".muse" / "coordination" / "reservations"
        assert len(list(rdir.glob("*.json"))) == 2


# ---------------------------------------------------------------------------
# Reservation — to_dict / from_dict
# ---------------------------------------------------------------------------


class TestReservationRoundTrip:
    def test_to_dict_from_dict(self) -> None:
        now = _now()
        future = _future()
        res = Reservation(
            reservation_id="test-uuid",
            run_id="agent-7",
            branch="feature-y",
            addresses=["src/x.py::func"],
            created_at=now,
            expires_at=future,
            operation="move",
        )
        d = res.to_dict()
        res2 = Reservation.from_dict(d)
        assert res2.reservation_id == "test-uuid"
        assert res2.run_id == "agent-7"
        assert res2.branch == "feature-y"
        assert res2.addresses == ["src/x.py::func"]
        assert res2.operation == "move"
        # Timestamps round-trip via ISO 8601
        assert abs((res2.expires_at - future).total_seconds()) < 1

    def test_schema_version_in_dict(self) -> None:
        res = Reservation(
            reservation_id="x", run_id="r", branch="b",
            addresses=[], created_at=_now(), expires_at=_future(), operation=None
        )
        assert res.to_dict()["schema_version"] == 1


# ---------------------------------------------------------------------------
# Reservation — is_active
# ---------------------------------------------------------------------------


class TestReservationIsActive:
    def test_active_when_future_expiry(self, tmp_path: pathlib.Path) -> None:
        res = create_reservation(tmp_path, run_id="r", branch="main", addresses=[], ttl_seconds=3600)
        assert res.is_active()

    def test_inactive_when_past_expiry(self) -> None:
        res = Reservation(
            reservation_id="x", run_id="r", branch="b",
            addresses=[], created_at=_past(120), expires_at=_past(60), operation=None
        )
        assert not res.is_active()


# ---------------------------------------------------------------------------
# load_all_reservations / active_reservations
# ---------------------------------------------------------------------------


class TestLoadReservations:
    def test_load_all_includes_expired(self, tmp_path: pathlib.Path) -> None:
        create_reservation(tmp_path, run_id="r1", branch="main", addresses=[], ttl_seconds=3600)
        # Manually write an expired reservation.
        past = _past(120)
        expired = Reservation(
            reservation_id="expired-uuid",
            run_id="r2",
            branch="main",
            addresses=[],
            created_at=_past(200),
            expires_at=past,
            operation=None,
        )
        rdir = tmp_path / ".muse" / "coordination" / "reservations"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "expired-uuid.json").write_text(json.dumps(expired.to_dict()) + "\n")

        all_res = load_all_reservations(tmp_path)
        assert len(all_res) == 2

    def test_active_reservations_filters_expired(self, tmp_path: pathlib.Path) -> None:
        create_reservation(tmp_path, run_id="r1", branch="main", addresses=[], ttl_seconds=3600)
        past = _past(120)
        expired = Reservation(
            reservation_id="expired-uuid",
            run_id="r2", branch="main", addresses=[],
            created_at=_past(200), expires_at=past, operation=None,
        )
        rdir = tmp_path / ".muse" / "coordination" / "reservations"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "expired-uuid.json").write_text(json.dumps(expired.to_dict()) + "\n")

        active = active_reservations(tmp_path)
        assert len(active) == 1
        assert active[0].run_id == "r1"

    def test_empty_dir_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        rdir = tmp_path / ".muse" / "coordination" / "reservations"
        rdir.mkdir(parents=True, exist_ok=True)
        assert load_all_reservations(tmp_path) == []

    def test_nonexistent_dir_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        assert load_all_reservations(tmp_path) == []

    def test_corrupt_file_skipped(self, tmp_path: pathlib.Path) -> None:
        rdir = tmp_path / ".muse" / "coordination" / "reservations"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "bad.json").write_text("not valid json{{{")
        result = load_all_reservations(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# Intent — create and load
# ---------------------------------------------------------------------------


class TestCreateIntent:
    def test_creates_json_file(self, tmp_path: pathlib.Path) -> None:
        intent = create_intent(
            tmp_path,
            reservation_id="res-uuid",
            run_id="agent-2",
            branch="feature-z",
            addresses=["src/billing.py::Invoice"],
            operation="rename",
            detail="rename to InvoiceRecord",
        )
        idir = tmp_path / ".muse" / "coordination" / "intents"
        assert idir.exists()
        files = list(idir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["intent_id"] == intent.intent_id
        assert data["reservation_id"] == "res-uuid"
        assert data["operation"] == "rename"
        assert data["detail"] == "rename to InvoiceRecord"
        assert data["schema_version"] == 1

    def test_empty_detail_defaults_to_empty_string(self, tmp_path: pathlib.Path) -> None:
        intent = create_intent(
            tmp_path, reservation_id="", run_id="r", branch="main",
            addresses=[], operation="modify",
        )
        assert intent.detail == ""

    def test_multiple_intents(self, tmp_path: pathlib.Path) -> None:
        create_intent(tmp_path, reservation_id="", run_id="a", branch="main",
                      addresses=["x.py::f"], operation="rename")
        create_intent(tmp_path, reservation_id="", run_id="b", branch="main",
                      addresses=["x.py::g"], operation="delete")
        idir = tmp_path / ".muse" / "coordination" / "intents"
        assert len(list(idir.glob("*.json"))) == 2


# ---------------------------------------------------------------------------
# Intent — to_dict / from_dict
# ---------------------------------------------------------------------------


class TestIntentRoundTrip:
    def test_to_dict_from_dict(self) -> None:
        now = _now()
        intent = Intent(
            intent_id="intent-uuid",
            reservation_id="res-uuid",
            run_id="agent-3",
            branch="dev",
            addresses=["src/y.py::Bar"],
            operation="extract",
            created_at=now,
            detail="extract helper",
        )
        d = intent.to_dict()
        intent2 = Intent.from_dict(d)
        assert intent2.intent_id == "intent-uuid"
        assert intent2.reservation_id == "res-uuid"
        assert intent2.operation == "extract"
        assert intent2.detail == "extract helper"
        assert intent2.addresses == ["src/y.py::Bar"]

    def test_schema_version_in_dict(self) -> None:
        intent = Intent(
            intent_id="x", reservation_id="", run_id="r", branch="b",
            addresses=[], operation="modify", created_at=_now(), detail="",
        )
        assert intent.to_dict()["schema_version"] == 1


# ---------------------------------------------------------------------------
# load_all_intents
# ---------------------------------------------------------------------------


class TestLoadAllIntents:
    def test_empty_dir(self, tmp_path: pathlib.Path) -> None:
        idir = tmp_path / ".muse" / "coordination" / "intents"
        idir.mkdir(parents=True, exist_ok=True)
        assert load_all_intents(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path: pathlib.Path) -> None:
        assert load_all_intents(tmp_path) == []

    def test_loads_created_intents(self, tmp_path: pathlib.Path) -> None:
        create_intent(tmp_path, reservation_id="r", run_id="a", branch="main",
                      addresses=["x.py::f"], operation="rename")
        create_intent(tmp_path, reservation_id="r", run_id="b", branch="dev",
                      addresses=["y.py::g"], operation="modify")
        intents = load_all_intents(tmp_path)
        assert len(intents) == 2
        ops = {i.operation for i in intents}
        assert "rename" in ops
        assert "modify" in ops

    def test_corrupt_intent_skipped(self, tmp_path: pathlib.Path) -> None:
        idir = tmp_path / ".muse" / "coordination" / "intents"
        idir.mkdir(parents=True, exist_ok=True)
        (idir / "bad.json").write_text("{invalid")
        result = load_all_intents(tmp_path)
        assert result == []
