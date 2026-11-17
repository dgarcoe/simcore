"""Cursed items (`items.X.curse`): whoever carries one gets its condition (which may hurt or
change their mind), a sticky one can't be given, dropped or traded away, and only the items or
actions in `broken_by` break the curse (the item crumbles). Mixed into SimulationEngine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simcore.agents.agent import Agent


class CursesMixin:
    def _cursed_items(self) -> dict:
        return {name: item.curse for name, item in self.config.items.items() if item.curse}

    def _curse_holds(self, agent: Agent, item: str) -> bool:
        """A sticky cursed item the agent carries: it won't leave their hands."""
        spec = self.config.items.get(item)
        return bool(
            spec and spec.curse and spec.curse.sticky and agent.state.resources.get(item, 0) >= 1
        )

    async def _sync_curses(self, agent: Agent, tick: int) -> None:
        """Give the curse's condition to whoever carries the item, and lift it once it's gone."""
        curses = self._cursed_items()
        if not curses or not agent.alive:
            return
        wanted = {
            c.condition for item, c in curses.items() if agent.state.resources.get(item, 0) >= 1
        }
        for item, curse in curses.items():
            carries = agent.state.resources.get(item, 0) >= 1
            if carries and curse.condition not in agent.state.conditions:
                if await self.add_condition(agent, curse.condition, tick, f"the {item}"):
                    told = f": {curse.description}" if curse.description else ""
                    agent.observe(
                        f"The {item} is cursed{told}."
                        + (" It won't leave your hands." if curse.sticky else ""),
                        tick,
                        importance=0.95,
                    )
            elif (
                not carries
                and curse.condition in agent.state.conditions
                and curse.condition not in wanted
            ):
                await self.remove_condition(agent, curse.condition, tick, f"without the {item}")

    async def _curses_tick(self, tick: int) -> None:
        if self._cursed_items():
            for agent in self.living_agents:
                await self._sync_curses(agent, tick)

    async def _break_curses(self, agent: Agent, by: str, tick: int) -> bool:
        """`by` (an item used on the agent, or an action done to or by it) breaks the curses of the
        items it carries that it can break: the item crumbles and its condition goes."""
        broke = False
        for item, curse in self._cursed_items().items():
            if by in curse.broken_by and agent.state.resources.get(item, 0) >= 1:
                agent.state.resources.pop(item, None)
                await self._transfer(tick, "effect", item, 1, agent.name, "", agent.location)
                await self.remove_condition(agent, curse.condition, tick, f"{by} broke the curse")
                agent.observe(
                    f"The curse of the {item} is broken ({by}); it crumbles to dust.",
                    tick,
                    importance=0.9,
                )
                for other in self._witnesses(agent):
                    other.observe(
                        f"The curse of {agent.name}'s {item} was broken.", tick, importance=0.7
                    )
                broke = True
        return broke

    def _breakers(self) -> set[str]:
        return {b for c in self._cursed_items().values() for b in c.broken_by}

    def _curses_prompt(self, agent: Agent) -> str:
        lines = []
        for item, curse in self._cursed_items().items():
            if agent.state.resources.get(item, 0) >= 1:
                text = f"Your {item} is cursed"
                text += "; you can't give, drop or trade it" if curse.sticky else ""
                if curse.broken_by:
                    text += f"; it can be broken with: {', '.join(curse.broken_by)}"
                lines.append(text + ".")
        return "\n".join(lines)
