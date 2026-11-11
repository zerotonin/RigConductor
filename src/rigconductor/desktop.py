# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — desktop                                         ║
# ║  « double-click icons for the two wizards »                      ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Desktop launchers (freedesktop ``.desktop`` files) for the wizards."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LAUNCHERS = {
    "rigconductor": (
        "Rig Conductor",
        "gui",
        "camera-video",
        "Record with Motif, analyse on Aoraki, export to USB",
    ),
    "rigconductor-training": (
        "Rig Conductor — training data",
        "label",
        "applications-science",
        "Pick frames, label them, train new pose models",
    ),
}


def entry_text(name: str, command: str, icon: str, comment: str, exe: Path) -> str:
    return (
        "[Desktop Entry]\nType=Application\n"
        f"Name={name}\nComment={comment}\nExec={exe} {command}\nIcon={icon}\n"
        "Terminal=false\nCategories=Science;\n"
    )


def conductor_exe() -> Path:
    """The ``rigconductor`` script of the running environment."""
    return Path(sys.executable).with_name("rigconductor")


def install(home: Path | None = None, exe: Path | None = None, run=subprocess.run) -> list[Path]:
    """Write both launchers to the app menu and (when it exists) the desktop."""
    home = home or Path.home()
    exe = exe or conductor_exe()
    apps = home / ".local" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    written = []
    for stem, (name, command, icon, comment) in LAUNCHERS.items():
        text = entry_text(name, command, icon, comment, exe)
        targets = [apps / f"{stem}.desktop"]
        if (home / "Desktop").is_dir():
            targets.append(home / "Desktop" / f"{stem}.desktop")
        for t in targets:
            t.write_text(text, encoding="utf-8")
            t.chmod(0o755)
            written.append(t)
            if t.parent.name == "Desktop":
                run(["gio", "set", str(t), "metadata::trusted", "true"], capture_output=True)
    return written
