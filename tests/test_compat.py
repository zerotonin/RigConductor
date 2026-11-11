from pathlib import Path

from rigconductor.compat import ClipInfo, check_model_vs_clips
from rigconductor.models import parse_registry

REG = """
- id: flies13_base
  organism: drosophila
  kind: sleap-topdown
  expects: {channels: 1, polarity: bright, invert: false, min_side_px: 224, fps: 150,
            input_size: 1024}
- id: lab_v1
  organism: drosophila
  kind: sleap-topdown
  grayscale_input: true
  expects: {channels: 1, polarity: bright, invert: true, min_side_px: 224, fps: 25}
- id: bare
  organism: drosophila
  kind: sleap-topdown
"""
FLIES13, LAB, BARE = parse_registry(REG)


def clip(mean=220.0, pix="yuv420p", side=512, fps=25.0):
    return ClipInfo(Path("a.mp4"), side, side, fps, pix, mean)


def by_suffix(checks):
    return {c.name.split(": ")[-1]: c for c in checks}


def test_flies13_on_our_rig_fails_polarity_and_fps():
    c = by_suffix(check_model_vs_clips(FLIES13, [clip()], invert=False))
    assert not c["polarity"].ok and "dark" in c["polarity"].detail
    assert not c["frame rate"].ok and not c["frame rate"].blocking  # pose model: warning
    assert c["frame rate"].line.startswith("[WARN]")
    assert not c["channels"].ok  # 3-channel clips, 1-channel model, no conversion
    assert not c["input size"].ok and c["input size"].blocking  # 512 px clips, 1024 px model


def test_lab_model_with_inversion_passes():
    checks = check_model_vs_clips(LAB, [clip(), clip()], invert=True)
    assert all(c.ok for c in checks), [c.line for c in checks]


def test_forgetting_inversion_is_caught():
    c = by_suffix(check_model_vs_clips(LAB, [clip()], invert=False))
    assert not c["polarity"].ok and "Toggle inversion" in c["polarity"].hint


def test_small_clips_and_missing_expects():
    c = by_suffix(check_model_vs_clips(LAB, [clip(side=160)], invert=True))
    assert not c["frame size"].ok
    bare = check_model_vs_clips(BARE, [clip()])
    assert len(bare) == 1 and not bare[0].ok and "expects" in bare[0].detail
    assert not check_model_vs_clips(LAB, [])[0].ok
