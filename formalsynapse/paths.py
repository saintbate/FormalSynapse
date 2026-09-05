"""Repository path helpers."""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """Walk up from this file until ``pyproject.toml`` is found."""
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd()


def benchmarks_dir() -> Path:
    return repo_root() / "benchmarks"


def golden_dir() -> Path:
    return benchmarks_dir() / "golden"


def smoke_dir() -> Path:
    return benchmarks_dir() / "smoke"


def default_output_dir() -> Path:
    """Trajectory / metric dumps. Same colon/space rule as :func:`default_workdir`."""
    root = repo_root()
    raw = str(root)
    if ":" in raw or " " in raw:
        return Path.home() / ".cache" / "formalsynapse" / "output"
    return root / "output"


def default_workdir() -> Path:
    """Scratch directory for harness runs.

    ``sby`` and ``$PATH`` both split on ``:``, so if the repository path contains ``:`` or a
    space the work directory is placed under ``~/.cache/formalsynapse/work`` instead.
    """
    root = repo_root()
    raw = str(root)
    if ":" in raw or " " in raw:
        return Path.home() / ".cache" / "formalsynapse" / "work"
    return root / "work"
