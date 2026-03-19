"""Stress tests for the commit DAG and merge-base algorithm.

Exercises:
- Linear chains of 500 commits.
- Wide fan-out / fan-in (octopus merge shapes).
- Criss-cross merge (ambiguous LCA — should still find *some* ancestor).
- Independent histories (no common ancestor → None).
- find_merge_base symmetry: find_merge_base(a, b) == find_merge_base(b, a).
- Missing commit handles gracefully (None parent pointers in corrupt graphs).
- Diamond topology: four-node diamond always finds the root.
- Double diamond: two diamonds chained together.
- Long parallel branches that converge at a single point.
"""

import datetime
import pathlib

import pytest

from muse.core.merge_engine import find_merge_base
from muse.core.store import CommitRecord, write_commit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(root: pathlib.Path, cid: str, parent: str | None = None, parent2: str | None = None) -> None:
    write_commit(root, CommitRecord(
        commit_id=cid,
        repo_id="repo",
        branch="main",
        snapshot_id=f"snap-{cid}",
        message=cid,
        committed_at=datetime.datetime.now(datetime.timezone.utc),
        parent_commit_id=parent,
        parent2_commit_id=parent2,
    ))


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    muse = tmp_path / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Linear chain
# ---------------------------------------------------------------------------


class TestLinearChain:
    def test_chain_of_500_finds_base(self, repo: pathlib.Path) -> None:
        """LCA of two commits on a 500-long linear chain is the shared ancestor."""
        prev: str | None = None
        for i in range(500):
            cid = f"c{i:04d}"
            _write(repo, cid, prev)
            prev = cid

        # Branch off at c0100
        _write(repo, "branch-tip", "c0100")

        base = find_merge_base(repo, "c0499", "branch-tip")
        assert base == "c0100"

    def test_lca_of_adjacent_commits_is_parent(self, repo: pathlib.Path) -> None:
        _write(repo, "root")
        _write(repo, "child", "root")
        assert find_merge_base(repo, "root", "child") == "root"
        assert find_merge_base(repo, "child", "root") == "root"

    def test_long_chain_lca_symmetry(self, repo: pathlib.Path) -> None:
        """find_merge_base(a, b) == find_merge_base(b, a) on a long chain."""
        prev: str | None = None
        for i in range(100):
            cid = f"n{i:03d}"
            _write(repo, cid, prev)
            prev = cid

        _write(repo, "left", "n050")
        _write(repo, "right", "n050")

        assert find_merge_base(repo, "left", "right") == "n050"
        assert find_merge_base(repo, "right", "left") == "n050"

    def test_same_commit_returns_itself(self, repo: pathlib.Path) -> None:
        _write(repo, "solo")
        assert find_merge_base(repo, "solo", "solo") == "solo"

    def test_one_is_ancestor_of_other(self, repo: pathlib.Path) -> None:
        """When A is a direct ancestor of B, LCA is A."""
        prev: str | None = None
        for i in range(20):
            cid = f"x{i:02d}"
            _write(repo, cid, prev)
            prev = cid
        assert find_merge_base(repo, "x00", "x19") == "x00"
        assert find_merge_base(repo, "x19", "x00") == "x00"


# ---------------------------------------------------------------------------
# Diamond topology
# ---------------------------------------------------------------------------


