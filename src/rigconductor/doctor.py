# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — doctor                                          ║
# ║  « is the rig ready? »                                           ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Readiness checks: Motif, camera, disk, cluster, DrosoDB.        ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Readiness checks shown on the wizard's first screen and by ``rigconductor doctor``."""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rigconductor.config import ConductorConfig
from rigconductor.constants import MAX_SNAPSHOT_AGE_H, MIN_FREE_DISK_GB


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""
    blocking: bool = True  # False = shown as a warning, never stops the session

    @property
    def line(self) -> str:
        mark = "OK  " if self.ok else ("FAIL" if self.blocking else "WARN")
        s = f"[{mark}] {self.name}: {self.detail}"
        return s if self.ok or not self.hint else f"{s}\n       → {self.hint}"


def check_motif(make_client: Callable[[], object]) -> list[Check]:
    try:
        cli = make_client()
        v = cli.version()  # type: ignore[attr-defined]
    except Exception as e:
        return [
            Check(
                "Motif",
                False,
                str(e),
                "Start the recorder: `sudo supervisorctl restart all` on the rig PC",
            )
        ]
    out = [Check("Motif", True, f"software {v.get('software')} (API {v.get('api')})")]
    try:
        st = cli.camera()  # type: ignore[attr-defined]
    except Exception as e:
        return out + [Check("Camera", False, str(e), "Check the camera's power and USB cable")]
    if st.is_recording:
        out.append(Check("Camera", True, f"recording to {st.filename}"))
    elif st.is_ok:
        out.append(Check("Camera", True, f"serial {st.serial}, status {st.status}"))
    else:
        out.append(
            Check(
                "Camera",
                False,
                f"serial {st.serial}, status {st.status}",
                "Motif cannot talk to the camera: check power and the USB3 cable, "
                "then restart Motif (`sudo supervisorctl restart all`)",
            )
        )
    return out


def check_disk(data_root: Path, min_free_gb: float = MIN_FREE_DISK_GB) -> Check:
    if not Path(data_root).is_dir():
        return Check("Data disk", False, f"{data_root} missing", "Mount the data disk (see fstab)")
    u = shutil.disk_usage(data_root)
    free = u.free / 1e9
    return Check(
        "Data disk",
        free >= min_free_gb,
        f"{free:.0f} GB free of {u.total / 1e9:.0f} GB at {data_root}",
        "" if free >= min_free_gb else "Export and delete old recordings before recording",
    )


def check_ssh(host: str, run=subprocess.run) -> Check:
    try:
        r = run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "echo ok"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return Check(
            "Cluster", False, str(e), "Is the network cable in? Off campus you need the VPN"
        )
    if r.returncode == 0 and r.stdout.strip() == "ok":
        return Check("Cluster", True, f"ssh {host} answers")
    return Check(
        "Cluster",
        False,
        (r.stderr or r.stdout).strip()[-200:] or f"ssh {host} failed",
        "Off campus: connect the University VPN first. On campus: tell Bart (SSH key)",
    )


def check_snapshot(path: Path | None, max_age_h: float = MAX_SNAPSHOT_AGE_H) -> Check:
    if not path:
        return Check("DrosoDB", True, "not configured for this profile")
    p = Path(path)
    if not p.is_file():
        return Check(
            "DrosoDB",
            False,
            f"snapshot missing at {p}",
            "Is the cable to the tablet plugged in and Syncthing running on both? "
            "No cable: on the tablet run 'DrosoDB → USB stick', then 'Import DrosoDB from USB'",
        )
    age_h = (time.time() - p.stat().st_mtime) / 3600
    return Check(
        "DrosoDB",
        age_h <= max_age_h,
        f"snapshot is {age_h:.0f} h old",
        ""
        if age_h <= max_age_h
        else "Not synced recently: check the tablet cable, or import by USB stick",
    )


def check_import(module: str, label: str, hint: str) -> Check:
    try:
        __import__(module)
        return Check(label, True, f"{module} importable")
    except Exception as e:
        return Check(label, False, str(e)[:120], hint)


def run_all(
    cfg: ConductorConfig,
    profile: str,
    make_client: Callable[[], object],
    ddb_snapshot: Path | None = None,
    with_ssh: bool = True,
) -> list[Check]:
    checks = check_motif(make_client)
    checks.append(check_disk(cfg.data_root))
    if with_ssh:
        checks.append(check_ssh(cfg.cluster.ssh_host))
    if profile == "drosophila_triad":
        checks.append(
            check_import("thermokourt", "ThermoKourt", "pip install -e ~/PyProjects/thermokourt")
        )
        checks.append(check_snapshot(ddb_snapshot))
    cache = cfg.registry_cache(profile)
    checks.append(
        Check(
            "Model registry",
            cache.is_file(),
            f"cached at {cache}" if cache.is_file() else "no cached registry",
            "" if cache.is_file() else "Press 'Refresh models' once the cluster is reachable",
        )
    )
    return checks
