"""Items with a fuse (`items.<name>.fuse`): lit when an agent picks one up, they go off `ticks`
later wherever they are. The fuse keeps burning when the item is given, stolen or dropped, so a
lit stick of dynamite is a hot potato. When it goes off the unit is spent, `on_holder` hits whoever
carries it and `on_others` everyone else at the place. Mixed into SimulationEngine.

Units are followed by counting, each tick, how many every agent and every place holds: units that
left one holder and reached another in the same tick carry their fuse with them (lit or not); a
unit an agent gets unlit is lit then.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent

Fuses = dict[str, list[int | None]]  # holder ("agent:Ana" / "place:Bar") → per unit: lit at tick


class FusesMixin:
    def _init_fuses(self) -> None:
        self._fused = {name: item.fuse for name, item in self.config.items.items() if item.fuse}
        self._fuses: dict[str, Fuses] = {name: {} for name in self._fused}

    def _holdings(self, item: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for agent in self.living_agents:
            n = int(agent.state.resources.get(item, 0) + 1e-9)
            if n > 0:
                out[f"agent:{agent.name}"] = n
        for loc in self.environment.locations.values():
            n = int((loc.stock or {}).get(item, 0) + 1e-9)
            if n > 0:
                out[f"place:{loc.name}"] = n
        return out

    def _follow_fuses(self, item: str, tick: int) -> Fuses:
        """Match this tick's holdings with last tick's: who got which unit (and its fuse)."""
        before = self._fuses[item]
        now = self._holdings(item)
        moving: list[int | None] = []
        kept: Fuses = {}
        for holder, units in before.items():
            have = now.get(holder, 0)
            units = sorted(units, key=lambda t: (t is None, t if t is not None else 0))
            # the oldest lit units are the ones that left (handing over the lit one)
            if have < len(units):
                moving.extend(units[: len(units) - have])
                units = units[len(units) - have:]
            kept[holder] = units
        moving.sort(key=lambda t: (t is None, t if t is not None else 0))
        for holder, have in now.items():
            units = kept.setdefault(holder, [])
            while len(units) < have:
                lit = moving.pop(0) if moving else None
                if lit is None and holder.startswith("agent:"):
                    lit = tick  # picked up: the fuse starts burning
                    self._fuse_warning(holder.split(":", 1)[1], item, tick)
                units.append(lit)
        self._fuses[item] = {h: u for h, u in kept.items() if u and now.get(h)}
        return self._fuses[item]

    def _fuse_warning(self, name: str, item: str, tick: int) -> None:
        agent = self._find_agent_by_name(name)
        if agent is not None:
            agent.observe(f"You picked up {item}: its fuse is lit. It goes off in "
                          f"{self._fused[item].ticks} ticks, wherever it is.", tick, importance=0.9)

    async def _fuses_tick(self, tick: int) -> None:
        for item, fuse in self._fused.items():
            for holder, units in list(self._follow_fuses(item, tick).items()):
                due = [t for t in units if t is not None and tick - t >= fuse.ticks]
                for lit in due:
                    units.remove(lit)
                    await self._go_off(item, holder, tick)

    async def _go_off(self, item: str, holder: str, tick: int) -> None:
        fuse = self._fused[item]
        kind, name = holder.split(":", 1)
        carrier = self._find_agent_by_name(name) if kind == "agent" else None
        if carrier is not None:
            place = carrier.location
            carrier.state.resources[item] = carrier.state.resources.get(item, 0) - 1
            if carrier.state.resources[item] <= 1e-9:
                carrier.state.resources.pop(item, None)
        else:
            place = name
            loc = self.environment.get_location(name)
            if loc is not None and loc.stock.get(item, 0) > 0:
                loc.stock[item] -= 1
                if loc.stock[item] <= 1e-9:
                    loc.stock.pop(item)
        here = [a for a in self.living_agents if a.location == place]
        others = [a for a in here if a is not carrier]
        text = (fuse.message or "{item} goes off in {place}").format(item=item, place=place)
        hurt = []
        if carrier is not None and carrier.alive:
            await self._apply_outcome(carrier, fuse.on_holder, tick, f"{item} went off in hand")
            hurt.append(carrier.name)
        if not fuse.on_others.empty:
            for other in others:
                if other.alive:
                    await self._apply_outcome(other, fuse.on_others, tick, f"{item} went off")
                    hurt.append(other.name)
        for agent in here:
            agent.observe(f"{text}!" + (" It was in my hands." if agent is carrier else ""), tick,
                          importance=0.95)
        self.environment.log_event(tick, "fuse", text, location=place, agents=hurt)
        await self.event_bus.emit(SimEvent(
            type=EventType.FUSE, tick=tick,
            data={"item": item, "agent": carrier.name if carrier else "", "location": place,
                  "hurt": hurt, "message": text},
            source=carrier.name if carrier else "",
        ))

    def _fuses_prompt(self, agent: Agent, tick: int) -> str:
        """Lit items the agent carries or sees here, with the ticks left."""
        lines = []
        for item, fuse in self._fused.items():
            for holder, units in self._fuses.get(item, {}).items():
                mine = holder == f"agent:{agent.name}"
                here = holder == f"place:{agent.location}"
                for lit in units:
                    if lit is None or not (mine or here):
                        continue
                    left = max(1, fuse.ticks - (tick - lit))
                    where = "you carry" if mine else "lies here"
                    lines.append(
                        f"DANGER: a lit {item} {where}; it goes off in {left} tick"
                        f"{'s' if left != 1 else ''}"
                        + (" — give it to someone, drop it or get far away" if mine else
                           " — get away from here or take it somewhere else")
                    )
        return "\n".join(lines)

    def fuses_state(self) -> dict:
        return {
            item: {h: [t for t in u if t is not None] for h, u in held.items()
                   if any(t is not None for t in u)}
            for item, held in self._fuses.items()
            if any(any(t is not None for t in u) for u in held.values())
        }
