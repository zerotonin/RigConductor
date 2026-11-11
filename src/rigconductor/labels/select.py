# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — labels.select                                   ║
# ║  « which frames are worth a labeller's time »                    ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Pure numpy: scores per frame from a pose file, then a pick that ║
# ║  mixes unsure, contact and evenly spread frames.                 ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Frame selection for new labels.

Scores come from the SLEAP analysis export of an arena (``tracks``,
``point_scores``, ``track_occupancy``). Three kinds of frames are picked:

* ``unsure``: the model found fewer flies than expected or placed points with
  low confidence;
* ``contact``: two thoraxes are closer than ``LABEL_CONTACT_BL`` body lengths
  (identity swaps and leg mix-ups happen here);
* ``uniform``: spread evenly over the clip, so ordinary frames stay covered.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from rigconductor.constants import LABEL_ANCHOR, LABEL_CONTACT_BL, LABEL_MIX

KINDS = ("unsure", "contact", "uniform")
DEFAULT_BODY_PX = 30.0


@dataclass(frozen=True)
class Pick:
    frame: int
    reason: str
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def unsure_scores(point_scores: np.ndarray, occupancy: np.ndarray, n_expected: int) -> np.ndarray:
    """Per-frame doubt: missing flies plus low point confidence, 0 (sure) … 2.

    Args:
        point_scores: ``(n_tracks, n_nodes, n_frames)``, NaN where no point.
        occupancy: ``(n_frames, n_tracks)``, 1 where a track has an instance.
        n_expected: Flies that should be in every frame.
    """
    present = occupancy.astype(bool).sum(axis=1)
    missing = np.clip(n_expected - present, 0, None) / max(n_expected, 1)
    ps = np.nan_to_num(point_scores, nan=0.0)
    occ = occupancy.T.astype(bool)[:, None, :]  # (tracks, 1, frames)
    n_pts = np.maximum(present * ps.shape[1], 1)
    mean_score = (ps * occ).sum(axis=(0, 1)) / n_pts
    low = np.where(present > 0, 1.0 - np.clip(mean_score, 0, 1), 1.0)
    return missing + low


def body_length_px(tracks: np.ndarray, node_names: list[str]) -> float:
    """Median head-to-abdomen distance, a body-length estimate in px."""
    if "head" not in node_names or "abdomen" not in node_names:
        return DEFAULT_BODY_PX
    h, a = node_names.index("head"), node_names.index("abdomen")
    d = np.linalg.norm(tracks[:, :, h, :] - tracks[:, :, a, :], axis=1)
    d = d[np.isfinite(d)]
    return float(np.median(d)) if d.size else DEFAULT_BODY_PX


def contact_scores(tracks: np.ndarray, node_names: list[str]) -> np.ndarray:
    """Per-frame closeness of the two nearest flies, 0 (apart) … 1 (touching).

    Args:
        tracks: ``(n_tracks, 2, n_nodes, n_frames)`` positions, NaN where missing.
        node_names: Skeleton node names.
    """
    n_frames = tracks.shape[-1]
    if LABEL_ANCHOR not in node_names or tracks.shape[0] < 2:
        return np.zeros(n_frames)
    xy = tracks[:, :, node_names.index(LABEL_ANCHOR), :]  # (tracks, 2, frames)
    reach = LABEL_CONTACT_BL * body_length_px(tracks, node_names)
    best = np.full(n_frames, np.inf)
    for i in range(xy.shape[0]):
        for j in range(i + 1, xy.shape[0]):
            d = np.linalg.norm(xy[i] - xy[j], axis=0)
            best = np.fmin(best, np.where(np.isfinite(d), d, np.inf))
    return np.clip(1.0 - best / reach, 0.0, 1.0)


def _far_enough(f: int, taken: list[int], gap: int) -> bool:
    return all(abs(f - t) >= gap for t in taken)


def _top(
    scores: np.ndarray, k: int, taken: list[int], gap: int, banned: set[int], rng
) -> list[Pick]:
    out: list[Pick] = []
    for f in np.lexsort((rng.random(len(scores)), -scores)):  # ties in random order
        if len(out) >= k or scores[f] <= 0:
            break
        f = int(f)
        if f not in banned and _far_enough(f, taken, gap):
            out.append(Pick(f, "", float(scores[f])))
            taken.append(f)
    return out


def _spread(n_frames: int, k: int, taken: list[int], gap: int, banned: set[int]) -> list[Pick]:
    out: list[Pick] = []
    if k <= 0 or n_frames <= 0:
        return out
    for f in np.linspace(0, n_frames - 1, num=4 * k + 2)[1:-1].astype(int):
        if len(out) >= k:
            break
        f = int(f)
        if f not in banned and _far_enough(f, taken, gap):
            out.append(Pick(f, "uniform"))
            taken.append(f)
    return out


def quotas(n: int, mix: dict[str, float] | None = None) -> dict[str, int]:
    """Split ``n`` frames over the kinds by ``mix`` (rounded, sums to ``n``)."""
    mix = {k: float((mix or LABEL_MIX).get(k, 0.0)) for k in KINDS}
    total = sum(mix.values()) or 1.0
    q = {k: int(n * v / total) for k, v in mix.items()}
    rest = n - sum(q.values())
    for k in sorted(KINDS, key=lambda k: -(n * mix[k] / total - q[k]))[:rest]:
        q[k] += 1
    return q


def pick_frames(
    n_frames: int,
    n: int,
    unsure: np.ndarray | None = None,
    contact: np.ndarray | None = None,
    mix: dict[str, float] | None = None,
    min_gap: int = 1,
    exclude: set[int] | None = None,
    seed: int = 0,
) -> list[Pick]:
    """Choose ``n`` frames of one arena clip.

    Args:
        n_frames: Frames in the clip.
        n: Frames wanted.
        unsure: Doubt per frame (``unsure_scores``) or None without a pose file.
        contact: Closeness per frame (``contact_scores``) or None.
        mix: Shares per kind; defaults to ``LABEL_MIX``.
        min_gap: Picked frames are at least this many frames apart.
        exclude: Frames that must not be picked (already labelled).
        seed: Seed for breaking ties between equal scores.

    Returns:
        Picks sorted by frame. Kinds without scores, or without enough distinct
        frames, are filled up with evenly spread frames.
    """
    q = quotas(n, mix)
    rng = np.random.default_rng(seed)
    banned = set(exclude or ())
    taken: list[int] = []
    picks: list[Pick] = []
    for kind, scores in (("unsure", unsure), ("contact", contact)):
        if scores is None or q[kind] == 0:
            continue
        got = _top(np.asarray(scores, float), q[kind], taken, min_gap, banned, rng)
        picks += [Pick(p.frame, kind, p.score) for p in got]
    picks += _spread(n_frames, n - len(picks), taken, min_gap, banned)
    return sorted(picks, key=lambda p: p.frame)
