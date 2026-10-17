"""Core Agent class — the autonomous entity in a simulation."""

from __future__ import annotations

from dataclasses import dataclass, field

from simcore.agents.actions import Action, ActionType
from simcore.agents.memory import MemorySystem
from simcore.agents.persona import Persona


@dataclass
class AgentState:
    """Mutable state of an agent that changes each tick."""

    location: str = ""
    mood: float = 0.0  # -1.0 to 1.0
    energy: float = 1.0  # 0.0 to 1.0
    resources: dict[str, float] = field(default_factory=dict)
    current_action: Action | None = None
    custom: dict[str, object] = field(default_factory=dict)
    health: float = 1.0  # 0.0 (dead) to 1.0
    status: str = "alive"  # alive | dead | departed


class Agent:
    """An autonomous agent in the simulation."""

    def __init__(self, persona: Persona, state: AgentState | None = None,
                 memory_config: dict | None = None, agent_id: str | None = None):
        self.persona = persona
        self.state = state or AgentState()
        self.memory = MemorySystem(**(memory_config or {}))
        self.id = agent_id or persona.name.lower().replace(" ", "_")
        self._action_history: list[Action] = []

    @property
    def alive(self) -> bool:
        return self.state.status == "alive"

    @property
    def name(self) -> str:
        return self.persona.name

    @property
    def location(self) -> str:
        return self.state.location

    @location.setter
    def location(self, value: str) -> None:
        self.state.location = value

    def record_action(self, action: Action, tick: int) -> None:
        """Record an action in history and memory."""
        self._action_history.append(action)
        if len(self._action_history) > 100:
            self._action_history.pop(0)
        self.memory.add(
            tick=tick,
            content=f"I {action.describe()}",
            importance=0.3 if action.type == ActionType.WAIT else 0.5,
        )

    def observe(self, observation: str, tick: int, importance: float = 0.5) -> None:
        """Record an observation in memory."""
        self.memory.add(tick=tick, content=observation, importance=importance)

    def build_prompt(self, observation: str, language: str = "en") -> str:
        """Build the full prompt for LLM decision-making."""
        from simcore.agents.actions import ActionType
        from simcore.llm.prompts import language_directive
        memory_context = self.memory.get_context()
        persona_prompt = self.persona.to_prompt()

        if self.state.energy < 0.15:
            energy_desc = "CRITICAL — you are exhausted, you MUST rest this turn"
        elif self.state.energy < 0.3:
            energy_desc = "low — consider resting soon"
        elif self.state.energy > 0.6:
            energy_desc = "high"
        else:
            energy_desc = "moderate"
        if self.state.health < 0.3:
            health_desc = "CRITICAL — you are badly hurt or sick and could die; get help or treatment"
        elif self.state.health < 0.6:
            health_desc = "hurt — you are injured or unwell"
        elif self.state.health < 0.9:
            health_desc = "minor injuries"
        else:
            health_desc = "good"
        state_desc = (
            f"Current state: You are at {self.state.location}. "
            f"Mood: {'positive' if self.state.mood > 0.2 else 'negative' if self.state.mood < -0.2 else 'neutral'}. "
            f"Energy: {energy_desc}. Health: {health_desc}."
        )
        if self.state.resources:
            resources_str = ", ".join(f"{k}: {v}" for k, v in self.state.resources.items())
            state_desc += f" Resources: {resources_str}."

        # Pull last 3 dialogue lines THIS agent said, to discourage repetition.
        recent_lines = []
        recent_targets = set()
        for a in reversed(self._action_history[-12:]):
            if a.type in (ActionType.SPEAK, ActionType.TRADE, ActionType.INTERACT) and a.content:
                recent_lines.append(f'- "{a.content}"')
                if a.target:
                    recent_targets.add(a.target)
                if len(recent_lines) >= 3:
                    break
        recent_dialogue = ""
        if recent_lines:
            recent_dialogue = (
                "Things you said recently (DO NOT repeat these phrases — speak with fresh wording):\n"
                + "\n".join(reversed(recent_lines))
            )
        if recent_targets:
            already = ", ".join(sorted(recent_targets))
            recent_dialogue += (
                f"\n\nYou ALREADY interacted recently with: {already}. "
                f"If you speak to any of them again, DO NOT start with a greeting — go straight to the point."
            )

        return f"""{persona_prompt}

{state_desc}

{memory_context}

{recent_dialogue}

Current situation:
{observation}

Decide your next action. Respond in this exact JSON format:
{{
    "action": "move|speak|trade|interact|wait|observe|work|rest",
    "target": "<see target rules below>",
    "content": "<what you say or do>",
    "reasoning": "<brief internal thought>"
}}

Target rules (strict):
- For "move": target MUST be the exact name of an available location (not a person).
- For "speak", "trade", "interact": target MUST be the exact name of a real person listed in "People here" above. NEVER a location name, shop name, building, or invented person. If "People here" is empty or "You are alone", you CANNOT speak/trade/interact — choose move, observe, work, or rest instead.
- For "observe", "work", "rest", "wait": target can be empty or a brief noun (e.g., "surroundings").

{language_directive(language)}"""

    def apply_action(self, action: Action, tick: int) -> None:
        """Apply an action's effects on the agent's state."""
        self.state.current_action = action
        self.record_action(action, tick)

        match action.type:
            case ActionType.MOVE:
                self.state.location = action.target
            case ActionType.REST:
                self.state.energy = min(1.0, self.state.energy + 0.3)
                self.state.mood = min(1.0, self.state.mood + 0.05)
                # Resting slowly heals light wounds; serious ones need treatment.
                if 0.3 <= self.state.health < 1.0:
                    self.state.health = min(1.0, self.state.health + 0.02)
            case ActionType.WORK:
                self.state.energy = max(0.0, self.state.energy - 0.2)
            case ActionType.SPEAK | ActionType.INTERACT:
                self.state.mood = min(1.0, self.state.mood + 0.03)

    def apply_tick_decay(self) -> None:
        """Apply per-tick passive decay: energy drains, mood drifts toward neutral."""
        self.state.energy = max(0.0, self.state.energy - 0.008)
        # Slow drift toward 0 — isolation or routine pulls mood to neutral
        if self.state.mood > 0:
            self.state.mood = max(0.0, self.state.mood - 0.004)
        elif self.state.mood < 0:
            self.state.mood = min(0.0, self.state.mood + 0.004)

    def to_dict(self) -> dict:
        """Serialize agent state for storage/streaming."""
        relationships = {
            name: rel.describe()
            for name, rel in self.memory.relationships.items()
        }
        return {
            "id": self.id,
            "name": self.name,
            "profession": self.persona.profession,
            "location": self.state.location,
            "status": self.state.status,
            "personality": {
                "openness": self.persona.personality.openness,
                "conscientiousness": self.persona.personality.conscientiousness,
                "extraversion": self.persona.personality.extraversion,
                "agreeableness": self.persona.personality.agreeableness,
                "neuroticism": self.persona.personality.neuroticism,
            },
            "state": {
                "mood": self.state.mood,
                "energy": self.state.energy,
                "health": self.state.health,
                "status": self.state.status,
                "location": self.state.location,
                "current_action": self.state.current_action.describe() if self.state.current_action else None,
                "resources": self.state.resources,
            },
            "relationships": relationships,
            "memory_summary": self.memory.get_context(max_entries=5),
        }
