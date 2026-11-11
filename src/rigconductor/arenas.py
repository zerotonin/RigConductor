# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — arenas                                          ║
# ║  « arena layouts: load, number, save, show »                     ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Qt-free helpers behind the arena editor. Layouts are ThermoKourt ║
# ║  arenas JSON files in full camera coordinates.                   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Arena layout helpers (ThermoKourt arenas JSON, full camera coordinates)."""

from __future__ import annotations

import json
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rigconductor import ConductorError
from rigconductor.constants import ARENA_RADIUS_TOL, TK_CROP_PADDING

Circle = tuple[float, float, float]  # cx, cy, r


@dataclass
class Layout:
    """Arena circles plus the file metadata ThermoKourt expects.

    Attributes:
        circles: (cx, cy, r) in full camera pixels, row-major order.
        frame_width: Full camera frame width.
        frame_height: Full camera frame height.
        meta: Other keys of the JSON file (recording, padding, …), kept on save.
    """

    circles: list[Circle]
    frame_width: int
    frame_height: int
    meta: dict = field(default_factory=dict)


def sort_row_major(circles: list[Circle]) -> list[Circle]:
    """Order top→bottom, left→right exactly like ThermoKourt numbers its arenas."""
    if not circles:
        return []
    by_y = sorted(circles, key=lambda c: c[1])
    med_r = statistics.median(c[2] for c in by_y)
    rows, cur = [], [by_y[0]]
    for c in by_y[1:]:
        if abs(c[1] - cur[-1][1]) > med_r:
            rows.append(cur)
            cur = [c]
        else:
            cur.append(c)
    rows.append(cur)
    return [c for row in rows for c in sorted(row, key=lambda c: c[0])]


def uniform_radii(circles: list[Circle]) -> list[Circle]:
    """Give every arena the median radius (the arenas on one plate are identical)."""
    if not circles:
        return []
    r = statistics.median(c[2] for c in circles)
    return [(cx, cy, r) for cx, cy, _ in circles]


def radius_outliers(circles: list[Circle], tol: float = ARENA_RADIUS_TOL) -> list[int]:
    """Indices of arenas whose radius differs from the median by more than ``tol``."""
    if len(circles) < 3:
        return []
    med = statistics.median(c[2] for c in circles)
    return [i for i, c in enumerate(circles) if abs(c[2] / med - 1) > tol]


def scale_radii(circles: list[Circle], factor: float) -> list[Circle]:
    return [(cx, cy, r * factor) for cx, cy, r in circles]


def load_layout(path: Path) -> Layout:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    arenas = d if isinstance(d, list) else d.get("arenas", [])
    meta = {} if isinstance(d, list) else {k: v for k, v in d.items() if k != "arenas"}
    circles = [(float(a["cx"]), float(a["cy"]), float(a["r"])) for a in arenas]
    return Layout(
        circles=circles,
        frame_width=int(meta.pop("frame_width", 0)),
        frame_height=int(meta.pop("frame_height", 0)),
        meta=meta,
    )


def save_layout(path: Path, layout: Layout) -> Path:
    """Write ``layout`` as a ThermoKourt arenas JSON (circles renumbered row-major)."""
    circles = sort_row_major(layout.circles)
    meta = {"padding": TK_CROP_PADDING, **layout.meta}
    for stale in ("video_offset", "video_crop", "clip_box"):  # full-frame coordinates;
        meta.pop(stale, None)  # the next cut writes the clip boxes again
    d = {
        **meta,
        "frame_width": layout.frame_width,
        "frame_height": layout.frame_height,
        "arenas": [
            {"cx": round(cx, 2), "cy": round(cy, 2), "r": round(r, 2), "idx": i}
            for i, (cx, cy, r) in enumerate(circles)
        ],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return path


def video_offset(rec_dir: Path) -> tuple[int, int]:
    """Where the recording's video sits in the full camera frame ((0, 0) unless
    Rig Conductor cropped it)."""
    red = Path(rec_dir) / "reduced.json"
    if not red.is_file():
        return 0, 0
    d = json.loads(red.read_text(encoding="utf-8"))
    return int(d["x"]), int(d["y"])


def grab_frame(rec_dir: Path, out_png: Path, at_fraction: float = 0.5) -> Path:
    """Save one frame of the recording's first chunk as PNG (ffmpeg, no OpenCV)."""
    chunks = sorted(Path(rec_dir).glob("[0-9]" * 6 + ".mp4"))
    if not chunks:
        raise ConductorError(f"no Motif video in {rec_dir}")
    dur = _duration_s(chunks[0])
    r = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{dur * at_fraction:.2f}",
            "-i",
            str(chunks[0]),
            "-frames:v",
            "1",
            str(out_png),
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not Path(out_png).is_file():
        raise ConductorError(f"could not read a frame: {r.stderr.strip()[-200:]}")
    return Path(out_png)


def _duration_s(video: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0
