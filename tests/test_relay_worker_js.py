"""The subscriber relay (telegram-relay/worker.mjs) has its own tests in node's test runner; run them from pytest too."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_relay_worker_checks_pass():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    if int((version.split(".") or ["0"])[0] or 0) < 20:
        pytest.skip("node 20 or newer is needed for the Worker tests (found %s)" % version)
    result = subprocess.run([node, "--test", "telegram-relay/worker.test.mjs"], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]


def test_deploy_script_parses():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    result = subprocess.run([bash, "-n", "telegram-relay/deploy.sh"], cwd=str(ROOT), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
