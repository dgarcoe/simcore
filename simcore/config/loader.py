"""Load and validate YAML simulation configs."""

from __future__ import annotations

from pathlib import Path

import yaml

from simcore.config.schema import SimulationConfig


def load_config(path: str | Path) -> SimulationConfig:
    """Load a simulation config from a YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Empty config file: {path}")

    # The YAML has a top-level "simulation" key for metadata,
    # but agents, environment, etc. are at root level
    sim_data = raw.get("simulation", {})
    sim_data["llm"] = raw.get("llm", {})
    sim_data["environment"] = raw.get("environment", {})
    sim_data["agents"] = raw.get("agents", [])
    sim_data["events"] = raw.get("events", {})
    sim_data["items"] = raw.get("items", {})
    sim_data["conditions"] = raw.get("conditions", {})
    sim_data["actions"] = raw.get("actions", {})
    sim_data["needs"] = raw.get("needs", {})
    sim_data["rules"] = raw.get("rules", [])

    return SimulationConfig(**sim_data)
