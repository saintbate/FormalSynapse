from __future__ import annotations

from pathlib import Path

import pytest

from formalsynapse.paths import repo_root


@pytest.fixture
def root() -> Path:
    return repo_root()
