"""Tiny .env reader/writer (KEY=VALUE lines; no secrets are ever printed)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from .config import ROOT

ENV_PATH = ROOT / ".env"


def read_env(path: Optional[Path] = None) -> Dict[str, str]:
    p = path or ENV_PATH
    out: Dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def load_env(path: Optional[Path] = None) -> None:
    """Load .env into os.environ; real environment variables win."""
    for k, v in read_env(path).items():
        if v and not os.environ.get(k):
            os.environ[k] = v


def write_env_value(key: str, value: str, path: Optional[Path] = None) -> None:
    p = path or ENV_PATH
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    new = "%s=%s" % (key, value)
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = new
            break
    else:
        lines.append(new)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(p, 0o600)
