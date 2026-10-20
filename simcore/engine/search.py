"""Looking for someone: a search that lasts several turns — ask the people here, go to the
best clue, rule out the places already checked, give up if they don't turn up. Mixed into
SimulationEngine (uses the awareness helpers: _last_seen, _where_is, _note_move)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from simcore.agents.actions import Action
from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent

SEARCH_TICKS = 8  # give up after this long


def _ago(now: int, then: int) -> str:
    d = now - then
    return "just now" if d <= 0 else f"{d} tick{'s' if d > 1 else ''} ago"


class SearchMixin:
    """Needs the engine's agents, environment, event_bus and the awareness helpers."""

    def _init_search(self) -> None:
        # seeker id → {"target", "why", "message", "since", "checked": {place: tick},
        #              "clues": {by: (place, tick)}, "asked": set(names), "found": tick | None}
        self._searches: dict[str, dict] = {}

    # ── Helpers ────────────────────────────────────────────────────────

    def _absent_person(self, agent: Agent, target: str) -> Agent | None:
        """Someone alive the agent names who is not here (so it has to go and find them)."""
        if not target or self._speech_targets(agent, target):
            return None
        other = self._find_agent_by_name(target)
        if other is None or other is agent or not other.alive or other.location == agent.location:
            return None
        return other

    def _origin_of(self, name: str) -> str:
        for cfg in self.config.agents:
            if cfg.name == name:
                return cfg.starting_location
        return ""

    def _clue_list(self, search: dict, tick: int) -> list[dict]:
        return [
            {"by": by, "place": place, "ago": tick - when}
            for by, (place, when) in sorted(search["clues"].items(), key=lambda kv: -kv[1][1])
        ]

    async def _search_event(
        self, agent: Agent, search: dict, step: str, tick: int, place: str = ""
    ) -> None:
        await self.event_bus.emit(
            SimEvent(
                type=EventType.AGENT_SEARCH,
                tick=tick,
                data={
                    "agent": agent.name,
                    "target": search["target"],
                    "step": step,
                    "place": place,
                    "why": search["why"],
                    "clues": self._clue_list(search, tick),
                },
                source=agent.name,
            )
        )

    def _own_clue(self, agent: Agent, search: dict, target: Agent) -> None:
        seen = self._where_is(agent, target)
        if seen:
            search["clues"]["you"] = (seen[0], seen[1])

    # ── The search ─────────────────────────────────────────────────────

    async def _apply_find(
        self, agent: Agent, action: Action, tick: int, message: str = ""
    ) -> Action:
        target = self._find_agent_by_name(action.target)
        if target is None or target is agent:
            return await self._refuse(agent, action, tick, "I don't know anyone by that name")
        search = self._searches.get(agent.id)
        if not search or search["target"] != target.name:
            search = {
                "target": target.name,
                "why": (action.content or "").strip(),
                "message": message,
                "since": tick,
                "checked": {},
                "clues": {},
                "asked": set(),
                "found": None,
            }
            self._searches[agent.id] = search
            agent.observe(
                f"I'm looking for {target.name}"
                + (f" ({search['why']})" if search["why"] else "")
                + ".",
                tick,
                importance=0.5,
            )
            await self._search_event(agent, search, "started", tick, agent.location)
        elif message:
            search["message"] = message
        action.target = target.name

        if not target.alive:
            await self._end_search(agent, search, "lost", tick)
            return action
        if target.location == agent.location:
            await self._found(agent, search, tick)
            return action
        self._own_clue(agent, search, target)
        search["checked"][agent.location] = tick

        # 1. Ask the people here who haven't been asked yet.
        here = [
            o
            for o in self.living_agents
            if o.location == agent.location
            and o is not agent
            and o.name not in search["asked"]
            and not (self._mind(o) and not self._mind(o)[1].speech)
        ]
        if here:
            names = [o.name for o in here]
            search["asked"].update(names)
            question = f"Has anyone seen {target.name}?"
            self._speech_log.append(
                {
                    "tick": tick,
                    "speaker": agent.name,
                    "to": names,
                    "text": question,
                    "location": agent.location,
                    "public": True,
                }
            )
            told = []
            for o in here:
                seen = self._where_is(o, target)
                o.observe(f'{agent.name} asked me: "{question}"', tick, importance=0.4)
                if seen and (o.name not in search["clues"] or search["clues"][o.name][1] < seen[1]):
                    search["clues"][o.name] = (seen[0], seen[1])
                    told.append(f"{o.name} saw {target.name} at {seen[0]} ({_ago(tick, seen[1])})")
            if not told and await self._track_roll(agent, target, tick):
                search["clues"]["tracks"] = (target.location, tick)
                told.append(f"I found signs that {target.name} went to {target.location}")
            agent.observe(
                ("; ".join(told) + ".") if told else f"Nobody here has seen {target.name}.",
                tick,
                importance=0.6,
            )
            action.parameters = {**(action.parameters or {}), "step": "asked"}
            await self._search_event(agent, search, "asked", tick, agent.location)
            return action

        # 2. Go to the best place not checked since the clue about it.
        dest = self._next_place(agent, search, target)
        if dest is None:
            await self._end_search(agent, search, "gave_up", tick)
            return action
        origin = agent.location
        if not self.environment.move_agent(agent.id, origin, dest):
            search["checked"][dest] = tick  # full: try elsewhere next time
            return action
        agent.state.location = dest
        self._note_move(agent, origin, dest, tick)
        search["checked"][dest] = tick
        action.parameters = {
            **(action.parameters or {}),
            "step": "moved",
            "place": dest,
            "from": origin,
        }
        await self._search_event(agent, search, "moved", tick, dest)
        if target.location == dest:
            await self._found(agent, search, tick)
        return action

    def _next_place(self, agent: Agent, search: dict, target: Agent) -> str | None:
        checked = search["checked"]

        def usable(place: str) -> bool:
            loc = self.environment.get_location(place)
            return bool(loc) and place != agent.location and self._can_enter(agent, loc)[0]

        # freshest clue whose place we haven't checked since
        for place, when in sorted(search["clues"].values(), key=lambda c: -c[1]):
            if usable(place) and checked.get(place, -1) < when:
                return place
        origin = self._origin_of(target.name)
        if origin and origin not in checked and usable(origin):
            return origin
        for loc in self.environment.locations.values():
            if (
                not loc.virtual
                and loc.type != "virtual"
                and loc.name not in checked
                and usable(loc.name)
            ):
                return loc.name
        return None

    async def _found(self, agent: Agent, search: dict, tick: int) -> None:
        if search["found"] is not None:
            return
        search["found"] = tick
        agent.observe(f"I found {search['target']}.", tick, importance=0.7)
        await self._gain_xp(agent, 10, tick)
        target = self._find_agent_by_name(search["target"])
        if target is not None:
            target.observe(f"{agent.name} was looking for me and found me.", tick, importance=0.5)
        await self._search_event(agent, search, "found", tick, agent.location)

    async def _end_search(self, agent: Agent, search: dict, step: str, tick: int) -> None:
        self._searches.pop(agent.id, None)
        if step == "lost":
            agent.observe(f"I can't find {search['target']}: they are gone.", tick, importance=0.6)
        elif step == "gave_up":
            agent.observe(f"I couldn't find {search['target']}.", tick, importance=0.5)
        await self._search_event(agent, search, step, tick, agent.location)

    async def _searches_tick(self, tick: int) -> None:
        """Start of a tick: close found searches, notice who turned up, give up on old ones."""
        for agent_id, search in list(self._searches.items()):
            agent = self.agents.get(agent_id)
            if agent is None or not agent.alive:
                self._searches.pop(agent_id, None)
                continue
            mind = self._mind(agent)
            if mind and not mind[1].memories:
                self._searches.pop(agent_id, None)  # a mind that forgot who it was stops looking
                continue
            if search["found"] is not None:
                if search["found"] < tick - 1:  # its turn after finding them has passed
                    self._searches.pop(agent_id, None)
                continue
            target = self._find_agent_by_name(search["target"])
            if target is None or not target.alive:
                await self._end_search(agent, search, "lost", tick)
            elif target.location == agent.location:
                await self._found(agent, search, tick)
            elif tick - search["since"] >= SEARCH_TICKS:
                await self._end_search(agent, search, "gave_up", tick)

    def _search_answered(self, agent: Agent, names: list[str]) -> None:
        """Talking to the person found closes the search."""
        search = self._searches.get(agent.id)
        if search and search["found"] is not None and search["target"] in names:
            self._searches.pop(agent.id, None)

    # ── Prompt and state ───────────────────────────────────────────────

    def _search_prompt(self, agent: Agent, tick: int) -> str:
        search = self._searches.get(agent.id)
        if not search:
            return ""
        name = search["target"]
        if search["found"] is not None:
            text = f"You found {name}: they are here."
            if search["message"]:
                text += f' You wanted to tell them: "{search["message"]}".'
            elif search["why"]:
                text += f" You were looking for them ({search['why']})."
            return text
        target = self._find_agent_by_name(name)
        if target is not None:
            self._own_clue(agent, search, target)
        why = f" ({search['why']})" if search["why"] else ""
        lines = [f"You are looking for {name}{why}."]
        clues = [
            f"{'you' if c['by'] == 'you' else c['by']} saw them at {c['place']} "
            f"({_ago(tick, tick - c['ago'])})"
            for c in self._clue_list(search, tick)
        ]
        if clues:
            lines.append("Clues: " + "; ".join(clues) + ".")
        if search["checked"]:
            lines.append("Already checked: " + ", ".join(search["checked"]) + ".")
        lines.append(
            f'Use "find" with target "{name}" to keep looking (ask the people here or go to '
            "the best clue), or do something else if it is more urgent."
        )
        return " ".join(lines)

    def _search_state(self, agent: Agent) -> dict | None:
        search = self._searches.get(agent.id)
        if not search:
            return None
        tick = self.clock.current_tick
        return {
            "target": search["target"],
            "why": search["why"],
            "found": search["found"] is not None,
            "checked": list(search["checked"]),
            "clues": self._clue_list(search, tick),
        }
