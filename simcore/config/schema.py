"""Pydantic schemas for simulation configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PersonalityConfig(BaseModel):
    openness: float = Field(0.5, ge=0.0, le=1.0)
    conscientiousness: float = Field(0.5, ge=0.0, le=1.0)
    extraversion: float = Field(0.5, ge=0.0, le=1.0)
    agreeableness: float = Field(0.5, ge=0.0, le=1.0)
    neuroticism: float = Field(0.5, ge=0.0, le=1.0)


class AgentConfig(BaseModel):
    name: str
    age: int = 30
    profession: str = "citizen"
    personality: PersonalityConfig = Field(default_factory=PersonalityConfig)
    goals: list[str] = Field(default_factory=list)
    starting_location: str = ""
    resources: dict[str, float] = Field(default_factory=dict)
    backstory: str = ""
    # 0 = dead, 1 = fully healthy. Below ~0.3 the agent knows it is badly hurt.
    health: float = Field(1.0, ge=0.0, le=1.0)
    # Tick at which the agent enters the world (0 = present from the start).
    arrives_at: int = Field(0, ge=0)


class SpawnConfig(AgentConfig):
    """An agent brought in by an event; ``count`` > 1 numbers the names."""

    count: int = Field(1, ge=1, le=50)


USE_KEYS = ("health", "energy", "mood")


class ItemConfig(BaseModel):
    """An object agents can carry, give, trade and use."""

    description: str = ""
    # Consumed when used (food, medicine); otherwise it is kept (a tool).
    consumable: bool = True
    # Change applied to whoever the item is used on: {health: 0.4, energy: 0.2, mood: 0.1}.
    on_use: dict[str, float] = Field(default_factory=dict)

    @field_validator("on_use")
    @classmethod
    def _check_on_use(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = set(value) - set(USE_KEYS)
        if unknown:
            raise ValueError(f"on_use only accepts {', '.join(USE_KEYS)} (got {', '.join(sorted(unknown))})")
        return {k: max(-1.0, min(1.0, float(v))) for k, v in value.items()}


def _non_negative(value: dict[str, float]) -> dict[str, float]:
    bad = [k for k, v in value.items() if v < 0]
    if bad:
        raise ValueError(f"amounts must be >= 0 ({', '.join(bad)})")
    return value


class LocationConfig(BaseModel):
    name: str
    type: str = "generic"
    position: tuple[int, int] = (0, 0)
    capacity: int = 50
    properties: dict[str, object] = Field(default_factory=dict)
    city: str = ""
    virtual: bool = False
    # Items lying here that anyone can take.
    stock: dict[str, float] = Field(default_factory=dict)
    # Items added to the inventory of whoever works here, per work action.
    produces: dict[str, float] = Field(default_factory=dict)

    @field_validator("stock", "produces")
    @classmethod
    def _check_amounts(cls, value: dict[str, float]) -> dict[str, float]:
        return _non_negative(value)


class EnvironmentConfig(BaseModel):
    type: str = "grid"
    size: tuple[int, int] = (20, 20)
    locations: list[LocationConfig] = Field(default_factory=list)
    global_state: dict[str, object] = Field(default_factory=dict)


class ScheduledEventConfig(BaseModel):
    tick: int
    type: str
    description: str
    effects: dict[str, object] = Field(default_factory=dict)
    translations: dict[str, str] = Field(default_factory=dict)

    @field_validator("effects")
    @classmethod
    def _check_effects(cls, value: dict[str, object]) -> dict[str, object]:
        from simcore.engine.effects import parse_effects
        parse_effects(value)
        return value


class EventsConfig(BaseModel):
    scheduled: list[ScheduledEventConfig] = Field(default_factory=list)
    injectable: bool = True


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    reflection_model: str | None = None
    cache: bool = True
    temperature: float = 0.7
    max_tokens: int = 500
    # Penalize repetition. 0.0 = off, 0.5-1.5 typical. Helps small local models.
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0


class SimulationConfig(BaseModel):
    """Root configuration for a SimCore simulation."""

    name: str = "Untitled Simulation"
    description: str = ""
    time_step: str = "1 hour"
    duration: str = "7 days"
    seed: int = 42
    # Language that agents should use when speaking/writing content.
    # ISO 639-1 code (e.g. "en", "pt", "es"). Can be overridden at runtime.
    language: str = "en"
    llm: LLMConfig = Field(default_factory=LLMConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    agents: list[AgentConfig] = Field(default_factory=list)
    events: EventsConfig = Field(default_factory=EventsConfig)
    # Optional item catalog; items not listed here still work, without description or use.
    items: dict[str, ItemConfig] = Field(default_factory=dict)
