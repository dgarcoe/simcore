"""Coveted items: who carries them, for how long, and what happens when the carrier falls.

* ``items.<name>.visible``: everyone sees who carries it.
* ``items.<name>.drops``: it falls to the ground (the place's stock) when its carrier dies or
  leaves.
* ``rules[].when.holds: {item, ticks, location}``: true while the same living agent has carried the
  item for at least ``ticks`` ticks in a row (optionally only while at ``location``). The rule's
  description and every text in ``then`` may say ``{holder}``; it is also a valid effect target.
* ``rules[].when.count.item``: count the living agents that carry the item.

Mixed into SimulationEngine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent
    from simcore.config.schema import RuleConfig

HOLDER = "{holder}"


def fill_holder(value: Any, holder: str) -> Any:
    """Replace {holder} in every text of an effects mapping (keys included)."""
    if isinstance(value, str):
        return value.replace(HOLDER, holder)
    if isinstance(value, list):
        return [fill_holder(v, holder) for v in value]
    if isinstance(value, dict):
        return {fill_holder(k, holder): fill_holder(v, holder) for k, v in value.items()}
    return value


class ArtifactsMixin:
    def _init_artifacts(self) -> None:
        # item → (carrier name, tick since it has carried it without interruption)
        self._holders: dict[str, tuple[str, int]] = {}
        self._tracked_items = sorted(
            {r.when.holds.item for r in self.config.rules if r.when.holds}
            | {name for name, item in self.config.items.items() if item.visible}
        )

    def _carriers(self, item: str) -> list[Agent]:
        return [a for a in self.living_agents if a.state.resources.get(item, 0) >= 1]

    def _update_holders(self, tick: int) -> None:
        for item in self._tracked_items:
            carriers = self._carriers(item)
            current = self._holders.get(item)
            if current and any(a.name == current[0] for a in carriers):
                continue
            if carriers:
                carrier = sorted(carriers, key=lambda a: a.name)[0]
                self._holders[item] = (carrier.name, tick)
            else:
                self._holders.pop(item, None)

    def _held_for(self, item: str, tick: int) -> tuple[str, int] | None:
        found = self._holders.get(item)
        if not found:
            return None
        return found[0], tick - found[1]

    def _hold_match(self, rule: RuleConfig, tick: int) -> str | None:
        """The holder's name when the rule's `holds` test passes, else None."""
        spec = rule.when.holds
        held = self._held_for(spec.item, tick)
        if not held or held[1] < spec.ticks:
            return None
        if spec.location:
            carrier = self._find_agent_by_name(held[0])
            if carrier is None or carrier.location != spec.location:
                return None
        return held[0]

    async def _drop_artifacts(self, agent: Agent, tick: int) -> None:
        """Items marked `drops` fall where their carrier dies or leaves."""
        place = self.environment.get_location(agent.location)
        for name, item in self.config.items.items():
            amount = agent.state.resources.get(name, 0)
            if not item.drops or amount <= 0:
                continue
            agent.state.resources.pop(name, None)
            if place is None:
                continue
            place.stock[name] = place.stock.get(name, 0) + amount
            for other in self._witnesses(agent):
                other.observe(
                    f"The {name} fell from {agent.name}'s hands here, in {place.name}.",
                    tick,
                    importance=0.95,
                )
            await self._transfer(
                tick,
                "dropped",
                name,
                amount,
                source=agent.name,
                to=f"location:{place.name}",
                location=place.name,
            )
        await self._refresh_holders(tick)

    def _artifacts_prompt(self, agent: Agent, tick: int) -> str:
        lines = []
        for item in self._tracked_items:
            cfg = self.config.items.get(item)
            if agent.state.resources.get(item, 0) >= 1 and cfg and cfg.visible:
                lines.append(f"You carry the {item}: everyone who sees you knows it.")
        hourly = self.clock.tick_duration_minutes == 60
        for rule in self.config.rules:
            spec = rule.when.holds
            if not spec or "end" not in rule.then:  # only the races someone wins
                continue
            where = f" in {spec.location}" if spec.location else ""
            span = f"{spec.ticks} hours" if hourly else f"{spec.ticks} turns"
            line = f"Whoever keeps the {spec.item}{where} for {span} in a row wins"
            held = self._held_for(spec.item, tick)
            if held is None:
                line += "; nobody carries it now"
                lying = [p.name for p in self.environment.locations.values()
                         if p.stock.get(spec.item, 0) >= 1]
                if lying:
                    line += f", it lies in {lying[0]}"
            elif held[0] == agent.name:
                line += f"; you have kept it for {held[1]} of {spec.ticks}"
            elif self._knows_holder(agent, spec.item):
                carrier = self._find_agent_by_name(held[0])
                at = f" (at {carrier.location})" if carrier else ""
                line += f"; {held[0]}{at} has kept it for {held[1]}"
            lines.append(line + ".")
        return "\n".join(dict.fromkeys(lines))

    def _knows_holder(self, agent: Agent, item: str) -> bool:
        cfg = self.config.items.get(item)
        return bool(cfg and cfg.visible) or self.config.awareness == "full"

    def _carries_visible(self, other: Agent) -> list[str]:
        return [
            name
            for name in self._tracked_items
            if self.config.items.get(name)
            and self.config.items[name].visible
            and other.state.resources.get(name, 0) >= 1
        ]

    def holders_state(self, tick: int) -> dict:
        out = {}
        for item, (name, since) in self._holders.items():
            out[item] = {"agent": name, "since": since, "ticks": tick - since}
        return out

    async def _refresh_holders(self, tick: int) -> None:
        """Update who carries each tracked item and announce when it changes hands."""
        if not self._tracked_items:
            return
        before = dict(self._holders)
        self._update_holders(tick)
        for item in self._tracked_items:
            old, new = before.get(item), self._holders.get(item)
            if (old and old[0]) == (new and new[0]):
                continue
            await self.event_bus.emit(
                SimEvent(
                    type=EventType.ITEM_TRANSFER,
                    tick=tick,
                    data={
                        "kind": "holder",
                        "item": item,
                        "amount": 1,
                        "from": old[0] if old else "",
                        "to": new[0] if new else "",
                        "location": "",
                    },
                    source=new[0] if new else (old[0] if old else ""),
                )
            )