class TestDiamondTopology:
    def test_simple_diamond(self, repo: pathlib.Path) -> None:
        """
        root
        /  \\
       L    R
        \\  /
         M  (merge commit, not relevant here — just find LCA of L and R)
        """
        _write(repo, "root")
        _write(repo, "L", "root")
        _write(repo, "R", "root")
        assert find_merge_base(repo, "L", "R") == "root"

    def test_double_diamond(self, repo: pathlib.Path) -> None:
        """
        A
        / \\
       B   C
        \\ /
         D
        / \\
       E   F
        \\ /
         G
        LCA(E, F) should be D.
        """
        _write(repo, "A")
        _write(repo, "B", "A")
        _write(repo, "C", "A")
        _write(repo, "D", "B", "C")
        _write(repo, "E", "D")
        _write(repo, "F", "D")
        assert find_merge_base(repo, "E", "F") == "D"

    def test_criss_cross_merge(self, repo: pathlib.Path) -> None:
        """
        Criss-cross: A and B are each other's ancestor via two different merge paths.
        X → L1 → M1(L1,R1)
        X → R1 → M2(R1,L1)
        LCA of M1 and M2 should be either L1 or R1 (both are valid LCAs).
        The algorithm must not return None or crash.
        """
        _write(repo, "X")
        _write(repo, "L1", "X")
        _write(repo, "R1", "X")
        _write(repo, "M1", "L1", "R1")
        _write(repo, "M2", "R1", "L1")

        base = find_merge_base(repo, "M1", "M2")
        # Any of X, L1, R1 is a valid common ancestor; None is not acceptable.
        assert base is not None
        assert base in {"X", "L1", "R1"}

    def test_octopus_three_branch_fan_in(self, repo: pathlib.Path) -> None:
        """Three branches that all diverged from the same root."""
        _write(repo, "root")
        _write(repo, "branch-a", "root")
        _write(repo, "branch-b", "root")
        _write(repo, "branch-c", "root")

        assert find_merge_base(repo, "branch-a", "branch-b") == "root"
        assert find_merge_base(repo, "branch-a", "branch-c") == "root"
        assert find_merge_base(repo, "branch-b", "branch-c") == "root"


# ---------------------------------------------------------------------------
# Independent histories
# ---------------------------------------------------------------------------


class TestDisjointHistories:
    def test_no_common_ancestor_returns_none(self, repo: pathlib.Path) -> None:
        _write(repo, "island-a")
        _write(repo, "island-b")
        assert find_merge_base(repo, "island-a", "island-b") is None

    def test_long_independent_chains_return_none(self, repo: pathlib.Path) -> None:
        prev_a: str | None = None
        prev_b: str | None = None
        for i in range(20):
            a = f"a{i:02d}"
            b = f"b{i:02d}"
            _write(repo, a, prev_a)
            _write(repo, b, prev_b)
            prev_a = a
            prev_b = b
        assert find_merge_base(repo, "a19", "b19") is None

    def test_missing_commit_id_graceful(self, repo: pathlib.Path) -> None:
        """Asking for an LCA where one commit doesn't exist should return None, not raise."""
        _write(repo, "real")
        result = find_merge_base(repo, "real", "ghost-commit-that-does-not-exist")
        # The ghost has no ancestors, so no common ancestor found.
        assert result is None


# ---------------------------------------------------------------------------
# Ancestor-set correctness
# ---------------------------------------------------------------------------


class TestAncestorCorrectness:
    def test_merge_commit_has_both_parents_as_ancestors(self, repo: pathlib.Path) -> None:
        _write(repo, "root")
        _write(repo, "A", "root")
        _write(repo, "B", "root")
        _write(repo, "merge", "A", "B")
        _write(repo, "feature", "A")

        # LCA of feature and merge: feature branched from A, merge contains A.
        # So A is the common ancestor.
        base = find_merge_base(repo, "feature", "merge")
        assert base == "A"

    def test_wide_history_with_shared_root(self, repo: pathlib.Path) -> None:
        """100 branches diverging from a shared root, pairwise LCA is root."""
        _write(repo, "root")
        branches = [f"br{i:03d}" for i in range(50)]
        for br in branches:
            _write(repo, br, "root")

        # Check a sampling of pairs
        for i in range(0, 50, 10):
            for j in range(i + 1, 50, 10):
                assert find_merge_base(repo, branches[i], branches[j]) == "root"

    def test_deep_branch_divergence(self, repo: pathlib.Path) -> None:
        """Branches diverge at root, each has 50 commits. LCA is root."""
        _write(repo, "root")
        prev_a: str | None = "root"
        prev_b: str | None = "root"
        for i in range(50):
            a = f"da{i:02d}"
            b = f"db{i:02d}"
            _write(repo, a, prev_a)
            _write(repo, b, prev_b)
            prev_a = a
            prev_b = b

        assert find_merge_base(repo, "da49", "db49") == "root"

    def test_multiple_merge_bases_chain(self, repo: pathlib.Path) -> None:
        """A → B → C; branch D from B. LCA of C and D is B."""
        _write(repo, "A")
        _write(repo, "B", "A")
        _write(repo, "C", "B")
        _write(repo, "D", "B")
        assert find_merge_base(repo, "C", "D") == "B"
        assert find_merge_base(repo, "D", "C") == "B"
