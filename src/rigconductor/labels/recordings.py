# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — labels.recordings                               ║
# ║  « which recordings can give new training frames »               ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Find cut (and ideally analysed) recordings under the data root.

A recording qualifies when it has arena clips; pose files make the frame
choice smarter (unsure and contact frames) but are not required.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rigconductor.labels.store import scale_name
from rigconductor.reduce import recorded_fps

ARENA_RE = re.compile(r"_arena_(\d+)\.mp4$")
DEFAULT_FPS = 25.0


@dataclass
class Recording:
    name: str
    path: Path
    clips: dict[int, Path] = field(default_factory=dict)
    poses: dict[int, Path] = field(default_factory=dict)
    diameter_px: float | None = None
    fps: float = DEFAULT_FPS

    @property
    def scale(self) -> str:
        return scale_name(self.diameter_px) if self.diameter_px else "?"

    @property
    def date(self) -> str:
        m = re.search(r"(\d{8})_\d{6}", self.name)
        return f"{m[1][:4]}-{m[1][4:6]}-{m[1][6:]}" if m else ""

    def clip_entries(self) -> list[dict]:
        """Arguments for the worker's ``select`` task, one per arena clip."""
        return [
            {
                "recording": self.name,
                "arena": a,
                "clip": str(c),
                "pose": str(self.poses[a]) if a in self.poses else None,
                "fps": self.fps,
                "scale": self.scale,
            }
            for a, c in sorted(self.clips.items())
        ]


def _diameter(clips_dir: Path, name: str) -> float | None:
    p = clips_dir / f"{name}_arenas.json"
    if not p.is_file():
        return None
    try:
        arenas = json.loads(p.read_text(encoding="utf-8")).get("arenas", [])
        radii = sorted(float(a.get("r", a.get("radius", 0))) for a in arenas)
    except (ValueError, AttributeError):
        return None
    return 2 * radii[len(radii) // 2] if radii else None


def read_recording(rec_dir: Path) -> Recording | None:
    clips_dir = rec_dir / "clips"
    if not clips_dir.is_dir():
        return None
    name = rec_dir.name
    rec = Recording(name, rec_dir)
    for c in clips_dir.glob(f"{name}_arena_*.mp4"):
        m = ARENA_RE.search(c.name)
        if m:
            rec.clips[int(m[1])] = c
    if not rec.clips:
        return None
    for a in rec.clips:
        h5 = rec_dir / "pose" / f"{name}_arena_{a:02d}.h5"
        if h5.is_file():
            rec.poses[a] = h5
    rec.diameter_px = _diameter(clips_dir, name)
    rec.fps = recorded_fps(rec_dir) or DEFAULT_FPS
    return rec


def find_recordings(data_root: Path) -> list[Recording]:
    """Recordings with clips under ``data_root`` (two levels: camera / recording), newest first."""
    out = []
    for d in sorted(Path(data_root).glob("*/*/clips")):
        r = read_recording(d.parent)
        if r:
            out.append(r)
    return sorted(out, key=lambda r: r.name.rsplit("_", 2)[-2:], reverse=True)
