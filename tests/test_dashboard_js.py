import shutil
import subprocess
from pathlib import Path

import pytest


def test_dashboard_js():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "tests/dashboard_check.js"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
