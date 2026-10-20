"""Core Agent class — the autonomous entity in a simulation."""

from __future__ import annotations

from dataclasses import dataclass, field

from simcore.agents.actions import Action, ActionType, fmt_amount
from simcore.agents.memory import MemorySystem
from simcore.agents.persona import Persona

ALL_ACTIONS = ("move|speak|whisper|invite|follow|find|interact|wait|observe|work|rest|give|take|drop|"
               "use|trade|accept|decline|attack|heal|found_group|join|leave|expel|call_vote|vote")
# What a mind without speech can't do (see simcore.config.schema.MindConfig).
SPEECH_ACTION_NAMES = frozenset({"speak", "whisper", "invite", "interact", "trade", "give", "accept",
                                 "decline", "post", "found_group", "join", "expel", "call_vote", "vote"})


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
    # Scenario conditions → ticks left (None = until something removes it).
    conditions: dict[str, int | None] = field(default_factory=dict)
    # Scenario needs → level 0..1 (hunger, sleep…).
    needs: dict[str, float] = field(default_factory=dict)
    # What the agent believes: {text, source, truth (True/False/None), secret, tick}.
    beliefs: list[dict] = field(default_factory=list)
    # Daily plan: steps, the current one, when it was made, and whether to redo it.
    plan: list[str] = field(default_factory=list)
    plan_step: int = 0
    plan_tick: int = -1
    replan: bool = False


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

    def persona_prompt(self, mind=None) -> str:
        """Who the agent is; a condition's mind (simcore.config.schema.MindConfig) replaces it."""
        if mind is None:
            return self.persona.to_prompt()
        p = self.persona
        identity = mind.identity.replace("{name}", p.name).replace("{profession}", p.profession)
        lines = [identity or f"You are {p.name}."]
        if mind.memories:
            lines.append(f"Personality: {p.personality.describe()}.")
            if p.backstory:
                lines.append(f"Who you were: {p.backstory}")
        if mind.goals:
            lines.append(f"Your goals now: {', '.join(mind.goals)}.")
        return "\n".join(lines)

    def build_prompt(self, observation: str, language: str = "en", mind=None) -> str:
        """Build the full prompt for LLM decision-making (``mind``: a condition's MindConfig)."""
        from simcore.agents.actions import ActionType
        from simcore.llm.prompts import language_directive
        forgets = mind is not None and not mind.memories
        mute = mind is not None and not mind.speech
        memory_context = "" if forgets else self.memory.get_context()
        persona_prompt = self.persona_prompt(mind)

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
        held = {k: v for k, v in self.state.resources.items() if v > 0}
        if held:
            state_desc += f" Your inventory: {', '.join(f'{k} x{fmt_amount(v)}' for k, v in held.items())}."
        else:
            state_desc += " Your inventory: nothing."

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
        if forgets or mute:
            recent_lines, recent_targets = [], set()
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

        actions = ALL_ACTIONS
        if mind is not None:
            allowed = list(mind.actions) or ALL_ACTIONS.split("|")
            if mute:
                allowed = [a for a in allowed if a not in SPEECH_ACTION_NAMES]
            actions = "|".join(dict.fromkeys(["wait", *allowed]))
        limits = ""
        if mute:
            limits += ("\nYou cannot speak or communicate in any way: never use speak, whisper, invite, "
                       "interact, trade or give.")
        if mind is not None and mind.actions:
            limits += f"\nThe only actions you can take: {actions.replace('|', ', ')}."

        return f"""{persona_prompt}{limits}

{state_desc}

{memory_context}

{recent_dialogue}

Current situation:
{observation}

Decide your next action. Respond in this exact JSON format:
{{
    "action": "{actions}",
    "target": "<see target rules below>",
    "content": "<what you say or do>",
    "item": "<item name, only for give/take/drop/use/trade>",
    "amount": 1,
    "ask_item": "<only for trade: what you want in return>",
    "ask_amount": 1,
    "share": "<optional, with speak/interact: something you believe or a secret you decide to tell them>",
    "place": "<only for invite: where to meet; empty = a quiet place>",
    "goal": "<only for found_group: what the group is for>",
    "options": ["<only for call_vote: the choices>"],
    "emotion": "<optional: how you feel now, e.g. calm, afraid, angry, sad, happy, hopeful>",
    "intent": "<optional, with speak/whisper: persuade, deceive or intimidate>",
    "plan_step_done": false,
    "reasoning": "<brief internal thought>"
}}

Item rules:
- "give": hand "amount" of an item from your inventory to "target" (a person here).
- "take": pick up an item listed under "Items you can take here". "drop": leave one of yours here.
- "use": use an item from your inventory on yourself (empty target) or on a person here (e.g. medicine to heal them).
- "trade": offer "item" x "amount" to "target" in exchange for "ask_item" x "ask_amount". They answer later.
- "accept" / "decline": answer an offer made to you; "target" is the person who made it.
- Only use item names you actually have or that are available here.
- If "Special actions" are listed in your situation, you may use one: put its name in "action".

Target rules (strict):
- For "move": target MUST be the exact name of an available location (not a person).
- For "speak" and "interact": target is one or more people listed in "People here", e.g. "Ana" or "Ana, Bob", or "everyone" to address the whole room. Everyone here hears what you say with "speak" ("interact" is private).
- "whisper": like "speak" but only the target hears the words (others just see you whisper).
- "invite": you go to "place" and ask the target (people here) to join you there, to talk alone. "follow": go wherever the target (a person here) goes this turn.
- "attack": hurt someone here (with your best weapon, or your fists); it can kill. "heal": treat someone here (or yourself, empty target) with a healing item or your skills.
- "found_group": start a group (content: its name, goal: its purpose, target: people here you invite). "join" (target: the group), "leave" your group, "expel" (leaders only, target: a member).
- "call_vote": put a matter to the vote of the people here (content: the question, options: the choices). "vote" (content: your option) when there is a vote here.
- "find": look for someone who is NOT here (target: their name). It asks the people here or goes to the best clue; keep using it until you find them.
- For "trade", "give": target MUST be the exact name of a real person listed in "People here" above. NEVER a location name, shop name, building, or invented person. If "People here" is empty or "You are alone", you CANNOT speak/trade/interact — choose move, observe, work, or rest instead.
- If several people are waiting for your answer, answer them all in one line (target "Ana, Bob").
- Don't ask what someone here already asked or answered. If a conversation goes in circles, stop asking and act (move, work, give, use…) or say something new.
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
        kinds = {name: rel.kind for name, rel in self.memory.relationships.items() if rel.kind}
        scores = {name: round(rel.sentiment, 2) for name, rel in self.memory.relationships.items()}
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
                "conditions": dict(self.state.conditions),
                "needs": {k: round(v, 3) for k, v in self.state.needs.items()},
                "beliefs": list(self.state.beliefs),
                "plan": {"steps": list(self.state.plan), "current": self.state.plan_step} if self.state.plan else None,
                "location": self.state.location,
                "current_action": self.state.current_action.describe() if self.state.current_action else None,
                "resources": self.state.resources,
            },
            "relationships": relationships,
            "relationship_kinds": kinds,
            "relationship_scores": scores,
            "memory_summary": self.memory.get_context(max_entries=5),
        }
