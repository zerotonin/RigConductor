import json

from rigconductor.arenas import (
    Layout,
    load_layout,
    radius_outliers,
    save_layout,
    sort_row_major,
    uniform_radii,
    video_offset,
)

TRIAD = [
    (1557, 494, 140),
    (1092, 491, 139),
    (861, 748, 137),
    (1791, 754, 139),
    (1329, 751, 128),
    (1320, 1196, 114),
    (861, 1193, 134),
]


def test_row_major_matches_thermokourt_numbering():
    out = sort_row_major(TRIAD)
    assert [c[:2] for c in out[:2]] == [(1092, 491), (1557, 494)]
    assert [c[0] for c in out[2:5]] == [861, 1329, 1791]
    assert [c[0] for c in out[5:]] == [861, 1320]


def test_uniform_and_outliers():
    assert radius_outliers(sort_row_major(TRIAD)) == [6]
    assert {c[2] for c in uniform_radii(TRIAD)} == {137}
    assert radius_outliers(uniform_radii(TRIAD)) == []


def test_layout_roundtrip_renumbers_and_drops_offset(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(
        json.dumps(
            {
                "recording": "r",
                "frame_width": 2600,
                "frame_height": 2160,
                "video_offset": [670, 296],
                "video_crop": {"x": 670, "y": 296, "w": 944, "h": 976},
                "clip_box": "x, y, w, h",
                "padding": 0.1,
                "arenas": [
                    {"cx": 900, "cy": 900, "r": 100, "idx": 5, "clip": [790, 790, 220, 220]},
                    {"cx": 100, "cy": 900, "r": 100, "idx": 0},
                ],
            }
        ),
        encoding="utf-8",
    )
    lay = load_layout(p)
    assert (lay.frame_width, lay.frame_height) == (2600, 2160)
    save_layout(p, lay)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert [a["cx"] for a in d["arenas"]] == [100, 900] and [a["idx"] for a in d["arenas"]] == [
        0,
        1,
    ]
    assert "video_offset" not in d and d["recording"] == "r" and d["padding"] == 0.1
    assert "video_crop" not in d and all("clip" not in a for a in d["arenas"])  # re-cut writes them
    save_layout(tmp_path / "new" / "b.json", Layout([(1, 2, 3)], 10, 10))
    assert (tmp_path / "new" / "b.json").is_file()


def test_video_offset(tmp_path):
    assert video_offset(tmp_path) == (0, 0)
    (tmp_path / "reduced.json").write_text(json.dumps({"x": 670, "y": 296}), encoding="utf-8")
    assert video_offset(tmp_path) == (670, 296)
