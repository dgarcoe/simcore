"""The story of a run: key moments picked from the events, and (optionally) a chronicle written
chapter by chapter by the LLM. Mixed into SimulationEngine."""

from __future__ import annotations

import logging

from simcore.engine.events import EventType, SimEvent

logger = logging.getLogger("simcore")

STRONG = 0.7  # moments from here on are announced (story_moment)
MAX_MOMENTS = 400
CHAPTER_MOMENTS = 14  # moments given to the narrator per chapter


def _txt(value) -> str:
    return str(value or "").strip()


def score_event(e: SimEvent) -> tuple[float, str, list[str], str] | None:
    """How much an event matters to the story: (importance 0..1, text, agents, place) or None."""
    d, t = e.data or {}, e.type
    place = _txt(d.get("location") or d.get("place"))
    if t == EventType.AGENT_DEATH:
        cause = _txt(d.get("cause"))
        return (
            1.0,
            f"{d.get('agent', e.source)} died{f' ({cause})' if cause else ''}",
            [_txt(d.get("agent", e.source))],
            place,
        )
    if t == EventType.CONDITION_CHANGE:
        if d.get("change") == "transformed":
            return 0.9, f"{d['agent']} became {d.get('into')}", [d["agent"]], place
        if d.get("change") == "added" and d.get("cause"):
            return 0.4, f"{d['agent']} is now {d['condition']} ({d['cause']})", [d["agent"]], place
        return None
    if t == EventType.BELIEF_SHARED:
        if d.get("secret"):
            return (
                0.8,
                f'{d["from"]} revealed a secret to {d["to"]}: "{d["text"]}"',
                [d["from"], d["to"]],
                place,
            )
        if not d.get("from"):
            return 0.4, f'A rumor reached {d["to"]}: "{d["text"]}"', [d["to"]], place
        if d.get("truth") is False:
            return (
                0.5,
                f'{d["from"]} told {d["to"]} something false: "{d["text"]}"',
                [d["from"], d["to"]],
                place,
            )
        return None
    if t == EventType.SIMULATION_END and d.get("message"):
        return 1.0, _txt(d["message"]), [], ""
    if t == EventType.RULE_FIRED and d.get("description"):
        ends = isinstance(d.get("effects"), dict) and "end" in d["effects"]
        return (0.9 if ends else 0.7), _txt(d["description"]), [], ""
    if t in (EventType.SCHEDULED_EVENT, EventType.INJECTED_EVENT) and d.get("description"):
        return 0.7, _txt(d["description"]), [], place
    if t == EventType.AGENT_SPAWN:
        return 0.5, f"{d.get('agent', e.source)} arrived", [_txt(d.get("agent", e.source))], place
    if t == EventType.AGENT_DEPART:
        return 0.5, f"{d.get('agent', e.source)} left", [_txt(d.get("agent", e.source))], place
    if t == EventType.AGENT_SEARCH and d.get("step") == "found":
        return 0.5, f"{d['agent']} found {d['target']}", [d["agent"], d["target"]], place
    if t == EventType.PLACE_CHANGE and d.get("cause"):
        return 0.5, _txt(d["cause"]), [], _txt(d.get("location"))
    if t == EventType.SPEECH_IGNORED:
        return 0.3, f"{e.source} ignored {d.get('agent')}", [e.source, _txt(d.get("agent"))], place
    if t == EventType.AGENT_ACTION and d.get("action_type") == "custom" and d.get("name"):
        if d.get("success", True) is False:
            return None
        action = _txt(d.get("action"))
        return (
            0.5 if d.get("target") else 0.35,
            f"{e.source} {action}",
            [e.source, _txt(d.get("target"))],
            place,
        )
    return None


class StoryMixin:
    """Needs the engine's config, agents, event_bus, llm and clock."""

    def _init_story(self) -> None:
        self._moments: list[dict] = []
        self._chapters: list[dict] = []
        self._chapter_from = 1
        self.event_bus.on_all(self._story_listen)

    async def _story_listen(self, e: SimEvent) -> None:
        if e.type in (EventType.STORY_MOMENT, EventType.STORY_CHAPTER):
            return
        scored = score_event(e)
        if not scored:
            return
        importance, text, agents, place = scored
        if not text:
            return
        moment = {
            "tick": e.tick,
            "importance": importance,
            "text": text,
            "kind": e.type.value,
            "agents": [a for a in agents if a],
            "place": place,
        }
        self._moments.append(moment)
        if len(self._moments) > MAX_MOMENTS:
            self._moments = self._moments[-MAX_MOMENTS:]
        if importance >= STRONG and e.type != EventType.SIMULATION_END:
            await self.event_bus.emit(
                SimEvent(type=EventType.STORY_MOMENT, tick=e.tick, data=moment)
            )

    # ── Chapters ───────────────────────────────────────────────────────

    def _cast(self) -> list[str]:
        out = []
        for a in self.agents.values():
            status = a.state.status if a.state.status != "alive" else ""
            conds = [
                c
                for c in a.state.conditions
                if c in self.config.conditions
                and (
                    self.config.conditions[c].mind
                    or self.config.conditions[c].controller == "rules"
                )
            ]
            note = status or (", ".join(conds) if conds else "")
            out.append(f"{a.name} ({a.persona.profession}{'; ' + note if note else ''})")
        return out

    def _top_moments(self, start: int, end: int, limit: int = CHAPTER_MOMENTS) -> list[dict]:
        span, seen = [], set()
        for m in self._moments:  # the same thing told twice (a rule and its effect) counts once
            if start <= m["tick"] <= end and m["text"] not in seen:
                seen.add(m["text"])
                span.append(m)
        best = sorted(span, key=lambda m: -m["importance"])[:limit]
        return sorted(best, key=lambda m: m["tick"])

    async def _write_chapter(self, tick: int) -> None:
        moments = self._top_moments(self._chapter_from, tick)
        start, self._chapter_from = self._chapter_from, tick + 1
        if not moments or not hasattr(self.llm, "chronicle"):
            return
        lang = getattr(self.config, "language", "en")
        try:
            title, text = await self.llm.chronicle(
                [f"[t{m['tick']}] {m['text']}" for m in moments],
                self._cast(),
                [c["title"] for c in self._chapters],
                lang,
            )
        except Exception as exc:  # the story is a nice-to-have: never break the run
            logger.warning(f"Chapter failed: {exc}")
            return
        if not text:
            return
        chapter = {
            "n": len(self._chapters) + 1,
            "title": title or f"Chapter {len(self._chapters) + 1}",
            "text": text,
            "from_tick": start,
            "to_tick": tick,
        }
        self._chapters.append(chapter)
        await self.event_bus.emit(SimEvent(type=EventType.STORY_CHAPTER, tick=tick, data=chapter))

    async def _story_after_tick(self, tick: int) -> None:
        if self.config.story and tick % self.config.chapter_every == 0:
            await self._write_chapter(tick)

    async def _story_at_end(self, tick: int) -> None:
        if self.config.story and self.end_reason != "stopped" and self._chapter_from <= tick:
            await self._write_chapter(tick)

    def story(self) -> dict:
        return {"chapters": list(self._chapters), "moments": list(self._moments)}

    def story_summary(self, limit: int = 15) -> list[dict]:
        """The run's most important moments, in order (for the post-mortem)."""
        return self._top_moments(0, self.clock.current_tick, limit)
