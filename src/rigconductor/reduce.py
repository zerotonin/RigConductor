# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — reduce                                          ║
# ║  « crop the recording to the arenas »                            ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Frame-exact re-encode of the Motif store to the arena box,      ║
# ║  re-timed to the real camera rate.                               ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Crop a Motif recording to the arenas ('reduce') to save disk space.

Runs after the cut: every chunk ``NNNNNN.mp4`` of the Motif image store is
re-encoded to the bounding box of all arenas plus a padding, frame for frame
(``-fps_mode passthrough``), so the ``NNNNNN.npz`` frame timestamps stay valid.
A chunk is replaced only after its cropped copy decodes to the same number of
frames. With ``gray`` the colour is dropped too (``format=gray``, still stored
as yuv420p: on a mono camera that changes nothing; a gray pix_fmt file came out
38 % larger in the 2026-11-11 test). ``metadata.yaml`` gets the new
``imgshape`` (the original is kept as
``metadata.full.yaml``) and ``reduced.json`` records the crop offset, so
full-frame coordinates map to the reduced video as ``(x - x0, y - y0)``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import yaml

from rigconductor import ConductorError
from rigconductor.constants import (
    FPS_SNAP_TOL,
    PROGRESS_EVERY_S,
    REDUCE_ALIGN_PX,
    REDUCE_CRF,
    REDUCE_PADDING_PX,
)

REDUCED_JSON = "reduced.json"


def reduce_box(
    circles: list[tuple[float, float, float]],
    pad: float,
    width: int,
    height: int,
    align: int = REDUCE_ALIGN_PX,
) -> tuple[int, int, int, int]:
    """(x, y, w, h) covering every circle + ``pad`` px, grown to multiples of
    ``align`` and clamped to the frame."""
    if not circles:
        raise ConductorError("no arenas to crop to")
    x0 = min(cx - r for cx, _, r in circles) - pad
    y0 = min(cy - r for _, cy, r in circles) - pad
    x1 = max(cx + r for cx, _, r in circles) + pad
    y1 = max(cy + r for _, cy, r in circles) + pad
    return _box(_aligned(x0, x1, width, align), _aligned(y0, y1, height, align))


