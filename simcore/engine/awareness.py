"""Situational awareness and private talk: what the people here are doing, where everyone was
last seen, empty places, who would overhear; whisper, invite someone aside and follow someone.
Mixed into SimulationEngine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from simcore.agents.actions import Action, ActionType
from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent

LEFT_TICKS = 3  # "Carla went to the Farm" is shown this long
INVITE_TICKS = 3  # an invitation (and the wait for it) lasts this long
FOLLOW_PASSES = 3  # chains of followers resolved per tick


def _ago(now: int, then: int) -> str:
    d = now - then
    return "just now" if d <= 0 else f"{d} tick{'s' if d > 1 else ''} ago"


class AwarenessMixin:
    """Needs the engine's agents, environment, event_bus and the conversation helpers."""

    def _init_awareness(self) -> None:
        # observer id → name → (place, tick, how: "seen" | "left")
        self._last_seen: dict[str, dict[str, tuple[str, int, str]]] = {}
        # agent id → (tick, description of what they did)
        self._doing: dict[str, tuple[int, str]] = {}
        # agent id → (tick, from place)
        self._arrived: dict[str, tuple[int, str]] = {}
        # invitee id → inviter name → {"place", "text", "tick"}
        self._invites: dict[str, dict[str, dict]] = {}
        # inviter id → {"who": [names], "place", "until"}
        self._waiting: dict[str, dict] = {}
        self._follows: list[tuple[Agent, str]] = []

    # ── What everyone sees ─────────────────────────────────────────────

    def _see_around(self, tick: int) -> None:
        """At the start of a tick, everyone notes who is with them."""
        living = self.living_agents
        for agent in living:
            seen = self._last_seen.setdefault(agent.id, {})
            for other in living:
                if other.id != agent.id and other.location == agent.location:
                    seen[other.name] = (agent.location, tick, "seen")

    def _note_move(self, agent: Agent, origin: str, dest: str, tick: int) -> None:
        self._arrived[agent.id] = (tick, origin)
        for other in self.living_agents:
            if other.id != agent.id and other.location == origin:
                self._last_seen.setdefault(other.id, {})[agent.name] = (dest, tick, "left")
        for waiter in self.living_agents:
            wait = self._waiting.get(waiter.id)
            if wait and agent.name in wait["who"] and dest == wait["place"] == waiter.location:
                waiter.observe(f"{agent.name} came to meet me at {dest}.", tick, importance=0.6)
                wait["who"].remove(agent.name)
                if not wait["who"]:
                    del self._waiting[waiter.id]
        box = self._invites.get(agent.id)
        if box:
            for name in [n for n, inv in box.items() if inv["place"] == dest]:
                del box[name]

    def _note_doing(self, agent: Agent, action: Action, tick: int) -> None:
        self._doing[agent.id] = (tick, action.describe())

    # ── Prompt ─────────────────────────────────────────────────────────

    def _where_is(self, agent: Agent, other: Agent) -> tuple[str, int, str] | None:
        if self.config.awareness == "full":
            return (other.location, self.clock.current_tick, "seen")
        return self._last_seen.get(agent.id, {}).get(other.name)

    def _awareness_prompt(self, agent: Agent, tick: int, speech: bool = True) -> str:
        lines = []
        here = [a for a in self.living_agents if a.location == agent.location and a.id != agent.id]
        if here:
            lines.append("What the people here are doing:")
            for o in here:
                bits = []
                came = self._arrived.get(o.id)
                if came and tick - came[0] <= 1 and came[1] and came[1] != o.location:
                    bits.append(f"just arrived from {came[1]}")
                doing = self._doing.get(o.id)
                if doing and doing[0] < tick and not (came and came[0] == doing[0]):
                    bits.append(doing[1])
                looks = self._looks(o, tick)
                if looks:
                    bits.append(looks)
                if o.state.health < 0.3:
                    bits.append("badly hurt")
                elif o.state.health < 0.6:
                    bits.append("hurt")
                lines.append(f"- {o.name}: {'; '.join(bits) or 'nothing in particular'}")

        left, elsewhere = [], []
        for o in self.living_agents:
            if o.id == agent.id or o in here:
                continue
            seen = self._where_is(agent, o)
            if seen is None:
                continue
            place, when, how = seen
            if how == "left" and tick - when <= LEFT_TICKS:
                left.append(f"{o.name} went to {place} ({_ago(tick, when)})")
            elif self.config.awareness == "full":
                elsewhere.append(f"{o.name} at {place}")
            else:
                elsewhere.append(f"{o.name} at {place} ({_ago(tick, when)})")
        if left:
            lines.append("Who left: " + "; ".join(left) + ".")
        if elsewhere:
            label = "Where the others are" if self.config.awareness == "full" else "Last seen"
            lines.append(f"{label}: " + "; ".join(elsewhere) + ".")

        empty = self._empty_places(agent, tick)
        if empty:
            lines.append("Places with nobody there (as far as you know): " + ", ".join(empty) + ".")

        if here and speech:
            lines.append(
                f'If you "speak" here, {", ".join(o.name for o in here)} will hear it. '
                '"whisper" and "interact" are private. To talk alone, "invite" someone to a '
                'quiet place ("place") or "follow" them.'
            )

        box = self._invites.get(agent.id, {})
        for name in list(box):
            if (
                tick - box[name]["tick"] > INVITE_TICKS
                or self._find_agent_by_name(name) is None
                or not self._find_agent_by_name(name).alive
            ):
                del box[name]
        for name, inv in box.items():
            words = f': "{inv["text"]}"' if inv["text"] else ""
            lines.append(
                f"{name} asks you to come to {inv['place']}{words}. "
                f'Go there ("move") if you want to meet them, or ignore it.'
            )
        wait = self._waiting.get(agent.id)
        if wait and (wait["until"] < tick or agent.location != wait["place"]):
            self._waiting.pop(agent.id, None)
            wait = None
        if wait:
            lines.append(f"You are waiting for {', '.join(wait['who'])} at {wait['place']}.")
        return "\n".join(lines)

    def _empty_places(self, agent: Agent, tick: int) -> list[str]:
        places = [
            p
            for p in self.environment.locations.values()
            if not p.virtual and p.type != "virtual" and p.name != agent.location and not p.closed
        ]
        if self.config.awareness == "full":
            busy = {a.location for a in self.living_agents}
        else:
            busy = {
                place
                for place, when, _ in self._last_seen.get(agent.id, {}).values()
                if tick - when <= LEFT_TICKS * 2
            }
        return [p.name for p in places if p.name not in busy][:5]

    # ── Private talk ───────────────────────────────────────────────────

    def _pick_quiet_place(self, agent: Agent, text: str) -> str:
        """A place named in the words, or the first empty place the agent knows of."""
        low = (text or "").lower()
        for p in self.environment.locations.values():
            if p.name.lower() in low and p.name != agent.location:
                return p.name
        empty = self._empty_places(agent, self.clock.current_tick)
        return empty[0] if empty else ""

    async def _apply_invite(self, agent: Agent, action: Action, tick: int) -> Action:
        targets = self._speech_targets(agent, action.target)
        if not targets:
            return Action(type=ActionType.WAIT, reasoning="Nobody to invite here")
        place = action.place.strip() if action.place else ""
        loc = self.environment.get_location(place) if place else None
        if loc is None:
            loc = self.environment.get_location(
                self._pick_quiet_place(agent, place or action.content)
            )
        if loc is None or loc.name == agent.location:
            return await self._refuse(agent, action, tick, "there is no quieter place to go")
        allowed, why = self._can_enter(agent, loc)
        if not allowed or not self.environment.move_agent(agent.id, agent.location, loc.name):
            return await self._refuse(agent, action, tick, why or "it is full")
        origin = agent.location
        names = [t.name for t in targets]
        action.target = ", ".join(names)
        action.place = loc.name
        action.parameters = {**(action.parameters or {}), "targets": names, "from": origin}
        for t in targets:
            self._invites.setdefault(t.id, {})[agent.name] = {
                "place": loc.name,
                "text": (action.content or "").strip(),
                "tick": tick,
            }
            t.observe(
                f"{agent.name} asked me to come to {loc.name}"
                + (f': "{action.content}"' if action.content else "."),
                tick,
                importance=0.7,
            )
        for o in self.living_agents:
            if o.id != agent.id and o not in targets and o.location == origin:
                o.observe(
                    f"{agent.name} took {', '.join(names)} aside and left for {loc.name}.",
                    tick,
                    importance=0.4,
                )
        self._waiting[agent.id] = {"who": names, "place": loc.name, "until": tick + INVITE_TICKS}
        agent.state.location = loc.name
        self._note_move(agent, origin, loc.name, tick)
        return action

    def _apply_follow(self, agent: Agent, action: Action, tick: int) -> Action:
        leader = self._present(agent, action.target)
        if leader is None:
            return Action(type=ActionType.WAIT, reasoning="The person to follow is not here")
        action.target = leader.name
        self._follows.append((agent, leader.name))
        return action

    async def _resolve_follows(self, tick: int) -> None:
        """After everyone decided: followers go wherever their leader ended up this tick."""
        pending, self._follows = self._follows, []
        for _ in range(FOLLOW_PASSES):
            moved = False
            for follower, name in pending:
                leader = self._find_agent_by_name(name)
                if not follower.alive or leader is None or not leader.alive:
                    continue
                if leader.location == follower.location:
                    continue
                dest = self.environment.get_location(leader.location)
                allowed, why = self._can_enter(follower, dest) if dest else (False, "unknown place")
                if not allowed or not self.environment.move_agent(
                    follower.id, follower.location, dest.name
                ):
                    follower.observe(
                        f"I couldn't follow {leader.name} to {leader.location}: "
                        f"{why or 'it is full'}.",
                        tick,
                        importance=0.5,
                    )
                    continue
                origin = follower.location
                follower.state.location = dest.name
                self._note_move(follower, origin, dest.name, tick)
                moved = True
                await self.event_bus.emit(
                    SimEvent(
                        type=EventType.AGENT_MOVE,
                        tick=tick,
                        data={
                            "action": f"moves to {dest.name}",
                            "reasoning": f"following {leader.name}",
                            "action_type": "move",
                            "target": dest.name,
                            "content": "",
                            "follow": leader.name,
                        },
                        source=follower.name,
                    )
                )
            if not moved:
                break
