from rigconductor import desktop


def test_both_launchers_are_written(tmp_path):
    (tmp_path / "Desktop").mkdir()
    calls = []
    out = desktop.install(
        home=tmp_path, exe=tmp_path / "bin" / "rigconductor", run=lambda *a, **k: calls.append(a)
    )
    names = sorted(p.name for p in out if p.parent.name == "Desktop")
    assert names == ["rigconductor-training.desktop", "rigconductor.desktop"]
    text = (tmp_path / "Desktop" / "rigconductor-training.desktop").read_text(encoding="utf-8")
    assert f"Exec={tmp_path}/bin/rigconductor label" in text
    assert len(calls) == 2
