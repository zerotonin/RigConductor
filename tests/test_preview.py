import io
import json

from rigconductor.preview import iter_jpegs, load_arena_overlay, preview_url

JPG = b"\xff\xd8" + b"abc" * 10 + b"\xff\xd9"


def test_iter_jpegs_across_chunk_boundaries():
    body = b""
    for i in range(3):
        body += (
            b"--BOUNDARYSTRING\r\nContent-type: image/jpeg\r\n\r\n"
            + JPG[:-2]
            + bytes([i])
            + JPG[-2:]
        )
    frames = list(iter_jpegs(io.BytesIO(body), chunk=7))
    assert len(frames) == 3
    assert all(f.startswith(b"\xff\xd8") and f.endswith(b"\xff\xd9") for f in frames)
    assert [f[-3] for f in frames] == [0, 1, 2]


def test_preview_url_and_missing():
    raw = {"camera_info": {"stream": {"preview": {"url": "http://127.0.0.1:8310/stream"}}}}
    assert preview_url(raw) == "http://127.0.0.1:8310/stream"
    assert preview_url({"camera_info": {}}) is None


def test_overlay_from_thermokourt_json(tmp_path):
    p = tmp_path / "arenas.json"
    p.write_text(
        json.dumps(
            {
                "frame_width": 2600,
                "frame_height": 2160,
                "arenas": [{"cx": 1300, "cy": 1080, "r": 240, "idx": 0}],
            }
        ),
        encoding="utf-8",
    )
    circles, w = load_arena_overlay(p)
    assert circles == [(1300.0, 1080.0, 240.0)] and w == 2600
    assert load_arena_overlay(tmp_path / "missing.json") is None


def test_crop_box_pads_and_clamps():
    from rigconductor.preview import crop_box

    circles = [(100, 100, 50), (300, 200, 50)]
    assert crop_box(circles, 20, 650, 540) == (30, 30, 340, 240)
    assert crop_box(circles, 200, 650, 540) == (0, 0, 550, 450)  # clamped at 0 and edges
    assert crop_box([], 20, 650, 540) is None


def test_reduce_view_crops_and_overlays_genotypes(monkeypatch):
    import os

    import pytest

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QApplication

    from rigconductor.gui import preview as gp

    _app = QApplication.instance() or QApplication([])
    circles = [(100.0, 100.0, 50.0), (300.0, 200.0, 50.0)]
    monkeypatch.setattr(gp, "_detect", lambda img, n: list(circles))
    panel = gp.PreviewPanel(
        lambda: None, None, 2, lambda: {1: ["Oregon R", "nlg4 KO70"]}, padding=20
    )
    img = QImage(650, 540, QImage.Format.Format_Grayscale8)
    img.fill(120)
    panel.view.set_image(img)

    panel.reduce.setChecked(True)
    assert panel.view.crop == (30, 30, 340, 240)
    assert panel.view.genotypes == {1: ["Oregon R", "nlg4 KO70"]}
    panel.padding.setValue(0)
    assert panel.view.crop == (50, 50, 300, 200)
    for chk in (panel.chk_circles, panel.chk_labels, panel.chk_genotypes):
        chk.setChecked(False)
    assert not (panel.view.show_circles or panel.view.show_labels or panel.view.show_genotypes)
    panel.reduce.setChecked(False)
    assert panel.view.crop is None


def test_reduce_view_unticks_when_nothing_found(monkeypatch):
    import os

    import pytest

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QApplication

    from rigconductor.gui import preview as gp

    _app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gp, "_detect", lambda img, n: [])
    panel = gp.PreviewPanel(lambda: None, None, 10)
    img = QImage(650, 540, QImage.Format.Format_Grayscale8)
    img.fill(0)
    panel.view.set_image(img)
    panel.reduce.setChecked(True)
    assert not panel.reduce.isChecked() and panel.view.crop is None
