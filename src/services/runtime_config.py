"""AgentCore Platform v1.0"""

# Loads config/config.yaml -- the runtime parameter file.
#
# On the platform, AgentRegistry reads config/config.yaml and constructs the
# graph as Graph(config=...). A standalone entry point has no registry in front
# of it, so it must do the same thing itself; a graph constructed bare gets
# `self.config == {}` and every declared runtime value is dead. That failure is
# silent -- the agent answers, just never with the configured parameters.
#
# config/agent.yaml is the static MANIFEST and carries no runtime block; nothing
# here reads it.

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def load_runtime_config(path: Path | None = None) -> Dict[str, Any]:
    """Return the parsed runtime configuration, or {} when the file is absent.

    A missing file is tolerated (the resolver falls back to its own constants);
    a malformed file is not -- a config that cannot be parsed is a deployment
    error and must be visible at start-up rather than at the first request.
    """
    target = path or CONFIG_PATH
    if not target.exists():
        logger.warning("runtime config not found at %s; using built-in defaults", target)
        return {}
    with target.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{target} must contain a mapping at the top level, got {type(loaded).__name__}")
    return loaded
