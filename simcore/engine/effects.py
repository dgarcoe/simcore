"""Effects — what a scheduled or injected event changes in the world.

An event's ``effects`` mapping mixes two kinds of keys:

* reserved keys that act on agents::

    effects:
      health: {"Frank": -0.5, "location:Clinic": -0.2, "all": -0.05}
      kill: ["Rosa"]                 # or "location:Mine", "all"
      depart: "Hank"                 # leaves the simulation alive
      spawn:                         # new agents arrive
        - {name: "Rescue worker", profession: "paramedic",
           starting_location: "Plaza", count: 2}

* any other key is written to the world's ``global_state``, which every
  agent sees (``school_closed: true``, ``oxygen: 0.4``).

Targets are an agent name, ``location:<name>`` (everyone there) or ``all``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simcore.config.schema import SpawnConfig

RESERVED_KEYS = frozenset({"health", "kill", "depart", "spawn"})
LOCATION_PREFIX = "location:"
ALL = "all"


@dataclass
class Effects:
    global_updates: dict[str, Any] = field(default_factory=dict)
    health: dict[str, float] = field(default_factory=dict)
    kill: list[str] = field(default_factory=list)
    depart: list[str] = field(default_factory=list)
    spawn: list[SpawnConfig] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.global_updates or self.health or self.kill or self.depart or self.spawn)


def _targets(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ValueError(f"effects.{key} must be a name or a list of names")


def parse_effects(raw: dict[str, Any] | None) -> Effects:
    """Validate an effects mapping. Raises ValueError with a readable message."""
    if not raw:
        return Effects()
    if not isinstance(raw, dict):
        raise ValueError("effects must be a mapping")
    out = Effects()
    for key, value in raw.items():
        if key not in RESERVED_KEYS:
            out.global_updates[key] = value
            continue
        if key == "health":
            if not isinstance(value, dict):
                raise ValueError("effects.health must map targets to a change, e.g. {Frank: -0.5}")
            for target, delta in value.items():
                if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                    raise ValueError(f"effects.health.{target} must be a number between -1 and 1")
                out.health[str(target)] = max(-1.0, min(1.0, float(delta)))
        elif key == "kill":
            out.kill = _targets(value, key)
        elif key == "depart":
            out.depart = _targets(value, key)
        elif key == "spawn":
            items = value if isinstance(value, list) else [value]
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("effects.spawn entries must be agent definitions")
                out.spawn.append(SpawnConfig.model_validate(item))
    return out
