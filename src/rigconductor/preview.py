# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — preview                                         ║
# ║  « Motif's live picture, without Qt »                            ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  MJPEG stream reader, arena overlay loading, crop boxes.         ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Motif's live preview: an MJPEG stream (multipart JPEG over HTTP) whose URL the
recnode publishes in ``camera_info.stream.preview.url``. Qt-free so it can be
tested; the widget lives in ``gui/preview.py``."""

from __future__ import annotations

import json
import math
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

from rigconductor.constants import HTTP_TIMEOUT_S

SOI, EOI = b"\xff\xd8", b"\xff\xd9"


def iter_jpegs(stream, chunk: int = 16384, max_buffer: int = 8 << 20) -> Iterator[bytes]:
    """Yield complete JPEG images from a multipart MJPEG byte stream."""
    buf = b""
    while True:
        data = stream.read(chunk)
        if not data:
            return
        buf += data
        while True:
            a = buf.find(SOI)
            if a < 0:
                buf = buf[-1:]
                break
            b = buf.find(EOI, a + 2)
            if b < 0:
                buf = buf[a:]
                if len(buf) > max_buffer:  # garbage, start over
                    buf = b""
                break
            yield buf[a : b + 2]
            buf = buf[b + 2 :]


def preview_url(status_raw: dict) -> str | None:
    return (((status_raw.get("camera_info") or {}).get("stream") or {}).get("preview") or {}).get(
        "url"
    )


def follow(
    url: str,
    on_frame: Callable[[bytes], None],
    should_stop: Callable[[], bool],
    on_error: Callable[[str], None] = lambda s: None,
    timeout_s: float = HTTP_TIMEOUT_S,
) -> None:
    """Read the stream until ``should_stop()``; reconnect with back-off on errors."""
    delay = 1.0
    while not should_stop():
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as r:
                delay = 1.0
                for jpg in iter_jpegs(r):
                    on_frame(jpg)
                    if should_stop():
                        return
        except Exception as e:  # noqa: BLE001 - network glitches must not kill the GUI
            on_error(f"preview: {e}")
        if should_stop():
            return
        time.sleep(delay)
        delay = min(delay * 2, 10.0)


def load_arena_overlay(path: Path | None) -> tuple[list[tuple[float, float, float]], int] | None:
    """Arena circles (cx, cy, r) in full-frame px and the full frame width, from a
    ThermoKourt arenas JSON; ``None`` when there is no file yet."""
    if not path or not Path(path).is_file():
        return None
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    arenas = d if isinstance(d, list) else d.get("arenas", [])
    circles = [(float(a["cx"]), float(a["cy"]), float(a["r"])) for a in arenas]
    width = int(d.get("frame_width", 0)) if isinstance(d, dict) else 0
    if not width and isinstance(d, dict) and d.get("scale"):
        width = 0  # coordinates are already in preview scale
    return circles, width


def crop_box(
    circles: list[tuple[float, float, float]], pad: float, width: int, height: int
) -> tuple[int, int, int, int] | None:
    """Bounding box (x, y, w, h) of all circles plus ``pad`` px on each side,
    clamped to the image; ``None`` without circles."""
    if not circles:
        return None
    x0 = max(0, int(min(cx - r for cx, _, r in circles) - pad))
    y0 = max(0, int(min(cy - r for _, cy, r in circles) - pad))
    x1 = min(width, int(math.ceil(max(cx + r for cx, _, r in circles) + pad)))
    y1 = min(height, int(math.ceil(max(cy + r for _, cy, r in circles) + pad)))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0
