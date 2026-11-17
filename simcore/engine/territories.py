"""Territories groups fight over (`environment.locations[].territory`): a place belongs to the
group with members there and no rivals, is contested while two groups are there, pays `income`
into its group's common stash every `territories.every` ticks, and rules can end the run when a
group holds enough of them (`when.territories`). Mixed into SimulationEngine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from simcore.agents.actions import fmt_amount
from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent


class TerritoriesMixin:
    def _init_territories(self) -> None:
        self._territories = [loc.name for loc in self.config.environment.locations if loc.territory]
        self._income = {loc.name: dict(loc.income) for loc in self.config.environment.locations
                        if loc.territory and loc.income}
        self._owners: dict[str, str] = {}
        self._contested: dict[str, list[str]] = {}
        self._owned_since: dict[str, int] = {}
        self._held_history: list[dict[str, int]] = []  # territories held per group, each tick

    def _holders_here(self, place: str) -> dict[str, list[Agent]]:
        """Group → members at the place who can hold it (not knocked out)."""
        out: dict[str, list[Agent]] = {}
        ko = self.config.society.knockout.condition
        for agent in self.living_agents:
            if agent.location != place or (ko and ko in agent.state.conditions):
                continue
            group = self._faction_of(agent.name)
            if group:
                out.setdefault(group, []).append(agent)
        return out

    def _held_by(self, group: str) -> list[str]:
        return [p for p in self._territories if self._owners.get(p) == group]

    def _owner_of(self, place: str) -> str:
        return self._owners.get(place, "")

    async def _territories_tick(self, tick: int) -> None:
        if not self._territories:
            return
        for place in self._territories:
            here = self._holders_here(place)
            previous = self._owners.get(place, "")
            if len(here) >= 2:
                if sorted(here) != self._contested.get(place):
                    self._contested[place] = sorted(here)
                    await self._territory_event(place, previous, previous, tick, contested=sorted(here))
                continue
            self._contested.pop(place, None)
            if len(here) == 1:
                (group,) = here
                if group != previous:
                    self._owners[place] = group
                    self._owned_since[place] = tick
                    await self._territory_event(place, group, previous, tick)
                    for member in here[group]:
                        member.observe(f"Your group {group} takes {place}.", tick, importance=0.8)
                        await self._score_capture(member, tick)
                    for agent in self.living_agents:
                        if previous and self._faction_of(agent.name) == previous:
                            agent.observe(f"{group} took {place} from your group.", tick, importance=0.8)
        every = self.config.territories.every
        if tick % every == 0:
            for place, items in self._income.items():
                group = self._owners.get(place)
                if not group or group not in self._factions or place in self._contested:
                    continue
                stash = self._factions[group].setdefault("stash", {})
                for item, amount in items.items():
                    stash[item] = stash.get(item, 0) + amount
                    await self._transfer(tick, "income", item, amount, f"location:{place}", f"group:{group}", place)
        counts: dict[str, int] = {}
        for place, group in self._owners.items():
            if group in self._factions:
                counts[group] = counts.get(group, 0) + 1
        self._held_history.append(counts)
        del self._held_history[:-200]

    async def _territory_event(self, place: str, owner: str, previous: str, tick: int,
                               contested: list[str] | None = None) -> None:
        await self.event_bus.emit(SimEvent(
            type=EventType.TERRITORY_CHANGE, tick=tick,
            data={"place": place, "owner": owner, "previous": previous,
                  **({"contested": contested} if contested else {})},
        ))

    def _territory_winners(self, spec) -> list[str]:
        """Groups that held at least `gte` territories in each of the last `ticks` ticks."""
        recent = self._held_history[-spec.ticks:]
        if len(recent) < spec.ticks:
            return []
        groups = [spec.faction] if spec.faction else sorted({g for c in recent for g in c})
        winners = [g for g in groups if all(c.get(g, 0) >= spec.gte for c in recent)]
        return sorted(winners, key=lambda g: -recent[-1].get(g, 0))

    def _territories_prompt(self, agent: Agent) -> str:
        if not self._territories:
            return ""
        parts = []
        for place in self._territories:
            if place in self._contested:
                parts.append(f"{place}: contested ({' vs '.join(self._contested[place])})")
            else:
                parts.append(f"{place}: {self._owners.get(place) or 'nobody'}")
        mine = self._faction_of(agent.name)
        text = "Territories: " + "; ".join(parts) + "."
        if mine:
            held = self._held_by(mine)
            text += (f" Your group {mine} holds {len(held)} of {len(self._territories)}."
                     " A place is taken by being there with your group and no rivals;"
                     " it pays into your group's stash")
            pays = {p: i for p, i in self._income.items()}
            if pays:
                text += ": " + "; ".join(f"{p} {', '.join(f'{k} x{fmt_amount(v)}' for k, v in i.items())}"
                                          for p, i in pays.items())
            text += f" every {self.config.territories.every} ticks."
        return text

    def territories_state(self) -> dict:
        if not self._territories:
            return {}
        return {p: {"owner": self._owners.get(p, ""), **({"contested": self._contested[p]}
                                                         if p in self._contested else {})}
                for p in self._territories}
