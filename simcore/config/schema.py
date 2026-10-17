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


class LocationConfig(BaseModel):
    name: str
    type: str = "generic"
    position: tuple[int, int] = (0, 0)
    capacity: int = 50
    properties: dict[str, object] = Field(default_factory=dict)
    city: str = ""
    virtual: bool = False


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
