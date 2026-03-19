"""Music-domain query DSL for the Muse VCS.

Allows agents and humans to query the commit history for musical content::

    muse music-query "note.pitch_class == 'Eb' and bar == 12"
    muse music-query "harmony.quality == 'dim' and bar == 8"
    muse music-query "author == 'agent-x' and track == 'piano.mid'"
    muse music-query "note.velocity > 80 and not bar == 4"
    muse music-query "agent_id == 'counterpoint-bot'"

Grammar (EBNF)
--------------

    query     = or_expr
    or_expr   = and_expr ( 'or' and_expr )*
    and_expr  = not_expr ( 'and' not_expr )*
    not_expr  = 'not' not_expr | atom
    atom      = '(' query ')' | comparison
    comparison = FIELD OP VALUE
    FIELD     = 'note.pitch' | 'note.pitch_class' | 'note.velocity'
              | 'note.channel' | 'note.duration'
              | 'bar' | 'track' | 'harmony.chord' | 'harmony.quality'
              | 'author' | 'agent_id' | 'model_id' | 'toolchain_id'
    OP        = '==' | '!=' | '>' | '<' | '>=' | '<='
    VALUE     = QUOTED_STRING | NUMBER

Supported field paths
---------------------

+---------------------+---------------------------------------------+
| Field               | Resolves to                                 |
+=====================+=============================================+
| note.pitch          | any note's MIDI pitch (integer 0–127)       |
+---------------------+---------------------------------------------+
| note.pitch_class    | pitch class name ("C", "C#", …, "B")       |
+---------------------+---------------------------------------------+
| note.velocity       | MIDI velocity (0–127)                       |
+---------------------+---------------------------------------------+
| note.channel        | MIDI channel (0–15)                         |
+---------------------+---------------------------------------------+
| note.duration       | note duration in beats (float)              |
+---------------------+---------------------------------------------+
| bar                 | 1-indexed bar number (assumes 4/4)          |
+---------------------+---------------------------------------------+
| track               | workspace-relative MIDI file path           |
+---------------------+---------------------------------------------+
| harmony.chord       | detected chord name ("Cmaj", "Fdim7", …)   |
+---------------------+---------------------------------------------+
| harmony.quality     | chord quality suffix ("maj", "min", "dim"…) |
+---------------------+---------------------------------------------+
| author              | commit author string                        |
+---------------------+---------------------------------------------+
| agent_id            | agent_id from commit provenance             |
+---------------------+---------------------------------------------+
| model_id            | model_id from commit provenance             |
+---------------------+---------------------------------------------+
| toolchain_id        | toolchain_id from commit provenance         |
+---------------------+---------------------------------------------+
"""

import logging
import pathlib
import re
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from muse.core.object_store import read_object
from muse.core.store import CommitRecord, get_commit_snapshot_manifest, read_commit
from muse.plugins.midi._query import (
    NoteInfo,
    detect_chord,
    notes_by_bar,
    walk_commits_for_track,
)
from muse.plugins.midi.midi_diff import extract_notes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AST node dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EqNode:
    """Leaf comparison: ``field OP value``."""

    field: str
    op: Literal["==", "!=", ">", "<", ">=", "<="]
    value: str | int | float


@dataclass
class AndNode:
    """Logical AND of two sub-expressions."""

    left: QueryNode
    right: QueryNode


@dataclass
class OrNode:
    """Logical OR of two sub-expressions."""

    left: QueryNode
    right: QueryNode


@dataclass
class NotNode:
    """Logical NOT of a sub-expression."""

    inner: QueryNode


QueryNode = EqNode | AndNode | OrNode | NotNode


# ---------------------------------------------------------------------------
# Query context and result types
# ---------------------------------------------------------------------------


@dataclass
class QueryContext:
    """Data available to the evaluator for one bar in one track at one commit."""

    commit: CommitRecord
    track: str
    bar: int
    notes: list[NoteInfo]
    chord: str
    ticks_per_beat: int


