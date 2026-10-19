"""The story of a run: key moments and a chronicle written chapter by chapter."""

import asyncio
import json

from fastapi.testclient import TestClient

from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType, SimEvent
from simcore.engine.simulation import SimulationEngine
from simcore.engine.story import score_event
from simcore.server.api import create_app

WORLD = {
    "name": "Story",
    "duration": "12 hours",
    "story": True,
    "chapter_every": 4,
    "environment": {"locations": [{"name": "Plaza"}, {"name": "Granja"}]},
    "agents": [
        {"name": "Ana", "starting_location": "Plaza", "secrets": ["Escondí la llave"]},
        {"name": "Bob", "starting_location": "Plaza"},
    ],
    "events": {
        "scheduled": [{"tick": 2, "type": "storm", "description": "Una tormenta corta los caminos"}]
    },
}


def make_engine(**changes) -> SimulationEngine:
    data = json.loads(json.dumps(WORLD))
    data.update(changes)
    engine = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    engine.tick_delay = 0
    engine.events = []
    engine.event_bus.on_all(lambda e: engine.events.append(e))
    return engine


def ev(kind, **data):
    return SimEvent(type=kind, tick=3, data=data, source=data.get("agent", ""))


def test_scoring():
    assert score_event(ev(EventType.AGENT_DEATH, agent="Ana", cause="hambre"))[:2] == (
        1.0,
        "Ana died (hambre)",
    )
    assert (
        score_event(
            ev(
                EventType.CONDITION_CHANGE,
                agent="Ana",
                condition="infected",
                change="transformed",
                into="zombie",
            )
        )[0]
        == 0.9
    )
    secret = score_event(
        ev(EventType.BELIEF_SHARED, **{"from": "Ana", "to": "Bob", "text": "x", "secret": True})
    )
    assert secret[0] == 0.8 and secret[2] == ["Ana", "Bob"]
    assert score_event(ev(EventType.AGENT_MOVE, action="moves to Plaza")) is None
    assert score_event(ev(EventType.AGENT_SEARCH, agent="Ana", target="Bob", step="asked")) is None


def test_moments_and_chapters_during_a_run():
    e = make_engine()
    asyncio.run(e.run())
    kinds = [x.type for x in e.events]
    assert EventType.STORY_MOMENT in kinds  # the storm is a strong moment
    chapters = [x.data for x in e.events if x.type == EventType.STORY_CHAPTER]
    assert [c["to_tick"] for c in chapters][:2] == [4, 8]
    assert chapters[0]["n"] == 1 and "tormenta" in chapters[0]["text"]
    assert e.story()["chapters"] == chapters


def test_no_chapters_unless_enabled():
    e = make_engine(story=False)
    asyncio.run(e.run())
    assert not [x for x in e.events if x.type == EventType.STORY_CHAPTER]
    assert e.story()["moments"]  # moments are always tracked


def test_api_story_and_postmortem_use_the_chapters():
    e = make_engine()
    asyncio.run(e.run())
    client = TestClient(create_app(e))
    story = client.get("/api/story").json()
    assert story["chapters"] and story["moments"]
    narrative = client.post("/api/postmortem/narrative").json()["narrative"]
    assert narrative.startswith("It began quietly; then")
