"""Location — a place in the simulation world."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Location:
    """A named location in the environment."""

    name: str
    type: str = "generic"
    position: tuple[int, int] = (0, 0)
    capacity: int = 50
    city: str = ""
    virtual: bool = False
    properties: dict[str, object] = field(default_factory=dict)
    stock: dict[str, float] = field(default_factory=dict)
    produces: dict[str, float] = field(default_factory=dict)
    closed: bool = False
    access: object | None = None  # simcore.config.schema.Requirement
    hazard: object | None = None  # simcore.config.schema.HazardConfig
    _occupants: set[str] = field(default_factory=set, repr=False)

    @property
    def occupant_count(self) -> int:
        return len(self._occupants)

    @property
    def is_full(self) -> bool:
        return self.occupant_count >= self.capacity

    def add_occupant(self, agent_id: str) -> bool:
        if self.is_full:
            return False
        self._occupants.add(agent_id)
        return True

    def remove_occupant(self, agent_id: str) -> None:
        self._occupants.discard(agent_id)

    def get_occupants(self) -> list[str]:
        return sorted(self._occupants)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "position": list(self.position),
            "capacity": self.capacity,
            "city": self.city,
            "virtual": self.virtual,
            "occupants": self.get_occupants(),
            "occupant_count": self.occupant_count,
            "properties": self.properties,
            "stock": self.stock,
            "produces": self.produces,
            "closed": self.closed,
            "access": self.access.model_dump(exclude_defaults=True) if self.access is not None else None,
            "hazard": self.hazard.model_dump() if self.hazard is not None else None,
        }