class NoteDict(TypedDict):
    """Serialisable representation of a note for query results."""

    pitch: int
    pitch_class: str
    velocity: int
    channel: int
    beat: float
    duration_beats: float


class QueryMatch(TypedDict):
    """A single match returned by :func:`run_query`."""

    commit_id: str
    commit_short: str
    commit_message: str
    author: str
    agent_id: str
    committed_at: str
    track: str
    bar: int
    notes: list[NoteDict]
    chord: str
    matched_on: str


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<LPAREN>  \(                             ) |
    (?P<RPAREN>  \)                             ) |
    (?P<OP>      ==|!=|>=|<=|>|<                ) |
    (?P<KW>      (?:and|or|not)(?![A-Za-z0-9_.]) ) |
    (?P<STR>     "(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*' ) |
    (?P<NUM>     -?\d+(?:\.\d+)?                ) |
    (?P<NAME>    [A-Za-z_][A-Za-z0-9_.]*        ) |
    (?P<WS>      \s+                            )
    """,
    re.VERBOSE,
)

_OpLiteral = Literal["==", "!=", ">", "<", ">=", "<="]
_VALID_OPS: frozenset[str] = frozenset({"==", "!=", ">", "<", ">=", "<="})


@dataclass
class Token:
    """A single lexed token."""

    kind: str
    value: str


def _tokenize(query: str) -> list[Token]:
    """Convert *query* string to a flat list of :class:`Token` objects.

    Whitespace tokens are discarded.  Raises ``ValueError`` on unrecognised input.
    """
    tokens: list[Token] = []
    for m in _TOKEN_RE.finditer(query):
        kind = m.lastgroup
        if kind is None or kind == "WS":
            continue
        tokens.append(Token(kind=kind, value=m.group()))
    # Verify full coverage.
    covered = sum(len(m.group()) for m in _TOKEN_RE.finditer(query) if m.lastgroup != "WS")
    no_ws = re.sub(r"\s+", "", query)
    if covered != len(no_ws):
        raise ValueError(f"Unrecognised characters in query: {query!r}")
    return tokens


# ---------------------------------------------------------------------------
# Recursive descent parser
# ---------------------------------------------------------------------------


class _Parser:
    """Recursive descent parser for the music query DSL."""

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> Token | None:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _consume(self, kind: str | None = None, value: str | None = None) -> Token:
        tok = self._peek()
        if tok is None:
            raise ValueError("Unexpected end of query")
        if kind is not None and tok.kind != kind:
            raise ValueError(f"Expected {kind!r}, got {tok.kind!r} ({tok.value!r})")
        if value is not None and tok.value != value:
            raise ValueError(f"Expected {value!r}, got {tok.value!r}")
        self._pos += 1
        return tok

    def parse(self) -> QueryNode:
        node = self._or_expr()
        if self._peek() is not None:
            raise ValueError(f"Unexpected token: {self._peek()!r}")
        return node

    def _or_expr(self) -> QueryNode:
        node = self._and_expr()
        while (tok := self._peek()) is not None and tok.kind == "KW" and tok.value == "or":
            self._consume()
            right = self._and_expr()
            node = OrNode(left=node, right=right)
        return node

    def _and_expr(self) -> QueryNode:
        node = self._not_expr()
        while (tok := self._peek()) is not None and tok.kind == "KW" and tok.value == "and":
            self._consume()
            right = self._not_expr()
            node = AndNode(left=node, right=right)
        return node

    def _not_expr(self) -> QueryNode:
        tok = self._peek()
        if tok is not None and tok.kind == "KW" and tok.value == "not":
            self._consume()
            return NotNode(inner=self._not_expr())
        return self._atom()

    def _atom(self) -> QueryNode:
        tok = self._peek()
        if tok is None:
            raise ValueError("Unexpected end of query in atom")
        if tok.kind == "LPAREN":
            self._consume("LPAREN")
            node = self._or_expr()
            self._consume("RPAREN")
            return node
        return self._comparison()

    def _comparison(self) -> QueryNode:
        field_tok = self._consume("NAME")
        op_tok = self._consume("OP")
        if op_tok.value not in _VALID_OPS:
            raise ValueError(f"Invalid operator: {op_tok.value!r}")

        val_tok = self._consume()
        if val_tok.kind == "STR":
            raw = val_tok.value[1:-1]
            raw = raw.replace('\\"', '"').replace("\\'", "'")
            value: str | int | float = raw
        elif val_tok.kind == "NUM":
            value = float(val_tok.value) if "." in val_tok.value else int(val_tok.value)
        elif val_tok.kind == "NAME":
            value = val_tok.value
        else:
            raise ValueError(f"Expected value, got {val_tok.kind!r}")

        _op_map: dict[str, _OpLiteral] = {
            "==": "==", "!=": "!=", ">": ">", "<": "<", ">=": ">=", "<=": "<=",
        }
        op_val = _op_map.get(op_tok.value)
        if op_val is None:
            raise ValueError(f"Invalid operator: {op_tok.value!r}")
        return EqNode(
            field=field_tok.value,
            op=op_val,
            value=value,
        )


def parse_query(query_str: str) -> QueryNode:
    """Parse a query string into an AST.

    Args:
        query_str: Music query expression.

    Returns:
        Root :data:`QueryNode` of the AST.

    Raises:
        ValueError: On parse error.
    """
    tokens = _tokenize(query_str)
    return _Parser(tokens).parse()


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def _compare(actual: str | int | float, op: str, expected: str | int | float) -> bool:
    """Apply a comparison operator to two values.

    String comparisons use ``==`` / ``!=`` only (other operators raise).
    Numeric comparisons support all six operators.
    """
    if isinstance(actual, str):
        if op == "==":
            return actual.lower() == str(expected).lower()
        if op == "!=":
            return actual.lower() != str(expected).lower()
        raise ValueError(f"Operator {op!r} not supported for string values")

    exp_num: float
    if isinstance(expected, str):
        try:
            exp_num = float(expected)
        except ValueError:
            raise ValueError(f"Cannot compare numeric field to {expected!r}")
    else:
        exp_num = float(expected)

    act_num = float(actual)
    if op == "==":
        return act_num == exp_num
    if op == "!=":
        return act_num != exp_num
    if op == ">":
        return act_num > exp_num
    if op == "<":
        return act_num < exp_num
    if op == ">=":
        return act_num >= exp_num
    if op == "<=":
        return act_num <= exp_num
    raise ValueError(f"Unknown operator {op!r}")


def _resolve_field(field: str, ctx: QueryContext) -> list[str | int | float]:
    """Resolve a field path to a list of candidate values from *ctx*.

    Note fields return one value per note in the bar; all other fields
    return a single-element list.  The evaluator matches if *any* candidate
    satisfies the predicate.
    """
    # --- Note fields ---
    if field == "note.pitch":
        return [n.pitch for n in ctx.notes]
    if field == "note.pitch_class":
        return [n.pitch_class_name for n in ctx.notes]
    if field == "note.velocity":
        return [n.velocity for n in ctx.notes]
    if field == "note.channel":
        return [n.channel for n in ctx.notes]
    if field == "note.duration":
        return [n.beat_duration for n in ctx.notes]
    # --- Bar / track ---
    if field == "bar":
        return [ctx.bar]
    if field == "track":
        return [ctx.track]
    # --- Harmony ---
    if field == "harmony.chord":
        return [ctx.chord]
    if field == "harmony.quality":
        for suffix in ("dim7", "maj7", "min7", "dom7", "sus2", "sus4", "aug", "dim", "maj", "min", "5"):
            if ctx.chord.endswith(suffix):
                return [suffix]
        return [""]
    # --- Commit provenance ---
    if field == "author":
        return [ctx.commit.author]
    if field == "agent_id":
        return [ctx.commit.agent_id]
    if field == "model_id":
        return [ctx.commit.model_id]
    if field == "toolchain_id":
        return [ctx.commit.toolchain_id]

    raise ValueError(f"Unknown field: {field!r}")


def evaluate_node(node: QueryNode, ctx: QueryContext) -> bool:
    """Recursively evaluate a query AST node against *ctx*.

    Args:
        node: The root (or sub) AST node.
        ctx:  Query context for the bar/track/commit being tested.

    Returns:
        ``True`` when the predicate matches, ``False`` otherwise.
    """
    if isinstance(node, EqNode):
        try:
            candidates = _resolve_field(node.field, ctx)
        except ValueError:
            return False
        return any(_compare(c, node.op, node.value) for c in candidates)

    if isinstance(node, AndNode):
        return evaluate_node(node.left, ctx) and evaluate_node(node.right, ctx)

    if isinstance(node, OrNode):
        return evaluate_node(node.left, ctx) or evaluate_node(node.right, ctx)

    if isinstance(node, NotNode):
        return not evaluate_node(node.inner, ctx)

    raise TypeError(f"Unknown AST node type: {type(node)}")


# ---------------------------------------------------------------------------
# Query runner
# ---------------------------------------------------------------------------


def run_query(
    query_str: str,
    root: pathlib.Path,
    start_commit_id: str,
    *,
    track_filter: str | None = None,
    from_commit_id: str | None = None,
    max_commits: int = 10_000,
    max_results: int = 1_000,
) -> list[QueryMatch]:
    """Evaluate a music query DSL expression over the commit history.

    Walks the parent chain from *start_commit_id*, loading each MIDI track,
    grouping notes by bar, and evaluating the query predicate against each
    (commit, track, bar) triple.

    Args:
        query_str:      Music query expression string.
        root:           Repository root.
        start_commit_id: Start of the walk (inclusive; usually HEAD).
        track_filter:   Restrict search to a single MIDI file path.
        from_commit_id: Stop before this commit ID (exclusive).
        max_commits:    Safety cap on commits walked.
        max_results:    Safety cap on results returned.

    Returns:
        List of :class:`QueryMatch` dicts, chronologically ordered
        (oldest first).

    Raises:
        ValueError: When the query string cannot be parsed.
    """
    ast = parse_query(query_str)
    results: list[QueryMatch] = []

    commit_id: str | None = start_commit_id
    seen: set[str] = set()
    commits_walked = 0

    while commit_id and commit_id not in seen and commits_walked < max_commits:
        seen.add(commit_id)
        commits_walked += 1

        if commit_id == from_commit_id:
            break

        commit = read_commit(root, commit_id)
        if commit is None:
            break

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
            except ValueError:
                continue

            notes = [NoteInfo.from_note_key(k, tpb) for k in keys]
            bar_map = notes_by_bar(notes)

            for bar_num, bar_notes in sorted(bar_map.items()):
                pcs = frozenset(n.pitch_class for n in bar_notes)
                chord = detect_chord(pcs)
                ctx = QueryContext(
                    commit=commit,
                    track=track_path,
                    bar=bar_num,
                    notes=bar_notes,
                    chord=chord,
                    ticks_per_beat=tpb,
                )
                if evaluate_node(ast, ctx):
                    results.append(
                        QueryMatch(
                            commit_id=commit.commit_id,
                            commit_short=commit.commit_id[:8],
                            commit_message=commit.message,
                            author=commit.author,
                            agent_id=commit.agent_id,
                            committed_at=commit.committed_at.isoformat(),
                            track=track_path,
                            bar=bar_num,
                            notes=[
                                NoteDict(
                                    pitch=n.pitch,
                                    pitch_class=n.pitch_class_name,
                                    velocity=n.velocity,
                                    channel=n.channel,
                                    beat=round(n.beat, 4),
                                    duration_beats=round(n.beat_duration, 4),
                                )
                                for n in bar_notes
                            ],
                            chord=chord,
                            matched_on=query_str,
                        )
                    )
                    if len(results) >= max_results:
                        logger.warning(
                            "⚠️ music-query hit max_results=%d — truncating",
                            max_results,
                        )
                        results.reverse()
                        return results

        commit_id = commit.parent_commit_id

    results.reverse()  # oldest-first
    return results
