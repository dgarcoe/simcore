"""Conversations: talking to several people at once, everyone present overhears, questions
waiting for an answer and conversations going in circles. Mixed into SimulationEngine."""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from simcore.agents.actions import Action, ActionType
from simcore.engine.events import EventType, SimEvent

if TYPE_CHECKING:
    from simcore.agents.agent import Agent

EVERYONE = re.compile(
    r"^(everyone|everybody|all|all of you|todos|todas|todo el mundo|todo mundo|vosotros|ustedes)$",
    re.IGNORECASE,
)
SPLIT = re.compile(r"\s*(?:,|;|/|&|\+|\by\b|\band\b)\s*", re.IGNORECASE)
TITLES = {
    "dr",
    "dra",
    "sr",
    "sra",
    "srta",
    "don",
    "dona",
    "doña",
    "mr",
    "mrs",
    "ms",
    "miss",
    "sir",
    "padre",
    "father",
    "señor",
    "señora",
    "senhor",
    "senhora",
    "capitan",
    "capitán",
    "captain",
    "doctor",
    "doctora",
}

LOG_SIZE = 200
SHOWN_LINES = 6  # recent lines shown in the prompt
HEARD_TICKS = 3  # how far back "recent conversation here" goes
INBOX_TICKS = 6  # an unanswered line is forgotten after this
SIMILAR = 0.8  # difflib ratio from which two lines count as the same thing
NOTE_TICKS = 3  # how long a "going in circles" note stays in the prompt
IGNORED_TURNS = 2  # turns a listener lets pass without answering before the speaker gives up


