import json
import shutil
import subprocess

import pytest
import yaml

from rigconductor import ConductorError
from rigconductor.reduce import _frames, reduce_box, reduce_recording

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")


def test_reduce_box_aligned_even_and_clamped():
    x, y, w, h = reduce_box([(1082.25, 952.25, 102), (1602.25, 1150.5, 100)], 20, 2600, 2160)
    assert w % 16 == 0 and h % 16 == 0 and x % 2 == 0 and y % 2 == 0
    assert x <= 1082.25 - 122 and x + w >= 1602.25 + 120
    assert y <= 952.25 - 122 and y + h >= 1150.5 + 120
    # near the border: stays inside the frame
    x, y, w, h = reduce_box([(30, 30, 25), (2590, 2150, 25)], 20, 2600, 2160)
    assert x >= 0 and y >= 0 and x + w <= 2600 and y + h <= 2160
    with pytest.raises(ConductorError):
        reduce_box([], 20, 100, 100)


def _store(tmp_path, n=30, w=320, h=240):
    rec = tmp_path / "rec_20261111_101458"
    rec.mkdir()
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={w}x{h}:rate=25",
            "-frames:v",
            str(n),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(rec / "000000.mp4"),
        ],
        check=True,
    )
    (rec / "000000.npz").write_bytes(b"timestamps")
    (rec / "metadata.yaml").write_text(
        yaml.safe_dump({"__store": {"imgshape": [h, w], "chunksize": 18000}, "camera": "x"}),
        encoding="utf-8",
    )
    return rec


@needs_ffmpeg
@pytest.mark.parametrize("keep_full", [False, True])
def test_reduce_recording_crops_verifies_and_records(tmp_path, keep_full):
    rec = _store(tmp_path)
    logs = []
    info = reduce_recording(
        rec, [(100, 100, 30), (200, 150, 30)], 10, keep_full=keep_full, log=logs.append
    )
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(rec / "000000.mp4"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    s = json.loads(out.stdout)["streams"][0]
    assert (s["width"], s["height"]) == (info["w"], info["h"]) and info["w"] < 320
    assert _frames(rec / "000000.mp4") == 30
    meta = yaml.safe_load((rec / "metadata.yaml").read_text(encoding="utf-8"))
    assert meta["__store"]["imgshape"] == [info["h"], info["w"]]
    assert yaml.safe_load((rec / "metadata.full.yaml").read_text(encoding="utf-8"))["__store"][
        "imgshape"
    ] == [
        240,
        320,
    ]
    assert (rec / "000000.npz").read_bytes() == b"timestamps"
    assert json.loads((rec / "reduced.json").read_text(encoding="utf-8"))["x"] == info["x"]
    assert (rec / "full_frame" / "000000.mp4").is_file() == keep_full
    assert not list(rec.glob("*.reduced.mp4"))
    # idempotent
    assert reduce_recording(rec, [(1, 1, 1)], 0, log=logs.append) == info
    assert "already cropped" in logs[-1]


@needs_ffmpeg
def test_reduce_gray_drops_colour(tmp_path):
    rec = _store(tmp_path)
    info = reduce_recording(rec, [(160, 120, 60)], 0, gray=True, log=lambda s: None)
    assert info["gray"] is True
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(rec / "000000.mp4"),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    n = info["w"] * info["h"]
    chroma = out[n:]
    assert max(chroma) - min(chroma) <= 2  # U and V flat: no colour left


@needs_ffmpeg
def test_reduce_retimes_to_the_real_camera_rate(tmp_path):
    import numpy as np

    rec = _store(tmp_path)  # mp4 labelled 25 fps
    np.savez(rec / "000000.npz", frame_time=1000 + np.arange(30) / 69.0)
    info = reduce_recording(rec, [(160, 120, 60)], 0, log=lambda s: None)
    assert info["fps"] == 69.0
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "csv=p=0",
            str(rec / "000000.mp4"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "69/1" and _frames(rec / "000000.mp4") == 30
