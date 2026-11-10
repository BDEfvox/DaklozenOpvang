"""Auto-discovers site modules: every module in this package that defines SOURCE (or SOURCES) is registered.

Adding a site = adding one file; nothing else to edit.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
from typing import Dict, List

from .base import Source, SourceBlocked, SourceError  # noqa: F401

_SKIP = {"base"}


def all_sources() -> Dict[str, Source]:
    found: Dict[str, Source] = {}
    for mod_info in pkgutil.iter_modules(__path__):
        if mod_info.name in _SKIP or mod_info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{mod_info.name}")
        except Exception as exc:  # a broken module must not take the others down
            print(f"[sources] could not import {mod_info.name}: {exc!r}", file=sys.stderr)
            continue
        items: List[Source] = []
        if hasattr(mod, "SOURCE"):
            items.append(mod.SOURCE)
        items.extend(getattr(mod, "SOURCES", []))
        for src in items:
            if not src.name:
                print(f"[sources] {mod_info.name}: source without a name, skipped", file=sys.stderr)
                continue
            found[src.name] = src
    return dict(sorted(found.items()))
