import sys

import pytest

from rigconductor import ConductorError, sleapenv


def test_parse_line():
    assert sleapenv.parse_line("@progress label 0.500 arena 3") == ("progress", (0.5, "arena 3"))
    assert sleapenv.parse_line('@result {"a": 1}') == ("result", {"a": 1})
    assert sleapenv.parse_line("hello") == ("log", "hello")


def test_run_task_reads_progress_and_result(tmp_path, monkeypatch):
    fake = tmp_path / "worker.py"
    fake.write_text(
        "import json, sys\n"
        "args = json.load(open(sys.argv[2], encoding='utf-8'))\n"
        "print('@progress label 0.5 half', flush=True)\n"
        "print('a log line')\n"
        "if args.get('fail'): sys.exit(3)\n"
        "print('@result ' + json.dumps({'task': sys.argv[1], 'n': args['n']}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sleapenv, "WORKER", fake)
    env = sleapenv.SleapEnv(__import__("pathlib").Path(sys.executable))
    seen, logs = [], []
    res = sleapenv.run_task(
        env, "select", {"n": 4}, log=logs.append, progress=lambda f, t: seen.append((f, t))
    )
    assert res == {"task": "select", "n": 4} and seen == [(0.5, "half")]
    assert logs == ["a log line"]
    with pytest.raises(ConductorError, match="a log line"):
        sleapenv.run_task(env, "select", {"n": 1, "fail": True})


def test_installer_has_pinned_versions(tmp_path):
    from rigconductor.constants import SLEAP_VERSION

    paths = sleapenv.write_installer(tmp_path)
    text = paths[0].read_text(encoding="utf-8")
    assert f'"${{SLEAP_VERSION:-{SLEAP_VERSION}}}"' in text and "@SLEAP" not in text
    assert all(p.stat().st_mode & 0o111 for p in paths)
    assert "@SLEAP" not in sleapenv.installer_text("RULES.md")


def test_probe_rejects_missing_python(tmp_path):
    assert sleapenv.probe(tmp_path / "nope") is None
