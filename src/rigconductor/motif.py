# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — motif                                           ║
# ║  « drive the loopbio recorder »                                  ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Motif REST client, recording discovery, an in-memory fake.      ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Motif (loopbio) recorder control over its REST API, plus an in-memory fake.

Only the handful of endpoints Conductor needs are wrapped; the wire format is
the same as the vendor's ``motifapi`` package (``X-Api-Key`` header, JSON body,
HTTPS with the recnode's self-signed certificate).
"""

from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import yaml

from rigconductor import ConductorError
from rigconductor.constants import HTTP_TIMEOUT_S, MOTIF_PORT, RECORDING_QUIET_S


class MotifError(ConductorError):
    pass


@dataclass
class CameraStatus:
    serial: str
    status: str  # e.g. "ready", "running", "pending", "ERROR", "unknown"
    filename: str | None  # the recording being written, if any
    raw: dict

    @property
    def is_recording(self) -> bool:
        info = self.raw.get("camera_info") or {}
        return (
            bool(self.filename) or self.status == "pending" or bool(info.get("record_is_recording"))
        )

    @property
    def is_ok(self) -> bool:
        return self.status.upper() not in {"ERROR", "UNKNOWN", ""}


class Recorder(Protocol):
    """What the session needs from a recorder (real Motif or the fake)."""

    def version(self) -> dict: ...
    def camera(self) -> CameraStatus: ...
    def start_recording(
        self, filename: str, duration_s: float, codec: str | None, metadata: dict | None
    ) -> dict: ...
    def stop_recording(self) -> dict: ...


def resolve_api_key(api_key: str | None = None, api_key_cmd: str | None = "recnode-apikey") -> str:
    key = api_key or os.environ.get("MOTIF_API_KEY")
    if key:
        return key
    if api_key_cmd and shutil.which(api_key_cmd):
        try:
            key = subprocess.check_output([api_key_cmd], text=True, timeout=10).strip()
        except (subprocess.SubprocessError, OSError) as e:
            raise MotifError(f"could not read the Motif API key via {api_key_cmd}: {e}") from e
        if key:
            return key
    raise MotifError(
        "no Motif API key: set MOTIF_API_KEY or make sure `recnode-apikey` is on the PATH"
    )


class MotifClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = MOTIF_PORT,
        serial: str | None = None,
        api_key: str | None = None,
        api_key_cmd: str | None = "recnode-apikey",
        timeout_s: float = HTTP_TIMEOUT_S,
    ):
        self.host, self.port, self.timeout_s = host, int(port), timeout_s
        self._serial = serial
        self._key = resolve_api_key(api_key, api_key_cmd)
        self._ssl = ssl.create_default_context()
        self._ssl.check_hostname = False
        self._ssl.verify_mode = ssl.CERT_NONE  # recnode uses a self-signed certificate

    # ── transport ──────────────────────────────────────────────────────
    def _call(self, method: str, endpoint: str, data: dict | None = None) -> dict:
        prefix = "" if endpoint == "version" else "api/1/"
        url = f"https://{self.host}:{self.port}/{prefix}{endpoint}"
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("X-Api-Key", self._key)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s, context=self._ssl) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode())
                msg = err.get("error", str(e))
            except Exception:
                msg = str(e)
            raise MotifError(f"Motif API {method} {endpoint}: {msg}") from e
        except (urllib.error.URLError, OSError) as e:
            raise MotifError(
                f"Motif is not reachable at {self.host}:{self.port} "
                f"(is the recnode running? `sudo supervisorctl status`): {e}"
            ) from e
        return json.loads(raw.decode()) if raw else {}

    # ── queries ────────────────────────────────────────────────────────
    def version(self) -> dict:
        return self._call("GET", "version")

    def cameras(self) -> list[dict]:
        return self._call("GET", "cameras").get("cameras", [])

    @property
    def serial(self) -> str:
        if not self._serial:
            cams = self.cameras()
            if not cams:
                raise MotifError("Motif reports no camera")
            self._serial = str(cams[0]["serial"])
        return self._serial

    def camera(self) -> CameraStatus:
        r = self._call("GET", f"camera/{self.serial}")
        info = r.get("camera_info") or {}
        return CameraStatus(
            serial=self.serial,
            status=str(info.get("status", "unknown")),
            filename=info.get("filename") or None,
            raw=r,
        )

    def recordings(self) -> list[dict]:
        return self._call("GET", f"camera/{self.serial}/recordings").get("recordings", [])

    # ── control ────────────────────────────────────────────────────────
    def start_recording(
        self,
        filename: str,
        duration_s: float,
        codec: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        data: dict = {"filename": filename, "duration": float(duration_s)}
        if codec:
            data["codec"] = codec
        if metadata:
            data["metadata"] = metadata
        return self._call("POST", f"camera/{self.serial}/recording/start", data)

    def stop_recording(self) -> dict:
        return self._call("POST", f"camera/{self.serial}/recording/stop", {})

    def read(self, name: str):
        """One camera setting, e.g. ``read("PixelFormat")`` → ``"Mono8"``."""
        r = self._call("GET", f"camera/{self.serial}/read/{name}")
        return r.get(str(self.serial)) if isinstance(r, dict) else r

    def configure(self, **settings) -> dict:
        """PATCH camera settings, e.g. ``AcquisitionFrameRate=50.0``."""
        return self._call("PATCH", f"camera/{self.serial}/configure", settings)


# ── helpers shared by real and fake ──────────────────────────────────────


def motif_dir_name(title: str, when: datetime) -> str:
    """Motif names the store ``<title lowercased>_<YYYYMMDD>_<HHMMSS>``."""
    return f"{title.lower()}_{when.strftime('%Y%m%d_%H%M%S')}"


def find_new_recording(
    data_root: Path, started_after: float, title: str | None = None
) -> Path | None:
    """The newest recording folder created after ``started_after`` (epoch seconds) whose
    name starts with ``title`` (lower-cased), if given. Motif writes
    ``<data_root>/<camera serial>/<title>_<stamp>``, so one level of
    sub-directories is searched as well."""
    root = Path(data_root)
    cands = []
    for d in list(root.iterdir()) + [c for s in root.iterdir() if s.is_dir() for c in s.iterdir()]:
        if not d.is_dir() or not (d / "metadata.yaml").is_file():
            continue
        if title and not d.name.startswith(title.lower()):
            continue
        if d.stat().st_mtime >= started_after - 5:
            cands.append(d)
    return max(cands, key=lambda d: d.stat().st_mtime) if cands else None


def recording_is_closed(
    rec_dir: Path, quiet_s: float = RECORDING_QUIET_S, now: float | None = None
) -> bool:
    """True when no file in the store changed for ``quiet_s`` seconds (``now`` = epoch s)."""
    files = [p for p in Path(rec_dir).iterdir() if p.is_file()]
    if not files:
        return False
    newest = max(p.stat().st_mtime for p in files)
    return (time.time() if now is None else now) - newest >= quiet_s


def wait_for_stop(
    rec: Recorder,
    timeout_s: float,
    poll_s: float = 2.0,
    log=None,
    clock=time.monotonic,
    sleep=time.sleep,
) -> None:
    t0 = clock()
    while rec.camera().is_recording:
        if clock() - t0 > timeout_s:
            raise MotifError(f"recording still running after {timeout_s:.0f} s")
        if log:
            log(f"recording… {clock() - t0:.0f} s")
        sleep(poll_s)


# ── fake for tests and dry runs ──────────────────────────────────────────


class FakeMotif:
    """Behaves like Motif from the outside: a recording lasts ``duration`` seconds
    of *fake clock* time, writes ``metadata.yaml`` and one chunk file into
    ``data_root/<title>_<stamp>``. Advance time with :meth:`tick` or pass a real
    clock for timing tests."""

    def __init__(self, data_root: Path, serial: str = "FAKE0001", camera_status: str = "ready"):
        self.data_root = Path(data_root)
        self.serial = serial
        self.camera_status = camera_status
        self.now = 0.0
        self.calls: list[tuple] = []
        self._rec: dict | None = None
        self.recordings_made: list[Path] = []

    def tick(self, s: float):
        self.now += s
        self._finish_if_due()

    def _finish_if_due(self):
        if self._rec and self.now >= self._rec["end"]:
            self._rec = None

    def version(self) -> dict:
        return {"api": 1, "software": "fake-6.1.10"}

    def configure(self, **settings) -> dict:
        self.settings = {**getattr(self, "settings", {}), **settings}
        self.calls.append(("configure", settings))
        return {}

    def read(self, name: str):
        return getattr(self, "settings", {}).get(name)

    def camera(self) -> CameraStatus:
        self._finish_if_due()
        return CameraStatus(
            serial=self.serial,
            status="running" if self._rec else self.camera_status,
            filename=self._rec["path"] if self._rec else None,
            raw={},
        )

    def start_recording(self, filename, duration_s, codec=None, metadata=None) -> dict:
        self.calls.append(("start", filename, duration_s, codec, metadata))
        if self.camera_status.upper() == "ERROR":
            raise MotifError("Motif API POST recording/start: camera not ready")
        if self._rec:
            raise MotifError("already recording")
        when = datetime.fromtimestamp(time.time())
        d = self.data_root / motif_dir_name(filename, when)
        d.mkdir(parents=True)
        meta = {
            "__store": {"class": "VideoImgStoreFFMPEG", "format": codec or "fake"},
            "title": filename,
            "camera_serial": self.serial,
            "acquisitionframerate": 25.0,
            "motif_version": "fake",
            "created_utc": when.isoformat(),
        }
        if metadata:
            meta.update(metadata)
        (d / "metadata.yaml").write_text(yaml.safe_dump(meta), encoding="utf-8")
        (d / "000000.mp4").write_bytes(b"\x00" * 16)
        self._rec = {"path": str(d), "end": self.now + float(duration_s)}
        self.recordings_made.append(d)
        return {"filename": str(d)}

    def stop_recording(self) -> dict:
        self.calls.append(("stop",))
        self._rec = None
        return {}
