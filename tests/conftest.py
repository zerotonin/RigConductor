import pytest

from rigconductor.config import ConductorConfig


@pytest.fixture
def cfg(tmp_path):
    data = tmp_path / "DATA"
    data.mkdir()
    return ConductorConfig(
        data_root=data,
        sessions_dir=tmp_path / "sessions",
        profiles={"fake": {"default_duration_s": 10}},
    )
