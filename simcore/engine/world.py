"""Needs (hunger, sleep…), dangerous or closed places and world rules. Mixed into SimulationEngine."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import HazardConfig, NeedConfig, Requirement, RuleConfig, RuleWhen
from simcore.engine.artifacts import MVP, WINNER, fill_holder
from simcore.engine.effects import parse_effects
from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent
    from simcore.world.location import Location

logger = logging.getLogger("simcore")

HIGH = 0.5  # a need above this shows up in the prompt
CRITICAL = 1.0


def _clamp_stat(agent: Agent, stat: str, delta: float) -> None:
    low = -1.0 if stat == "mood" else 0.0
    setattr(agent.state, stat, max(low, min(1.0, getattr(agent.state, stat) + delta)))


def _level(value: float) -> str:
    return "critical" if value >= CRITICAL else "high" if value >= HIGH else "ok"


class WorldMixin:
    """Needs the engine's config, agents, environment, event_bus and the ConditionsMixin helpers."""

    # ── Needs ───────────────────────────────────────────────────────────

    def _has_need(self, agent: Agent, need: NeedConfig) -> bool:
        return agent.alive and self._controller(agent) is None and self._meets(agent, need.who)

    async def _set_need(self, agent: Agent, name: str, value: float, tick: int, why: str = "") -> None:
        before = agent.state.needs.get(name, 0.0)
        value = max(0.0, min(1.0, value))
        agent.state.needs[name] = value
        old, new = _level(before), _level(value)
        if old != new:
            need = self.config.needs.get(name)
            label = need.label if need and need.label else name
            if new != "ok":
                agent.observe(f"My {label} is {new}.", tick, importance=0.6 if new == "high" else 0.85)
            await self.event_bus.emit(SimEvent(
                type=EventType.NEED_CHANGE, tick=tick,
                data={"agent": agent.name, "need": name, "label": label, "level": new,
                      "value": round(value, 3), "cause": why},
                source=agent.name,
            ))

    async def _needs_tick(self, tick: int) -> None:
        for agent in list(self.living_agents):
            for name, need in self.config.needs.items():
                if not self._has_need(agent, need):
                    continue
                await self._set_need(agent, name, agent.state.needs.get(name, 0.0) + need.grows, tick)
                if agent.state.needs[name] >= CRITICAL:
                    for stat, delta in need.at_max.items():
                        _clamp_stat(agent, stat, delta)
            if agent.state.health <= 0:
                label = ", ".join(n for n, v in agent.state.needs.items() if v >= CRITICAL) or "exhaustion"
                await self.kill(agent, tick, label, how="needs")

    async def _relieve(self, agent: Agent, name: str, tick: int, why: str) -> None:
        need = self.config.needs[name]
        current = agent.state.needs.get(name, 0.0)
        if current > 0:
            await self._set_need(agent, name, current - need.relief, tick, why)

    async def _satisfy_by_action(self, agent: Agent, action: Action, tick: int) -> None:
        if not self.config.needs or not agent.alive:
            return
        done = str(action.parameters.get("name") or action.type.value)
        for name, need in self.config.needs.items():
            sb = need.satisfied_by
            if not self._has_need(agent, need):
                continue
            here = not sb.places or agent.location in sb.places
            if sb.actions:
                if done in sb.actions and here:
                    await self._relieve(agent, name, tick, done)
            elif sb.places and here and action.type in (ActionType.REST, ActionType.WORK):
                await self._relieve(agent, name, tick, f"{done} at {agent.location}")

    async def _satisfy_by_item(self, agent: Agent, item: str, tick: int) -> None:
        for name, need in self.config.needs.items():
            if item in need.satisfied_by.items and self._has_need(agent, need):
                await self._relieve(agent, name, tick, item)

    def _needs_prompt(self, agent: Agent) -> str:
        lines = []
        for name, value in agent.state.needs.items():
            need = self.config.needs.get(name)
            if not need or value < HIGH:
                continue
            label = need.label or name
            ways = [f"use {i}" for i in need.satisfied_by.items]
            ways += [f"{a}{' at ' + '/'.join(need.satisfied_by.places) if need.satisfied_by.places else ''}"
                     for a in need.satisfied_by.actions]
            if not need.satisfied_by.actions and need.satisfied_by.places:
                ways.append(f"rest or work at {'/'.join(need.satisfied_by.places)}")
            level = "CRITICAL, it is hurting you" if value >= CRITICAL else "high"
            lines.append(f"{label}: {level}" + (f" (relief: {', '.join(ways)})" if ways else ""))
        return ("Your needs: " + "; ".join(lines) + ".") if lines else ""

    # ── Places: closed, restricted, dangerous ──────────────────────────

    def _can_enter(self, agent: Agent, place: Location) -> tuple[bool, str]:
        if place.closed:
            return False, "it is closed"
        if place.access is not None and not self._meets(agent, place.access):
            return False, "I'm not allowed in"
        return True, ""

    def _places_prompt(self) -> str:
        closed = [p.name for p in self.environment.locations.values() if p.closed]
        danger = [f"{p.name} ({p.hazard.label or 'dangerous'})"
                  for p in self.environment.locations.values() if p.hazard]
        parts = []
        if closed:
            parts.append(f"Closed places: {', '.join(closed)}.")
        if danger:
            parts.append(f"Dangerous places: {', '.join(danger)}.")
        return " ".join(parts)

    async def _hazards_tick(self, tick: int) -> None:
        for place in self.environment.locations.values():
            hz = place.hazard
            if not hz:
                continue
            for agent in [a for a in self.living_agents if a.location == place.name]:
                for stat, delta in hz.per_tick.items():
                    _clamp_stat(agent, stat, delta)
                agent.observe(f"{place.name} is {hz.label or 'dangerous'}!", tick, importance=0.8)
                if hz.condition:
                    await self.add_condition(agent, hz.condition, tick, hz.label or place.name)
                if agent.state.health <= 0:
                    await self.kill(agent, tick, hz.label or f"danger at {place.name}", how="hazard")

    async def _change_place(self, name: str, change: dict, tick: int, cause: str) -> None:
        place = self.environment.get_location(name)
        if place is None:
            logger.warning(f"Effect changes unknown place '{name}'")
            return
        if "closed" in change:
            place.closed = bool(change["closed"])
        if "hazard" in change:
            place.hazard = HazardConfig.model_validate(change["hazard"]) if change["hazard"] else None
        if "access" in change:
            place.access = Requirement.model_validate(change["access"]) if change["access"] else None
        text = []
        if "closed" in change:
            text.append(f"{name} is now {'closed' if place.closed else 'open'}")
        if "hazard" in change:
            text.append(f"{name} is {place.hazard.label or 'dangerous'}" if place.hazard else f"{name} is safe again")
        if text:
            self.environment.log_event(tick, "place_change", "; ".join(text), location=name)
        await self.event_bus.emit(SimEvent(
            type=EventType.PLACE_CHANGE, tick=tick,
            data={"location": name, "closed": place.closed,
                  "hazard": place.hazard.model_dump() if place.hazard else None,
                  "access": place.access.model_dump() if place.access else None, "cause": cause},
        ))

    # ── World rules ────────────────────────────────────────────────────

    def _rule_holds(self, rule: RuleConfig, tick: int) -> bool:
        return self._when_holds(rule.when, tick)

    def _when_holds(self, w: RuleWhen, tick: int) -> bool:
        if w.at is not None and tick != w.at:
            return False
        if w.every is not None and tick % w.every != 0:
            return False
        if w.from_tick is not None and tick < w.from_tick:
            return False
        if w.until is not None and tick > w.until:
            return False
        state = self.environment.global_state
        for key, cmp in w.global_state.items():
            if key not in state or not cmp.test(state[key]):
                return False
        if w.alive is not None and not w.alive.test(len(self.living_agents)):
            return False
        if w.count is not None and not w.count.test(self._count(w.count)):
            return False
        for name, spec in w.place.items():
            place = self.environment.get_location(name)
            if place is None:
                return False
            if spec.closed is not None and place.closed != spec.closed:
                return False
            if spec.hazard is not None and bool(place.hazard) != spec.hazard:
                return False
            if spec.occupants is not None and not spec.occupants.test(
                    sum(1 for a in self.living_agents if a.location == name)):
                return False
        if w.holds is not None and self._hold_spec_match(w.holds, tick) is None:
            return False
        if w.score is not None and not self._score_leaders(w.score):
            return False
        if w.territories is not None and not self._territory_winners(w.territories):
            return False
        if w.any and not any(self._when_holds(alt, tick) for alt in w.any):
            return False
        return True

    def _count(self, c) -> int:
        return sum(1 for a in self.living_agents
                   if (not c.condition or c.condition in a.state.conditions)
                   and (not c.not_condition or c.not_condition not in a.state.conditions)
                   and (not c.location or a.location == c.location)
                   and (not c.faction or self._faction_of(a.name) == c.faction)
                   and (not c.item or a.state.resources.get(c.item, 0) >= 1))

    def _score_leaders(self, cmp) -> list[str]:
        """Living agents whose points pass the test, best first."""
        return [n for n, p in self._ranking() if cmp.test(p)
                and (a := self._find_agent_by_name(n)) and a.alive]

    def _winner(self, rule: RuleConfig, tick: int) -> str:
        """Who wins when a rule fires: whoever met it (kept the item, reached the points, the
        group counted), else the last one standing, else the best score."""
        for w in rule.when.all_tests():
            if not self._when_holds(w, tick) and w is not rule.when:
                continue
            if w.holds is not None and (holder := self._hold_spec_match(w.holds, tick)):
                return holder
            if w.score is not None and (best := self._score_leaders(w.score)):
                return best[0]
            if w.count is not None and w.count.faction:
                return w.count.faction
            if w.territories is not None and (groups := self._territory_winners(w.territories)):
                return groups[0]
        if rule.winner == "group":
            return " & ".join(self._top_groups())
        if rule.winner == "person":
            return " & ".join(self._top_scorers())
        alive = self.living_agents
        if len(alive) == 1:
            return alive[0].name
        top = self._top_scorers() if self._achievements else []
        return " & ".join(top)

    async def _rules_tick(self, tick: int) -> None:
        await self._refresh_holders(tick)
        for i, rule in enumerate(self.config.rules):
            if i in self._fired_rules or not self._rule_holds(rule, tick):
                continue
            if rule.once:
                self._fired_rules.add(i)
            holder = self._hold_match(rule, tick)
            description, then = rule.description, rule.then
            if holder:
                description, then = fill_holder(description, holder), fill_holder(then, holder)
            winner = ""
            if WINNER in str(description) or WINNER in str(then) or "end" in then:
                winner = self._winner(rule, tick)
                description = fill_holder(description, winner or "nadie", WINNER)
                then = fill_holder(then, winner or "nadie", WINNER)
                if "end" in then:
                    self.end_winner = winner
            ends = "end" in then and self._achievements
            if MVP in str(description) or MVP in str(then) or ends:
                mvp = " & ".join(self._top_scorers(alive_only=False))
                description = fill_holder(description, mvp or "nadie", MVP)
                then = fill_holder(then, mvp or "nadie", MVP)
                if "end" in then:
                    self.end_mvp = mvp
            if description:
                self.environment.log_event(tick, "rule", description)
                for agent in self.living_agents:
                    agent.observe(f"World event: {description}", tick, importance=0.8)
            await self.event_bus.emit(SimEvent(
                type=EventType.RULE_FIRED, tick=tick,
                data={"rule": i, "description": description, "effects": then,
                      **({"holder": holder} if holder else {}),
                      **({"winner": winner} if winner else {})},
            ))
            await self.apply_effects(parse_effects(then), tick, cause=description)
            if not self._running:
                return

    def request_end(self, reason: str, message: str) -> None:
        self.end_reason = reason
        self.end_message = message
        self._running = False
