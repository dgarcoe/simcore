"""A richer society: attacking and healing with the right means, groups that are configured or
founded by the agents themselves, assemblies and votes, public reputation, favors owed, daily
routines and visible emotions. Mixed into SimulationEngine."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from simcore.agents.actions import Action, ActionType
from simcore.engine.effects import parse_effects
from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent

FISTS = (0.1, 0.6)  # damage, chance without a weapon
HEAL_BARE = 0.15  # what a healer does without an item
FRIENDS = 0.4  # mutual sentiment from which people may form a group
DISTRUST = -0.3  # a member this unhappy with the leader thinks of leaving
VOTE_TICKS = 2  # a vote stays open this long
FAVOR_TICKS = 24  # an unpaid favor sours things after this long
EMOTION_TICKS = 3  # a felt emotion lasts this long
THREAT_TICKS = 6  # someone who attacked recently is a fair target

EMOTIONS = {
    "angry": "looks furious",
    "afraid": "looks scared",
    "sad": "looks grief-stricken",
    "happy": "looks happy",
    "relieved": "looks relieved",
    "frustrated": "looks frustrated",
    "calm": "",
    "desperate": "looks desperate",
    "hopeful": "looks hopeful",
}
EMOTION_ALIASES = {
    "ira": "angry",
    "enfado": "angry",
    "enfadado": "angry",
    "furioso": "angry",
    "rabia": "angry",
    "anger": "angry",
    "miedo": "afraid",
    "asustado": "afraid",
    "fear": "afraid",
    "scared": "afraid",
    "terror": "afraid",
    "tristeza": "sad",
    "triste": "sad",
    "sadness": "sad",
    "pena": "sad",
    "alegria": "happy",
    "alegre": "happy",
    "feliz": "happy",
    "joy": "happy",
    "alivio": "relieved",
    "aliviado": "relieved",
    "frustracion": "frustrated",
    "frustrado": "frustrated",
    "calma": "calm",
    "tranquilo": "calm",
    "desesperacion": "desperate",
    "desesperado": "desperate",
    "esperanza": "hopeful",
    "esperanzado": "hopeful",
}


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def norm_emotion(text: str) -> str:
    key = _plain(text or "")
    if not key:
        return ""
    return key if key in EMOTIONS else EMOTION_ALIASES.get(key, "")


def reputation_label(value: float) -> str:
    if value >= 0.5:
        return "respected"
    if value >= 0.15:
        return "well regarded"
    if value <= -0.5:
        return "feared and hated"
    if value <= -0.15:
        return "distrusted"
    return ""


class SocietyMixin:
    """Needs the engine's config, agents, environment, event_bus, clock and the other mixins' helpers."""

    def _init_society(self) -> None:
        # faction name → {"goal", "leader", "members": [names], "rivals": set(), "color", "founded": tick}
        self._factions: dict[str, dict] = {}
        self._group_invites: dict[str, dict[str, str]] = {}  # invitee name → faction → inviter
        self._reputation: dict[str, float] = {}  # name → -1..1
        self._favors: list[dict] = []  # {"debtor", "creditor", "what", "tick", "soured"}
        self._feelings: dict[str, tuple[str, str, int]] = {}  # name → (emotion, toward, tick)
        self._votes: list[dict] = []
        self._aggressors: dict[str, int] = {}  # name → last tick they attacked someone
        self._decided: set[str] = set()  # configured votes already decided
        for name, f in self.config.factions.items():
            self._factions[name] = {
                "goal": f.goal,
                "leader": f.leader,
                "members": list(f.members),
                "rivals": set(f.rivals),
                "color": f.color,
                "founded": 0,
            }
        for a in self.config.agents:
            if a.faction:
                self._factions.setdefault(
                    a.faction,
                    {
                        "goal": "",
                        "leader": "",
                        "members": [],
                        "rivals": set(),
                        "color": "",
                        "founded": 0,
                    },
                )
                if a.name not in self._factions[a.faction]["members"]:
                    self._factions[a.faction]["members"].append(a.name)
        for f in self._factions.values():
            if not f["leader"] and f["members"]:
                f["leader"] = f["members"][0]

    # ── Groups ─────────────────────────────────────────────────────────

    def _faction_of(self, name: str) -> str:
        return next((f for f, d in self._factions.items() if name in d["members"]), "")

    async def _faction_event(
        self, faction: str, change: str, tick: int, agent: str = "", other: str = ""
    ) -> None:
        f = self._factions.get(faction, {})
        await self.event_bus.emit(
            SimEvent(
                type=EventType.FACTION_CHANGE,
                tick=tick,
                data={
                    "faction": faction,
                    "change": change,
                    "agent": agent,
                    "other": other,
                    "leader": f.get("leader", ""),
                    "members": list(f.get("members", [])),
                    "goal": f.get("goal", ""),
                },
                source=agent,
            )
        )

    async def _leave_faction(self, name: str, tick: int, change: str = "left") -> None:
        faction = self._faction_of(name)
        if not faction:
            return
        f = self._factions[faction]
        f["members"].remove(name)
        await self._faction_event(faction, change, tick, name)
        # Groups founded during the run dissolve when one person is left; configured ones when empty.
        if not f["members"] or (len(f["members"]) == 1 and f.get("founded", 0)):
            del self._factions[faction]
            await self._faction_event(faction, "dissolved", tick, name)
            return
        if f["leader"] == name:
            alive = [m for m in f["members"] if (a := self._find_agent_by_name(m)) and a.alive]
            if alive:
                f["leader"] = max(alive, key=lambda m: self._reputation.get(m, 0.0))
                for m in alive:
                    a = self._find_agent_by_name(m)
                    a.observe(f"{f['leader']} now leads {faction}.", tick, importance=0.6)
                await self._faction_event(faction, "leader", tick, f["leader"])

    async def _join_faction(self, name: str, faction: str, tick: int, invited_by: str = "") -> None:
        if self._faction_of(name) == faction:
            return
        await self._leave_faction(name, tick)
        f = self._factions.setdefault(
            faction,
            {
                "goal": "",
                "leader": name,
                "members": [],
                "rivals": set(),
                "color": "",
                "founded": tick,
            },
        )
        f["members"].append(name)
        self._group_invites.get(name, {}).pop(faction, None)
        await self._faction_event(faction, "joined", tick, name, invited_by)

    async def _apply_found_group(self, agent: Agent, action: Action, tick: int) -> Action:
        if not self.config.society.spontaneous_groups:
            return await self._refuse(agent, action, tick, "new groups can't be founded here")
        name = (action.content or "").strip()[:40] or f"{agent.name}'s group"
        if name in self._factions:
            return await self._refuse(
                agent, action, tick, f"there is already a group called {name}"
            )
        await self._leave_faction(agent.name, tick)
        self._factions[name] = {
            "goal": (action.goal or "").strip(),
            "leader": agent.name,
            "members": [agent.name],
            "rivals": set(),
            "color": "",
            "founded": max(tick, 1),
        }
        invited = [o for o in self._speech_targets(agent, action.target)] if action.target else []
        for o in invited:
            self._group_invites.setdefault(o.name, {})[name] = agent.name
            o.observe(
                f'{agent.name} founded "{name}" and invited me to join'
                + (f" (goal: {action.goal})" if action.goal else "")
                + ".",
                tick,
                importance=0.7,
            )
        agent.observe(f'I founded the group "{name}".', tick, importance=0.7)
        action.parameters = {
            **(action.parameters or {}),
            "faction": name,
            "invited": [o.name for o in invited],
        }
        await self._faction_event(
            name, "founded", tick, agent.name, ", ".join(o.name for o in invited)
        )
        return action

    async def _apply_join(self, agent: Agent, action: Action, tick: int) -> Action:
        wanted = (action.target or action.content or "").strip()
        faction = next((f for f in self._factions if _plain(f) == _plain(wanted)), "")
        invites = self._group_invites.get(agent.name, {})
        if not faction and len(invites) == 1:
            faction = next(iter(invites))
        if not faction:
            return await self._refuse(
                agent, action, tick, f"there is no group called {wanted or '?'}"
            )
        here = [
            m
            for m in self._factions[faction]["members"]
            if (a := self._find_agent_by_name(m)) and a.location == agent.location
        ]
        if faction not in invites and not here:
            return await self._refuse(
                agent, action, tick, f"nobody from {faction} is here to let me in"
            )
        await self._join_faction(agent.name, faction, tick, invites.get(faction, ""))
        action.target = faction
        return action

    async def _apply_leave(self, agent: Agent, action: Action, tick: int) -> Action:
        faction = self._faction_of(agent.name)
        if not faction:
            return await self._refuse(agent, action, tick, "I'm not in any group")
        await self._leave_faction(agent.name, tick)
        action.target = faction
        return action

    async def _apply_expel(self, agent: Agent, action: Action, tick: int) -> Action:
        faction = self._faction_of(agent.name)
        other = self._find_agent_by_name(action.target)
        if not faction or self._factions[faction]["leader"] != agent.name:
            return await self._refuse(
                agent, action, tick, "only a group's leader can expel someone"
            )
        if other is None or other.name not in self._factions[faction]["members"] or other is agent:
            return await self._refuse(agent, action, tick, f"{action.target} is not in my group")
        await self._leave_faction(other.name, tick, change="expelled")
        other.observe(f"{agent.name} expelled me from {faction}.", tick, importance=0.8)
        other.memory.update_relationship(agent.name, -0.3, tick, "expelled me")
        action.target = other.name
        return action

    async def _mark_rivals(self, a: str, b: str, tick: int) -> None:
        fa, fb = self._faction_of(a), self._faction_of(b)
        if fa and fb and fa != fb and fb not in self._factions[fa]["rivals"]:
            self._factions[fa]["rivals"].add(fb)
            self._factions[fb]["rivals"].add(fa)
            await self._faction_event(fa, "rival", tick, a, fb)

    # ── Attacking and healing ──────────────────────────────────────────

    def _weapon(self, agent: Agent) -> tuple[str, float, float, str]:
        """(item, damage, chance, ammo) of the best weapon the agent can use now; fists otherwise."""
        best = ("", FISTS[0], FISTS[1], "")
        for item, qty in agent.state.resources.items():
            spec = self.config.items.get(item)
            w = spec.weapon if spec else None
            if qty <= 0 or not w:
                continue
            if w.ammo and agent.state.resources.get(w.ammo, 0) < 1:
                continue
            if w.damage * w.chance > best[1] * best[2]:
                best = (item, w.damage, w.chance, w.ammo)
        return best

    def _is_threat(self, victim: Agent, tick: int) -> bool:
        if tick - self._aggressors.get(victim.name, -99) <= THREAT_TICKS:
            return True
        for name in victim.state.conditions:
            cond = self._condition(name)
            if cond and (cond.controller == "rules" or (cond.mind and not cond.mind.speech)):
                return True
        return False

    async def _apply_attack(self, agent: Agent, action: Action, tick: int) -> Action:
        victim = self._present(agent, action.target)
        if victim is None:
            return await self._refuse(
                agent, action, tick, f"{action.target or 'nobody'} is not here"
            )
        item, damage, chance, ammo = self._weapon(agent)
        if ammo:
            agent.state.resources[ammo] = agent.state.resources.get(ammo, 0) - 1
        threat = self._is_threat(victim, tick)
        hit = self._rng.random() < chance
        with_what = f" with the {item}" if item else ""
        params = {"weapon": item, "damage": 0.0, "killed": False, "hit": hit}
        if hit:
            victim.state.health = max(0.0, victim.state.health - damage)
            params["damage"] = round(damage, 2)
            victim.observe(f"{agent.name} attacked me{with_what}.", tick, importance=0.9)
            victim.memory.update_relationship(agent.name, -0.5, tick, "attacked me")
            self._feel(victim, "afraid" if damage >= 0.3 else "angry", agent.name, tick)
        else:
            victim.observe(f"{agent.name} tried to attack me{with_what}.", tick, importance=0.8)
            victim.memory.update_relationship(agent.name, -0.3, tick, "tried to attack me")
            self._feel(victim, "angry", agent.name, tick)
        self._feel(agent, "angry", victim.name, tick)
        self._aggressors[agent.name] = tick
        witnesses = [o for o in self._witnesses(agent) if o is not victim]
        for o in witnesses:
            o.observe(f"{agent.name} attacked {victim.name}{with_what}.", tick, importance=0.8)
            if not threat:
                self._feel(o, "afraid", agent.name, tick)
        self._rep(agent.name, 0.05 if threat else -0.3)  # the victim always counts as a witness
        await self._on_harm(agent, victim, tick, "attacked")
        await self._mark_rivals(agent.name, victim.name, tick)
        if hit and victim.state.health <= 0:
            params["killed"] = True
            await self.kill(victim, tick, f"{agent.name}{with_what}")
        action.target = victim.name
        action.parameters = {**(action.parameters or {}), **params}
        return action

    def _healing_item(self, agent: Agent, patient: Agent) -> str:
        best, score = "", 0.0
        for item, qty in agent.state.resources.items():
            spec = self.config.items.get(item)
            if qty <= 0 or not spec:
                continue
            cures = [c for c in spec.cures if c in patient.state.conditions]
            heals = spec.on_use.get("health", 0.0)
            value = len(cures) * 2 + heals
            if (cures or heals > 0) and value > score:
                best, score = item, value
        return best

    async def _apply_heal(self, agent: Agent, action: Action, tick: int) -> Action:
        target = (action.target or "").strip()
        patient = (
            agent
            if not target or _plain(target) in (_plain(agent.name), "self", "me", "myself", "yo")
            else self._present(agent, target)
        )
        if patient is None:
            return await self._refuse(agent, action, tick, f"{target} is not here")
        before = patient.state.health
        item = self._healing_item(agent, patient)
        if item:
            used = await self._apply_item_action(
                agent, Action(type=ActionType.USE, item=item, target=patient.name), tick
            )
            if used.type != ActionType.USE:
                return used
        elif self._is_healer(agent):
            patient.state.health = min(1.0, patient.state.health + HEAL_BARE)
            for name in list(patient.state.conditions):
                if name in self.config.society.heal_removes:
                    await self.remove_condition(patient, name, tick, f"treated by {agent.name}")
            if patient is not agent:
                patient.observe(f"{agent.name} treated my wounds.", tick, importance=0.7)
                patient.memory.update_relationship(agent.name, 0.15, tick, "treated my wounds")
        else:
            return await self._refuse(
                agent, action, tick, "I have nothing to heal with and I'm no healer"
            )
        if patient is not agent:
            self._rep(agent.name, 0.1)
            self._owe(patient.name, agent.name, "being healed", tick)
            self._feel(patient, "relieved", agent.name, tick)
        action.target = "" if patient is agent else patient.name
        action.item = item
        action.parameters = {
            **(action.parameters or {}),
            "healed": round(patient.state.health - before, 2),
            "with": item or "care",
        }
        return action

    def _is_healer(self, agent: Agent) -> bool:
        prof = _plain(agent.persona.profession)
        return any(_plain(h) in prof for h in self.config.society.healers)

    # ── Reputation, favors, feelings ───────────────────────────────────

    def _rep(self, name: str, delta: float) -> None:
        self._reputation[name] = max(-1.0, min(1.0, self._reputation.get(name, 0.0) + delta))

    def _owe(self, debtor: str, creditor: str, what: str, tick: int) -> None:
        self._favors.append(
            {"debtor": debtor, "creditor": creditor, "what": what, "tick": tick, "soured": False}
        )

    def _repay(self, payer: str, receiver: str) -> None:
        """Giving to (or healing) someone you owe settles your oldest debt with them."""
        for f in self._favors:
            if f["debtor"] == payer and f["creditor"] == receiver:
                self._favors.remove(f)
                return

    def _feel(self, agent: Agent, emotion: str, toward: str, tick: int) -> None:
        if emotion:
            self._feelings[agent.name] = (emotion, toward, tick)

    def _emotion(self, agent: Agent, tick: int) -> tuple[str, str]:
        felt = self._feelings.get(agent.name)
        if felt and tick - felt[2] <= EMOTION_TICKS:
            return felt[0], felt[1]
        mood = agent.state.mood
        if mood <= -0.6:
            return "desperate", ""
        if mood <= -0.3:
            return "sad", ""
        if mood >= 0.5:
            return "happy", ""
        return "calm", ""

    async def _after_action_society(self, agent: Agent, action: Action, tick: int) -> None:
        emotion = norm_emotion(action.emotion)
        if emotion:
            self._feel(agent, emotion, "", tick)
        if action.type == ActionType.GIVE and action.target:
            self._repay(agent.name, action.target)
            self._owe(action.target, agent.name, f"{action.item or 'a gift'}", tick)
            self._rep(agent.name, 0.05)
        if action.type == ActionType.HEAL and action.target:
            self._repay(agent.name, action.target)

    # ── Votes ──────────────────────────────────────────────────────────

    async def _apply_call_vote(self, agent: Agent, action: Action, tick: int) -> Action:
        asked = (action.content or "").strip()
        if not asked:
            return await self._refuse(agent, action, tick, "a vote needs a question")
        if any(v["place"] == agent.location for v in self._votes):
            return await self._refuse(agent, action, tick, "there is already a vote here")
        key = next(
            (
                k
                for k, v in self.config.votes.items()
                if _plain(k) == _plain(asked) or _plain(v.question) == _plain(asked)
            ),
            "",
        )
        cfg = self.config.votes.get(key)
        if cfg and not self._can_vote_on(key, tick):
            return await self._refuse(agent, action, tick, "that matter can't be put to the vote now")
        question = cfg.question if cfg else asked
        options = (
            list(cfg.options)
            if cfg
            else [o.strip() for o in action.options if str(o).strip()] or ["yes", "no"]
        )
        vote = {
            "id": key,
            "question": question,
            "options": options,
            "place": agent.location,
            "caller": agent.name,
            "until": tick + (cfg.duration if cfg else VOTE_TICKS),
            "ballots": {},
        }
        self._votes.append(vote)
        for o in self.living_agents:
            if o.location == agent.location and o is not agent:
                o.observe(
                    f'{agent.name} called a vote: "{question}" ({" / ".join(options)}).',
                    tick,
                    importance=0.7,
                )
        action.parameters = {**(action.parameters or {}), "question": question, "options": options}
        await self.event_bus.emit(
            SimEvent(
                type=EventType.VOTE_CALLED,
                tick=tick,
                data={
                    "question": question,
                    "options": options,
                    "place": agent.location,
                    "caller": agent.name,
                    "until": vote["until"],
                },
                source=agent.name,
            )
        )
        return action

    def _can_vote_on(self, key: str, tick: int) -> bool:
        cfg = self.config.votes[key]
        return tick >= cfg.from_tick and key not in self._decided and not any(v["id"] == key for v in self._votes)

    async def _apply_vote(self, agent: Agent, action: Action, tick: int) -> Action:
        vote = next((v for v in self._votes if v["place"] == agent.location), None)
        if vote is None:
            return await self._refuse(agent, action, tick, "there is no vote here")
        choice = (action.content or action.target or "").strip()
        option = next((o for o in vote["options"] if _plain(o) == _plain(choice)), "")
        if not option:
            return await self._refuse(
                agent, action, tick, f"'{choice}' is not an option ({', '.join(vote['options'])})"
            )
        vote["ballots"][agent.name] = option
        action.content = option
        action.parameters = {**(action.parameters or {}), "question": vote["question"]}
        return action

    async def _close_votes(self, tick: int) -> None:
        for vote in [v for v in self._votes if tick > v["until"]]:
            self._votes.remove(vote)
            counts = {o: 0 for o in vote["options"]}
            for option in vote["ballots"].values():
                counts[option] += 1
            top = max(counts.values()) if counts else 0
            winners = [o for o, c in counts.items() if c == top]
            winner = winners[0] if top > 0 and len(winners) == 1 else ""
            outcome = (
                f'"{vote["question"]}": {winner}'
                if winner
                else f'"{vote["question"]}": no decision'
            )
            self.environment.log_event(
                tick, "vote", f"Vote at {vote['place']} — {outcome}", location=vote["place"]
            )
            for o in self.living_agents:
                if o.location == vote["place"] or o.name in vote["ballots"]:
                    o.observe(
                        f"The vote {outcome} ({', '.join(f'{k} {v}' for k, v in counts.items())}).",
                        tick,
                        importance=0.7,
                    )
            await self.event_bus.emit(
                SimEvent(
                    type=EventType.VOTE_RESULT,
                    tick=tick,
                    data={
                        "question": vote["question"],
                        "options": vote["options"],
                        "counts": counts,
                        "winner": winner,
                        "place": vote["place"],
                        "caller": vote["caller"],
                        "ballots": dict(vote["ballots"]),
                    },
                )
            )
            cfg = self.config.votes.get(vote["id"])
            if cfg and winner and cfg.once:
                self._decided.add(vote["id"])
            if cfg and winner and cfg.options.get(winner):
                await self.apply_effects(parse_effects(cfg.options[winner]), tick, cause=outcome)

    # ── Each tick ──────────────────────────────────────────────────────

    async def _society_tick(self, tick: int) -> None:
        await self._close_votes(tick)
        for f in self._favors:
            if not f["soured"] and tick - f["tick"] >= FAVOR_TICKS:
                f["soured"] = True
                debtor = self._find_agent_by_name(f["debtor"])
                creditor = self._find_agent_by_name(f["creditor"])
                if debtor and creditor and debtor.alive and creditor.alive:
                    creditor.memory.update_relationship(
                        debtor.name, -0.2, tick, f"never repaid {f['what']}"
                    )
                    creditor.observe(
                        f"{debtor.name} never repaid me for {f['what']}.", tick, importance=0.5
                    )
                    self._rep(debtor.name, -0.05)
        for faction, f in list(self._factions.items()):
            for m in list(f["members"]):
                a = self._find_agent_by_name(m)
                if a is None or not a.alive:
                    await self._leave_faction(m, tick, change="left")

    def _phase(self, tick: int) -> tuple[str, str]:
        minutes = tick * self.clock.tick_duration_minutes
        hour = (minutes % 1440) // 60
        phase = (
            "night"
            if hour >= 22 or hour < 6
            else "morning"
            if hour < 12
            else "day"
            if hour < 19
            else "evening"
        )
        return f"{hour:02d}:{minutes % 60:02d}", phase

    def _routine(self, agent: Agent, phase: str) -> dict[str, list[str]]:
        routines = self.config.routines
        prof = _plain(agent.persona.profession)
        for key in (
            agent.name,
            *[k for k in routines if k not in (agent.name, "*") and _plain(k) in prof],
            "*",
        ):
            if key in routines and phase in routines[key]:
                return routines[key][phase]
        return {}

    # ── Prompt and state ───────────────────────────────────────────────

    def _society_prompt(self, agent: Agent, tick: int) -> str:
        lines = []
        clock, phase = self._phase(tick)
        routine = self._routine(agent, phase)
        if routine:
            todo = "; ".join(
                f"{act}" + (f" at {'/'.join(places)}" if places else "")
                for act, places in routine.items()
            )
            lines.append(f"It is {clock} ({phase}). At this time you usually: {todo}.")
        else:
            usual = {
                "night": "most people sleep",
                "morning": "people start their day",
                "day": "people work and run errands",
                "evening": "people gather and rest",
            }[phase]
            lines.append(f"It is {clock} ({phase}): {usual}.")

        faction = self._faction_of(agent.name)
        if faction:
            f = self._factions[faction]
            others = [m for m in f["members"] if m != agent.name]
            lead = "you lead it" if f["leader"] == agent.name else f"leader: {f['leader']}"
            text = f"You belong to {faction} ({lead}{'; goal: ' + f['goal'] if f['goal'] else ''}"
            text += f"; members: {', '.join(others)})" if others else ")"
            if f["rivals"]:
                text += f". Rival groups: {', '.join(sorted(f['rivals']))}"
            lines.append(text + ".")
            leader = self._find_agent_by_name(f["leader"])
            rel = (
                agent.memory.relationships.get(f["leader"])
                if leader and leader is not agent
                else None
            )
            if rel and rel.sentiment <= DISTRUST:
                lines.append(f'You no longer trust {f["leader"]}: you could "leave" the group.')
        others_groups = [
            f"{n} (leader {d['leader']})" for n, d in self._factions.items() if n != faction
        ]
        if others_groups:
            lines.append("Other groups: " + "; ".join(others_groups) + ".")
        for name, inviter in self._group_invites.get(agent.name, {}).items():
            if name in self._factions:
                lines.append(
                    f'{inviter} invited you to join "{name}": use "join" with target "{name}" if you want.'
                )
        if self.config.society.spontaneous_groups:
            here = [
                o
                for o in self._witnesses(agent)
                if not self._faction_of(o.name) or self._faction_of(o.name) != faction
            ]
            friends = [
                o.name
                for o in here
                if (r1 := agent.memory.relationships.get(o.name))
                and r1.sentiment >= FRIENDS
                and (r2 := o.memory.relationships.get(agent.name))
                and r2.sentiment >= FRIENDS
            ]
            if len(friends) >= 2 and not faction:
                lines.append(
                    f"{' and '.join(friends[:3])} think like you: you could form a group together "
                    f'("found_group", content: its name, goal: what it is for, target: who you invite).'
                )

        for vote in self._votes:
            if vote["place"] == agent.location and agent.name not in vote["ballots"]:
                lines.append(
                    f'There is a vote here: "{vote["question"]}" (options: {", ".join(vote["options"])}). '
                    f'Vote with "vote" and content: your option.'
                )
        open_matters = [v.question for k, v in self.config.votes.items() if self._can_vote_on(k, tick)]
        if (
            open_matters
            and len(self._witnesses(agent)) >= 2
            and not any(v["place"] == agent.location for v in self._votes)
        ):
            lines.append(
                'Matters you can put to a vote here ("call_vote", content: the matter): '
                + "; ".join(open_matters)
                + "."
            )

        fame = [
            f"{o.name} ({lbl})"
            for o in self._witnesses(agent)
            if (lbl := reputation_label(self._reputation.get(o.name, 0.0)))
        ]
        if fame:
            lines.append("Reputation of the people here: " + "; ".join(fame) + ".")
        owe = [f"{f['creditor']} ({f['what']})" for f in self._favors if f["debtor"] == agent.name]
        owed = [f"{f['debtor']} ({f['what']})" for f in self._favors if f["creditor"] == agent.name]
        if owe:
            lines.append("You owe favors to: " + "; ".join(owe) + ".")
        if owed:
            lines.append("People who owe you: " + "; ".join(owed) + ".")

        emotion, toward = self._emotion(agent, tick)
        if emotion and emotion != "calm":
            lines.append(f"You feel {emotion}" + (f" towards {toward}" if toward else "") + ".")
        means = []
        item, damage, _, _ = self._weapon(agent)
        means.append(
            f'"attack" someone here ({f"your {item}: damage {damage:.1f}" if item else "only your fists"})'
        )
        healing = [
            i
            for i, q in agent.state.resources.items()
            if q > 0
            and (spec := self.config.items.get(i))
            and (spec.on_use.get("health", 0) > 0 or spec.cures)
        ]
        if healing or self._is_healer(agent):
            means.append(
                f'"heal" someone ({", ".join(healing) if healing else "with your skills"})'
            )
        lines.append("You can " + " or ".join(means) + ".")
        return "\n".join(lines)

    def _looks(self, other: Agent, tick: int) -> str:
        emotion, _ = self._emotion(other, tick)
        return EMOTIONS.get(emotion, "")

    def _society_state(self, agent: Agent) -> dict:
        tick = self.clock.current_tick
        emotion, toward = self._emotion(agent, tick)
        data = {"emotion": emotion, "reputation": round(self._reputation.get(agent.name, 0.0), 2)}
        faction = self._faction_of(agent.name)
        if faction:
            data["faction"] = faction
        if toward:
            data["emotion_toward"] = toward
        owe = [
            {"to": f["creditor"], "what": f["what"]}
            for f in self._favors
            if f["debtor"] == agent.name
        ]
        if owe:
            data["owes"] = owe
        return data

    def society_state(self) -> dict:
        return {
            "factions": {
                n: {
                    "goal": f["goal"],
                    "leader": f["leader"],
                    "members": list(f["members"]),
                    "rivals": sorted(f["rivals"]),
                    "color": f["color"],
                }
                for n, f in self._factions.items()
            },
            "votes": [
                {
                    "question": v["question"],
                    "options": v["options"],
                    "place": v["place"],
                    "until": v["until"],
                    "votes": len(v["ballots"]),
                }
                for v in self._votes
            ],
        }

    def _count_faction(self, faction: str) -> int:
        f = self._factions.get(faction)
        return (
            sum(1 for m in f["members"] if (a := self._find_agent_by_name(m)) and a.alive)
            if f
            else 0
        )
