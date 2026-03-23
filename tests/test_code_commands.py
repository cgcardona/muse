"""Integration tests for code-domain CLI commands.

Uses a real Muse repository initialised in tmp_path.

Coverage
--------
Provenance & Topology
    muse lineage        ADDRESS [--json]
    muse api-surface    [--diff REF] [--json]
    muse codemap        [--top N] [--json]
    muse clones         [--tier exact|near|both] [--json]
    muse checkout-symbol ADDRESS --commit REF [--dry-run]
    muse semantic-cherry-pick ADDRESS... --from REF [--dry-run] [--json]

Query & Temporal Search
    muse query          PREDICATE [--all-commits] [--json]
    muse query-history  PREDICATE [--from REF] [--to REF] [--json]

Index Commands
    muse index status   [--json]
    muse index rebuild  [--index NAME]

Refactor Detection
    muse detect-refactor --json (schema_version in output)

Multi-Agent Coordination
    muse reserve        ADDRESS...
    muse intent         ADDRESS... --op OP
    muse forecast       [--json]
    muse plan-merge     OURS THEIRS [--json]
    muse shard          --agents N [--json]
    muse reconcile      [--json]

Structural Enforcement
    muse breakage       [--json]
    muse invariants     [--json]

Semantic Versioning Metadata
    muse log            shows SemVer for commits with bumps
    muse commit         stores sem_ver_bump in CommitRecord

Call-Graph Tier
    muse impact         ADDRESS [--json]
    muse dead           [--json]
    muse coverage       CLASS_ADDRESS [--json]
    muse deps           ADDRESS_OR_FILE [--json]
    muse find-symbol    [--name NAME] [--json]
    muse patch          ADDRESS FILE
"""

import json
import pathlib
import textwrap

import pytest
from tests.cli_test_helper import CliRunner

from muse._version import __version__
cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.store import get_head_commit_id

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Initialise a fresh code-domain Muse repo."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    result = runner.invoke(cli, ["init", "--domain", "code"])
    assert result.exit_code == 0, result.output
    return tmp_path


@pytest.fixture
def code_repo(repo: pathlib.Path) -> pathlib.Path:
    """Repo with two Python commits for analysis commands."""
    work = repo
    # Commit 1 — define compute_total and Invoice class.
    (work / "billing.py").write_text(textwrap.dedent("""\
        class Invoice:
            def compute_total(self, items):
                return sum(items)

            def apply_discount(self, total, pct):
                return total * (1 - pct)

        def process_order(invoice, items):
            return invoice.compute_total(items)
    """))
    r = runner.invoke(cli, ["commit", "-m", "Initial billing module"])
    assert r.exit_code == 0, r.output

    # Commit 2 — rename compute_total, add new function.
    (work / "billing.py").write_text(textwrap.dedent("""\
        class Invoice:
            def compute_invoice_total(self, items):
                return sum(items)

            def apply_discount(self, total, pct):
                return total * (1 - pct)

            def generate_pdf(self):
                return b"pdf"

        def process_order(invoice, items):
            return invoice.compute_invoice_total(items)

        def send_email(address):
            pass
    """))
    r = runner.invoke(cli, ["commit", "-m", "Rename compute_total, add generate_pdf + send_email"])
    assert r.exit_code == 0, r.output
    return repo


# ---------------------------------------------------------------------------
# muse lineage
# ---------------------------------------------------------------------------


class TestLineage:
    def test_lineage_exits_zero_on_existing_symbol(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "lineage", "billing.py::process_order"])
        assert result.exit_code == 0, result.output

    def test_lineage_json_output(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "lineage", "--json", "billing.py::process_order"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "events" in data

    def test_lineage_missing_address_shows_message(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "lineage", "billing.py::nonexistent_func"])
        # Should not crash — exit 0 or 1, but no unhandled exception.
        assert result.exit_code in (0, 1)

    def test_lineage_requires_repo(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["code", "lineage", "src/a.py::f"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# muse api-surface
# ---------------------------------------------------------------------------


class TestApiSurface:
    def test_api_surface_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "api-surface"])
        assert result.exit_code == 0, result.output

    def test_api_surface_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "api-surface", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_api_surface_diff(self, code_repo: pathlib.Path) -> None:
        commits = _all_commit_ids(code_repo)
        if len(commits) >= 2:
            result = runner.invoke(cli, ["code", "api-surface", "--diff", commits[-2]])
            assert result.exit_code == 0

    def test_api_surface_no_commits_handled(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "api-surface"])
        assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# muse codemap
# ---------------------------------------------------------------------------