def _aligned(lo: float, hi: float, limit: int, align: int) -> tuple[int, int]:
    """Start (even) and size (multiple of ``align``) along one axis covering
    [lo, hi], kept inside [0, limit]."""
    import math

    start = max(0, math.floor(lo))
    start -= start % 2
    end = min(limit, math.ceil(hi))
    size = min(limit - limit % 2, -(-(end - start) // align) * align)
    if start + size > limit:  # grow towards the other side instead
        start = max(0, limit - size)
        start -= start % 2
    return start, size


def _box(xs: tuple[int, int], ys: tuple[int, int]) -> tuple[int, int, int, int]:
    return xs[0], ys[0], xs[1], ys[1]


def recorded_fps(rec_dir: Path) -> float | None:
    """Real camera rate: Motif frame timestamps, snapped to the set
    ``acquisitionframerate`` when within 1 % (jitter like 69.001 gives time bases
    MPEG-4 cannot store); else the timestamp rate to 2 decimals, else metadata.
    Same rule as ThermoKourt's ``recorded_fps``."""
    import numpy as np

    measured = None
    for npz in sorted(Path(rec_dir).glob("[0-9]" * 6 + ".npz")):
        try:
            with np.load(npz, allow_pickle=True) as z:
                t = np.asarray(z["frame_time"], dtype=float)
            if t.size > 10 and np.median(np.diff(t)) > 0:
                measured = 1.0 / float(np.median(np.diff(t)))
                break
        except Exception:  # noqa: BLE001 - fall back to metadata
            pass
    meta = Path(rec_dir) / "metadata.yaml"
    nominal = None
    if meta.is_file():
        v = (yaml.safe_load(meta.read_text(encoding="utf-8")) or {}).get("acquisitionframerate")
        nominal = float(v) if v else None
    if measured and nominal and abs(measured / nominal - 1) <= FPS_SNAP_TOL:
        return nominal
    return round(measured, 2) if measured else nominal


def _header_frames(video: Path) -> int:
    """Frame count from the container header (instant; 0 if unknown)."""
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_frames",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


def _run_ffmpeg_progress(cmd: list[str], on_frame: Callable[[int], None]):
    """Run ffmpeg with ``-progress pipe:1``; ``on_frame(n)`` gets the frame count
    as it encodes. Returns (returncode, stderr text)."""
    import tempfile

    with tempfile.TemporaryFile(mode="w+") as err:
        proc = subprocess.Popen(
            [*cmd[:-1], "-progress", "pipe:1", "-nostats", cmd[-1]],
            stdout=subprocess.PIPE,
            stderr=err,
            text=True,
        )
        for line in proc.stdout:
            value = line.split("=", 1)[1].strip() if line.startswith("frame=") else ""
            if value.isdigit():
                on_frame(int(value))
        rc = proc.wait()
        err.seek(0)
        return rc, err.read()


def is_reduced(rec_dir: Path) -> dict | None:
    p = Path(rec_dir) / REDUCED_JSON
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _frames(path: Path) -> int:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_packets",
            "-show_entries",
            "stream=nb_read_packets,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise ConductorError(f"ffprobe failed on {path.name}: {r.stderr.strip()[-200:]}")
    return int(json.loads(r.stdout)["streams"][0]["nb_read_packets"])


def reduce_recording(
    rec_dir: Path,
    circles: list[tuple[float, float, float]],
    padding_px: float = REDUCE_PADDING_PX,
    crf: int = REDUCE_CRF,
    keep_full: bool = False,
    gray: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    """Crop every chunk of the Motif store in ``rec_dir``; returns the reduced.json dict."""
    rec_dir = Path(rec_dir)
    done = is_reduced(rec_dir)
    if done:
        log(f"reduce: already cropped to {done['w']}×{done['h']}")
        return done
    meta_p = rec_dir / "metadata.yaml"
    meta = yaml.safe_load(meta_p.read_text(encoding="utf-8"))
    h_full, w_full = (int(v) for v in meta["__store"]["imgshape"][:2])
    x, y, w, h = reduce_box(circles, padding_px, w_full, h_full)
    chunks = sorted(p for p in rec_dir.glob("[0-9]" * 6 + ".mp4"))
    if not chunks:
        raise ConductorError(f"no Motif video chunks in {rec_dir}")
    before = sum(c.stat().st_size for c in chunks)
    log(
        f"reduce: {w_full}×{h_full} → {w}×{h} at ({x}, {y}), padding {padding_px:g} px, "
        f"{len(chunks)} chunk(s), {before / 1e6:.0f} MB"
    )
    fps = recorded_fps(rec_dir)
    rate = ["-r", f"{fps:g}"] if fps else []  # input option: re-time at the real rate
    full_dir = rec_dir / "full_frame"
    after = 0
    frames = [_header_frames(c) for c in chunks]
    total = sum(frames) or 1
    done_before = 0
    last = [0.0]

    def report(n: int, chunk_name: str):
        now = time.monotonic()
        if now - last[0] >= PROGRESS_EVERY_S:
            frac = min(1.0, (done_before + n) / total)
            log(f"@progress reduce {frac:.4f} {chunk_name}: frame {n} of {total}")
            last[0] = now

    for k, c in enumerate(chunks):
        tmp = c.with_name(c.stem + ".reduced.mp4")
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *rate,
            "-i",
            str(c),
            "-vf",
            f"crop={w}:{h}:{x}:{y}" + (",format=gray" if gray else ""),
            "-fps_mode",
            "passthrough",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(tmp),
        ]
        rc, err = _run_ffmpeg_progress(cmd, lambda n, name=c.name: report(n, name))
        if rc != 0 and "fps_mode" in err:  # ffmpeg < 5.1
            i = cmd.index("-fps_mode")
            cmd[i : i + 2] = ["-vsync", "passthrough"]
            rc, err = _run_ffmpeg_progress(cmd, lambda n, name=c.name: report(n, name))
        if rc != 0:
            tmp.unlink(missing_ok=True)
            raise ConductorError(f"reduce: ffmpeg failed on {c.name}: {err.strip()[-300:]}")
        done_before += frames[k]
        log(
            f"@progress reduce {min(1.0, done_before / total):.4f} checking {c.name} frame by frame"
        )
        n_in, n_out = _frames(c), _frames(tmp)
        if n_in != n_out:
            tmp.unlink(missing_ok=True)
            raise ConductorError(
                f"reduce: {c.name} has {n_in} frames but the crop has {n_out}; original kept"
            )
        if keep_full:
            full_dir.mkdir(exist_ok=True)
            shutil.move(str(c), full_dir / c.name)
        tmp.replace(c)
        after += c.stat().st_size
        log(f"reduce: {c.name} ok ({n_out} frames)")
    if not (rec_dir / "metadata.full.yaml").exists():
        shutil.copy2(meta_p, rec_dir / "metadata.full.yaml")
    meta["__store"]["imgshape"] = [h, w, *meta["__store"]["imgshape"][2:]]
    meta["conductor_reduced"] = {"x": x, "y": y, "w": w, "h": h, "padding_px": padding_px}
    meta_p.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    info = {
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "full_width": w_full,
        "full_height": h_full,
        "padding_px": padding_px,
        "gray": bool(gray),
        "fps": fps,
        "crf": crf,
        "bytes_before": before,
        "bytes_after": after,
        "full_frame_kept": bool(keep_full),
    }
    (rec_dir / REDUCED_JSON).write_text(json.dumps(info, indent=2), encoding="utf-8")
    log(
        f"reduce: {before / 1e6:.0f} MB → {after / 1e6:.0f} MB "
        f"({100 * (1 - after / before):.0f} % saved)"
        + (f"; full-frame originals kept in {full_dir}" if keep_full else "")
    )
    return info
