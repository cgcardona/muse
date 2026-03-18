"""Sparse / block / full tensor diff for numerical arrays.

Diffs flat 1-D numerical arrays element-wise with an epsilon tolerance.
Floating-point values within ``schema.epsilon`` of each other are not
considered changed — this prevents noise from triggering spurious diffs in
simulation state, velocity curves, and weight matrices.

Three output modes (``schema.diff_mode``):

- ``"sparse"`` — one ``ReplaceOp`` per changed element. Best for data where
  a small fraction of elements change (e.g. sparse gradient updates).
- ``"block"``  — groups adjacent changed elements into contiguous range ops.
  Best for data where changes cluster (e.g. a section of a velocity curve
  was edited).
- ``"full"``   — emits a single ``ReplaceOp`` for the entire array if any
  element changed. Best for very large tensors where per-element ops are
  prohibitively expensive, or when the domain only cares "did anything change?"

Public API
----------
- :func:`diff` — ``list[float]`` × ``list[float]`` → ``StructuredDelta``.
"""
from __future__ import annotations

import hashlib
import logging

from muse.core.schema import TensorSchema
from muse.domain import DomainOp, ReplaceOp, StructuredDelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _float_content_id(values: list[float]) -> str:
    """Deterministic SHA-256 for a list of float values."""
    payload = ",".join(f"{v:.10g}" for v in values)
    return hashlib.sha256(payload.encode()).hexdigest()


def _single_content_id(value: float) -> str:
    """Deterministic SHA-256 for a single float value."""
    return hashlib.sha256(f"{value:.10g}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Top-level diff entry point
# ---------------------------------------------------------------------------


def diff(
    schema: TensorSchema,
    base: list[float],
    target: list[float],
    *,
    domain: str,
    address: str = "",
) -> StructuredDelta:
    """Diff two 1-D numerical arrays under the given ``TensorSchema``.

    Length mismatches are treated as a full replacement. For equal-length
    arrays, the ``diff_mode`` on the schema controls the output granularity.

    Args:
        schema:  The ``TensorSchema`` declaring dtype, epsilon, and diff_mode.
        base:    Base (ancestor) array of float values.
        target:  Target (newer) array of float values.
        domain:  Domain tag for the returned ``StructuredDelta``.
        address: Address prefix for generated op entries.

    Returns:
        A ``StructuredDelta`` with ``ReplaceOp`` entries for changed elements
        and a human-readable summary.
    """
    eps = schema["epsilon"]
    ops: list[DomainOp] = []

    # Length mismatch → full replacement regardless of diff_mode
    if len(base) != len(target):
        old_cid = _float_content_id(base)
        new_cid = _float_content_id(target)
        ops = [
            ReplaceOp(
                op="replace",
                address=address,
                position=None,
                old_content_id=old_cid,
                new_content_id=new_cid,
                old_summary=f"tensor[{len(base)}] (prev)",
                new_summary=f"tensor[{len(target)}] (new)",
            )
        ]
        return StructuredDelta(
            domain=domain,
            ops=ops,
            summary=f"tensor length changed {len(base)}→{len(target)}",
        )

    # Identify changed indices. Strict `>` so that eps=0.0 means exact equality:
    # identical values (|b-t|=0) are never flagged, while any actual difference is.
    changed: list[int] = [
        i for i, (b, t) in enumerate(zip(base, target)) if abs(b - t) > eps
    ]

    if not changed:
        return StructuredDelta(
            domain=domain, ops=[], summary="no numerical changes"
        )

    mode = schema["diff_mode"]

    if mode == "full":
        old_cid = _float_content_id(base)
        new_cid = _float_content_id(target)
        ops = [
            ReplaceOp(
                op="replace",
                address=address,
                position=None,
                old_content_id=old_cid,
                new_content_id=new_cid,
                old_summary=f"tensor[{len(base)}] (prev)",
                new_summary=f"tensor[{len(target)}] (new)",
            )
        ]
        summary = f"{len(changed)} element{'s' if len(changed) != 1 else ''} changed"

    elif mode == "sparse":
        for i in changed:
            ops.append(
                ReplaceOp(
                    op="replace",
                    address=address,
                    position=i,
                    old_content_id=_single_content_id(base[i]),
                    new_content_id=_single_content_id(target[i]),
                    old_summary=f"[{i}]={base[i]:.6g}",
                    new_summary=f"[{i}]={target[i]:.6g}",
                )
            )
        n = len(changed)
        summary = f"{n} element{'s' if n != 1 else ''} changed"

    else:  # "block"
        # Group adjacent changed indices into contiguous ranges
        blocks: list[tuple[int, int]] = []  # (start, end) inclusive
        run_start = changed[0]
        run_end = changed[0]
        for idx in changed[1:]:
            if idx == run_end + 1:
                run_end = idx
            else:
                blocks.append((run_start, run_end))
                run_start = idx
                run_end = idx
        blocks.append((run_start, run_end))

        for start, end in blocks:
            block_base = base[start : end + 1]
            block_target = target[start : end + 1]
            label = f"[{start}]" if start == end else f"[{start}:{end+1}]"
            ops.append(
                ReplaceOp(
                    op="replace",
                    address=address,
                    position=start,
                    old_content_id=_float_content_id(block_base),
                    new_content_id=_float_content_id(block_target),
                    old_summary=f"{label} (prev)",
                    new_summary=f"{label} (new)",
                )
            )
        n = len(changed)
        summary = (
            f"{n} element{'s' if n != 1 else ''} changed "
            f"in {len(blocks)} block{'s' if len(blocks) != 1 else ''}"
        )

    logger.debug(
        "numerical.diff %r mode=%r: %d changed of %d elements",
        address,
        mode,
        len(changed),
        len(base),
    )

    return StructuredDelta(domain=domain, ops=ops, summary=summary)
