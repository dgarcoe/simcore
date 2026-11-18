"""Points and achievements: doing an action (so many times), visiting places, carrying an item,
having a condition, being alive at the end, or penalties for going too long without doing
something. Agents see their points, the ranking and what earns them, so they go for them.
Mixed into SimulationEngine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import Achievement
from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent


class ScoresMixin:
    def _init_scores(self) -> None:
        self._scores: dict[str, int] = {}
        self._earned: dict[str, dict[str, int]] = {}  # agent → achievement → times earned
        self._did: dict[str, dict[str, int]] = {}  # agent → action → times done
        self._last_did: dict[str, dict[str, int]] = {}  # agent → action → last tick done
        self._since: dict[str, dict[str, int]] = {}  # agent → achievement → tick of last award
        self._visited: dict[str, set[str]] = {}

    @property
    def _achievements(self) -> dict[str, Achievement]:
        return self.config.scores.achievements

    def _score_of(self, name: str) -> int:
        return self._scores.get(name, 0)

    def _ranking(self) -> list[tuple[str, int]]:
        alive = {a.name for a in self.living_agents}
        names = [a.name for a in self.agents.values() if a.name in alive or a.name in self._scores]
        return sorted(((n, self._score_of(n)) for n in names), key=lambda x: (-x[1], x[0]))

    def _top_scorers(self, alive_only: bool = True) -> list[str]:
        ranking = [(n, p) for n, p in self._ranking()
                   if not alive_only or self._find_agent_by_name(n).alive]
        if not ranking:
            return []
        best = ranking[0][1]
        return [n for n, p in ranking if p == best]

    async def _award(self, agent: Agent, key: str, ach: Achievement, tick: int) -> None:
        self._scores[agent.name] = self._score_of(agent.name) + ach.points
        earned = self._earned.setdefault(agent.name, {})
        earned[key] = earned.get(key, 0) + 1
        self._since.setdefault(agent.name, {})[key] = tick
        what = ach.description or key
        sign = f"+{ach.points}" if ach.points >= 0 else str(ach.points)
        agent.observe(
            f"Points: «{what}» {sign} (you have {self._score_of(agent.name)}).",
            tick,
            importance=0.7,
        )
        await self.event_bus.emit(
            SimEvent(
                type=EventType.SCORE_CHANGE,
                tick=tick,
                data={
                    "agent": agent.name,
                    "achievement": key,
                    "description": what,
                    "points": ach.points,
                    "total": self._score_of(agent.name),
                },
                source=agent.name,
            )
        )

    def _earned_once(self, agent: Agent, key: str) -> bool:
        return self._earned.get(agent.name, {}).get(key, 0) > 0

    def _places_left(self, agent: Agent, visited: str | list[str]) -> list[str]:
        wanted = list(self.environment.locations) if visited == "all" else visited
        seen = self._visited.get(agent.name, set()) | {agent.location}  # where it is counts
        return [p for p in wanted if p not in seen]

    async def _scores_after_action(self, agent: Agent, action: Action, tick: int) -> None:
        """Count what the agent did and award what it earns."""
        if not self._achievements or not agent.alive:
            return
        self._visited.setdefault(agent.name, set()).add(agent.location)
        key = ""
        if action.type == ActionType.CUSTOM:
            if (action.parameters or {}).get("name") and (action.parameters or {}).get(
                "success", True
            ):
                key = action.parameters["name"]
        elif action.type != ActionType.WAIT:
            key = action.type.value
        if key:
            did = self._did.setdefault(agent.name, {})
            did[key] = did.get(key, 0) + 1
            self._last_did.setdefault(agent.name, {})[key] = tick
        for name, ach in self._achievements.items():
            w = ach.when
            if w.action and w.action == key:
                n = self._did[agent.name][key]
                if (ach.repeat and n % w.times == 0) or (
                    not ach.repeat and n >= w.times and not self._earned_once(agent, name)
                ):
                    await self._award(agent, name, ach, tick)
            elif w.visited is not None and not self._earned_once(agent, name):
                if not self._places_left(agent, w.visited):
                    await self._award(agent, name, ach, tick)

    async def _scores_tick(self, tick: int) -> None:
        """Carrying, having a condition, and penalties for what wasn't done in time."""
        if not self._achievements:
            return
        for agent in self.living_agents:
            self._visited.setdefault(agent.name, set()).add(agent.location)
            for name, ach in self._achievements.items():
                w = ach.when
                last = self._since.get(agent.name, {}).get(name)
                if w.carries or w.condition:
                    has = (not w.carries or agent.state.resources.get(w.carries, 0) >= 1) and (
                        not w.condition or w.condition in agent.state.conditions
                    )
                    if not has:
                        continue
                    if not self._earned_once(agent, name) or (
                        ach.repeat and tick - last >= w.every
                    ):
                        await self._award(agent, name, ach, tick)
                elif w.without:
                    done = self._last_did.get(agent.name, {}).get(w.without, 0)
                    if tick - max(done, last or 0) >= w.every:
                        await self._award(agent, name, ach, tick)
                elif w.wealth:
                    if (not self._earned_once(agent, name)
                            and agent.state.resources.get(self.currency, 0) + 1e-9 >= w.wealth):
                        await self._award(agent, name, ach, tick)
                elif w.in_territory:
                    group = self._faction_of(agent.name)
                    if not group or self._owner_of(agent.location) != group:
                        continue
                    if not self._earned_once(agent, name) or (
                        ach.repeat and tick - last >= w.every
                    ):
                        await self._award(agent, name, ach, tick)

    async def _score_capture(self, agent: Agent, tick: int) -> None:
        """The agent was there when its group took a territory."""
        for name, ach in self._achievements.items():
            if ach.when.captures and (ach.repeat or not self._earned_once(agent, name)):
                await self._award(agent, name, ach, tick)

    async def _score_knockout(self, agent: Agent, tick: int) -> None:
        """The agent knocked someone out (or killed them) in a fight."""
        for name, ach in self._achievements.items():
            if ach.when.knocks_out and (ach.repeat or not self._earned_once(agent, name)):
                await self._award(agent, name, ach, tick)

    def _group_ranking(self) -> list[tuple[str, int]]:
        """Groups by their members' points plus `territories.group_points` per territory held."""
        bonus = self.config.territories.group_points
        out = []
        for group, f in self._factions.items():
            points = sum(self._score_of(m) for m in f["members"])
            points += bonus * len(self._held_by(group)) if hasattr(self, "_held_by") else 0
            rich = self.config.economy.group_wealth_points
            if rich:
                points += int(rich * (f.get("stash") or {}).get(self.currency, 0))
            out.append((group, points))
        return sorted(out, key=lambda x: (-x[1], x[0]))

    def _top_groups(self) -> list[str]:
        ranking = self._group_ranking()
        if not ranking:
            return []
        return [g for g, p in ranking if p == ranking[0][1]]

    async def _scores_at_end(self, tick: int) -> None:
        for agent in self.living_agents:
            for name, ach in self._achievements.items():
                if ach.when.alive_at_end and not self._earned_once(agent, name):
                    await self._award(agent, name, ach, tick)

    def _scores_prompt(self, agent: Agent) -> str:
        if not self._achievements or not self.config.scores.visible:
            return ""
        ranking = self._ranking()
        top = ", ".join(f"{'you' if n == agent.name else n} {p}" for n, p in ranking[:5])
        place = next((i + 1 for i, (n, _) in enumerate(ranking) if n == agent.name), 0)
        lines = [
            f"Points (the story rewards these): you have {self._score_of(agent.name)}, "
            f"{_ordinal(place)} of {len(ranking)}. Ranking: {top}."
        ]
        groups = self._group_ranking()
        if groups:
            mine = self._faction_of(agent.name)
            fights = bool(getattr(self, "_territories", None))

            def held(g: str) -> str:
                return f" ({len(self._held_by(g))} places)" if fights else ""
            lines.append("Groups: " + ", ".join(
                f"{g}{' (yours)' if g == mine else ''} {p}{held(g)}" for g, p in groups[:6]) + ".")
        gain, lose = [], []
        for name, ach in self._achievements.items():
            if ach.points >= 0 and self._earned_once(agent, name) and not ach.repeat:
                continue
            w, what = ach.when, ach.description or name
            sign = f"+{ach.points}" if ach.points >= 0 else str(ach.points)
            extra = ""
            if w.action and w.times > 1:
                done = self._did.get(agent.name, {}).get(w.action, 0)
                extra = f" ({done % w.times if ach.repeat else done}/{w.times})"
            elif w.visited is not None:
                left = self._places_left(agent, w.visited)
                extra = f" (still to visit: {', '.join(left)})"
            elif w.without:
                extra = f' (every {w.every} ticks without "{w.without}")'
            elif w.wealth:
                extra = f" (you have {int(agent.state.resources.get(self.currency, 0))})"
            (gain if ach.points >= 0 else lose).append(f"{what} {sign}{extra}")
        if gain:
            lines.append("Earn: " + "; ".join(gain) + ".")
        if lose:
            lines.append("Lose: " + "; ".join(lose) + ".")
        return "\n".join(lines)

    def scores_state(self) -> dict:
        if not self._achievements:
            return {}
        return {
            "ranking": [{"agent": n, "points": p} for n, p in self._ranking()],
            **({"groups": [{"group": g, "points": p} for g, p in self._group_ranking()]}
               if self._factions else {}),
            "earned": {n: dict(e) for n, e in self._earned.items()},
        }


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
