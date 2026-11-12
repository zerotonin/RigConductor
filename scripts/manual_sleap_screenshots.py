# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — manual_sleap_screenshots                        ║
# ║  « SLEAP itself, for the annotation chapter of the manual »      ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Runs with the SLEAP 1.6 python, not the Conductor's: it opens   ║
# ║  the real SLEAP window offscreen on a copy of a labelled file.   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Screenshots of SLEAP for the annotation chapter (docs/manual/img/sleap_*.png).

Copies a labelled ``.slp`` (with embedded frames, e.g. a label set version) to a
temporary folder, makes it look like a fresh package (suggestions on every
frame, the model's guess on the shown frame), opens SLEAP offscreen and draws
numbered markers. The original file is never changed.

Usage::

    <sleap env>/bin/python scripts/manual_sleap_screenshots.py --labels SET/v1.pkg.slp \\
        [--frame N] [--out docs/manual/img]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import sleap_io as sio  # noqa: E402
from qtpy.QtCore import QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from qtpy.QtGui import QColor, QFont, QImage, QPainter, QPen  # noqa: E402
from qtpy.QtWidgets import QApplication, QWidget  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from rigconductor.constants import LABEL_FEMALE_HIDDEN, WONG  # noqa: E402  (stdlib only)

MARK = QColor(*WONG["vermillion"])
BADGE_PX = 24
WINDOW = (1400, 900)
ZOOM_PAD_PX = 70  # around a fly when zooming in
POINT_ZOOM = 3  # upscaling of the points figure
FIG_PALETTE = ("blue", "orange", "bluish_green", "reddish_purple", "sky_blue", "vermillion")


def pump(app: QApplication, seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


# ── choosing and staging the frame ────────────────────────────────


def _visible(inst) -> np.ndarray:
    return ~np.isnan(inst.numpy()).any(axis=1)


def best_frame(labels) -> int:
    """Frame with 3 flies, 2 complete males, and the flies furthest apart."""
    nodes = labels.skeletons[0].node_names
    t = nodes.index("thorax")
    best, score = 0, -1.0
    for k, lf in enumerate(labels.labeled_frames):
        users = lf.user_instances
        if len(users) != 3 or sum(_visible(u).all() for u in users) < 2:
            continue
        xy = np.array([u.numpy()[t] for u in users])
        gap = min(np.linalg.norm(xy[i] - xy[j]) for i in range(3) for j in range(i + 1, 3))
        if gap > score:
            best, score = k, gap
    return best


def _guess(u, sk, rng):
    xy = u.numpy() + rng.normal(0, 2.0, u.numpy().shape)
    return sio.PredictedInstance.from_numpy(
        xy, skeleton=sk, point_scores=np.full(len(sk.nodes), 0.8), score=0.8
    )


def left_sign(users, nodes) -> float:
    """Sign of (heading × eyeL) for this camera: which side is the fly's left."""
    h, t, e = (nodes.index(n) for n in ("head", "thorax", "eyeL"))
    for u in users:
        xy = u.numpy()
        if not np.isnan(xy[[h, t, e]]).any():
            d, v = xy[h] - xy[t], xy[e] - xy[h]
            return float(np.sign(d[0] * v[1] - d[1] * v[0]) or 1)
    return 1.0


def hidden_head(u, nodes, sign: float) -> np.ndarray:
    """Where the female's missing head and eyes would be (for showing them hidden)."""
    xy = u.numpy().copy()
    t, a = xy[nodes.index("thorax")], xy[nodes.index("abdomen")]
    d = t - a
    n = np.linalg.norm(d) or 1.0
    head = t + d * 0.5
    perp = np.array([-d[1], d[0]]) / n * 0.25 * n * sign
    xy[nodes.index("head")] = head
    xy[nodes.index("eyeL")] = head + perp + d * 0.1
    xy[nodes.index("eyeR")] = head - perp + d * 0.1
    return xy


def stage(src: Path, work: Path, k: int, corrected: bool) -> tuple[Path, list]:
    """A package-like copy: suggestions on every frame, every fly a model guess.

    With ``corrected`` frame ``k`` holds labelled flies instead, the female's head
    points placed and marked not visible. Returns the file and frame ``k``'s flies.
    """
    labels = sio.load_slp(str(src))
    sk = labels.skeletons[0]
    nodes = sk.node_names
    labels.suggestions = [
        sio.SuggestionFrame(video=lf.video, frame_idx=lf.frame_idx) for lf in labels.labeled_frames
    ]
    rng = np.random.default_rng(1)
    users = list(labels.labeled_frames[k].user_instances)
    for lf in labels.labeled_frames:
        lf.instances = [_guess(u, sk, rng) for u in lf.user_instances]
    if corrected:
        fem = female_index(users, nodes)
        sign = left_sign(users, nodes)
        flies = []
        for i, u in enumerate(users):
            if i == fem:
                inst = sio.Instance.from_numpy(hidden_head(u, nodes, sign), skeleton=sk)
                for n in LABEL_FEMALE_HIDDEN:
                    inst.points["visible"][nodes.index(n)] = False
            else:
                inst = sio.Instance.from_numpy(u.numpy(), skeleton=sk)
            flies.append(inst)
        labels.labeled_frames[k].instances = flies
    out = work / ("labelled" if corrected else "fresh") / "labels.pkg.slp"
    out.parent.mkdir()
    labels.save(str(out), embed=True)
    return out, users


# ── markers ───────────────────────────────────────────────────────


def rect_of(win: QWidget, target) -> QRect:
    """Window rectangle of a widget, or ``(widget, QRect)`` in that widget's pixels."""
    if isinstance(target, tuple):
        w, r = target
        return QRect(w.mapTo(win, r.topLeft()), r.size())
    return QRect(target.mapTo(win, QPoint(0, 0)), target.size())


def shoot(win: QWidget, name: str, marks: list, out: Path, legend: dict) -> None:
    img = win.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(13)
    p.setFont(font)
    for n, (_label, target) in enumerate(marks, start=1):
        r = rect_of(win, target).adjusted(-3, -3, 3, 3)
        p.setPen(QPen(MARK, 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 4, 4)
        x = min(max(r.left() - BADGE_PX // 2, 0), img.width() - BADGE_PX)
        y = r.top() - BADGE_PX // 2
        if y < 0:  # top edge (menus): badge under the box, not over its text
            x, y = min(r.right() - 4, img.width() - BADGE_PX), r.bottom() - 4
        y = min(y, img.height() - BADGE_PX)
        badge = QRect(x, y, BADGE_PX, BADGE_PX)
        p.setPen(QPen(QColor("white"), 1.5))
        p.setBrush(MARK)
        p.drawEllipse(badge)
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(n))
    p.end()
    img.save(str(out / f"{name}.png"))
    legend[name] = [label for label, _ in marks]
    print(f"{name}.png  {len(marks)} markers", flush=True)


def menu_rect(win, title: str) -> tuple:
    bar = win.menuBar()
    act = next(a for a in bar.actions() if a.text().replace("&", "") == title)
    return (bar, bar.actionGeometry(act))


def view_rect(win, xy: np.ndarray, pad: float = 12) -> tuple:
    """Rectangle around image points, in the frame view's pixels."""
    view = win.player.view
    pts = xy[~np.isnan(xy).any(axis=1)]
    a = view.mapFromScene(QPointF(*(pts.min(axis=0) - pad)))
    b = view.mapFromScene(QPointF(*(pts.max(axis=0) + pad)))
    return (view.viewport(), QRect(a, b).normalized())


def zoom_on(win, xy: np.ndarray) -> None:
    pts = xy[~np.isnan(xy).any(axis=1)]
    lo, hi = pts.min(axis=0) - ZOOM_PAD_PX, pts.max(axis=0) + ZOOM_PAD_PX
    win.player.view.zoomToRect(QRectF(QPointF(*lo), QPointF(*hi)))


# ── SLEAP windows ─────────────────────────────────────────────────


def open_sleap(app, path: Path, k: int):
    import sleap.gui.app as sleap_app

    win = sleap_app.MainWindow(labels_path=str(path), no_usage_data=True)
    win.resize(*WINDOW)
    win.show()
    pump(app, 1.0)
    lf = win.labels.labeled_frames[k]
    win.commands.gotoVideoAndFrame(lf.video, lf.frame_idx)
    win.suggestions_dock.wgt_layout.parent().parent().raise_()
    pump(app, 1.0)
    return win, lf


def female_index(users, nodes) -> int:
    head = nodes.index("head")
    return next((i for i, u in enumerate(users) if np.isnan(u.numpy()[head]).any()), 2)


def shots(app, fresh: Path, labelled: Path, k: int, users, out: Path, legend: dict) -> None:
    win, lf = open_sleap(app, fresh, k)
    nodes = win.labels.skeletons[0].node_names
    fem = female_index(users, nodes)
    male = next(i for i in range(3) if i != fem)
    shoot(
        win,
        "sleap_1_window",
        [
            ("Go menu: next and previous suggestion", menu_rect(win, "Go")),
            ("Labels menu: add and delete flies", menu_rect(win, "Labels")),
            ("a fly guessed by the model", view_rect(win, users[male].numpy())),
            ("seek bar: suggested frames are marked", win.player.seekbar),
            ("Labeling Suggestions tab: frames to label", win.suggestions_dock.table),
            ("Previous / Next suggestion and progress", win.suggestions_dock.suggested_count_label),
            ("status bar: frame and instance counts", win.statusBar()),
        ],
        out,
        legend,
    )
    win.hide()

    win, lf = open_sleap(app, labelled, k)
    sign = left_sign(users, nodes)
    zoom_on(win, users[male].numpy())
    pump(app, 0.8)
    xy = users[male].numpy()
    pick = [nodes.index(n) for n in ("thorax", "head", "wingL", "forelegL4")]
    shoot(
        win.player,  # only the frame: the fly stays large on the page
        "sleap_2_male",
        [(f"{nodes[i]}", view_rect(win, xy[[i]], 7)) for i in pick],
        out,
        legend,
    )
    fxy = hidden_head(users[fem], nodes, sign)
    zoom_on(win, fxy)
    pump(app, 0.8)
    hid = [nodes.index(n) for n in LABEL_FEMALE_HIDDEN]
    shoot(
        win.player,
        "sleap_3_female",
        [
            ("head, eyeL, eyeR: placed and not visible", view_rect(win, fxy[hid], 14)),
            ("thorax", view_rect(win, fxy[[nodes.index("thorax")]], 7)),
        ],
        out,
        legend,
    )
    win.hide()


# ── the points figure (drawn, not SLEAP) ──────────────────────────


def points_figure(labels, k: int, out: Path) -> None:
    """A male, zoomed, every point named in the fly's own colours."""
    lf = labels.labeled_frames[k]
    sk = labels.skeletons[0]
    nodes = sk.node_names
    users = lf.user_instances
    fem = female_index(users, nodes)
    male = next(i for i in range(3) if i != fem and _visible(users[i]).all())
    xy = users[male].numpy()
    img = np.asarray(lf.image)
    if img.ndim == 3 and img.shape[2] == 1:
        img = img[:, :, 0]
    lo = np.maximum(np.floor(xy.min(axis=0) - 40).astype(int), 0)
    hi = np.minimum(np.ceil(xy.max(axis=0) + 40).astype(int), img.shape[1::-1])
    crop = np.ascontiguousarray(img[lo[1] : hi[1], lo[0] : hi[0]])
    h, w = crop.shape[:2]
    fmt = QImage.Format.Format_Grayscale8 if crop.ndim == 2 else QImage.Format.Format_RGB888
    q = QImage(crop.data, w, h, crop.strides[0], fmt).copy()
    q = q.scaled(w * POINT_ZOOM, h * POINT_ZOOM, Qt.AspectRatioMode.KeepAspectRatio)
    q = q.convertToFormat(QImage.Format.Format_RGB32)
    pts = (xy - lo) * POINT_ZOOM
    p = QPainter(q)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(15)
    p.setFont(font)
    p.setPen(QPen(QColor(*WONG["yellow"]), 2))
    for e in sk.edges:
        a, b = pts[nodes.index(e.source.name)], pts[nodes.index(e.destination.name)]
        p.drawLine(QPointF(*a), QPointF(*b))
    centre = pts.mean(axis=0)
    for i, name in enumerate(nodes):
        c = QColor(*WONG[FIG_PALETTE[i % len(FIG_PALETTE)]])
        x, y = pts[i]
        p.setPen(QPen(QColor("white"), 1.5))
        p.setBrush(c)
        p.drawEllipse(QPointF(x, y), 6, 6)
        d = np.array([x, y]) - centre
        d = d / (np.linalg.norm(d) or 1) * 18
        tx, ty = x + d[0] - (len(name) * 8 if d[0] < 0 else 0), y + d[1] + 5
        p.setPen(QPen(QColor("black"), 4))
        p.drawText(QPointF(tx, ty), name)  # dark rim for contrast
        p.setPen(QPen(QColor("white"), 1))
        p.drawText(QPointF(tx, ty), name)
    p.end()
    q.save(str(out / "sleap_points.png"))
    print("sleap_points.png", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--labels", required=True, help="labelled .slp with embedded frames")
    ap.add_argument("--frame", type=int, default=None, help="labelled frame to show")
    ap.add_argument("--out", default=str(ROOT / "docs/manual/img"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    labels = sio.load_slp(a.labels)
    k = best_frame(labels) if a.frame is None else a.frame
    print(f"frame {k} of {len(labels)}", flush=True)
    points_figure(labels, k, out)
    legend_file = out / "markers.json"
    legend = json.loads(legend_file.read_text(encoding="utf-8")) if legend_file.is_file() else {}
    with tempfile.TemporaryDirectory() as tmp:
        fresh, users = stage(Path(a.labels), Path(tmp), k, corrected=False)
        labelled, _ = stage(Path(a.labels), Path(tmp), k, corrected=True)
        shots(app, fresh, labelled, k, users, out, legend)
    legend_file.write_text(json.dumps(legend, indent=1), encoding="utf-8")
    os._exit(0)  # SLEAP's threads and preference writer stay out of the way


if __name__ == "__main__":
    sys.exit(main())
