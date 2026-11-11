# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — config                                          ║
# ║  « the rig's settings file »                                     ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Loads ~/.config/rigconductor/rig.yaml (or $RIGCONDUCTOR_RIG);   ║
# ║  defaults come from constants.                                   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Conductor configuration: ``~/.config/rigconductor/rig.yaml`` (or ``$RIGCONDUCTOR_RIG``).

Machine paths, the camera serial and cluster names live here, never in code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from rigconductor.constants import (
    CAMERA_SETTINGS,
    DEFAULT_CLIP_MODE,
    MOTIF_CODEC,
    MOTIF_PORT,
    POLL_INTERVAL_S,
    REDUCE_CRF,
    REDUCE_PADDING_PX,
)

DEFAULT_PATH = Path("~/.config/rigconductor/rig.yaml").expanduser()

EXAMPLE = f"""\
# rigconductor rig configuration
data_root: /mnt/DATA                        # where Motif writes recordings (Record: Directory)
sessions_dir: ~/.local/share/rigconductor/sessions
motif:
  host: 127.0.0.1
  port: {MOTIF_PORT}
  serial: "40366151"                        # camera serial; empty = first camera
  codec: {MOTIF_CODEC}                            # a StoreCodecs name from /etc/recnode.yml
  api_key_cmd: recnode-apikey               # or set MOTIF_API_KEY in the environment
  camera:                                   # set on the camera before every recording
    ExposureTime: {CAMERA_SETTINGS["ExposureTime"]:g}                     # µs
    Gain: {CAMERA_SETTINGS["Gain"]:g}                                   # dB
    AcquisitionFrameRate: {CAMERA_SETTINGS["AcquisitionFrameRate"]:g}                  # fps
cluster:
  ssh_host: aoraki                          # alias in ~/.ssh/config, key-based login
  lab_root: /projects/sciences/zoology/geurten_lab
  poll_interval_s: {POLL_INTERVAL_S}
profiles:
  drosophila_triad:
    thermokourt_rig: ~/.config/thermokourt/rig.yaml
    registry: HeatAggression/models/registry.yaml      # relative to lab_root
    default_duration_s: 1800
  tadpole_24well:
    project: tadpole_project                           # relative to lab_root
    registry: tadpole_project/models/registry.yaml
    results_root: ~/data/tadpole_results
    default_duration_s: 600
export:
  clips: plain          # none | plain | skeleton | events | both
  unmount: false        # true: unmount the USB disk after a verified export
  blind: false
reduce:                 # crop the full recording to the arenas after the cut
  enabled: true
  padding_px: {REDUCE_PADDING_PX}        # camera pixels on each side
  crf: {REDUCE_CRF}               # x264 quality of the cropped video (lower = better, bigger)
  keep_full: false      # true: move the full-frame chunks to full_frame/ instead of deleting
"""


@dataclass
class MotifConfig:
    host: str = "127.0.0.1"
    port: int = MOTIF_PORT
    serial: str | None = None
    codec: str = MOTIF_CODEC
    api_key_cmd: str = "recnode-apikey"
    camera: dict = field(default_factory=lambda: dict(CAMERA_SETTINGS))  # set before recording


@dataclass
class ClusterConfig:
    ssh_host: str = "aoraki"
    lab_root: str = "/projects/sciences/zoology/geurten_lab"
    poll_interval_s: int = POLL_INTERVAL_S


@dataclass
class ExportConfig:
    clips: str = DEFAULT_CLIP_MODE
    unmount: bool = False
    blind: bool = False


@dataclass
class ReduceConfig:
    enabled: bool = True
    padding_px: int = REDUCE_PADDING_PX
    crf: int = REDUCE_CRF
    keep_full: bool = False


@dataclass
class ConductorConfig:
    data_root: Path
    sessions_dir: Path
    motif: MotifConfig = field(default_factory=MotifConfig)
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    profiles: dict[str, dict] = field(default_factory=dict)
    export: ExportConfig = field(default_factory=ExportConfig)
    reduce: ReduceConfig = field(default_factory=ReduceConfig)
    path: Path | None = None

    def profile(self, name: str) -> dict:
        d = dict(self.profiles.get(name) or {})
        for k, v in d.items():
            if isinstance(v, str) and v.startswith("~"):
                d[k] = str(Path(v).expanduser())
        return d

    def registry_remote(self, profile: str) -> str | None:
        r = self.profile(profile).get("registry")
        if not r:
            return None
        return r if r.startswith("/") else f"{self.cluster.lab_root}/{r}"

    def registry_cache(self, profile: str) -> Path:
        return self.sessions_dir.parent / "registry" / f"{profile}.yaml"

    @classmethod
    def load(cls, path: str | Path | None = None) -> ConductorConfig:
        p = Path(path or os.environ.get("RIGCONDUCTOR_RIG") or DEFAULT_PATH).expanduser()
        if not p.is_file():
            raise FileNotFoundError(
                f"rig config not found: {p}\nCreate it with:  rigconductor init-config"
            )
        with open(p, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        m = dict(d.get("motif") or {})
        m["camera"] = {**CAMERA_SETTINGS, **(m.get("camera") or {})}
        motif = MotifConfig(**m)
        if motif.serial is not None:
            motif.serial = str(motif.serial)
        return cls(
            data_root=Path(d.get("data_root", "/mnt/DATA")).expanduser(),
            sessions_dir=Path(
                d.get("sessions_dir", "~/.local/share/rigconductor/sessions")
            ).expanduser(),
            motif=motif,
            cluster=ClusterConfig(**(d.get("cluster") or {})),
            profiles=d.get("profiles") or {},
            export=ExportConfig(**(d.get("export") or {})),
            reduce=ReduceConfig(**(d.get("reduce") or {})),
            path=p,
        )


def write_example(path: str | Path | None = None, overwrite: bool = False) -> Path:
    p = Path(path or DEFAULT_PATH).expanduser()
    if p.exists() and not overwrite:
        raise FileExistsError(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(EXAMPLE, encoding="utf-8")
    return p