def norm_name(text: str) -> str:
    """Lowercase, no accents, no punctuation, no titles: 'Dr. Álvarez' → 'alvarez'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = re.sub(r"[^\w ]", " ", text.lower()).split()
    kept = [w for w in words if w not in _TITLES_ASCII]
    return " ".join(kept or words)


_TITLES_ASCII = {
    unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode() for t in TITLES
}


def _similar(a: str, b: str) -> bool:
    x, y = a.lower().strip(), b.lower().strip()
    return bool(x and y) and SequenceMatcher(None, x, y).ratio() >= SIMILAR


class ConversationsMixin:
    """Needs the engine's agents, living_agents, _find_agent_by_name, _present and _wake."""

    def _init_conversations(self) -> None:
        self._speech_log: deque[dict] = deque(maxlen=LOG_SIZE)
        # listener id → speaker name → {"text", "count", "tick"}
        self._inbox: dict[str, dict[str, dict]] = {}
        # agent id → [(note, until_tick)]
        self._conv_notes: dict[str, list[tuple[str, int]]] = {}

    # ── Who is being spoken to ─────────────────────────────────────────

    def _match_agent(self, name: str) -> Agent | None:
        """Tolerant lookup: no accents, case or titles; a unique first name or surname works."""
        key = norm_name(name)
        if not key:
            return None
        for pool in (self.living_agents, list(self.agents.values())):
            exact = [a for a in pool if norm_name(a.name) == key]
            if len(exact) == 1:
                return exact[0]
            if len(key) >= 3:
                part = [
                    a
                    for a in pool
                    if key in norm_name(a.name).split() or norm_name(a.name).startswith(key)
                ]
                if len(part) == 1:
                    return part[0]
        return None

    def _speech_targets(self, agent: Agent, target: str) -> list[Agent]:
        """'Ana', 'Ana, Bob', 'Ana y Bob', 'everyone'… → the living people here it names."""
        if not isinstance(target, str) or not target.strip():
            return []
        here = [a for a in self.living_agents if a.location == agent.location and a.id != agent.id]
        if EVERYONE.match(target.strip()):
            return here
        whole = self._find_agent_by_name(target)
        if whole is not None:
            return [whole] if whole in here else []
        found: list[Agent] = []
        for part in SPLIT.split(target):
            other = self._find_agent_by_name(part) if part.strip() else None
            if other is not None and other in here and other not in found:
                found.append(other)
        return found

    def _addressees(self, agent: Agent, action: Action) -> list[Agent]:
        """Everyone an action is aimed at (several for speech, one for the rest)."""
        names = action.parameters.get("targets") if action.parameters else None
        if names:
            out = [self._present(agent, n) for n in names]
            return [a for a in out if a is not None]
        other = self._present(agent, action.target) if action.target else None
        return [other] if other else []

    def _check_speech(self, agent: Agent, action: Action) -> Action:
        """Keep whoever is here among the people spoken to; wait only if nobody is."""
        targets = self._speech_targets(agent, action.target)
        if not targets:
            named = self._find_agent_by_name(action.target)
            if named and named.state.status == "dead" and named.location == agent.location:
                return Action(type=ActionType.WAIT, reasoning=f"{named.name} is dead")
            return Action(type=ActionType.WAIT, reasoning="Target not present at this location")
        names = [a.name for a in targets]
        action.target = ", ".join(names)
        action.parameters = {**(action.parameters or {}), "targets": names}
        return action

    # ── Hearing ─────────────────────────────────────────────────────────

    def _record_speech(self, agent: Agent, action: Action, tick: int) -> None:
        targets = self._addressees(agent, action)
        text = (action.content or "").strip()
        if not targets or not text:
            return
        names = [t.name for t in targets]
        public = action.type == ActionType.SPEAK
        self._note_repetition(agent, names, text, tick)
        self._speech_log.append(
            {
                "tick": tick,
                "speaker": agent.name,
                "to": names,
                "text": text,
                "location": agent.location,
                "public": public,
            }
        )
        # Speaking to someone answers them.
        mine = self._inbox.get(agent.id)
        if mine:
            for n in names:
                mine.pop(n, None)
        for t in targets:
            box = self._inbox.setdefault(t.id, {})
            prev = box.get(agent.name)
            count = prev["count"] + 1 if prev and _similar(prev["text"], text) else 1
            box[agent.name] = {"text": text, "count": count, "tick": tick, "skipped": 0}
        if action.type == ActionType.WHISPER:
            for other in self.living_agents:
                if (
                    other.id != agent.id
                    and other not in targets
                    and other.location == agent.location
                ):
                    other.observe(
                        f"I saw {agent.name} whisper something to {', '.join(names)}.",
                        tick,
                        importance=0.3,
                    )
        if public:
            to = ", ".join(names)
            for other in self.living_agents:
                if (
                    other.id != agent.id
                    and other not in targets
                    and other.location == agent.location
                ):
                    other.observe(
                        f'I heard {agent.name} say to {to}: "{text}"', tick, importance=0.4
                    )

    async def _check_ignored(self, agent: Agent, action: Action, tick: int) -> None:
        """A listener who could answer and did something else is ignoring the speaker."""
        box = self._inbox.get(agent.id)
        if not box:
            return
        addressed = set(action.parameters.get("targets") or []) if action.parameters else set()
        if action.target:
            addressed.add(action.target)
        for name, msg in list(box.items()):
            speaker = self._present(agent, name)
            if speaker is None or msg["tick"] >= tick or name in addressed:
                continue  # gone, said this very tick, or answered
            msg["skipped"] = msg.get("skipped", 0) + 1
            if msg["skipped"] < IGNORED_TURNS:
                continue
            del box[name]
            self._note(
                speaker,
                f'{agent.name} has ignored you {msg["skipped"]} times ("{msg["text"]}"). '
                "Don't insist: "
                "assume they won't answer and change course: ask someone else, go somewhere else "
                "or act on your own.",
                tick,
            )
            speaker.observe(f"{agent.name} is ignoring me.", tick, importance=0.6)
            speaker.memory.update_relationship(agent.name, -0.05, tick, "ignored me")
            self._wake(speaker)
            await self.event_bus.emit(
                SimEvent(
                    type=EventType.SPEECH_IGNORED,
                    tick=tick,
                    data={
                        "agent": speaker.name,
                        "by": agent.name,
                        "text": msg["text"],
                        "turns": msg["skipped"],
                    },
                    source=agent.name,
                )
            )

    def _note(self, agent: Agent, text: str, tick: int) -> None:
        notes = [n for n in self._conv_notes.get(agent.id, []) if n[1] >= tick and n[0] != text]
        notes.append((text, tick + NOTE_TICKS))
        self._conv_notes[agent.id] = notes[-3:]

    def _note_repetition(self, agent: Agent, names: list[str], text: str, tick: int) -> None:
        """Repeating yourself, or what someone here already asked, adds a note to the prompt."""
        mine = [
            e
            for e in self._speech_log
            if e["speaker"] == agent.name and set(e["to"]) & set(names) and tick - e["tick"] <= 8
        ][-4:]
        if any(_similar(e["text"], text) for e in mine):
            who = ", ".join(names)
            self._note(
                agent,
                f"You keep saying the same thing to {who} and the conversation is going in "
                "circles. Don't ask it again: act on what you know, do something else, "
                "or say something genuinely new.",
                tick,
            )
            agent.observe(f"My conversation with {who} is going in circles.", tick, importance=0.6)
            self._wake(agent)
            return
        for i, e in enumerate(self._speech_log):
            if (
                e["speaker"] == agent.name
                or e["location"] != agent.location
                or not e["public"]
                or tick - e["tick"] > HEARD_TICKS
                or not _similar(e["text"], text)
            ):
                continue
            answer = next(
                (
                    r
                    for r in list(self._speech_log)[i + 1 :]
                    if r["speaker"] in e["to"] and e["speaker"] in r["to"]
                ),
                None,
            )
            note = f'{e["speaker"]} already said that ("{e["text"]}")'
            note += f', and {answer["speaker"]} answered: "{answer["text"]}".' if answer else "."
            self._note(agent, note + " No need to repeat it.", tick)
            return

    # ── Prompt ──────────────────────────────────────────────────────────

    def _conversation_prompt(self, agent: Agent, tick: int) -> str:
        lines = []
        recent = [
            e
            for e in self._speech_log
            if tick - e["tick"] <= HEARD_TICKS
            and e["location"] == agent.location
            and (e["public"] or e["speaker"] == agent.name or agent.name in e["to"])
        ][-SHOWN_LINES:]
        if recent:
            lines.append("Recent conversation here:")
            for e in recent:
                who = "You" if e["speaker"] == agent.name else e["speaker"]
                to = ", ".join("you" if n == agent.name else n for n in e["to"])
                lines.append(f'- {who} → {to}: "{e["text"]}"')
        box = self._inbox.get(agent.id, {})
        for name in list(box):
            speaker = self._present(agent, name)
            if speaker is None or tick - box[name]["tick"] > INBOX_TICKS:
                del box[name]
        if box:
            lines.append("Waiting for your answer:")
            for name, m in box.items():
                times = f" (said {m['count']} times)" if m["count"] > 1 else ""
                lines.append(f'- {name}: "{m["text"]}"{times}')
            if len(box) > 1:
                lines.append(
                    'You can answer several people in one turn: "target": "'
                    + ", ".join(box)
                    + '" (or "everyone"), with one reply for all.'
                )
        notes = [n for n, until in self._conv_notes.get(agent.id, []) if until >= tick]
        lines.extend(f"Note: {n}" for n in notes)
        return "\n".join(lines)
