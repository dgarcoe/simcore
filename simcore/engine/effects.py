"""Effects — what a scheduled or injected event changes in the world.

An event's ``effects`` mapping mixes two kinds of keys:

* reserved keys that act on agents::

    effects:
      health: {"Frank": -0.5, "location:Clinic": -0.2, "all": -0.05}
      kill: ["Rosa"]                 # or "location:Mine", "all"
      depart: "Hank"                 # leaves the simulation alive
      items: {"location:Clinic": {medicine: 10}, "Tomás": {food: -2}}
      add_condition: {"Tomás": infected}             # scenario conditions
      remove_condition: {"all": [panicked]}
      needs: {"Ana": {hunger: -0.5}}                  # scenario needs
      locations: {"Church": {closed: true, hazard: {label: on fire, per_tick: {health: -0.1}}}}
      adjust: {oxygen: -0.1}                          # add to numeric world variables
      end: "The colony has fallen"                    # ends the run
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

RESERVED_KEYS = frozenset({"health", "kill", "depart", "spawn", "items", "add_condition",
                           "remove_condition", "needs", "locations", "adjust", "end"})
LOCATION_KEYS = frozenset({"closed", "hazard", "access"})
LOCATION_PREFIX = "location:"
ALL = "all"


@dataclass
class Effects:
    global_updates: dict[str, Any] = field(default_factory=dict)
    health: dict[str, float] = field(default_factory=dict)
    kill: list[str] = field(default_factory=list)
    depart: list[str] = field(default_factory=list)
    spawn: list[SpawnConfig] = field(default_factory=list)
    # target → {item: change}; location targets change the place's stock.
    items: dict[str, dict[str, float]] = field(default_factory=dict)
    # target → condition names
    add_condition: dict[str, list[str]] = field(default_factory=dict)
    remove_condition: dict[str, list[str]] = field(default_factory=dict)
    needs: dict[str, dict[str, float]] = field(default_factory=dict)
    # location name → {closed, hazard (dict or None to remove), access (dict or None)}
    locations: dict[str, dict[str, Any]] = field(default_factory=dict)
    adjust: dict[str, float] = field(default_factory=dict)
    end: str = ""

    @property
    def empty(self) -> bool:
        return not (self.global_updates or self.health or self.kill or self.depart or self.spawn
                    or self.items or self.add_condition or self.remove_condition or self.needs
                    or self.locations or self.adjust or self.end)


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
        elif key == "items":
            if not isinstance(value, dict):
                raise ValueError("effects.items must map targets to items, e.g. {Ana: {food: 2}}")
            for target, changes in value.items():
                if not isinstance(changes, dict) or not all(
                    isinstance(n, (int, float)) and not isinstance(n, bool) for n in changes.values()
                ):
                    raise ValueError(f"effects.items.{target} must map item names to numbers")
                out.items[str(target)] = {str(k): float(n) for k, n in changes.items()}
        elif key == "needs":
            if not isinstance(value, dict) or not all(isinstance(v, dict) for v in value.values()):
                raise ValueError("effects.needs must map targets to needs, e.g. {Ana: {hunger: -0.5}}")
            for target, changes in value.items():
                if not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in changes.values()):
                    raise ValueError(f"effects.needs.{target} must map need names to numbers")
                out.needs[str(target)] = {str(k): float(n) for k, n in changes.items()}
        elif key == "locations":
            if not isinstance(value, dict) or not all(isinstance(v, dict) for v in value.values()):
                raise ValueError("effects.locations must map place names to changes, e.g. {Church: {closed: true}}")
            from simcore.config.schema import HazardConfig, Requirement
            for place, change in value.items():
                unknown = set(change) - LOCATION_KEYS
                if unknown:
                    raise ValueError(f"effects.locations.{place} only accepts {', '.join(sorted(LOCATION_KEYS))}")
                if "closed" in change and not isinstance(change["closed"], bool):
                    raise ValueError(f"effects.locations.{place}.closed must be true or false")
                if change.get("hazard") is not None:
                    HazardConfig.model_validate(change["hazard"])
                if change.get("access") is not None:
                    Requirement.model_validate(change["access"])
                out.locations[str(place)] = dict(change)
        elif key == "adjust":
            if not isinstance(value, dict) or not all(
                    isinstance(n, (int, float)) and not isinstance(n, bool) for n in value.values()):
                raise ValueError("effects.adjust must map world variables to numbers, e.g. {oxygen: -0.1}")
            out.adjust = {str(k): float(n) for k, n in value.items()}
        elif key == "end":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("effects.end must be the text explaining why the run ends")
            out.end = value.strip()
        elif key in ("add_condition", "remove_condition"):
            if not isinstance(value, dict):
                raise ValueError(f"effects.{key} must map targets to conditions, e.g. {{Ana: infected}}")
            getattr(out, key).update({str(t): _targets(v, f"{key}.{t}") for t, v in value.items()})
        elif key == "spawn":
            items = value if isinstance(value, list) else [value]
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("effects.spawn entries must be agent definitions")
                out.spawn.append(SpawnConfig.model_validate(item))
    return out
