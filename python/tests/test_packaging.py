"""Packaging checks.

The Python package lives in ``python/`` but the git repository (and its
``v*`` release tags) is rooted one level up. setuptools-scm must be told
where the repository root is, otherwise every build silently falls back to
version ``0.0.0`` and can't be published.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_DIR.parent


@pytest.mark.skipif(
    not (REPO_ROOT / ".git").exists() or shutil.which("git") is None,
    reason="needs a git checkout of the repository",
)
def test_version_derived_from_git_tags():
    pytest.importorskip("setuptools_scm")
    result = subprocess.run(
        [sys.executable, "-m", "setuptools_scm"],
        cwd=PYTHON_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    version = result.stdout.strip().splitlines()[-1]
    assert version != "0.0.0", (
        "setuptools-scm fell back to 0.0.0 - it isn't finding the git "
        "repository root from python/pyproject.toml"
    )
