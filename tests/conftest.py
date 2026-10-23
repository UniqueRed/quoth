import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from quoth.domain import load_domain  # noqa: E402


@pytest.fixture
def domain():
    return load_domain("order")
