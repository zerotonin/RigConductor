# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.preview                                     ║
# ║  « see the camera before recording »                             ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Live preview, toggleable overlays and Reduce view. OpenCV       ║
# ║  runs in a child process (it clashes with Qt in this one).       ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Live camera preview with arena overlay and a 'find arenas in this image' check."""

from __future__ import annotations

import threading

import numpy as np
from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rigconductor import preview as pv
from rigconductor.constants import (
    ARENA_DETECT_TIMEOUT_S,
    BLACK_FRAME_MEAN,
    COLOUR_ARENAS_OK,
    COLOUR_ARENAS_PARTIAL,
    COLOUR_RIG_LAYOUT,
    LABEL_BOX_RGBA,
    N_ARENAS,
    OVERLAY_FONT_PX,
    OVERLAY_PEN_PX,
    REDUCE_PADDING_MAX_PX,
    REDUCE_PADDING_PX,
)


class PreviewThread(QThread):
    frame = pyqtSignal(bytes)
    problem = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self._stop = threading.Event()

    def run(self):
        pv.follow(self.url, self.frame.emit, self._stop.is_set, self.problem.emit)

    def stop(self):
        self._stop.set()
        self.wait(3000)


class PreviewView(QLabel):
    """Shows the stream, optionally cropped, with toggleable overlays drawn in
    screen pixels so lines and text stay readable at any zoom."""

    PEN_W = OVERLAY_PEN_PX
    FONT_PX = OVERLAY_FONT_PX

    def __init__(self):
        super().__init__("no preview yet")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background: #111; color: #aaa;")
        self.image: QImage | None = None
        self.overlay: list[tuple[float, float, float]] = []  # image-px circles
        self.overlay_colour = QColor(*COLOUR_RIG_LAYOUT)
        self.crop: tuple[int, int, int, int] | None = None  # x, y, w, h in image px
        self.show_circles = True
        self.show_labels = True
        self.show_genotypes = True
        self.genotypes: dict[int, list[str]] = {}

    def set_image(self, img: QImage):
        self.image = img
        self._render()

    def resizeEvent(self, e):  # noqa: N802
        self._render()
        super().resizeEvent(e)

    def _render(self):
        if self.image is None or self.image.isNull():
            return
        x0, y0 = 0, 0
        img = self.image
        if self.crop:
            x0, y0, w, h = self.crop
            img = img.copy(QRect(x0, y0, w, h))
        pm = QPixmap.fromImage(img).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        s = pm.width() / img.width() if img.width() else 1.0
        if self.overlay and (self.show_circles or self.show_labels or self.show_genotypes):
            self._paint_overlay(pm, x0, y0, s)
        self.setPixmap(pm)

    def _paint_overlay(self, pm: QPixmap, x0: float, y0: float, s: float):
        from rigconductor.gui.vialboard import genotype_colour

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(p.font())
        font.setPixelSize(self.FONT_PX)
        font.setBold(True)
        p.setFont(font)
        fm = QFontMetrics(font)
        pad = 4
        for i, (cx, cy, r) in enumerate(self.overlay):
            x, y, rr = (cx - x0) * s, (cy - y0) * s, r * s
            if self.show_circles:
                pen = QPen(self.overlay_colour)
                pen.setWidth(self.PEN_W)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(x, y), rr, rr)
            if self.show_labels:
                _text_box(p, fm, f"A{i:02d}", QPointF(x, y), self.overlay_colour, centre=True)
            gts = self.genotypes.get(i) if self.show_genotypes else None
            if gts:  # inside the arena, under its number: never covers a neighbour
                gy = y + fm.height() * (0.9 if self.show_labels else -len(gts) / 2 + 0.5)
                for k, g in enumerate(gts):
                    text = fm.elidedText(g, Qt.TextElideMode.ElideRight, int(1.7 * rr) - 2 * pad)
                    _text_box(
                        p,
                        fm,
                        text,
                        QPointF(x, gy + k * fm.height()),
                        genotype_colour(g),
                        centre=True,
                    )
        p.end()


def _text_box(
    p: QPainter, fm: QFontMetrics, text: str, at: QPointF, colour: QColor, centre: bool = False
):
    """Coloured text on a dark rounded box; ``at`` is the top-left (or the centre)."""
    pad = 4
    w, h = fm.horizontalAdvance(text) + 2 * pad, fm.height()
    x, y = (at.x() - w / 2, at.y() - h / 2) if centre else (at.x(), at.y())
    rect = QRectF(x, y, w, h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(*LABEL_BOX_RGBA))
    p.drawRoundedRect(rect, 4, 4)
    p.setPen(colour)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