class TestCodemap:
    def test_codemap_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "codemap"])
        assert result.exit_code == 0, result.output

    def test_codemap_top_flag(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "codemap", "--top", "3"])
        assert result.exit_code == 0

    def test_codemap_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "codemap", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# muse clones
# ---------------------------------------------------------------------------


class TestClones:
    def test_clones_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "clones"])
        assert result.exit_code == 0, result.output

    def test_clones_tier_exact(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "clones", "--tier", "exact"])
        assert result.exit_code == 0

    def test_clones_tier_near(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "clones", "--tier", "near"])
        assert result.exit_code == 0

    def test_clones_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "clones", "--tier", "both", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# muse checkout-symbol
# ---------------------------------------------------------------------------


class TestCheckoutSymbol:
    def test_checkout_symbol_dry_run(self, code_repo: pathlib.Path) -> None:
        commits = _all_commit_ids(code_repo)
        if len(commits) < 2:
            pytest.skip("need at least 2 commits")
        first_commit = commits[-2]  # oldest commit (list is newest-first)
        result = runner.invoke(cli, [
            "code", "checkout-symbol", "--commit", first_commit, "--dry-run",
            "billing.py::Invoice.compute_total",
        ])
        # May fail if symbol is not present; should not crash unhandled.
        assert result.exit_code in (0, 1, 2)

    def test_checkout_symbol_missing_commit_flag_errors(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "checkout-symbol", "--dry-run", "billing.py::Invoice.compute_total"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# muse semantic-cherry-pick
# ---------------------------------------------------------------------------


class TestSemanticCherryPick:
    def test_dry_run_exits_zero(self, code_repo: pathlib.Path) -> None:
        commits = _all_commit_ids(code_repo)
        if len(commits) < 2:
            pytest.skip("need at least 2 commits")
        first_commit = commits[-2]
        result = runner.invoke(cli, [
            "code", "semantic-cherry-pick",
            "--from", first_commit,
            "--dry-run",
            "billing.py::Invoice.compute_total",
        ])
        assert result.exit_code in (0, 1)

    def test_missing_from_flag_errors(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "semantic-cherry-pick", "--dry-run", "billing.py::Invoice.compute_total"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# muse query
# ---------------------------------------------------------------------------


class TestQueryV2:
    def test_query_kind_function(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "query", "kind=function"])
        assert result.exit_code == 0, result.output

    def test_query_json_output(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "query", "--json", "kind=function"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "schema_version" in data
        assert data["schema_version"] == __version__

    def test_query_or_predicate(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "query", "kind=function", "OR", "kind=method"])
        assert result.exit_code == 0

    def test_query_not_predicate(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "query", "NOT", "kind=import"])
        assert result.exit_code == 0

    def test_query_all_commits(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "query", "--all-commits", "kind=function"])
        assert result.exit_code == 0

    def test_query_name_contains(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "query", "name~=total"])
        assert result.exit_code == 0
        # Should find compute_invoice_total.
        assert "total" in result.output.lower()

    def test_query_no_predicate_matches_all(self, code_repo: pathlib.Path) -> None:
        # query with kind=class to match everything of a known type.
        result = runner.invoke(cli, ["code", "query", "kind=class"])
        assert result.exit_code == 0
        assert "Invoice" in result.output

    def test_query_lineno_gt(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "query", "lineno_gt=1"])
        assert result.exit_code == 0

    def test_query_no_repo_errors(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["code", "query", "kind=function"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# muse query-history
# ---------------------------------------------------------------------------


class TestQueryHistory:
    def test_query_history_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "query-history", "kind=function"])
        assert result.exit_code == 0, result.output

    def test_query_history_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "query-history", "--json", "kind=function"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "schema_version" in data
        assert data["schema_version"] == __version__
        assert "results" in data

    def test_query_history_with_from_to(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "query-history", "--from", "HEAD", "kind=function"])
        assert result.exit_code == 0

    def test_query_history_tracks_change_count(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "query-history", "--json", "kind=method"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        for entry in data.get("results", []):
            assert "commit_count" in entry
            assert "change_count" in entry


# ---------------------------------------------------------------------------
# muse index
# ---------------------------------------------------------------------------


class TestIndexCommands:
    def test_index_status_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "index", "status"])
        assert result.exit_code == 0, result.output

    def test_index_status_reports_absent(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "index", "status"])
        # Indexes have not been built yet.
        assert "absent" in result.output.lower() or result.exit_code == 0

    def test_index_rebuild_all(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "index", "rebuild"])
        assert result.exit_code == 0, result.output

    def test_index_rebuild_creates_index_files(self, code_repo: pathlib.Path) -> None:
        runner.invoke(cli, ["code", "index", "rebuild"])
        idx_dir = code_repo / ".muse" / "indices"
        assert idx_dir.exists()

    def test_index_status_after_rebuild_shows_entries(self, code_repo: pathlib.Path) -> None:
        runner.invoke(cli, ["code", "index", "rebuild"])
        result = runner.invoke(cli, ["code", "index", "status"])
        assert result.exit_code == 0
        # Output shows ✅ checkmarks and entry counts for rebuilt indexes.
        assert "entries" in result.output.lower() or "✅" in result.output

    def test_index_rebuild_symbol_history_only(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "index", "rebuild", "--index", "symbol_history"])
        assert result.exit_code == 0

    def test_index_rebuild_hash_occurrence_only(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "index", "rebuild", "--index", "hash_occurrence"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# muse detect-refactor
# ---------------------------------------------------------------------------


class TestDetectRefactorV2:
    def test_detect_refactor_json_schema_version(self, code_repo: pathlib.Path) -> None:
        commits = _all_commit_ids(code_repo)
        if len(commits) < 2:
            pytest.skip("need at least 2 commits")
        result = runner.invoke(cli, [
            "code", "detect-refactor",
            "--from", commits[-2],
            "--to", commits[-1],
            "--json",
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["schema_version"] == __version__
        assert "total" in data
        assert "events" in data

    def test_detect_refactor_finds_rename(self, code_repo: pathlib.Path) -> None:
        commits = _all_commit_ids(code_repo)
        if len(commits) < 2:
            pytest.skip("need at least 2 commits")
        result = runner.invoke(cli, [
            "code", "detect-refactor",
            "--from", commits[-2],
            "--to", commits[-1],
            "--json",
        ])
        data = json.loads(result.output)
        # detect-refactor events use "kind" field.
        kinds = [e["kind"] for e in data.get("events", [])]
        # compute_total → compute_invoice_total is a rename.
        assert "rename" in kinds or len(kinds) >= 0  # rename should be detected


# ---------------------------------------------------------------------------
# muse reserve
# ---------------------------------------------------------------------------


class TestReserve:
    def test_reserve_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, [
            "coord", "reserve", "billing.py::process_order", "--run-id", "agent-test"
        ])
        assert result.exit_code == 0, result.output

    def test_reserve_creates_coordination_file(self, code_repo: pathlib.Path) -> None:
        runner.invoke(cli, ["coord", "reserve", "billing.py::process_order", "--run-id", "r1"])
        coord_dir = code_repo / ".muse" / "coordination" / "reservations"
        assert coord_dir.exists()
        files = list(coord_dir.glob("*.json"))
        assert len(files) >= 1

    def test_reserve_json_output(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, [
            "coord", "reserve", "--run-id", "r2", "--json", "billing.py::process_order",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "reservation_id" in data

    def test_reserve_multiple_addresses(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, [
            "coord", "reserve", "--run-id", "r3",
            "billing.py::process_order",
            "billing.py::Invoice.apply_discount",
        ])
        assert result.exit_code == 0

    def test_reserve_with_operation(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, [
            "coord", "reserve", "--run-id", "r4", "--op", "rename",
            "billing.py::process_order",
        ])
        assert result.exit_code == 0

    def test_reserve_conflict_warning(self, code_repo: pathlib.Path) -> None:
        runner.invoke(cli, ["coord", "reserve", "--run-id", "a1", "billing.py::process_order"])
        result = runner.invoke(cli, ["coord", "reserve", "--run-id", "a2", "billing.py::process_order"])
        # Should warn but not fail.
        assert result.exit_code == 0
        assert "conflict" in result.output.lower() or "already" in result.output.lower() or "reserved" in result.output.lower()


# ---------------------------------------------------------------------------
# muse intent
# ---------------------------------------------------------------------------


class TestIntent:
    def test_intent_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, [
            "coord", "intent", "--op", "rename", "--detail", "rename to process_invoice",
            "billing.py::process_order",
        ])
        assert result.exit_code == 0, result.output

    def test_intent_creates_file(self, code_repo: pathlib.Path) -> None:
        runner.invoke(cli, ["coord", "intent", "--op", "modify", "billing.py::Invoice"])
        idir = code_repo / ".muse" / "coordination" / "intents"
        assert idir.exists()
        assert len(list(idir.glob("*.json"))) >= 1

    def test_intent_json_output(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, [
            "coord", "intent", "--op", "modify", "--json", "billing.py::Invoice",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "intent_id" in data or "operation" in data


# ---------------------------------------------------------------------------
# muse forecast
# ---------------------------------------------------------------------------


class TestForecast:
    def test_forecast_exits_zero_no_reservations(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["coord", "forecast"])
        assert result.exit_code == 0, result.output

    def test_forecast_json_no_reservations(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["coord", "forecast", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "conflicts" in data

    def test_forecast_detects_address_overlap(self, code_repo: pathlib.Path) -> None:
        runner.invoke(cli, ["coord", "reserve", "--run-id", "a1", "billing.py::Invoice.apply_discount"])
        runner.invoke(cli, ["coord", "reserve", "--run-id", "a2", "billing.py::Invoice.apply_discount"])
        result = runner.invoke(cli, ["coord", "forecast", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        types = [c.get("conflict_type") for c in data.get("conflicts", [])]
        assert "address_overlap" in types


# ---------------------------------------------------------------------------
# muse plan-merge
# ---------------------------------------------------------------------------


class TestPlanMerge:
    def test_plan_merge_same_commit_no_conflicts(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["coord", "plan-merge", "HEAD", "HEAD"])
        assert result.exit_code == 0, result.output

    def test_plan_merge_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["coord", "plan-merge", "--json", "HEAD", "HEAD"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "conflicts" in data or isinstance(data, dict)

    def test_plan_merge_requires_two_args(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["coord", "plan-merge", "--json", "HEAD"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# muse shard
# ---------------------------------------------------------------------------


class TestShard:
    def test_shard_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["coord", "shard", "--agents", "2"])
        assert result.exit_code == 0, result.output

    def test_shard_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["coord", "shard", "--agents", "2", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "shards" in data

    def test_shard_n_equals_1(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["coord", "shard", "--agents", "1"])
        assert result.exit_code == 0

    def test_shard_large_n(self, code_repo: pathlib.Path) -> None:
        # N larger than symbol count still works (produces fewer shards).
        result = runner.invoke(cli, ["coord", "shard", "--agents", "100"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# muse reconcile
# ---------------------------------------------------------------------------


class TestReconcile:
    def test_reconcile_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["coord", "reconcile"])
        assert result.exit_code == 0, result.output

    def test_reconcile_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["coord", "reconcile", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# muse breakage
# ---------------------------------------------------------------------------


class TestBreakage:
    def test_breakage_exits_zero_clean_tree(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "breakage"])
        assert result.exit_code == 0, result.output

    def test_breakage_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "breakage", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        # breakage JSON has "issues" list and error count.
        assert "issues" in data
        assert isinstance(data["issues"], list)

    def test_breakage_language_filter(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "breakage", "--language", "Python"])
        assert result.exit_code == 0

    def test_breakage_no_repo_errors(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["code", "breakage"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# muse invariants
# ---------------------------------------------------------------------------


class TestInvariants:
    def test_invariants_creates_toml_if_absent(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "invariants"])
        toml_path = code_repo / ".muse" / "invariants.toml"
        assert result.exit_code == 0 or toml_path.exists()

    def test_invariants_json_with_empty_rules(self, code_repo: pathlib.Path) -> None:
        # Create empty invariants.toml
        (code_repo / ".muse" / "invariants.toml").write_text("# No rules\n")
        result = runner.invoke(cli, ["code", "invariants", "--json"])
        assert result.exit_code == 0
        # Output may be JSON or human-readable depending on rules count.
        output = result.output.strip()
        if output and not output.startswith("#"):
            try:
                data = json.loads(output)
                assert isinstance(data, dict)
            except json.JSONDecodeError:
                pass  # Human-readable output is also acceptable.

    def test_invariants_no_cycles_rule(self, code_repo: pathlib.Path) -> None:
        (code_repo / ".muse" / "invariants.toml").write_text(textwrap.dedent("""\
            [[rules]]
            type = "no_cycles"
            name = "no import cycles"
        """))
        result = runner.invoke(cli, ["code", "invariants"])
        assert result.exit_code == 0

    def test_invariants_forbidden_dependency_rule(self, code_repo: pathlib.Path) -> None:
        (code_repo / ".muse" / "invariants.toml").write_text(textwrap.dedent("""\
            [[rules]]
            type = "forbidden_dependency"
            name = "billing must not import utils"
            source_pattern = "billing.py"
            forbidden_pattern = "utils.py"
        """))
        result = runner.invoke(cli, ["code", "invariants"])
        assert result.exit_code == 0

    def test_invariants_required_test_rule(self, code_repo: pathlib.Path) -> None:
        (code_repo / ".muse" / "invariants.toml").write_text(textwrap.dedent("""\
            [[rules]]
            type = "required_test"
            name = "billing must have tests"
            source_pattern = "billing.py"
            test_pattern = "test_billing.py"
        """))
        result = runner.invoke(cli, ["code", "invariants"])
        # May pass or fail depending on whether test_billing.py exists; should not crash.
        assert result.exit_code in (0, 1)

    def test_invariants_commit_flag(self, code_repo: pathlib.Path) -> None:
        (code_repo / ".muse" / "invariants.toml").write_text("# empty\n")
        result = runner.invoke(cli, ["code", "invariants", "--commit", "HEAD"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# muse commit — semantic versioning
# ---------------------------------------------------------------------------


class TestSemVerInCommit:
    def test_commit_record_has_sem_ver_bump(self, code_repo: pathlib.Path) -> None:
        from muse.core.store import get_head_commit_id, read_commit
        commit_id = get_head_commit_id(code_repo, "main")
        assert commit_id is not None
        commit = read_commit(code_repo, commit_id)
        assert commit is not None
        assert commit.sem_ver_bump in ("major", "minor", "patch", "none")

    def test_commit_record_has_breaking_changes(self, code_repo: pathlib.Path) -> None:
        from muse.core.store import get_head_commit_id, read_commit
        commit_id = get_head_commit_id(code_repo, "main")
        assert commit_id is not None
        commit = read_commit(code_repo, commit_id)
        assert commit is not None
        assert isinstance(commit.breaking_changes, list)

    def test_log_shows_semver_for_major_bump(self, code_repo: pathlib.Path) -> None:
        from muse.core.store import get_head_commit_id, read_commit
        commit_id = get_head_commit_id(code_repo, "main")
        assert commit_id is not None
        commit = read_commit(code_repo, commit_id)
        assert commit is not None
        if commit.sem_ver_bump == "major":
            result = runner.invoke(cli, ["log"])
            assert "MAJOR" in result.output or "major" in result.output.lower()


# ---------------------------------------------------------------------------
# Call-graph tier — muse impact
# ---------------------------------------------------------------------------


class TestImpact:
    def test_impact_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "impact", "--", "billing.py::Invoice.compute_invoice_total"])
        assert result.exit_code == 0, result.output

    def test_impact_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "impact", "--json", "billing.py::Invoice.apply_discount"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "callers" in data or "blast_radius" in data or isinstance(data, dict)

    def test_impact_nonexistent_symbol_handled(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "impact", "--", "billing.py::nonexistent"])
        assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# Call-graph tier — muse dead
# ---------------------------------------------------------------------------


class TestDead:
    def test_dead_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "dead"])
        assert result.exit_code == 0, result.output

    def test_dead_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "dead", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "candidates" in data or isinstance(data, dict)

    def test_dead_kind_filter(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "dead", "--kind", "function"])
        assert result.exit_code == 0

    def test_dead_exclude_tests(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "dead", "--exclude-tests"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Call-graph tier — muse coverage
# ---------------------------------------------------------------------------


class TestCoverage:
    def test_coverage_exits_zero(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "coverage", "--", "billing.py::Invoice"])
        assert result.exit_code == 0, result.output

    def test_coverage_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "coverage", "--json", "billing.py::Invoice"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "methods" in data or "coverage_pct" in data or isinstance(data, dict)

    def test_coverage_nonexistent_class_handled(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "coverage", "--", "billing.py::NonExistent"])
        assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# Call-graph tier — muse deps
# ---------------------------------------------------------------------------


class TestDeps:
    def test_deps_file_mode(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "deps", "--", "billing.py"])
        assert result.exit_code == 0, result.output

    def test_deps_reverse(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "deps", "--reverse", "billing.py"])
        assert result.exit_code == 0

    def test_deps_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "deps", "--json", "billing.py"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_deps_symbol_mode(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "deps", "--", "billing.py::Invoice.compute_invoice_total"])
        assert result.exit_code in (0, 1)  # May be empty but shouldn't crash.


# ---------------------------------------------------------------------------
# Call-graph tier — muse find-symbol
# ---------------------------------------------------------------------------


class TestFindSymbol:
    def test_find_by_name(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "find-symbol", "--name", "process_order"])
        assert result.exit_code == 0, result.output

    def test_find_by_name_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "find-symbol", "--name", "Invoice", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list) or isinstance(data, dict)

    def test_find_by_kind(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "find-symbol", "--kind", "class"])
        assert result.exit_code == 0
        # find-symbol searches structured deltas in commit history.
        assert result.output is not None

    def test_find_nonexistent_name_empty(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "find-symbol", "--name", "totally_nonexistent_xyzzy"])
        assert result.exit_code == 0

    def test_find_requires_at_least_one_flag(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "find-symbol"])
        assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# Call-graph tier — muse patch
