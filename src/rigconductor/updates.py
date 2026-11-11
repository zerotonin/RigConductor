# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — updates                                         ║
# ║  « is there a newer version, and get it »                        ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  git fetch and fast-forward pull for the Conductor and           ║
# ║  ThermoKourt; never merges, never touches local changes.         ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Check for and install updates of the rig's own code (git)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rigconductor.constants import GIT_TIMEOUT_S, UPDATE_PACKAGES


@dataclass
class RepoStatus:
    """State of one package's git checkout.

    Attributes:
        name: Package name.
        path: Repository root, None when the package is not a git checkout.
        branch: Current branch.
        upstream: Tracked remote branch, e.g. ``origin/main``.
        behind: Commits on the remote not here yet.
        ahead: Local commits not on the remote.
        changed: Tracked files with uncommitted changes.
        error: Why the check could not be done.
        new_commits: One-line summaries of the commits an update would bring.
    """

    name: str
    path: Path | None = None
    branch: str = ""
    upstream: str = ""
    behind: int = 0
    ahead: int = 0
    changed: list[str] = field(default_factory=list)
    error: str = ""
    new_commits: list[str] = field(default_factory=list)

    @property
    def can_update(self) -> bool:
        return not self.error and self.behind > 0 and self.ahead == 0 and not self.changed

    def summary(self) -> str:
        if self.error:
            return f"{self.name}: {self.error}"
        if self.behind == 0:
            state = "up to date"
        else:
            state = f"{self.behind} new commit{'s' if self.behind != 1 else ''} on {self.upstream}"
        blockers = []
        if self.changed:
            blockers.append(f"{len(self.changed)} files changed locally")
        if self.ahead:
            blockers.append(f"{self.ahead} local commits not pushed")
        extra = f" (cannot update: {', '.join(blockers)})" if self.behind and blockers else ""
        return f"{self.name}: {state}{extra}"


def _git(path: Path, *args: str, run=subprocess.run) -> str:
    r = run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    if r.returncode != 0:
        err = r.stderr.strip()
        raise RuntimeError(err.splitlines()[-1] if err else "git failed")
    return r.stdout.strip()


def repo_of(package: str) -> Path | None:
    """Git root of an installed package (editable installs point into the checkout)."""
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.origin:
        return None
    try:
        return Path(_git(Path(spec.origin).parent, "rev-parse", "--show-toplevel"))
    except (RuntimeError, OSError):
        return None


def check_repo(name: str, path: Path | None, fetch: bool = True, run=subprocess.run) -> RepoStatus:
    st = RepoStatus(name, path)
    if path is None:
        st.error = "not a git checkout, cannot be updated from here"
        return st
    try:
        st.branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD", run=run)
        st.upstream = _git(
            path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", run=run
        )
    except RuntimeError:
        st.error = "no remote branch to compare with"
        return st
    if fetch:
        try:
            _git(path, "fetch", "--quiet", run=run)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            st.error = f"cannot reach the repository ({e})"
            return st
    ahead, behind = _git(
        path, "rev-list", "--left-right", "--count", "HEAD...@{u}", run=run
    ).split()
    st.ahead, st.behind = int(ahead), int(behind)
    porcelain = _git(path, "status", "--porcelain", "--untracked-files=no", run=run)
    st.changed = [line[3:] for line in porcelain.splitlines() if line.strip()]
    if st.behind:
        st.new_commits = _git(path, "log", "--oneline", "HEAD..@{u}", run=run).splitlines()
    return st


def check_all(fetch: bool = True) -> list[RepoStatus]:
    return [
        check_repo(p, repo_of(p), fetch)
        for p in UPDATE_PACKAGES
        if importlib.util.find_spec(p) is not None
    ]


def update_repo(st: RepoStatus, run=subprocess.run) -> str:
    """Fast-forward one checkout; reinstall when its pyproject changed."""
    if not st.can_update:
        raise RuntimeError(st.summary())
    before = _git(st.path, "rev-parse", "HEAD", run=run)
    _git(st.path, "pull", "--ff-only", "--quiet", run=run)
    changed = _git(st.path, "diff", "--name-only", f"{before}..HEAD", run=run).splitlines()
    msg = f"{st.name}: updated by {st.behind} commit{'s' if st.behind != 1 else ''}"
    if "pyproject.toml" in changed:
        r = run(
            [sys.executable, "-m", "pip", "install", "--quiet", "-e", str(st.path)],
            capture_output=True,
            text=True,
        )
        msg += "; reinstalled" if r.returncode == 0 else f"; reinstall FAILED: {r.stderr[-200:]}"
    return msg
