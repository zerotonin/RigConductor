import numpy as np

from rigconductor.labels import select

NODES = ["head", "thorax", "abdomen"]


def _tracks(n_frames=100, gap_frames=(), touch_frames=()):
    """3 flies at fixed places; flies 0 and 1 touch on ``touch_frames``."""
    t = np.zeros((3, 2, 3, n_frames))
    for k, x in enumerate((0.0, 100.0, 200.0)):
        t[k, 0, :, :] = x + np.array([-10, 0, 10])[:, None]
    for f in touch_frames:
        t[1, 0, :, f] = t[0, 0, :, f] + 5
    for f in gap_frames:
        t[2, :, :, f] = np.nan
    return t


def test_quotas_add_up_and_follow_the_mix():
    q = select.quotas(10, {"unsure": 0.4, "contact": 0.4, "uniform": 0.2})
    assert q == {"unsure": 4, "contact": 4, "uniform": 2}
    assert sum(select.quotas(7).values()) == 7


def test_unsure_counts_missing_flies_and_low_scores():
    occ = np.ones((5, 3))
    occ[2, 2] = 0
    scores = np.ones((3, 3, 5))
    scores[:, :, 4] = 0.2
    u = select.unsure_scores(scores, occ, 3)
    assert u[0] == 0 and u[2] > 0.3 and u[4] > 0.7
    assert np.argmax(u) == 4


def test_contact_is_high_where_thoraxes_meet():
    c = select.contact_scores(_tracks(touch_frames=[30]), NODES)
    assert c[30] > 0.5 and c[0] == 0


def test_pick_mixes_kinds_keeps_gaps_and_skips_excluded():
    n = 1000
    unsure = np.zeros(n)
    unsure[[10, 11, 12, 500]] = 1.0
    contact = np.zeros(n)
    contact[[700, 701]] = 1.0
    picks = select.pick_frames(n, 6, unsure, contact, min_gap=5, exclude={500})
    frames = [p.frame for p in picks]
    assert len(picks) == 6 and frames == sorted(frames)
    assert 500 not in frames
    assert all(b - a >= 5 for a, b in zip(frames, frames[1:], strict=False))
    kinds = {p.reason for p in picks}
    assert {"unsure", "contact", "uniform"} <= kinds


def test_without_poses_everything_is_spread():
    picks = select.pick_frames(300, 5)
    assert [p.reason for p in picks] == ["uniform"] * 5
    assert picks[0].frame > 0 and picks[-1].frame < 299
