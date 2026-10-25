import pytest


@pytest.fixture(autouse=True)
def _outputs_in_tmp(tmp_path, monkeypatch):
    """Tests must not write into the real <root>/outputs folder."""
    monkeypatch.setenv("DECKGEN_OUTPUTS_DIR", str(tmp_path / "outputs"))
