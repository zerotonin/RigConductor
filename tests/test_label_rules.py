import numpy as np

from rigconductor.labels import rules

NODES = ["head", "thorax", "abdomen", "eyeL", "eyeR"]


def fly(x, female=False, user=True):
    xy = np.array([[x, 10], [x, 20], [x, 30], [x - 2, 8], [x + 2, 8]], float)
    if female:
        xy[[0, 3, 4]] = np.nan
    return rules.Inst(xy, user=user)


def test_good_triad_is_accepted():
    v = rules.check_frame([fly(10), fly(50), fly(90, female=True)], NODES, (100, 100))
    assert v.status == "accepted" and not v.warnings


def test_half_corrected_and_wrong_count_are_rejected():
    v = rules.check_frame([fly(10), fly(50, user=False), fly(90, True)], NODES, (100, 100))
    assert v.status == "rejected" and any("half-corrected" in r for r in v.reasons)
    v = rules.check_frame([fly(10), fly(90, True)], NODES, (100, 100))
    assert v.status == "rejected" and "2 flies labelled, expected 3" in v.reasons


def test_missing_thorax_duplicates_and_outside_points():
    a = fly(10)
    a.xy[1] = np.nan
    v = rules.check_frame([a, fly(50), fly(150, True)], NODES, (100, 100))
    assert "fly 1: no thorax" in v.reasons
    assert "fly 3: points outside the image" in v.reasons
    v = rules.check_frame([fly(10), fly(10), fly(90, True)], NODES, (100, 100))
    assert "flies 1 and 2 are the same fly (labelled twice)" in v.reasons


def test_overlapping_flies_in_courtship_are_fine():
    male, female = fly(40), fly(90, True)
    mounting = fly(40)
    mounting.xy = mounting.xy + [[0, 8]] * len(NODES)  # on top, shifted along the body
    mounting.xy[1] = male.xy[1]  # thoraxes on the same spot
    v = rules.check_frame([male, mounting, female], NODES, (100, 100))
    assert not any("same fly" in r for r in v.reasons)


def test_female_rule_is_a_warning_and_unlabelled_frames_pass_through():
    v = rules.check_frame([fly(10), fly(50), fly(90)], NODES, (100, 100))
    assert v.status == "accepted" and v.warnings
    assert rules.check_frame([fly(10, user=False)], NODES, (100, 100)).status == "unlabelled"


def test_skeleton_problem():
    assert rules.skeleton_problem(NODES, NODES) is None
    assert "order" in rules.skeleton_problem(NODES[::-1], NODES)
    assert "missing" in rules.skeleton_problem(NODES[:2], NODES)
