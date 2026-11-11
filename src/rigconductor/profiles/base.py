# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — profiles.base                                   ║
# ║  « the contract every organism fulfils »                         ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Profile interface and the context a session hands to it.        ║
# ╚══════════════════════════════════════════════════════════════════╝
"""The Profile interface and the context a session hands to it."""

from __future__ import annotations

import shlex
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rigconductor import ConductorError
from rigconductor.config import ConductorConfig
from rigconductor.constants import DEFAULT_DURATION_S
from rigconductor.models import Model

Log = Callable[[str], None]


@dataclass
class SessionContext:
    cfg: ConductorConfig
    session_id: str
    metadata: dict  # profile-specific, from the wizard's Animals screen
    recording_dir: Path
    log: Log
    state: dict = field(default_factory=dict)  # profile scratch, persisted with the session

    @property
    def clips_dir(self) -> Path:
        return self.recording_dir / "clips"

    @property
    def results_dir(self) -> Path:
        return self.recording_dir / "results"

    # shell helpers shared by profiles
    def run(self, cmd: list[str], **kw) -> subprocess.CompletedProcess:
        self.log("$ " + " ".join(shlex.quote(c) for c in cmd))
        r = subprocess.run(cmd, capture_output=True, text=True, **kw)
        if r.returncode != 0:
            raise ConductorError(f"command failed ({r.returncode}): {cmd[0]}\n{r.stderr[-2000:]}")
        return r

    def ssh(self, remote_cmd: str) -> str:
        r = self.run(["ssh", "-o", "BatchMode=yes", self.cfg.cluster.ssh_host, remote_cmd])
        return r.stdout.strip()


class Profile(ABC):
    name: str
    organism: str
    title: str
    n_units: int  # arenas or wells
    unit_noun: str  # "arena" | "well"

    def config(self, cfg: ConductorConfig) -> dict:
        return cfg.profile(self.name)

    def default_duration_s(self, cfg: ConductorConfig) -> int:
        return int(self.config(cfg).get("default_duration_s", DEFAULT_DURATION_S))

    def ddb_snapshot(self, cfg: ConductorConfig) -> Path | None:
        return None

    # ── metadata ───────────────────────────────────────────────────────
    @abstractmethod
    def validate_metadata(self, meta: dict, cfg: ConductorConfig) -> list[str]:
        """Human-readable problems; empty list means go."""

    def motif_metadata(self, meta: dict) -> dict:
        """What is written into Motif's metadata.yaml at recording start (JSON-safe)."""
        return {"conductor_profile": self.name, "conductor": meta}

    # ── stages ─────────────────────────────────────────────────────────
    @abstractmethod
    def cut(self, ctx: SessionContext) -> list[Path]: ...

    @abstractmethod
    def register(self, ctx: SessionContext) -> None: ...

    @abstractmethod
    def upload(self, ctx: SessionContext) -> None: ...

    @abstractmethod
    def submit(self, ctx: SessionContext, model: Model) -> str:
        """Submit the cluster job; return the job id."""

    @abstractmethod
    def poll(self, ctx: SessionContext, interval_s: float) -> bool:
        """Block until the job finished; True if every task completed."""

    @abstractmethod
    def fetch(self, ctx: SessionContext) -> list[Path]: ...

    @abstractmethod
    def score(self, ctx: SessionContext, model: Model) -> None: ...

    @abstractmethod
    def export_files(self, ctx: SessionContext, blind: bool, clips: str) -> dict[str, Path]:
        """``{relative name in the bundle: source path}``."""

    # ── optional ───────────────────────────────────────────────────────
    def clips_for_check(self, ctx: SessionContext) -> list[Path]:
        """Clips the model will see (for the model-vs-data check)."""
        return sorted(ctx.clips_dir.glob("*.mp4")) if ctx.clips_dir.is_dir() else []

    def inverts_for(self, ctx: SessionContext, model: Model) -> bool | None:
        """Whether the cluster step inverts the clips for this model (None = model default)."""
        return None

    def preview_image(self, ctx: SessionContext) -> Path | None:
        return None

    def cluster_queue(self, ctx: SessionContext) -> tuple[str, str, list[int]] | None:
        """Remote pose dir, trial name and unit indices of a pull-queue run."""
        return None

    def unit_label(self, index: int) -> str:
        """How a cluster task (= one arena / well) is named to the operator."""
        return f"{self.unit_noun} {index}"

    def archive_analysis(self, ctx: SessionContext, label: str) -> str | None:
        """Move the results of the last cluster run aside before a rerun with another
        model; returns a description of where they went."""
        return None

    def reset_analysis(self, ctx: SessionContext) -> None:
        """Clear profile-side state of a cluster run (job id, …)."""
        ctx.state.pop("job_id", None)

    def arena_layout_path(self, ctx: SessionContext) -> Path | None:
        """The arena layout the cut used (editable in 'Fix arenas'); None → no editor."""
        return None

    def save_arena_layout(self, ctx: SessionContext, layout) -> list[Path]:
        """Store an edited layout where the next cut will use it."""
        raise NotImplementedError

    def reset_cut(self, ctx: SessionContext) -> Path | None:
        """Move the clips aside so the next cut starts fresh; returns where to."""
        if not ctx.clips_dir.is_dir():
            return None
        from datetime import datetime

        old = ctx.clips_dir.with_name(f"clips.old-{datetime.now():%Y%m%d_%H%M%S}")
        ctx.clips_dir.rename(old)
        return old

    def arena_circles(self, ctx: SessionContext) -> list[tuple[float, float, float]] | None:
        """Arena circles (cx, cy, r) in full-frame px after the cut, for 'reduce';
        ``None`` → this profile does not crop recordings."""
        return None

    # ── offline relay (rigconductor.relay) ─────────────────────────────
    def relay_files(self, ctx: SessionContext) -> dict[str, Path]:
        """Files a relay needs to run upload → fetch, relative to the recording dir."""
        files = {}
        meta = ctx.recording_dir / "metadata.yaml"
        if meta.is_file():
            files["metadata.yaml"] = meta
        for c in sorted(ctx.clips_dir.glob("*.mp4")) if ctx.clips_dir.is_dir() else []:
            files[f"clips/{c.name}"] = c
        return files

    def result_files(self, ctx: SessionContext) -> dict[str, Path]:
        """Files the relay sends back, relative to the recording dir."""
        r = ctx.results_dir
        return (
            {f"results/{p.name}": p for p in sorted(r.iterdir()) if p.is_file()}
            if r.is_dir()
            else {}
        )

    def adopt_relay_state(self, ctx: SessionContext, relay_profile_state: dict) -> None:
        """Merge what the relay learned (job id, …) into the rig's profile state."""
        for k in ("job_id",):
            if k in relay_profile_state:
                ctx.state[k] = relay_profile_state[k]