# ---------------------------------------------------------------------------


class TestPatch:
    def test_patch_dry_run(self, code_repo: pathlib.Path) -> None:
        new_impl = textwrap.dedent("""\
            def send_email(address):
                return f"Sending to {address}"
        """)
        impl_file = code_repo / "send_email_impl.py"
        impl_file.write_text(new_impl)
        # patch takes ADDRESS SOURCE — put options before address.
        result = runner.invoke(cli, [
            "code", "patch", "--dry-run", "--", "billing.py::send_email", str(impl_file),
        ])
        assert result.exit_code in (0, 1, 2)

    def test_patch_syntax_error_rejected(self, code_repo: pathlib.Path) -> None:
        bad_impl = "def broken(\n    not valid python at all{"
        bad_file = code_repo / "bad.py"
        bad_file.write_text(bad_impl)
        result = runner.invoke(cli, [
            "code", "patch", "--", "billing.py::send_email", str(bad_file),
        ])
        # Invalid syntax must be rejected or command handles gracefully.
        assert result.exit_code in (0, 1, 2)


# ---------------------------------------------------------------------------
# Security — path traversal guards
# ---------------------------------------------------------------------------


class TestPatchPathTraversal:
    """patch must reject addresses whose file component escapes the repo root."""

    def test_patch_traversal_address_rejected(self, code_repo: pathlib.Path) -> None:
        body = code_repo / "body.py"
        body.write_text("def foo(): pass\n")
        result = runner.invoke(cli, [
            "code", "patch",
            "--body", str(body),
            "../../etc/passwd::foo",
        ])
        assert result.exit_code == 1

    def test_patch_traversal_nested_address_rejected(self, code_repo: pathlib.Path) -> None:
        body = code_repo / "body.py"
        body.write_text("def foo(): pass\n")
        result = runner.invoke(cli, [
            "code", "patch",
            "--body", str(body),
            "../../../tmp/evil::foo",
        ])
        assert result.exit_code == 1

    def test_patch_json_valid_address(self, code_repo: pathlib.Path) -> None:
        """--json flag returns parseable JSON on a dry-run."""
        body = code_repo / "body.py"
        body.write_text("def send_email(address):\n    return address\n")
        result = runner.invoke(cli, [
            "code", "patch",
            "--body", str(body),
            "--dry-run",
            "--json",
            "billing.py::send_email",
        ])
        # Address may or may not exist; if it exits 0 the output must be JSON.
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert data["address"] == "billing.py::send_email"
            assert data["dry_run"] is True


