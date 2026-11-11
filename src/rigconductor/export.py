# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — export                                          ║
# ║  « results onto a removable disk »                               ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Detect, mount, copy with a SHA-256 manifest, verify, unmount.   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Deliver results to a removable disk: detect, mount, copy, manifest, verify, unmount."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rigconductor import ConductorError

MANIFEST = "MANIFEST.sha256"


@dataclass
class Disk:
    device: str  # /dev/sdb1
    size: str
    fstype: str | None
    label: str | None
    mountpoint: str | None

    @property
    def text(self) -> str:
        name = self.label or Path(self.device).name
        state = f"mounted at {self.mountpoint}" if self.mountpoint else "not mounted"
        return f"{name} ({self.size}, {self.fstype or '?'}; {state})"


def removable_disks(run=subprocess.run) -> list[Disk]:
    """Partitions on removable block devices (USB sticks, USB disks) that carry a filesystem."""
    r = run(
        ["lsblk", "-J", "-o", "NAME,PATH,RM,HOTPLUG,SIZE,FSTYPE,LABEL,MOUNTPOINT,TYPE"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise ConductorError(f"lsblk failed: {r.stderr.strip()}")
    return parse_lsblk(r.stdout)


def parse_lsblk(text: str) -> list[Disk]:
    out: list[Disk] = []

    def walk(node: dict, parent_removable: bool):
        rm = bool(node.get("rm")) or bool(node.get("hotplug")) or parent_removable
        if node.get("type") == "part" and rm and node.get("fstype"):
            out.append(
                Disk(
                    device=node.get("path") or f"/dev/{node['name']}",
                    size=str(node.get("size")),
                    fstype=node.get("fstype"),
                    label=node.get("label"),
                    mountpoint=node.get("mountpoint"),
                )
            )
        for ch in node.get("children") or []:
            walk(ch, rm)

    for dev in json.loads(text).get("blockdevices", []):
        walk(dev, False)
    return out


def mount(disk: Disk, run=subprocess.run) -> Path:
    if disk.mountpoint:
        return Path(disk.mountpoint)
    r = run(["udisksctl", "mount", "-b", disk.device], capture_output=True, text=True)
    if r.returncode != 0:
        raise ConductorError(
            f"could not mount {disk.device}: {r.stderr.strip() or r.stdout.strip()}"
        )
    m = re.search(r" at (\S+)", r.stdout)
    if not m:
        raise ConductorError(f"udisksctl gave no mount point: {r.stdout.strip()}")
    disk.mountpoint = m.group(1).rstrip(".")
    return Path(disk.mountpoint)


def unmount(disk: Disk, run=subprocess.run) -> None:
    r = run(["udisksctl", "unmount", "-b", disk.device], capture_output=True, text=True)
    if r.returncode != 0:
        raise ConductorError(f"could not unmount {disk.device}: {r.stderr.strip()}")
    disk.mountpoint = None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_bundle(files: dict[str, Path], dest: Path, log=None) -> Path:
    """Copy ``{relative name: source}`` into ``dest`` and write a sha256 manifest.
    Returns the manifest path. Refuses to overwrite a non-empty ``dest``."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ConductorError(f"{dest} already exists and is not empty; choose another session name")
    dest.mkdir(parents=True, exist_ok=True)
    lines = []
    for rel, src in sorted(files.items()):
        src = Path(src)
        if not src.is_file():
            raise ConductorError(f"export: missing {src}")
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
        lines.append(f"{sha256_of(src)}  {rel}")
        if log:
            log(f"export: {rel} ({src.stat().st_size / 1e6:.1f} MB)")
    man = dest / MANIFEST
    man.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return man


def verify_bundle(dest: Path) -> list[str]:
    """Re-read every file against the manifest; returns the names that differ or are missing."""
    man = Path(dest) / MANIFEST
    if not man.is_file():
        return [MANIFEST]
    bad = []
    for line in man.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        p = Path(dest) / rel
        if not p.is_file() or sha256_of(p) != digest:
            bad.append(rel)
    return bad


def sync_disk():
    subprocess.run(["sync"], check=False)
