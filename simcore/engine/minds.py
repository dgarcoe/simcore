"""Daily plans, beliefs / secrets / rumors and typed relationships. Mixed into SimulationEngine."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import AgentConfig, BeliefConfig
from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent

logger = logging.getLogger("simcore")

# Relationship kinds whose loss hits hard, and that make relatives take sides.
FAMILY = re.compile(
    r"brother|sister|sibling|mother|father|parent|son|daughter|child|famil|partner|wife|husband|spouse|"
    r"lover|herman|madre|padre|hij|famil|pareja|espos|marido|mujer|novi|irm[aã]|m[aã]e|pai|filh|esposa",
    re.IGNORECASE,
)
FRIEND = re.compile(r"friend|amig|best", re.IGNORECASE)
MAX_BELIEFS = 12


def _norm(text: str) -> str:
    return re.sub(r"[^\w ]", "", text.lower()).strip()


def _same_belief(a: str, b: str) -> bool:
    x, y = _norm(a), _norm(b)
    return bool(x and y) and (x == y or (len(y) > 12 and y in x) or (len(x) > 12 and x in y))


class MindsMixin:
    """Needs the engine's config, agents, llm, event_bus, _rng and helpers."""

    # ── Setup ───────────────────────────────────────────────────────────

    def _init_mind(self, agent: Agent, cfg: AgentConfig, tick: int = 0) -> None:
        for name, rel in cfg.relationships.items():
            r = agent.memory.update_relationship(name, rel.sentiment, tick, rel.type)
            r.kind = rel.type
            r.interactions = 0
        for b in cfg.beliefs:
            belief = b if isinstance(b, BeliefConfig) else BeliefConfig(text=str(b))
            agent.state.beliefs.append({"text": belief.text, "source": "", "truth": belief.true,
                                        "secret": False, "tick": tick})
        for s in cfg.secrets:
            agent.state.beliefs.append({"text": s, "source": "", "truth": True, "secret": True, "tick": tick})

    # ── Prompt ──────────────────────────────────────────────────────────

    def _minds_prompt(self, agent: Agent) -> str:
        lines = []
        st = agent.state
        if st.plan:
            steps = "; ".join(f"{i + 1}. {s}" for i, s in enumerate(st.plan))
            current = st.plan[st.plan_step] if st.plan_step < len(st.plan) else "(all done)"
            lines.append(f"Your plan for today: {steps}. Current step: {current}. "
                         "Follow it unless something more important happens; set \"plan_step_done\": true when done.")
        secrets = [b for b in st.beliefs if b.get("secret")]
        heard = [b for b in st.beliefs if not b.get("secret")][-MAX_BELIEFS:]
        if heard:
            lines.append("What you believe (not necessarily true):")
            for b in heard:
                src = f" (told by {b['source']})" if b.get("source") and b["source"] != "rumor" else (
                    " (a rumor going around)" if b.get("source") == "rumor" else "")
                lines.append(f"- {b['text']}{src}")
        if secrets:
            lines.append("Your secrets (you decide whether to reveal them, with \"share\"):")
            lines.extend(f"- {b['text']}" for b in secrets)
        return "\n".join(lines)

    # ── Plans ───────────────────────────────────────────────────────────

    async def _maybe_plan(self, agent: Agent, observation: str, tick: int, lang: str) -> None:
        if not self.config.planning or not hasattr(self.llm, "plan"):
            return
        st = agent.state
        due = not st.plan or tick - st.plan_tick >= self.config.plan_every or st.plan_step >= len(st.plan)
        if not (due or st.replan):
            return
        reason = "something important happened" if st.replan and not due else "new day" if st.plan else "first plan"
        persona = agent.persona.to_prompt()
        goals = "; ".join(agent.persona.goals)
        steps = await self.llm.plan(persona, f"Your goals: {goals}\n\nCurrent situation:\n{observation}", lang)
        st.replan = False
        if not steps:
            return
        st.plan, st.plan_step, st.plan_tick = steps, 0, tick
        agent.observe(f"My plan: {'; '.join(steps)}", tick, importance=0.6)
        await self.event_bus.emit(SimEvent(
            type=EventType.AGENT_PLAN, tick=tick,
            data={"agent": agent.name, "steps": steps, "reason": reason},
            source=agent.name,
        ))

    def _advance_plan(self, agent: Agent, action: Action) -> None:
        st = agent.state
        if action.plan_step_done and st.plan and st.plan_step < len(st.plan):
            st.plan_step += 1

    def _wake(self, agent: Agent) -> None:
        """Something important happened: replan on the next turn."""
        if self.config.planning:
            agent.state.replan = True

    # ── Beliefs, secrets and rumors ────────────────────────────────────

    async def _learn(self, agent: Agent, text: str, source: str, truth: bool | None, tick: int,
                     secret: bool = False) -> None:
        text = text.strip()
        if not text or not agent.alive:
            return
        known = next((b for b in agent.state.beliefs if _same_belief(b["text"], text)), None)
        if known is None:
            agent.state.beliefs.append({"text": text, "source": source, "truth": truth,
                                        "secret": False, "tick": tick})
            # keep secrets plus the most recent things heard
            secrets = [b for b in agent.state.beliefs if b.get("secret")]
            heard = [b for b in agent.state.beliefs if not b.get("secret")][-MAX_BELIEFS * 2:]
            agent.state.beliefs = secrets + heard
        teller = "a rumor" if source == "rumor" else source
        agent.observe(f"{teller} told me: {text}", tick, importance=0.8)
        self._wake(agent)
        await self.event_bus.emit(SimEvent(
            type=EventType.BELIEF_SHARED, tick=tick,
            data={"from": "" if source == "rumor" else source, "to": agent.name, "text": text,
                  "truth": truth, "secret": secret},
            source=source if source != "rumor" else agent.name,
        ))

    async def _share(self, speaker: Agent, listener: Agent, text: str, tick: int) -> None:
        own = next((b for b in speaker.state.beliefs if _same_belief(b["text"], text)), None)
        truth = own.get("truth") if own else None  # made up on the spot: unknown (maybe a lie)
        secret = bool(own and own.get("secret"))
        await self._learn(listener, text, speaker.name, truth, tick, secret=secret)

    async def _after_action_minds(self, agent: Agent, action: Action, tick: int) -> None:
        self._advance_plan(agent, action)
        if action.share and action.type in (ActionType.SPEAK, ActionType.INTERACT, ActionType.TRADE,
                                            ActionType.GIVE):
            for other in self._addressees(agent, action):
                await self._share(agent, other, action.share, tick)

    # ── Relationships ──────────────────────────────────────────────────

    def _kind(self, agent: Agent, other_name: str) -> str:
        rel = agent.memory.relationships.get(other_name)
        return rel.kind if rel else ""

    async def _grieve(self, dead: Agent, tick: int, cause: str) -> None:
        """Close ones learn of a death even if they weren't there; how much it hurts depends on the bond."""
        for other in self.living_agents:
            rel = other.memory.relationships.get(dead.name)
            if not rel or other.location == dead.location:
                continue  # witnesses already reacted in kill()
            if FAMILY.search(rel.kind):
                other.state.mood = max(-1.0, other.state.mood - 0.5)
                other.observe(f"Terrible news: my {rel.kind} {dead.name} has died{f' ({cause})' if cause else ''}.",
                              tick, importance=1.0)
                self._wake(other)
            elif FRIEND.search(rel.kind) or rel.sentiment > 0.5:
                other.state.mood = max(-1.0, other.state.mood - 0.2)
                other.observe(f"I heard {dead.name} has died.", tick, importance=0.8)
                self._wake(other)

    def _witness_grief(self, witness: Agent, dead: Agent) -> float:
        """Extra mood loss for a witness, by relationship (on top of the base shock)."""
        kind = self._kind(witness, dead.name)
        if FAMILY.search(kind):
            return 0.4
        if FRIEND.search(kind):
            return 0.15
        return 0.0

    async def _on_harm(self, attacker: Agent, victim: Agent, tick: int, what: str) -> None:
        """Hurting someone turns their family against the attacker."""
        for other in self.living_agents:
            if other is attacker or other is victim:
                continue
            kind = self._kind(other, victim.name)
            if kind and FAMILY.search(kind):
                other.memory.update_relationship(attacker.name, -0.4, tick, f"{what} my {kind}")
                other.observe(f"{attacker.name} {what} my {kind} {victim.name}.", tick, importance=0.95)
                self._wake(other)
