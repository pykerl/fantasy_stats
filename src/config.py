"""Config loading. Secrets come from the environment, never from config.yaml."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
OUTPUT_DIR = ROOT / "output"
DOCS_DIR = ROOT / "docs"


class Config(dict):
    """dict with dotted-path lookup: cfg.get_path('yahoo.league_id')."""

    def get_path(self, path: str, default: Any = None) -> Any:
        node: Any = self
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def secret(self, path: str) -> str | None:
        """Resolve a `*_env` config entry to the environment variable's value."""
        var = self.get_path(path)
        if not var:
            return None
        value = os.environ.get(var)
        if not value:
            log.debug("environment variable %s is not set", var)
        return value or None


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else ROOT / "config.yaml"
    data = yaml.safe_load(path.read_text()) if path.exists() else {}

    # config.local.yaml is gitignored and shallow-merges over the committed one,
    # which is how you keep a real league id off GitHub.
    local = path.parent / "config.local.yaml"
    if local.exists():
        overlay = yaml.safe_load(local.read_text()) or {}
        for key, value in overlay.items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key].update(value)
            else:
                data[key] = value

    for directory in (CACHE_DIR, OUTPUT_DIR, DOCS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    return Config(data or {})
