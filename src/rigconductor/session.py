# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — session                                         ║
# ║  « one experiment, stage by stage »                              ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Idempotent, resumable stages with on-disk state.                ║
# ╚══════════════════════════════════════════════════════════════════╝
"""One experiment session, as idempotent stages with on-disk state.

State lives in ``<sessions_dir>/<session_id>.json`` (and a copy inside the recording
folder once it exists), so every stage can be re-run after a crash, a lost VPN or a
cancelled job. Finished stages are skipped.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from rigconductor import ConductorError
from rigconductor.config import ConductorConfig
from rigconductor.constants import CAMERA_SETTING_TOL, DEFAULT_CLIP_MODE, RECORD_GRACE_S
from rigconductor.export import Disk, mount, sync_disk, unmount, verify_bundle, write_bundle
from rigconductor.models import Model, by_id, load_cached
from rigconductor.motif import Recorder, find_new_recording, recording_is_closed, wait_for_stop
from rigconductor.profiles import Profile, SessionContext, make_profile

STAGES = (
    "record",
    "cut",
    "reduce",
    "register",
    "upload",
    "submit",
    "poll",
    "fetch",
    "score",
    "export",
)
Log = Callable[[str], None]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_session_id(profile: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    return f"{profile}_{when.strftime('%Y%m%d_%H%M%S')}"


@dataclass
class ConductorSpec:
    profile: str
    session_id: str
    title: str  # Motif recording title (becomes the folder prefix)
    duration_s: float
    codec: str | None = None
    operator: str | None = None
    metadata: dict = field(default_factory=dict)  # profile-specific
    recording_dir: str | None = None  # set after 'record', or given for an existing recording
    model_id: str | None = None
    export_device: str | None = None  # /dev/sdX1; None → export_dir
    export_dir: str | None = None
    blind: bool = False
    clips: str = DEFAULT_CLIP_MODE  # none | plain | skeleton | events | both
    unmount: bool = False  # unmount the USB disk after a verified export
    force_model: bool = False  # submit even if the model-vs-data check fails
    reduce: bool | None = None  # crop the recording to the arenas; None → rig.yaml
    reduce_padding_px: int | None = None  # None → rig.yaml
    preset: str | None = None  # rigconductor.presets key chosen on the first page
    gray: bool | None = None  # drop colour when re-encoding; None → preset

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> ConductorSpec:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _same_setting(wanted, got) -> bool:
    if isinstance(wanted, bool) or isinstance(got, bool):
        return str(wanted).lower() in (str(got).lower(), "on" if got is True else "off")
    try:
        w, g = float(wanted), float(got)
    except (TypeError, ValueError):
        return str(wanted).lower() == str(got).lower()
    return abs(w - g) <= max(abs(w), 1.0) * CAMERA_SETTING_TOL


def saved_state_for(cfg: ConductorConfig, rec_dir: Path) -> dict | None:
    """The session state belonging to a recording: the rig's copy in
    ``sessions_dir`` if it exists (newest), else the copy next to the video."""
    here = Path(rec_dir) / "conductor_session.json"
    if not here.is_file():
        return None
    st = json.loads(here.read_text(encoding="utf-8"))
    sid = (st.get("spec") or {}).get("session_id")
    rig = cfg.sessions_dir / f"{sid}.json" if sid else None
    if rig and rig.is_file():
        return json.loads(rig.read_text(encoding="utf-8"))
    return st


def adopt_saved_state(cfg: ConductorConfig, state: dict) -> ConductorSession:
    """Resume a session from a saved state (copied to ``sessions_dir`` if missing)."""
    spec = ConductorSpec.from_json(state["spec"])
    path = cfg.sessions_dir / f"{spec.session_id}.json"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return ConductorSession(cfg, spec)


class ConductorSession:
    def __init__(
        self,
        cfg: ConductorConfig,
        spec: ConductorSpec,
        recorder: Recorder | None = None,
        log: Log = print,
        profile: Profile | None = None,
    ):
        self.cfg, self.spec, self.log = cfg, spec, log
        self.recorder = recorder
        self.profile = profile or make_profile(spec.profile)
        self.probe = None  # clip prober override (tests); default compat.probe_clip
        self.state_path = cfg.sessions_dir / f"{spec.session_id}.json"
        self.state = self._load()

    # ── state ──────────────────────────────────────────────────────────
    def _load(self) -> dict:
        if self.state_path.is_file():
            st = json.loads(self.state_path.read_text(encoding="utf-8"))
            if st["spec"].get("recording_dir") and not self.spec.recording_dir:
                self.spec.recording_dir = st["spec"]["recording_dir"]
            return st
        return {"spec": self.spec.to_json(), "done": {}, "profile_state": {}, "created": now_iso()}

    def _save(self):
        self.state["spec"] = self.spec.to_json()
        self.state["updated"] = now_iso()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        if self.spec.recording_dir and Path(self.spec.recording_dir).is_dir():
            (Path(self.spec.recording_dir) / "conductor_session.json").write_text(
                json.dumps(self.state, indent=2), encoding="utf-8"
            )

    def _mark(self, stage: str, **info):
        self.state["done"][stage] = {"at": now_iso(), **info}
        self._save()

    def is_done(self, stage: str) -> bool:
        return stage in self.state["done"]

    def _ctx(self) -> SessionContext:
        if not self.spec.recording_dir:
            raise ConductorError("no recording yet: run 'record' or choose an existing recording")
        return SessionContext(
            cfg=self.cfg,
            session_id=self.spec.session_id,
            metadata=self.spec.metadata,
            recording_dir=Path(self.spec.recording_dir),
            log=self.log,
            state=self.state.setdefault("profile_state", {}),
        )

    def _model(self) -> Model:
        if not self.spec.model_id:
            raise ConductorError("choose a model first")
        return by_id(load_cached(self.cfg.registry_cache(self.spec.profile)), self.spec.model_id)

    # ── stages ─────────────────────────────────────────────────────────
    def record(self, clock=time.time, sleep=time.sleep, grace_s: float = RECORD_GRACE_S) -> Path:
        if self.is_done("record"):
            self.log(f"record: already done ({self.spec.recording_dir})")
            return Path(self.spec.recording_dir)  # type: ignore[arg-type]
        if self.spec.recording_dir:
            rec = Path(self.spec.recording_dir)
            if not (rec / "metadata.yaml").is_file():
                raise ConductorError(f"{rec} is not a Motif recording (no metadata.yaml)")
            self.log(f"record: using existing recording {rec}")
            self._mark("record", existing=True)
            return rec
        if self.recorder is None:
            raise ConductorError("no recorder configured")
        errs = self.profile.validate_metadata(self.spec.metadata, self.cfg)
        if errs:
            raise ConductorError("metadata incomplete:\n- " + "\n- ".join(errs))
        st = self.recorder.camera()
        if st.is_recording:
            raise ConductorError(f"Motif is already recording ({st.filename}); stop it first")
        if not st.is_ok:
            raise ConductorError(f"camera status is {st.status}; see the readiness checks")
        self.apply_camera_settings()
        meta = self.profile.motif_metadata(self.spec.metadata)
        meta["conductor_session"] = self.spec.session_id
        t0 = clock()
        self.recorder.start_recording(
            self.spec.title, self.spec.duration_s, self.spec.codec or self.cfg.motif.codec, meta
        )
        self.log(f"record: started '{self.spec.title}' for {self.spec.duration_s:.0f} s")
        self.state["record_started"] = now_iso()
        self._save()
        wait_for_stop(
            self.recorder,
            timeout_s=self.spec.duration_s + grace_s,
            log=lambda s: None,
            clock=clock,
            sleep=sleep,
        )
        rec = find_new_recording(self.cfg.data_root, t0, self.spec.title)
        if rec is None:
            raise ConductorError(
                f"Motif reported the recording finished but no new folder appeared under "
                f"{self.cfg.data_root} (title {self.spec.title!r})"
            )
        t1 = clock()
        while not recording_is_closed(rec, now=clock()):
            if clock() - t1 > grace_s:
                raise ConductorError(f"{rec} is still being written after {grace_s:.0f} s")
            sleep(1)
        self.spec.recording_dir = str(rec)
        self.log(f"record: {rec}")
        self._mark("record", recording_dir=str(rec))
        return rec

    def apply_camera_settings(self) -> dict:
        """Set the rig's standard camera values (rig.yaml ``motif.camera``) and read
        them back; raises if the camera did not take them."""
        wanted = dict(self.cfg.motif.camera or {})
        if not wanted or not hasattr(self.recorder, "configure"):
            return {}
        self.recorder.configure(**wanted)
        wrong = {}
        for name, value in wanted.items():
            got = self.recorder.read(name) if hasattr(self.recorder, "read") else value
            if not _same_setting(value, got):
                wrong[name] = (value, got)
        if wrong:
            detail = ", ".join(f"{k} wanted {v} got {g}" for k, (v, g) in wrong.items())
            raise ConductorError(f"camera did not take the standard settings: {detail}")
        shown = ", ".join(
            f"{k} {v}" for k, v in wanted.items() if not str(k).endswith(("Auto", "Enable"))
        )
        self.log(f"record: camera set to {shown}")
        return wanted

    def abort_recording(self):
        if self.recorder is not None:
            self.recorder.stop_recording()
            self.log("record: aborted by operator")

    def cut(self) -> list[Path]:
        if self.is_done("cut"):
            self.log("cut: already done")
            return sorted(self._ctx().clips_dir.glob("*.mp4"))
        clips = self.profile.cut(self._ctx())
        self.log(f"cut: {len(clips)} {self.profile.unit_noun} clips")
        self._mark("cut", n_clips=len(clips))
        return clips

    def reduce(self):
        """Crop the full recording to the arenas (+ padding) to save disk space."""
        if self.is_done("reduce"):
            self.log("reduce: already done")
            return
        rc = self.cfg.reduce
        on = rc.enabled if self.spec.reduce is None else self.spec.reduce
        ctx = self._ctx()
        circles = self.profile.arena_circles(ctx) if on else None
        if not on or not circles:
            self.log("reduce: skipped" + ("" if not on else " (profile has no arena layout)"))
            self._mark("reduce", skipped=True)
            return
        from rigconductor.reduce import reduce_recording

        pad = rc.padding_px if self.spec.reduce_padding_px is None else self.spec.reduce_padding_px
        gray = self.spec.gray
        if gray is None:
            from rigconductor.presets import by_key

            gray = by_key(self.spec.profile, self.spec.preset, self.cfg).gray
        info = reduce_recording(
            ctx.recording_dir,
            circles,
            pad,
            crf=rc.crf,
            keep_full=rc.keep_full,
            gray=gray,
            log=self.log,
        )
        self._mark(
            "reduce",
            size=f"{info['w']}x{info['h']}",
            saved_mb=round((info["bytes_before"] - info["bytes_after"]) / 1e6),
        )

    def redo_cut(self):
        """Throw away the clips and everything after them, so 'cut' runs again
        (e.g. with a new minimum clip size). The recording, and its crop, stay."""
        if not self.is_done("cut"):
            self.log("redo cut: nothing to redo")
            return
        ctx = self._ctx()
        old = self.profile.reset_cut(ctx)
        for st in STAGES[STAGES.index("cut") :]:
            if st != "reduce":  # the crop is applied to the recording itself
                self.state["done"].pop(st, None)
        self.state.pop("model_check", None)
        self._save()
        self.log(f"redo cut: old clips moved to {old}" if old else "redo cut: clips cleared")

    def redo_analysis(self):
        """Forget the cluster run (upload → score) so it can be sent again, e.g. after
        a failed job. Clips stay; the next upload only sends what changed."""
        stages = STAGES[STAGES.index("upload") : STAGES.index("export")]
        if not any(self.is_done(s) for s in stages):
            self.log("send again: nothing sent yet")
            return
        self.profile.reset_analysis(self._ctx())
        for st in stages:
            self.state["done"].pop(st, None)
        self._save()
        self.log("send again: cluster stages cleared")

    def rerun_with_model(self, model_id: str) -> str | None:
        """Analyse this recording again with another pose model: the earlier poses
        (and clips drawn from them) are archived, the cluster stages cleared and the
        new model set. Returns where the old results went (None if nothing to keep)."""
        old = self.spec.model_id
        archived = None
        if any(self.is_done(s) for s in ("upload", "submit", "fetch", "score")):
            archived = self.profile.archive_analysis(self._ctx(), old or "unknown-model")
            self.redo_analysis()
        self.spec.model_id = model_id
        self.spec.force_model = False
        self.state.pop("model_check", None)
        self._save()
        self.log(
            f"rerun: model {old or '-'} → {model_id}"
            + (f"; earlier results kept in {archived}" if archived else "")
        )
        return archived

    def register(self):
        if self.is_done("register"):
            self.log("register: already done")
            return
        errs = self.profile.validate_metadata(self.spec.metadata, self.cfg)
        if errs:
            raise ConductorError("metadata incomplete:\n- " + "\n- ".join(errs))
        self.profile.register(self._ctx())
        self._mark("register")

    def upload(self):
        if self.is_done("upload"):
            self.log("upload: already done")
            return
        self.profile.upload(self._ctx())
        self._mark("upload")

    def submit(self):
        if self.is_done("submit"):
            self.log(f"submit: already done (job {self.state['done']['submit'].get('job_id')})")
            return
        model = self._model()
        self.check_model(model)
        job = self.profile.submit(self._ctx(), model)
        self.log(
            f"submit: job {job} with model {model.id}"
            if job
            else f"submit: nothing to send, every arena already has its result ({model.id})"
        )
        self._mark("submit", job_id=job, model_id=model.id)

    def check_model(self, model: Model | None = None, probe=None) -> list:
        """Model-vs-data check; raises unless every check passes (or ``force_model``)."""
        from rigconductor.compat import check_model_vs_clips, probe_clip

        model = model or self._model()
        ctx = self._ctx()
        clips = self.profile.clips_for_check(ctx)[:3]  # a few clips are representative
        infos = [(probe or self.probe or probe_clip)(c) for c in clips]
        checks = check_model_vs_clips(model, infos, self.profile.inverts_for(ctx, model))
        for c in checks:
            self.log(c.line)
        self.state["model_check"] = {
            "model": model.id,
            "at": now_iso(),
            "ok": all(c.ok or not c.blocking for c in checks),
            "checks": [c.line for c in checks],
        }
        self._save()
        if not all(c.ok or not c.blocking for c in checks) and not self.spec.force_model:
            raise ConductorError(
                f"model {model.id} does not match these videos (see the checks above). "
                "Pick another model, or tick 'use anyway' if Bart says so."
            )
        return checks

    def poll(self, interval_s: float | None = None):
        if self.is_done("poll"):
            self.log("poll: job already finished")
            return
        ok = self.profile.poll(self._ctx(), interval_s or self.cfg.cluster.poll_interval_s)
        self._mark("poll", ok=ok)
        if not ok:
            raise ConductorError(
                "the cluster job did not finish cleanly; tell Bart the job number from 'status'"
            )

    def fetch(self):
        if self.is_done("fetch"):
            self.log("fetch: already done")
            return
        got = self.profile.fetch(self._ctx())
        self.log(f"fetch: {len(got)} result files")
        self._mark("fetch", n_files=len(got))

    def score(self):
        if self.is_done("score"):
            self.log("score: already done")
            return
        self.profile.score(self._ctx(), self._model())
        self._mark("score")

    def export(self, disk: Disk | None = None) -> Path:
        """Write the result bundle to ``disk`` (mounted if needed) or to ``spec.export_dir``."""
        files = self.profile.export_files(self._ctx(), self.spec.blind, self.spec.clips)
        files["session.json"] = self._write_session_copy()
        if disk is not None:
            root = mount(disk)
            self.spec.export_device = disk.device
        elif self.spec.export_dir:
            root = Path(self.spec.export_dir)
        else:
            raise ConductorError("choose a USB disk or an export folder")
        dest = root / self.spec.profile / self.spec.session_id
        man = write_bundle(files, dest, log=self.log)
        sync_disk()
        bad = verify_bundle(dest)
        if bad:
            raise ConductorError(
                f"verification failed for: {', '.join(bad)} — do not unplug, retry"
            )
        self.log(f"export: {len(files)} files verified at {dest}")
        self._mark("export", dest=str(dest), manifest=str(man), n_files=len(files))
        if disk is not None and self.spec.unmount:
            unmount(disk)
            self.log(f"export: {disk.device} unmounted — safe to unplug")
        elif disk is not None:
            self.log(f"export: {disk.device} stays mounted at {root} (eject it before unplugging)")
        return dest

    def _write_session_copy(self) -> Path:
        ctx = self._ctx()
        ctx.results_dir.mkdir(exist_ok=True)
        p = ctx.results_dir / "session.json"
        p.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        return p

    # ── driver ─────────────────────────────────────────────────────────
    def run(self, stages=STAGES, disk: Disk | None = None, poll_interval_s: float | None = None):
        for s in stages:
            self.log(f"── {s} ── {now_iso()[11:]}")
            if s == "record":
                self.record()
            elif s == "poll":
                self.poll(poll_interval_s)
            elif s == "export":
                self.export(disk)
            else:
                getattr(self, s)()
        self.log("session complete")

    def status_lines(self) -> list[str]:
        out = [f"session {self.spec.session_id} ({self.spec.profile})"]
        for s in STAGES:
            d = self.state["done"].get(s)
            extra = ""
            if d:
                extra = " ".join(f"{k}={v}" for k, v in d.items() if k != "at")
            out.append(f"  {s:9s} {'done ' + d['at'] + ' ' + extra if d else '-'}")
        return out
