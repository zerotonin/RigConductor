import sys
import traceback

import pytest

from rigconductor.config import ConductorConfig


@pytest.fixture(autouse=True)
def no_rig(monkeypatch):
    """Tests never ask the rig for its Motif key (and work on CI, which has none)."""
    monkeypatch.setenv("MOTIF_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def fail_on_qt_callback_errors(monkeypatch):
    """An exception inside a Qt callback (initializePage, a slot) fails the test.

    PyQt only prints these on Python 3.10 but aborts the whole run on 3.11+.
    """
    seen: list[str] = []
    monkeypatch.setattr(
        sys, "excepthook", lambda *exc: seen.append("".join(traceback.format_exception(*exc)))
    )
    yield
    assert not seen, "unhandled exception in a Qt callback:\n" + "\n".join(seen)


@pytest.fixture
def cfg(tmp_path):
    data = tmp_path / "DATA"
    data.mkdir()
    return ConductorConfig(
        data_root=data,
        sessions_dir=tmp_path / "sessions",
        profiles={"fake": {"default_duration_s": 10}},
    )
