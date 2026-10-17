"""Conditions (infected, zombie, panicked…), scenario actions (bite, shoot…) and
rule-controlled agents. Mixed into SimulationEngine."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from simcore.agents.actions import Action, ActionType, fmt_amount
from simcore.config.schema import ActionConfig, ConditionConfig, Outcome, Requirement
from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent

logger = logging.getLogger("simcore")

STATS = ("health", "energy", "mood")


class ConditionsMixin:
    """Needs the engine's config, agents, environment, event_bus, _rng and helpers."""

    # ── Conditions ──────────────────────────────────────────────────────

    def _condition(self, name: str) -> ConditionConfig | None:
        return self.config.conditions.get(name)

    def _controller(self, agent: Agent) -> ConditionConfig | None:
        """The rule-controlled condition driving this agent, if any (it then skips the LLM)."""
        for name in agent.state.conditions:
            cond = self._condition(name)
            if cond and cond.controller == "rules":
                return cond
        return None

    def _rule_condition_name(self, agent: Agent) -> str:
        for name in agent.state.conditions:
            cond = self._condition(name)
            if cond and cond.controller == "rules":
                return name
        return ""

    def _meets(self, agent: Agent, req: Requirement) -> bool:
        held = agent.state.conditions
        if any(c not in held for c in req.conditions):
            return False
        if any(c in held for c in req.not_conditions):
            return False
        if req.professions:
            prof = agent.persona.profession.lower()
            if not any(p.lower() in prof for p in req.professions):
                return False
        return all(agent.state.resources.get(k, 0) + 1e-9 >= v for k, v in req.items.items())

    async def _condition_event(self, agent: Agent, name: str, change: str, tick: int,
                               cause: str = "", into: str = "") -> None:
        await self.event_bus.emit(SimEvent(
            type=EventType.CONDITION_CHANGE, tick=tick,
            data={"agent": agent.name, "condition": name, "change": change, "into": into,
                  "cause": cause, "location": agent.location},
            source=agent.name,
        ))

    def _immune(self, agent: Agent, name: str) -> bool:
        """Already has it, or already turned into what it leads to (a zombie can't get infected)."""
        if name in agent.state.conditions:
            return True
        seen, current = set(), self._condition(name)
        while current and current.becomes and current.becomes not in seen:
            seen.add(current.becomes)
            if current.becomes in agent.state.conditions:
                return True
            current = self._condition(current.becomes)
        return False

    async def add_condition(self, agent: Agent, name: str, tick: int, cause: str = "",
                            silent: bool = False) -> bool:
        cond = self._condition(name)
        if cond is None:
            logger.warning(f"Unknown condition '{name}'")
            return False
        if not agent.alive or self._immune(agent, name):
            return False
        agent.state.conditions[name] = cond.duration
        if not silent:
            text = cond.description or name
            agent.observe(f"Something changed in me: {text}{f' ({cause})' if cause else ''}.", tick, importance=0.9)
            await self._condition_event(agent, name, "added", tick, cause)
        return True

    async def remove_condition(self, agent: Agent, name: str, tick: int, cause: str = "") -> bool:
        if name not in agent.state.conditions:
            return False
        del agent.state.conditions[name]
        agent.observe(f"I'm no longer {name}{f' ({cause})' if cause else ''}.", tick, importance=0.8)
        await self._condition_event(agent, name, "removed", tick, cause)
        return True

    async def _condition_tick(self, tick: int) -> None:
        """Per-tick effects, countdowns, transformations and contagion by place."""
        for agent in list(self.living_agents):
            for name in list(agent.state.conditions):
                cond = self._condition(name)
                if cond is None:
                    continue
                for stat, delta in cond.per_tick.items():
                    low = -1.0 if stat == "mood" else 0.0
                    setattr(agent.state, stat, max(low, min(1.0, getattr(agent.state, stat) + delta)))
                left = agent.state.conditions.get(name)
                if left is not None:
                    left -= 1
                    agent.state.conditions[name] = left
                    if left <= 0:
                        del agent.state.conditions[name]
                        if cond.becomes:
                            await self._transform(agent, name, cond.becomes, tick)
                        else:
                            agent.observe(f"I'm no longer {name}.", tick, importance=0.7)
                            await self._condition_event(agent, name, "removed", tick, "it wore off")
            if agent.state.health <= 0:
                await self.kill(agent, tick, ", ".join(agent.state.conditions) or "illness")

        # Contagion through being in the same place.
        for name, cond in self.config.conditions.items():
            if not cond.contagion or cond.contagion.via != "place":
                continue
            carriers = [a for a in self.living_agents if name in a.state.conditions]
            for carrier in carriers:
                for other in self._witnesses(carrier):
                    if not self._immune(other, name) and self._rng.random() < cond.contagion.chance:
                        await self.add_condition(other, name, tick, f"caught from {carrier.name}")

    async def _transform(self, agent: Agent, old: str, new: str, tick: int) -> None:
        cond = self._condition(new)
        agent.state.conditions[new] = cond.duration if cond else None
        agent.observe(f"I have become {new}.", tick, importance=1.0)
        for other in self._witnesses(agent):
            other.observe(f"{agent.name} has turned into {new}!", tick, importance=0.95)
        await self._condition_event(agent, old, "transformed", tick, into=new)

    async def _spread_by_interaction(self, a: Agent, b: Agent, tick: int) -> None:
        for x, y in ((a, b), (b, a)):
            for name in list(x.state.conditions):
                cond = self._condition(name)
                if (cond and cond.contagion and cond.contagion.via == "interaction"
                        and not self._immune(y, name) and self._rng.random() < cond.contagion.chance):
                    await self.add_condition(y, name, tick, f"caught from {x.name}")

    # ── Prompt pieces ──────────────────────────────────────────────────

    def _conditions_prompt(self, agent: Agent) -> str:
        lines = []
        own = []
        for name, left in agent.state.conditions.items():
            cond = self._condition(name)
            text = name + (f" — {cond.description}" if cond and cond.description else "")
            if left is not None and cond and cond.becomes:
                text += f" (in {left} ticks you will become {cond.becomes})"
            own.append(text)
        if own:
            lines.append("Your conditions: " + "; ".join(own) + ".")
        seen = []
        for other in self._witnesses(agent):
            looks = [self._condition(n).appearance for n in other.state.conditions
                     if self._condition(n) and self._condition(n).appearance]
            if looks:
                seen.append(f"{other.name} ({', '.join(looks)})")
        if seen:
            lines.append("You notice: " + "; ".join(seen) + ".")
        actions = self._available_actions(agent)
        if actions:
            lines.append("Special actions you can do now (use the name as \"action\"):")
            for name, act in actions:
                tgt = "target: a person here" if act.target == "agent" else "no target"
                cost = ", ".join(f"{k} x{fmt_amount(v)}" for k, v in act.cost.items())
                lines.append(f"- {name} ({tgt}{f'; costs {cost}' if cost else ''}): {act.description or name}")
        return "\n".join(lines)

    def _available_actions(self, agent: Agent) -> list[tuple[str, ActionConfig]]:
        out = []
        for name, act in self.config.actions.items():
            if not self._meets(agent, act.who):
                continue
            if any(agent.state.resources.get(k, 0) + 1e-9 < v for k, v in act.cost.items()):
                continue
            if act.target == "agent" and not any(self._meets(o, act.target_requires)
                                                 for o in self._witnesses(agent)):
                continue
            out.append((name, act))
        return out

    # ── Scenario actions ──────────────────────────────────────────────

    async def _apply_custom_action(self, agent: Agent, action: Action, tick: int) -> Action:
        name = str(action.parameters.get("name", ""))
        act = self.config.actions.get(name)
        if act is None:
            if name:  # the model named an action that doesn't exist
                return Action(type=ActionType.WAIT, reasoning=f"Parse error: '{name}' is not a valid action")
            return action  # free-form custom text, as before
        action.content = ""

        def refuse(why: str) -> Action:
            agent.observe(f"I tried to {name} but couldn't: {why}.", tick, importance=0.5)
            return Action(type=ActionType.WAIT, reasoning=f"Couldn't {name}: {why}. {action.reasoning}".strip())

        if not self._meets(agent, act.who):
            return refuse("I'm not able to")
        target = None
        if act.target == "agent":
            target = self._present(agent, action.target)
            if not target:
                return refuse(f"{action.target or 'nobody'} is not here")
            if not self._meets(target, act.target_requires):
                return refuse(f"it can't be done to {target.name}")
        for item, n in act.cost.items():
            if agent.state.resources.get(item, 0) + 1e-9 < n:
                return refuse(f"I need {fmt_amount(n)} {item}")
        for item, n in act.cost.items():
            agent.state.resources[item] = agent.state.resources.get(item, 0) - n
            if agent.state.resources[item] <= 1e-9:
                agent.state.resources.pop(item)

        success = self._rng.random() < act.chance
        tname = target.name if target else ""
        template = (act.message if success else act.fail_message) or (
            f"{name} {tname}".strip() if success else f"tries to {name} {tname} but fails".strip())
        action.content = template.replace("{target}", tname).replace("{actor}", agent.name)
        action.target = tname
        action.parameters = {**action.parameters, "success": success}
        cause = f"{agent.name} {action.content}"  # e.g. "Ramón muerde a Mateo", in the scenario's words
        if success:
            if target:
                await self._apply_outcome(target, act.on_target, tick, cause)
                target.observe(f"{agent.name} did '{name}' to me.", tick, importance=0.9)
                target.memory.update_relationship(agent.name, -0.1 if act.on_target.health < 0 else 0.05,
                                                  tick, name)
            elif not act.on_target.empty:
                await self._apply_outcome(agent, act.on_target, tick, cause)
            await self._apply_outcome(agent, act.on_self, tick, cause)
            if act.world:
                self.environment.global_state.update(act.world)
                await self.event_bus.emit(SimEvent(
                    type=EventType.ENVIRONMENT_CHANGE, tick=tick,
                    data={"global_state": dict(act.world), "cause": f"{agent.name}: {name}"},
                ))
        for other in self._witnesses(agent):
            if other is not target:
                other.observe(f"{agent.name} {action.content}.", tick, importance=0.6)
        return action

    async def _apply_outcome(self, agent: Agent, out: Outcome, tick: int, cause: str) -> None:
        if out.empty or not agent.alive:
            return
        for stat in STATS:
            delta = getattr(out, stat)
            if delta:
                low = -1.0 if stat == "mood" else 0.0
                setattr(agent.state, stat, max(low, min(1.0, getattr(agent.state, stat) + delta)))
        for name in out.remove_conditions:
            await self.remove_condition(agent, name, tick, cause)
        for name in out.add_conditions:
            await self.add_condition(agent, name, tick, cause)
        for item, delta in out.items.items():
            have = agent.state.resources.get(item, 0)
            delta = max(delta, -have)
            if delta:
                agent.state.resources[item] = have + delta
                if agent.state.resources[item] <= 1e-9:
                    agent.state.resources.pop(item)
                await self._transfer(tick, "effect", item, abs(delta), "" if delta > 0 else agent.name,
                                     agent.name if delta > 0 else "", agent.location)
        if out.kill or agent.state.health <= 0:
            await self.kill(agent, tick, cause)

    # ── Rule-controlled agents ────────────────────────────────────────

    def _rule_decision(self, agent: Agent, cond: ConditionConfig) -> Action:
        """No LLM: act on a target here, go where targets are, or roam."""
        own = self._rule_condition_name(agent)
        rules = cond.rules

        def is_target(other: Agent) -> bool:
            if rules.targets.conditions or rules.targets.not_conditions or rules.targets.professions:
                return self._meets(other, rules.targets)
            return own not in other.state.conditions and self._controller(other) is None

        here = [o for o in self._witnesses(agent) if is_target(o)]
        if rules.action and here:
            act = self.config.actions.get(rules.action)
            usable = [o for o in here if act is None or self._meets(o, act.target_requires)]
            if usable:
                victim = self._rng.choice(usable)
                return Action(type=ActionType.CUSTOM, target=victim.name,
                              parameters={"name": rules.action}, reasoning=f"{own}: goes for {victim.name}")
        places = [p for p in self.environment.locations.values() if not p.virtual and p.type != "virtual"]
        if rules.seek and not here:
            counts = {p.name: sum(1 for a in self.living_agents if a.location == p.name and is_target(a))
                      for p in places}
            best = max(counts.values(), default=0)
            if best > 0:
                options = [n for n, c in counts.items() if c == best and n != agent.location]
                if options:
                    return Action(type=ActionType.MOVE, target=self._rng.choice(options),
                                  reasoning=f"{own}: drawn to where people are")
        if places and self._rng.random() < rules.wander:
            options = [p.name for p in places if p.name != agent.location]
            if options:
                return Action(type=ActionType.MOVE, target=self._rng.choice(options), reasoning=f"{own}: roams")
        return Action(type=ActionType.WAIT, reasoning=f"{own}: lingers")