class PreviewPanel(QWidget):
    """Start/stop the stream, show it, overlay the rig's arenas, test detection,
    and zoom the view to the arenas ('Reduce view'). Nothing here touches the camera."""

    def __init__(
        self,
        url_provider,
        arenas_json=None,
        n_arenas: int = N_ARENAS,
        genotype_provider=None,
        padding: int = REDUCE_PADDING_PX,
        on_padding=None,
    ):
        super().__init__()
        self.on_padding = on_padding  # (int) -> None, camera px
        self.url_provider = url_provider  # () -> str | None
        self.arenas_json = arenas_json
        self.n_arenas = n_arenas
        self.genotype_provider = genotype_provider  # () -> {arena_idx: [genotype, ...]}
        self.thread: PreviewThread | None = None
        self.n_frames = 0
        self._crop_circles: list[tuple[float, float, float]] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = PreviewView()
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        self.toggle = QPushButton("Start preview")
        self.toggle.clicked.connect(self._toggle)
        row.addWidget(self.toggle)
        self.find = QPushButton("Find arenas in this image")
        self.find.clicked.connect(self.find_arenas)
        row.addWidget(self.find)
        self.reduce = QPushButton("Reduce view")
        self.reduce.setCheckable(True)
        self.reduce.setToolTip(
            "Find the arenas, then show only their bounding box plus the padding. "
            "Display only: the camera keeps recording the full image."
        )
        self.reduce.toggled.connect(self.reduce_view)
        row.addWidget(self.reduce)
        row.addWidget(QLabel("padding"))
        self.padding = QSpinBox()
        self.padding.setRange(0, REDUCE_PADDING_MAX_PX)
        self.padding.setValue(padding)
        self.padding.setSuffix(" px")
        self.padding.setToolTip(
            "Extra margin on each side, in camera pixels. The same padding is used when the "
            "full recording is cropped to the arenas after the cut."
        )
        self.padding.valueChanged.connect(self._update_crop)
        self.padding.valueChanged.connect(lambda v: self.on_padding and self.on_padding(v))
        row.addWidget(self.padding)
        row.addStretch(1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Overlays:"))
        self.chk_circles = QCheckBox("arenas")
        self.chk_labels = QCheckBox("numbers")
        self.chk_genotypes = QCheckBox("genotypes")
        for chk, attr in (
            (self.chk_circles, "show_circles"),
            (self.chk_labels, "show_labels"),
            (self.chk_genotypes, "show_genotypes"),
        ):
            chk.setChecked(True)
            chk.toggled.connect(lambda on, a=attr: self._set_overlay(a, on))
            row2.addWidget(chk)
        self.info = QLabel("")
        row2.addWidget(self.info, 1)
        lay.addLayout(row2)
        self._load_overlay()

    def _set_overlay(self, attr: str, on: bool):
        setattr(self.view, attr, on)
        self.view._render()

    def _load_overlay(self):
        ov = pv.load_arena_overlay(self.arenas_json)
        self._rig_overlay = ov  # (circles in full px, full width) or None

    def _scaled_rig_overlay(self, img_w: int):
        if not self._rig_overlay:
            return []
        circles, full_w = self._rig_overlay
        s = img_w / full_w if full_w else 1.0
        return [(cx * s, cy * s, r * s) for cx, cy, r in circles]

    def _refresh_genotypes(self):
        if self.genotype_provider is None:
            return
        try:
            self.view.genotypes = self.genotype_provider() or {}
        except Exception:  # noqa: BLE001 - an overlay must never break the preview
            self.view.genotypes = {}

    def showEvent(self, e):  # noqa: N802
        self._refresh_genotypes()  # vials may have changed on the animals page
        super().showEvent(e)

    def _toggle(self):
        if self.thread and self.thread.isRunning():
            self.stop()
            return
        url = None
        try:
            url = self.url_provider()
        except Exception as e:  # noqa: BLE001
            self.info.setText(f"preview unavailable: {e}")
            return
        if not url:
            self.info.setText("Motif publishes no preview stream for this camera")
            return
        self._refresh_genotypes()
        self.thread = PreviewThread(url)
        self.thread.frame.connect(self._on_jpeg)
        self.thread.problem.connect(self.info.setText)
        self.thread.start()
        self.toggle.setText("Stop preview")
        self.info.setText("connecting…")

    def stop(self):
        if self.thread:
            self.thread.stop()
            self.thread = None
        self.toggle.setText("Start preview")

    def _on_jpeg(self, data: bytes):
        img = QImage.fromData(data, "JPG")
        if img.isNull():
            return
        self.n_frames += 1
        if not self.view.overlay and self._rig_overlay:
            self.view.overlay = self._scaled_rig_overlay(img.width())
            self.view.overlay_colour = QColor(*COLOUR_RIG_LAYOUT)
        self.view.set_image(img)
        mean = _mean_gray(img)
        warn = "  ⚠ image is black — backlight on? lens cap off?" if mean < BLACK_FRAME_MEAN else ""
        zoom = "  · reduced view" if self.view.crop else ""
        self.info.setText(
            f"live · {img.width()}×{img.height()} · frame {self.n_frames}{zoom}{warn}"
        )

    # ── reduce view ──────────────────────────────────────────────────────
    def reduce_view(self, on: bool = True) -> bool:
        """Crop the display to all arenas + padding (after a fresh detection)."""
        if not on:
            self.view.crop = None
            self._crop_circles = []
            self.view._render()
            return False
        if self.find_arenas() == 0:
            self.reduce.blockSignals(True)
            self.reduce.setChecked(False)
            self.reduce.blockSignals(False)
            return False
        self._crop_circles = list(self.view.overlay)
        self._update_crop()
        return True

    def _preview_scale(self) -> float:
        """Preview px per camera px (Motif may send a downscaled preview)."""
        if self._rig_overlay and self._rig_overlay[1] and self.view.image is not None:
            return self.view.image.width() / self._rig_overlay[1]
        return 1.0

    def set_padding(self, v: int):
        self.padding.blockSignals(True)
        self.padding.setValue(v)
        self.padding.blockSignals(False)
        self._update_crop()

    def _update_crop(self, *_):
        if not self._crop_circles or self.view.image is None:
            return
        img = self.view.image
        self.view.crop = pv.crop_box(
            self._crop_circles,
            self.padding.value() * self._preview_scale(),
            img.width(),
            img.height(),
        )
        self.view._render()

    # ── arena detection ─────────────────────────────────────────────────
    def find_arenas(self) -> int:
        """Run ThermoKourt's arena detector on the current frame; returns arenas found."""
        img = self.view.image
        if img is None:
            self.info.setText("start the preview first")
            return 0
        try:
            circles = _detect(img, self.n_arenas)
        except RuntimeError as e:
            self.info.setText(f"arena detection failed: {e}")
            return 0
        self.view.overlay = circles
        ok = len(circles) == self.n_arenas
        self.view.overlay_colour = (
            QColor(*COLOUR_ARENAS_OK) if ok else QColor(*COLOUR_ARENAS_PARTIAL)
        )
        self._refresh_genotypes()
        if self._crop_circles:  # keep a reduced view in step with the new detection
            self._crop_circles = list(circles)
            self._update_crop()
        self.view._render()
        self.info.setText(
            f"{len(circles)}/{self.n_arenas} arenas found"
            + ("" if ok else " — check zoom, lighting and plate position")
        )
        return len(circles)


def _detect(img: QImage, n: int) -> list[tuple[float, float, float]]:
    # OpenCV's bundled GLib clashes with Qt's inside one process, so the detector
    # runs in a child process on a temporary PNG. Arenas are numbered row-major,
    # exactly as the cut clips are.
    import json
    import subprocess
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        png = f"{td}/frame.png"
        img.convertToFormat(QImage.Format.Format_Grayscale8).save(png)
        code = (
            "import json,sys,cv2;"
            "from thermokourt.extract.arena_detect import detect_arenas;"
            "f=cv2.imread(sys.argv[1],0);"
            "from thermokourt.extract.arena_extractor import Arena, sort_arenas_row_major;"
            "cs=detect_arenas(f,n_expected=int(sys.argv[2]));"
            "a=sort_arenas_row_major([Arena(cx=c.cx,cy=c.cy,r=c.r) for c in cs]);"
            "print(json.dumps([[c.cx,c.cy,c.r] for c in a]))"
        )
        r = subprocess.run(
            [sys.executable, "-c", code, png, str(n)],
            capture_output=True,
            text=True,
            timeout=ARENA_DETECT_TIMEOUT_S,
        )
    if r.returncode != 0:
        raise RuntimeError(
            "ThermoKourt not installed" if "No module named" in r.stderr else r.stderr[-200:]
        )
    return [(float(a), float(b), float(c)) for a, b, c in json.loads(r.stdout)]


def _to_gray_array(img: QImage) -> np.ndarray:
    g = img.convertToFormat(QImage.Format.Format_Grayscale8)
    w, h, bpl = g.width(), g.height(), g.bytesPerLine()
    buf = np.frombuffer(g.constBits().asstring(bpl * h), np.uint8).reshape(h, bpl)
    return buf[:, :w].copy()


def _mean_gray(img: QImage) -> float:
    small = img.scaled(64, 64)
    return float(_to_gray_array(small).mean())
