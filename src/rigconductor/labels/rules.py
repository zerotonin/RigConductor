# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — labels.rules                                    ║
# ║  « is this labelled frame good enough to train on? »             ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Pure numpy checks on one frame; the SLEAP-side worker feeds it. ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Checks for labelled frames coming back from a labeller.

A frame is *rejected* when training on it would teach the model something
wrong (half-corrected, wrong fly count, no thorax, points off the image,
duplicates). It gets a *warning* when it breaks a labelling convention that a
person should look at (the decapitated female's hidden head points).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from rigconductor.constants import LABEL_ANCHOR, LABEL_FEMALE_HIDDEN

DUPLICATE_PX = 2.0  # every shared point this close = the same fly labelled twice


@dataclass
class Inst:
    """One instance: ``xy`` (n_nodes, 2) with NaN where not visible."""

    xy: np.ndarray
    user: bool = True


@dataclass
class Verdict:
    status: str  # accepted | rejected | unlabelled
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def same_fly(a: np.ndarray, b: np.ndarray, tol: float = DUPLICATE_PX) -> bool:
    """True when two instances are copies: every point visible in both coincides.

    Flies that overlap (courtship, mounting, copulation) share at most some
    points, so they are not flagged; at least two shared points are needed.
    """
    both = ~np.isnan(a).any(axis=1) & ~np.isnan(b).any(axis=1)
    if both.sum() < 2:
        return False
    return bool((np.linalg.norm(a[both] - b[both], axis=1) < tol).all())


def _female_like(inst: Inst, nodes: list[str]) -> bool:
    idx = [nodes.index(n) for n in LABEL_FEMALE_HIDDEN if n in nodes]
    return bool(idx) and bool(np.isnan(inst.xy[idx]).any(axis=1).all())


def check_frame(
    instances: list[Inst],
    nodes: list[str],
    shape_hw: tuple[int, int],
    allowed_counts: tuple[int, ...] = (3,),
    n_females: int | None = 1,
) -> Verdict:
    """Judge one labelled frame.

    Args:
        instances: All instances on the frame (user and predicted).
        nodes: Skeleton node names, in order.
        shape_hw: Image height and width.
        allowed_counts: Valid numbers of user instances (3 for triads).
        n_females: Instances expected with hidden head points, None to skip.
    """
    users = [i for i in instances if i.user]
    if not users:
        return Verdict("unlabelled")
    reasons: list[str] = []
    if len(users) != len(instances):
        reasons.append("half-corrected: predicted and labelled flies mixed")
    if len(users) not in allowed_counts:
        want = " or ".join(str(c) for c in allowed_counts)
        flies = "fly" if len(users) == 1 else "flies"
        reasons.append(f"{len(users)} {flies} labelled, expected {want}")
    a = nodes.index(LABEL_ANCHOR) if LABEL_ANCHOR in nodes else None
    h, w = shape_hw
    for k, inst in enumerate(users, 1):
        if a is not None and np.isnan(inst.xy[a]).any():
            reasons.append(f"fly {k}: no {LABEL_ANCHOR}")
        pts = inst.xy[~np.isnan(inst.xy).any(axis=1)]
        if len(pts) and ((pts < 0).any() or (pts[:, 0] >= w).any() or (pts[:, 1] >= h).any()):
            reasons.append(f"fly {k}: points outside the image")
    for i in range(len(users)):
        for j in range(i + 1, len(users)):
            if same_fly(users[i].xy, users[j].xy):
                reasons.append(f"flies {i + 1} and {j + 1} are the same fly (labelled twice)")
    warnings: list[str] = []
    if n_females is not None and len(users) == max(allowed_counts):
        n = sum(_female_like(u, nodes) for u in users)
        if n != n_females:
            warnings.append(f"{n} flies with hidden head points, expected {n_females} (female)")
    return Verdict("rejected" if reasons else "accepted", reasons, warnings)


def skeleton_problem(nodes: list[str], expected: list[str]) -> str | None:
    """Text describing a skeleton mismatch, or None when identical."""
    if list(nodes) == list(expected):
        return None
    if set(nodes) == set(expected):
        return "skeleton nodes are in a different order"
    missing = [n for n in expected if n not in nodes]
    extra = [n for n in nodes if n not in expected]
    return f"skeleton differs (missing {missing}, extra {extra})"