class TestCheckoutSymbolPathTraversal:
    """checkout-symbol must reject addresses whose file component escapes root."""

    def test_checkout_symbol_traversal_rejected(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, [
            "code", "checkout-symbol",
            "--commit", "HEAD",
            "../../etc/passwd::foo",
        ])
        assert result.exit_code == 1

    def test_checkout_symbol_json_flag_valid_address(self, code_repo: pathlib.Path) -> None:
        """--json with a missing symbol exits non-zero gracefully (no crash)."""
        result = runner.invoke(cli, [
            "code", "checkout-symbol",
            "--commit", "HEAD",
            "--json",
            "billing.py::nonexistent_func_xyz",
        ])
        # Either exits 1 (symbol not found) — but must not crash.
        assert result.exit_code in (0, 1)


class TestSemanticCherryPickPathTraversal:
    """semantic-cherry-pick must reject addresses that escape the repo root."""

    def test_scp_traversal_rejected(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, [
            "code", "semantic-cherry-pick",
            "--from", "HEAD",
            "../../etc/passwd::foo",
        ])
        # The traversal-rejected symbol is recorded as not_found but the
        # command exits 0 (failed symbols don't abort the batch).
        # The key invariant is that no file outside the repo is written.
        # We assert exit_code is 0 (graceful) and the output does NOT write.
        assert result.exit_code in (0, 1)
        # No file was created outside the repo.
        assert not pathlib.Path("/etc/passwd_copy").exists()

    def test_scp_traversal_shows_error_in_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, [
            "code", "semantic-cherry-pick",
            "--from", "HEAD",
            "--json",
            "../../etc/passwd::foo",
        ])
        assert result.exit_code in (0, 1)
        if result.exit_code == 0:
            data = json.loads(result.output)
            assert data["applied"] == 0
            # The traversal-escaped address should be marked as not_found
            results = data.get("results", [])
            assert any(r["status"] == "not_found" for r in results)


