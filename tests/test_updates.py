import shutil
import subprocess

import pytest

from rigconductor.updates import RepoStatus, check_repo, update_repo

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git missing")


def _git(path, *args):
    subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@x",
            "PATH": "/usr/bin:/bin",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x",
            "HOME": str(path),
        },
    )


@pytest.fixture
def repos(tmp_path):
    """origin (bare), a clone 'rig' tracking it, and a 'dev' clone to push from."""
    origin, dev, rig = tmp_path / "origin.git", tmp_path / "dev", tmp_path / "rig"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(dev))
    (dev / "a.txt").write_text("1\n", encoding="utf-8")
    _git(dev, "add", "a.txt")
    _git(dev, "commit", "-m", "first")
    _git(dev, "push", "origin", "HEAD:main")
    _git(tmp_path, "clone", str(origin), str(rig))
    return dev, rig


def _push(dev, text, msg):
    (dev / "a.txt").write_text(text, encoding="utf-8")
    _git(dev, "commit", "-am", msg)
    _git(dev, "push", "origin", "HEAD:main")


def test_up_to_date_then_behind_then_updated(repos):
    dev, rig = repos
    assert check_repo("pkg", rig).summary() == "pkg: up to date"
    _push(dev, "2\n", "second")
    st = check_repo("pkg", rig)
    assert st.behind == 1 and st.can_update and st.new_commits[0].endswith("second")
    assert update_repo(st) == "pkg: updated by 1 commit"
    assert (rig / "a.txt").read_text(encoding="utf-8") == "2\n"
    assert check_repo("pkg", rig).behind == 0


def test_local_changes_block_the_update(repos):
    dev, rig = repos
    _push(dev, "2\n", "second")
    (rig / "a.txt").write_text("edited on the rig\n", encoding="utf-8")
    st = check_repo("pkg", rig)
    assert not st.can_update and "1 files changed locally" in st.summary()
    with pytest.raises(RuntimeError):
        update_repo(st)
    assert (rig / "a.txt").read_text(encoding="utf-8") == "edited on the rig\n"


def test_not_a_checkout():
    st = check_repo("pkg", None)
    assert not st.can_update and "not a git checkout" in st.summary()
    assert RepoStatus("x", error="boom").summary() == "x: boom"
