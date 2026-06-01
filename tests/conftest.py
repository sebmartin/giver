from pathlib import Path

import pytest


@pytest.fixture
def workflows_dir() -> Path:
    return Path(__file__).parent / "workflows"