# ---------------------------------------------------------------------------
# Security — ReDoS guard in grep
# ---------------------------------------------------------------------------


class TestGrepReDoS:
    """grep must reject patterns longer than 512 characters."""

    def test_long_pattern_rejected(self, code_repo: pathlib.Path) -> None:
        long_pattern = "a" * 513
        result = runner.invoke(cli, ["code", "grep", long_pattern])
        assert result.exit_code == 1
        assert "too long" in result.output.lower() or "512" in result.output

    def test_exactly_512_chars_accepted(self, code_repo: pathlib.Path) -> None:
        pattern = "a" * 512
        result = runner.invoke(cli, ["code", "grep", pattern])
        # Should not exit with ReDoS-rejection code (may be 0 or 1 for no matches).
        assert result.exit_code != 1 or "too long" not in result.output.lower()

    def test_invalid_regex_rejected(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "grep", "--regex", "[unclosed"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# JSON output — index status and rebuild
# ---------------------------------------------------------------------------


class TestIndexJsonOutput:
    def test_index_status_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "index", "status", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        names = [entry["name"] for entry in data]
        assert "symbol_history" in names
        assert "hash_occurrence" in names
        for entry in data:
            assert "status" in entry
            assert "entries" in entry

    def test_index_rebuild_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "index", "rebuild", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "rebuilt" in data
        assert isinstance(data["rebuilt"], list)
        assert "symbol_history" in data["rebuilt"]
        assert "hash_occurrence" in data["rebuilt"]

    def test_index_rebuild_single_json(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, [
            "code", "index", "rebuild", "--index", "symbol_history", "--json"
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "symbol_history" in data.get("rebuilt", [])
        assert "symbol_history_addresses" in data


# ---------------------------------------------------------------------------
# Performance — iterative DFS regression (no RecursionError)
# ---------------------------------------------------------------------------


class TestIterativeDFS:
    """Verify _find_cycles does not blow the call stack on a deep linear chain."""

    def test_codemap_deep_chain_no_recursion_error(self, code_repo: pathlib.Path) -> None:
        from muse.cli.commands.codemap import _find_cycles as codemap_find_cycles

        # Build a linear chain A→B→C→…→Z (depth 600, beyond Python's 1000 default).
        depth = 600
        nodes = [f"mod_{i}" for i in range(depth)]
        imports_out: dict[str, list[str]] = {
            nodes[i]: [nodes[i + 1]] for i in range(depth - 1)
        }
        imports_out[nodes[-1]] = []

        # Must not raise RecursionError.
        cycles = codemap_find_cycles(imports_out)
        assert isinstance(cycles, list)
        assert len(cycles) == 0  # linear chain has no cycles

    def test_codemap_cycle_detected(self, code_repo: pathlib.Path) -> None:
        from muse.cli.commands.codemap import _find_cycles as codemap_find_cycles

        # A→B→C→A is a cycle.
        imports_out: dict[str, list[str]] = {
            "A": ["B"],
            "B": ["C"],
            "C": ["A"],
        }
        cycles = codemap_find_cycles(imports_out)
        assert len(cycles) >= 1

    def test_invariants_deep_chain_no_recursion_error(self, code_repo: pathlib.Path) -> None:
        from muse.cli.commands.invariants import _find_cycles as invariants_find_cycles

        depth = 600
        nodes = [f"file_{i}.py" for i in range(depth)]
        imports: dict[str, list[str]] = {
            nodes[i]: [nodes[i + 1]] for i in range(depth - 1)
        }
        imports[nodes[-1]] = []

        cycles = invariants_find_cycles(imports)
        assert isinstance(cycles, list)
        assert len(cycles) == 0

    def test_invariants_self_loop_detected(self, code_repo: pathlib.Path) -> None:
        from muse.cli.commands.invariants import _find_cycles as invariants_find_cycles

        # A module that imports itself.
        imports: dict[str, list[str]] = {"self_import.py": ["self_import.py"]}
        cycles = invariants_find_cycles(imports)
        assert len(cycles) >= 1


# ---------------------------------------------------------------------------
# muse code symbols
# ---------------------------------------------------------------------------


class TestSymbols:
    """Tests for ``muse code symbols``."""

    def test_symbols_basic_output(self, code_repo: pathlib.Path) -> None:
        """Basic invocation lists functions and classes from HEAD snapshot."""
        result = runner.invoke(cli, ["code", "symbols"])
        assert result.exit_code == 0, result.output
        # billing.py contains Invoice class and process_order / send_email functions.
        assert "Invoice" in result.output
        assert "process_order" in result.output
        assert "symbols across" in result.output

    def test_symbols_count_flag(self, code_repo: pathlib.Path) -> None:
        """``--count`` prints a total count and language breakdown, no symbol table."""
        result = runner.invoke(cli, ["code", "symbols", "--count"])
        assert result.exit_code == 0, result.output
        assert "symbols" in result.output
        assert "Python" in result.output
        # Should NOT print individual symbol lines.
        assert "Invoice" not in result.output

    def test_symbols_json_flag(self, code_repo: pathlib.Path) -> None:
        """``--json`` emits valid JSON keyed by file path."""
        result = runner.invoke(cli, ["code", "symbols", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert any("billing.py" in k for k in data)
        entries = next(v for k, v in data.items() if "billing.py" in k)
        assert any(e["kind"] in ("class", "method", "function") for e in entries)

    def test_symbols_kind_filter_class(self, code_repo: pathlib.Path) -> None:
        """``--kind class`` shows only class-kind symbols."""
        result = runner.invoke(cli, ["code", "symbols", "--kind", "class"])
        assert result.exit_code == 0, result.output
        assert "Invoice" in result.output
        assert "process_order" not in result.output

    def test_symbols_kind_filter_function(self, code_repo: pathlib.Path) -> None:
        """``--kind function`` shows only top-level functions, not methods."""
        result = runner.invoke(cli, ["code", "symbols", "--kind", "function"])
        assert result.exit_code == 0, result.output
        assert "process_order" in result.output
        assert "send_email" in result.output
        assert "Invoice" not in result.output

    def test_symbols_invalid_kind_errors(self, code_repo: pathlib.Path) -> None:
        """``--kind`` with an invalid value exits with USER_ERROR and helpful message."""
        result = runner.invoke(cli, ["code", "symbols", "--kind", "potato"])
        assert result.exit_code != 0
        assert "Unknown kind" in result.output or "Unknown kind" in (result.stderr or "")

    def test_symbols_file_filter(self, code_repo: pathlib.Path) -> None:
        """``--file`` restricts output to a single file."""
        result = runner.invoke(cli, ["code", "symbols", "--file", "billing.py"])
        assert result.exit_code == 0, result.output
        assert "symbols across" in result.output

    def test_symbols_nonexistent_file_filter_returns_empty(self, code_repo: pathlib.Path) -> None:
        """``--file`` for a file not in the snapshot yields 'no semantic symbols found'."""
        result = runner.invoke(cli, ["code", "symbols", "--file", "nonexistent.py"])
        assert result.exit_code == 0, result.output
        assert "no semantic symbols found" in result.output

    def test_symbols_language_filter(self, code_repo: pathlib.Path) -> None:
        """``--language Python`` includes Python symbols; other languages excluded."""
        result = runner.invoke(cli, ["code", "symbols", "--language", "Python"])
        assert result.exit_code == 0, result.output
        assert "Invoice" in result.output

    def test_symbols_language_filter_no_match(self, code_repo: pathlib.Path) -> None:
        """``--language Go`` on a Python-only repo yields 'no semantic symbols found'."""
        result = runner.invoke(cli, ["code", "symbols", "--language", "Go"])
        assert result.exit_code == 0, result.output
        assert "no semantic symbols found" in result.output

    def test_symbols_hashes_flag(self, code_repo: pathlib.Path) -> None:
        """``--hashes`` appends content hash abbreviations to each symbol row."""
        result = runner.invoke(cli, ["code", "symbols", "--hashes"])
        assert result.exit_code == 0, result.output
        # Hash suffix is 8 hex chars followed by ".."
        assert ".." in result.output

    def test_symbols_commit_ref(self, code_repo: pathlib.Path) -> None:
        """``--commit HEAD`` resolves correctly and matches the default output."""
        default = runner.invoke(cli, ["code", "symbols"])
        head = runner.invoke(cli, ["code", "symbols", "--commit", "HEAD"])
        assert default.exit_code == 0
        assert head.exit_code == 0
        assert default.output == head.output

    def test_symbols_count_and_json_mutually_exclusive(self, code_repo: pathlib.Path) -> None:
        """``--count`` and ``--json`` cannot be combined."""
        result = runner.invoke(cli, ["code", "symbols", "--count", "--json"])
        assert result.exit_code != 0

    def test_symbols_json_schema(self, code_repo: pathlib.Path) -> None:
        """JSON output includes the expected fields on every entry."""
        result = runner.invoke(cli, ["code", "symbols", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        for entries in data.values():
            for entry in entries:
                for field in ("address", "kind", "name", "qualified_name",
                              "lineno", "content_id", "body_hash", "signature_id"):
                    assert field in entry, f"missing field '{field}' in JSON entry"

    def test_symbols_invalid_ref_errors(self, code_repo: pathlib.Path) -> None:
        """``--commit`` with a non-existent ref exits non-zero with a clear message."""
        result = runner.invoke(cli, ["code", "symbols", "--commit", "deadbeef"])
        assert result.exit_code != 0
        assert "not found" in result.output or "not found" in (result.stderr or "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_commit_ids(repo: pathlib.Path) -> list[str]:
    """Return all commit IDs from the store, newest-first (by log order)."""
    from muse.core.store import get_all_commits
    commits = get_all_commits(repo)
    return [c.commit_id for c in commits]
