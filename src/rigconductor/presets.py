# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — presets                                         ║
# ║  « one choice that fixes the video pipeline »                    ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Experiment presets: colour, frame rate, crop, default model.    ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Experiment presets: one choice on the first page that fixes how the video is
recorded and processed (colour, frame rate, crop to arenas, default pose model).

Built-in presets live here per profile; ``rig.yaml`` can override or add some::

    profiles:
      drosophila_triad:
        presets:
          heat: {gray: true, reduce: true, padding_px: 60, model: lab_oct2025_v2}
          courtship25: {label: "Courtship, fixed 25 fps", fps: 25}
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from rigconductor.config import ConductorConfig
from rigconductor.constants import REDUCE_PADDING_PX


@dataclass
class Preset:
    key: str
    label: str
    gray: bool = True  # drop colour everywhere it is re-encoded
    fps: float | None = None  # required camera rate; None = whatever the camera runs at
    reduce: bool = True  # crop the recording to the arenas after the cut
    padding_px: int = REDUCE_PADDING_PX
    model: str | None = None  # default pose model id
    notes: str = ""
    extra: dict = field(default_factory=dict)


BUILTIN: dict[str, dict[str, dict]] = {
    "drosophila_triad": {
        "heat": {
            "label": "Heat aggression: grayscale, camera frame rate, crop to arenas",
            "gray": True,
            "fps": None,
            "reduce": True,
            "padding_px": REDUCE_PADDING_PX,
            "model": "lab_oct2025_v2",
            "notes": "Any frame rate: clips are labelled with the real camera rate and "
            "ThermoKourt scores in seconds from it.",
        },
    },
    "tadpole_24well": {
        "plate": {
            "label": "Tadpole plate: grayscale, full frame",
            "gray": True,
            "fps": None,
            "reduce": False,
        },
    },
}


def presets_for(profile: str, cfg: ConductorConfig | None = None) -> list[Preset]:
    merged = {k: dict(v) for k, v in BUILTIN.get(profile, {}).items()}
    if cfg is not None:
        for k, v in (cfg.profile(profile).get("presets") or {}).items():
            merged.setdefault(k, {}).update(v or {})
    names = {f.name for f in fields(Preset)}
    out = []
    for k, d in merged.items():
        known = {n: v for n, v in d.items() if n in names and n not in ("key", "extra")}
        known.setdefault("label", k)
        out.append(Preset(key=k, extra={n: v for n, v in d.items() if n not in names}, **known))
    if not out:
        out.append(Preset(key="default", label="Default"))
    return out


def by_key(profile: str, key: str | None, cfg: ConductorConfig | None = None) -> Preset:
    ps = presets_for(profile, cfg)
    return next((p for p in ps if p.key == key), ps[0])
