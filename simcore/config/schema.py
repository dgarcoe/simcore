"""Pydantic schemas for simulation configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    # Conditions (scenario `conditions`) the agent starts with, e.g. [infected].
    conditions: list[str] = Field(default_factory=list)


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
    # Conditions removed from whoever the item is used on (an antidote cures "infected").
    cures: list[str] = Field(default_factory=list)

    @field_validator("on_use")
    @classmethod
    def _check_on_use(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = set(value) - set(USE_KEYS)
        if unknown:
            raise ValueError(f"on_use only accepts {', '.join(USE_KEYS)} (got {', '.join(sorted(unknown))})")
        return {k: max(-1.0, min(1.0, float(v))) for k, v in value.items()}


class Requirement(BaseModel):
    """Who can do something / be the target of it. Every listed check must pass."""

    conditions: list[str] = Field(default_factory=list)  # must have all of these
    not_conditions: list[str] = Field(default_factory=list)  # must have none of these
    professions: list[str] = Field(default_factory=list)  # any of these (substring match)
    items: dict[str, float] = Field(default_factory=dict)  # must carry at least this


class Outcome(BaseModel):
    """What happens to an agent: stats change, conditions, items, death."""

    health: float = Field(0.0, ge=-1.0, le=1.0)
    energy: float = Field(0.0, ge=-1.0, le=1.0)
    mood: float = Field(0.0, ge=-1.0, le=1.0)
    add_conditions: list[str] = Field(default_factory=list)
    remove_conditions: list[str] = Field(default_factory=list)
    items: dict[str, float] = Field(default_factory=dict)  # + receives, - loses
    kill: bool = False

    @property
    def empty(self) -> bool:
        return not (self.health or self.energy or self.mood or self.add_conditions
                    or self.remove_conditions or self.items or self.kill)


class ActionConfig(BaseModel):
    """A scenario-specific action (bite, shoot, barricade, heal…) the engine resolves."""

    description: str = ""
    target: Literal["agent", "none"] = "agent"  # a person here, or no target
    who: Requirement = Field(default_factory=Requirement)
    target_requires: Requirement = Field(default_factory=Requirement)
    chance: float = Field(1.0, ge=0.0, le=1.0)  # probability of success
    cost: dict[str, float] = Field(default_factory=dict)  # items spent (even on failure)
    on_target: Outcome = Field(default_factory=Outcome)
    on_self: Outcome = Field(default_factory=Outcome)
    world: dict[str, object] = Field(default_factory=dict)  # global_state updates on success
    # What the feed shows; {target} is replaced. Defaults to "<name> <target>".
    message: str = ""
    fail_message: str = ""


class ContagionConfig(BaseModel):
    chance: float = Field(0.2, ge=0.0, le=1.0)
    # interaction: when someone talks to / acts on a carrier (or the carrier on them);
    # place: every tick, to everyone at the same location as a carrier.
    via: Literal["interaction", "place"] = "interaction"


class RulesConfig(BaseModel):
    """Behaviour of agents controlled by rules instead of the LLM."""

    action: str = ""  # scenario action used on a target here (e.g. bite)
    targets: Requirement = Field(default_factory=Requirement)  # default: people without this condition
    seek: bool = True  # go where most targets are
    wander: float = Field(0.3, ge=0.0, le=1.0)  # otherwise, chance to roam


class ConditionConfig(BaseModel):
    """A state an agent can be in: infected, injured, drunk, zombie, panicked…"""

    description: str = ""  # what the agent itself is told
    appearance: str = ""  # what others see (empty: not visible)
    per_tick: dict[str, float] = Field(default_factory=dict)  # health / energy / mood each tick
    duration: int | None = Field(None, ge=1)  # ticks until it ends (or becomes `becomes`)
    becomes: str = ""
    contagion: ContagionConfig | None = None
    controller: Literal["llm", "rules"] = "llm"
    rules: RulesConfig = Field(default_factory=RulesConfig)
    color: str = ""  # tint for UIs, e.g. "#7bc950"

    @field_validator("per_tick")
    @classmethod
    def _check_per_tick(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = set(value) - set(USE_KEYS)
        if unknown:
            raise ValueError(f"per_tick only accepts {', '.join(USE_KEYS)}")
        return value


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
    # Optional states (infected, zombie…) and scenario-specific actions (bite, shoot…).
    conditions: dict[str, ConditionConfig] = Field(default_factory=dict)
    actions: dict[str, ActionConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_references(self) -> "SimulationConfig":
        conds, acts = set(self.conditions), set(self.actions)
        builtin = {"move", "speak", "trade", "interact", "wait", "observe", "work", "rest", "post",
                   "custom", "give", "take", "drop", "use", "accept", "decline"}
        problems: list[str] = []

        def need(names, where: str) -> None:
            for n in names:
                if n not in conds:
                    problems.append(f"{where} uses unknown condition '{n}'")

        for name in acts & builtin:
            problems.append(f"action '{name}' clashes with a built-in action")
        for a in self.agents:
            need(a.conditions, f"agent {a.name}")
        for name, item in self.items.items():
            need(item.cures, f"item {name}")
        for name, act in self.actions.items():
            for req in (act.who, act.target_requires):
                need(req.conditions + req.not_conditions, f"action {name}")
            for out in (act.on_target, act.on_self):
                need(out.add_conditions + out.remove_conditions, f"action {name}")
        from simcore.engine.effects import parse_effects
        for event in self.events.scheduled:
            fx = parse_effects(event.effects)
            for names in [*fx.add_condition.values(), *fx.remove_condition.values()]:
                need(names, f"event at tick {event.tick}")
            for spawn in fx.spawn:
                need(spawn.conditions, f"event at tick {event.tick}")
        for name, cond in self.conditions.items():
            if cond.becomes:
                need([cond.becomes], f"condition {name}")
            if cond.rules.action and cond.rules.action not in acts:
                problems.append(f"condition {name} uses unknown action '{cond.rules.action}'")
        if problems:
            raise ValueError("; ".join(problems))
        return self
