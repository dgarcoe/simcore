"""Territories groups fight over (`environment.locations[].territory`).

Strength is the number of a group's members at the place (knocked-out ones don't count).
- A place nobody holds goes at once to the strongest group there (a tie leaves it contested).
- A held place with none of its owner's members there falls at once the same way.
- A held place with rivals in it is contested; a rival group that outnumbers the owner's
  members there for `territories.siege` ticks in a row takes it (ties favour the owner).
A held place pays `income` into its group's stash every `territories.every` ticks
(`contested_income` of it while contested), and rules can end the run when a group holds
enough of them (`when.territories`). Mixed into SimulationEngine.
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
        self._income = {
            loc.name: dict(loc.income)
            for loc in self.config.environment.locations
            if loc.territory and loc.income
        }
        self._owners: dict[str, str] = {}
        self._contested: dict[str, list[str]] = {}
        self._siege: dict[str, tuple[str, int]] = {}  # place → (group besieging it, ticks)
        self._contest_sig: dict[str, list[str]] = {}
        self._owned_since: dict[str, int] = {}
        self._held_history: list[dict[str, int]] = []  # territories held per group, each tick

    def _holders_here(self, place: str) -> dict[str, list[Agent]]:
        """Group → members at the place who can hold it (not knocked out)."""
        out: dict[str, list[Agent]] = {}
        ko = self.config.society.knockout.condition
        for agent in self.living_agents:
            if agent.location != place or (ko and ko in agent.state.conditions):
                continue
            if getattr(self, "_recovering", {}).get(agent.name) and \
                    self.clock.current_tick <= self._recovering[agent.name]["until"]:
                continue  # in bed: doesn't hold anything
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
            await self._contest(place, tick)
        every = self.config.territories.every
        if tick % every == 0:
            for place, items in self._income.items():
                group = self._owners.get(place)
                if not group or group not in self._factions:
                    continue
                share = self.config.territories.contested_income if place in self._contested else 1
                stash = self._factions[group].setdefault("stash", {})
                for item, full in items.items():
                    amount = full * share
                    if amount <= 0:
                        continue
                    stash[item] = stash.get(item, 0) + amount
                    await self._transfer(
                        tick, "income", item, amount, f"location:{place}", f"group:{group}", place
                    )
        counts: dict[str, int] = {}
        for place, group in self._owners.items():
            if group in self._factions:
                counts[group] = counts.get(group, 0) + 1
        self._held_history.append(counts)
        del self._held_history[:-200]

    async def _contest(self, place: str, tick: int) -> None:
        here = self._holders_here(place)
        owner = self._owners.get(place, "")
        strength = {g: len(m) for g, m in here.items()}
        rivals = {g: n for g, n in strength.items() if g != owner}
        if not rivals:
            if place in self._contested:
                self._contested.pop(place)
                self._siege.pop(place, None)
                await self._territory_event(place, owner, owner, tick)
            return
        best = max(rivals.values())
        leaders = [g for g, n in rivals.items() if n == best]
        defenders = strength.get(owner, 0) if owner else 0
        if defenders == 0:
            # nobody's, or left unguarded: the strongest group there takes it at once
            if len(leaders) == 1:
                await self._take(place, leaders[0], here, tick)
                return
            self._siege.pop(place, None)
        else:
            leader = leaders[0] if len(leaders) == 1 and best > defenders else ""
            if leader:
                by, ticks = self._siege.get(place, ("", 0))
                ticks = ticks + 1 if by == leader else 1
                if ticks >= self.config.territories.siege:
                    await self._take(place, leader, here, tick)
                    return
                self._siege[place] = (leader, ticks)
                for agent in self.living_agents:
                    if self._faction_of(agent.name) == owner:
                        agent.observe(
                            f"{leader} outnumber your group at {place}: they take it unless you"
                            f" match them there ({ticks}/{self.config.territories.siege}).",
                            tick,
                            importance=0.8,
                        )
            else:
                self._siege.pop(place, None)
        groups = sorted(strength)
        siege = self._siege.get(place)
        sig = groups + ([f"{siege[0]}:{siege[1]}"] if siege else [])
        if sig != self._contest_sig.get(place):
            if place not in self._contested and owner:
                for agent in self.living_agents:
                    if self._faction_of(agent.name) == owner:
                        agent.observe(
                            f"Rivals ({', '.join(rivals)}) are in your territory {place}.",
                            tick,
                            importance=0.7,
                        )
            self._contest_sig[place] = sig
            self._contested[place] = groups
            await self._territory_event(place, owner, owner, tick, contested=groups)

    async def _take(self, place: str, group: str, here: dict, tick: int) -> None:
        previous = self._owners.get(place, "")
        self._contested.pop(place, None)
        self._contest_sig.pop(place, None)
        self._siege.pop(place, None)
        self._owners[place] = group
        self._owned_since[place] = tick
        await self._territory_event(place, group, previous, tick)
        for member in here[group]:
            member.observe(f"Your group {group} takes {place}.", tick, importance=0.8)
            await self._score_capture(member, tick)
        for agent in self.living_agents:
            if previous and self._faction_of(agent.name) == previous:
                agent.observe(f"{group} took {place} from your group.", tick, importance=0.8)

    def _siege_data(self, place: str) -> dict:
        if place not in self._siege:
            return {}
        by, ticks = self._siege[place]
        return {"siege": {"by": by, "progress": ticks, "needed": self.config.territories.siege}}

    async def _territory_event(
        self, place: str, owner: str, previous: str, tick: int, contested: list[str] | None = None
    ) -> None:
        await self.event_bus.emit(
            SimEvent(
                type=EventType.TERRITORY_CHANGE,
                tick=tick,
                data={
                    "place": place,
                    "owner": owner,
                    "previous": previous,
                    **({"contested": contested} if contested else {}),
                    **(self._siege_data(place) if contested else {}),
                },
            )
        )

    def _territory_winners(self, spec) -> list[str]:
        """Groups that held at least `gte` territories in each of the last `ticks` ticks."""
        recent = self._held_history[-spec.ticks :]
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
            if place in self._siege:
                by, ticks = self._siege[place]
                parts.append(
                    f"{place}: {self._owners.get(place)}, besieged by {by}"
                    f" ({ticks}/{self.config.territories.siege})"
                )
            elif place in self._contested:
                owner = self._owners.get(place)
                parts.append(
                    f"{place}: {owner + ', ' if owner else ''}"
                    f"contested ({' vs '.join(self._contested[place])})"
                )
            else:
                parts.append(f"{place}: {self._owners.get(place) or 'nobody'}")
        mine = self._faction_of(agent.name)
        text = "Territories: " + "; ".join(parts) + "."
        if mine:
            held = self._held_by(mine)
            text += (
                f" Your group {mine} holds {len(held)} of {len(self._territories)}."
                " An empty or unguarded place goes to the group with most members there."
                " To take a guarded one, outnumber its owner's members there for"
                f" {self.config.territories.siege} ticks in a row (a tie keeps the owner);"
                " so leave someone guarding yours and bring friends to attack."
                " A held place pays into its group's stash (less while rivals are in it)"
            )
            pays = {p: i for p, i in self._income.items()}
            if pays:
                text += ": " + "; ".join(
                    f"{p} {', '.join(f'{k} x{fmt_amount(v)}' for k, v in i.items())}"
                    for p, i in pays.items()
                )
            text += f" every {self.config.territories.every} ticks."
        return text

    def territories_state(self) -> dict:
        if not self._territories:
            return {}
        return {
            p: {
                "owner": self._owners.get(p, ""),
                **({"contested": self._contested[p]} if p in self._contested else {}),
                **self._siege_data(p),
            }
            for p in self._territories
        }
