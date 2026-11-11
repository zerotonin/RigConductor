# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — gui.progress                                    ║
# ║  « a tqdm-style bar for long local steps »                       ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Reads '@progress <stage> <fraction> <detail>' log lines         ║
# ║  (cutter, crop); shows percent, time so far and time left.       ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Progress bar for the stage pages."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

from rigconductor.gui.duration import format_duration

PROGRESS_PREFIX = "@progress "
STAGE_TEXT = {
    "cut": "Cutting into arenas",
    "reduce": "Cropping the recording",
    "render": "Drawing the clips",
    "label": "Training data",
}
BAR_STEPS = 1000


@dataclass
class ProgressLine:
    stage: str
    fraction: float
    detail: str


def parse_progress(line: str) -> ProgressLine | None:
    """``@progress cut 0.4200 3/10 arenas`` → ProgressLine, else None."""
    if not line.startswith(PROGRESS_PREFIX):
        return None
    parts = line[len(PROGRESS_PREFIX) :].split(" ", 2)
    try:
        frac = float(parts[1])
    except (IndexError, ValueError):
        return None
    return ProgressLine(parts[0], min(1.0, max(0.0, frac)), parts[2] if len(parts) > 2 else "")


class Eta:
    """Elapsed time and time left from a growing fraction (tqdm's estimate)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.t0: float | None = None

    def reset(self):
        self.t0 = None

    def update(self, fraction: float) -> tuple[float, float | None]:
        now = self.clock()
        if self.t0 is None:
            self.t0 = now
        elapsed = now - self.t0
        left = elapsed * (1 - fraction) / fraction if fraction > 0.01 and elapsed > 2 else None
        return elapsed, left


class ProgressPanel(QWidget):
    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.bar = QProgressBar()
        self.bar.setRange(0, BAR_STEPS)
        self.bar.setTextVisible(True)
        self.text = QLabel("")
        lay.addWidget(self.bar, 2)
        lay.addWidget(self.text, 3)
        self.stage: str | None = None
        self.eta = Eta()
        self.hide()

    def show_line(self, p: ProgressLine):
        if p.stage != self.stage or p.fraction < self.bar.value() / BAR_STEPS - 0.001:
            self.stage = p.stage
            self.eta.reset()
        elapsed, left = self.eta.update(p.fraction)
        self.bar.setValue(round(p.fraction * BAR_STEPS))
        self.bar.setFormat(f"{p.fraction * 100:.0f} %")
        timing = f"{format_duration(elapsed)} so far"
        if left is not None:
            timing += f", about {format_duration(left)} left"
        name = STAGE_TEXT.get(p.stage, p.stage)
        self.text.setText(f"<b>{name}</b> · {p.detail} · {timing}")
        self.show()

    def finish(self, ok: bool):
        if self.stage is None:
            return
        if ok:
            self.bar.setValue(BAR_STEPS)
            self.bar.setFormat("100 %")
        self.stage = None
