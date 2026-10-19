"""Looking for someone: ask the people here, go to the best clue, rule out places, give up."""

import asyncio
import json

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine
from simcore.llm.provider import LLMProvider

WORLD = {
    "name": "Search",
    "duration": "24 hours",
    "environment": {
        "locations": [
            {"name": "Plaza"},
            {"name": "Iglesia"},
            {"name": "Granja"},
            {"name": "Taberna"},
            {"name": "Cárcel", "closed": True},
        ]
    },
    "agents": [
        {"name": "Ana", "starting_location": "Plaza"},
        {"name": "Bob", "starting_location": "Plaza"},
        {"name": "Tomás", "starting_location": "Granja"},
        {"name": "Lola", "starting_location": "Taberna"},
    ],
}


def make_engine() -> SimulationEngine:
    engine = SimulationEngine(
        SimulationConfig.model_validate(json.loads(json.dumps(WORLD))), demo_mode=True
    )
    engine.events = []
    engine.event_bus.on_all(lambda e: engine.events.append(e))
    return engine


def ag(e, name):
    return e._find_agent_by_name(name)


def act(e, name, tick=1, **kw):
    async def decide(*_):
        return Action(**kw)

    e.llm.decide = decide
    return asyncio.run(e._agent_turn(ag(e, name), tick))


def steps(e):
    return [
        (x.data["agent"], x.data["step"], x.data["place"])
        for x in e.events
        if x.type == EventType.AGENT_SEARCH
    ]


def put(e, name, place):
    a = ag(e, name)
    e.environment.move_agent(a.id, a.location, place)
    a.state.location = place


def test_asks_the_people_here_first_and_they_hear_it():
    e = make_engine()
    put(e, "tomas", "Iglesia")
    e._last_seen.setdefault(ag(e, "bob").id, {})["Tomás"] = ("Iglesia", 0, "left")
    a = act(e, "ana", 1, type=ActionType.FIND, target="Tomás", content="avisarle del pozo")
    assert a.type == ActionType.FIND and a.parameters["step"] == "asked"
    assert ("Ana", "asked", "Plaza") in steps(e)
    search = e._searches[ag(e, "ana").id]
    assert search["clues"]["Bob"] == ("Iglesia", 0)
    p = e._build_observation(ag(e, "ana"), 2)
    assert "You are looking for Tomás (avisarle del pozo)." in p and "Bob saw them at Iglesia" in p
    assert 'Ana → you: "Has anyone seen Tomás?"' in e._build_observation(ag(e, "bob"), 1)


def test_goes_to_the_freshest_clue_and_finds_them():
    e = make_engine()
    put(e, "tomas", "Iglesia")
    e._last_seen.setdefault(ag(e, "bob").id, {})["Tomás"] = ("Iglesia", 0, "left")
    act(e, "ana", 1, type=ActionType.FIND, target="Tomás")  # asks Bob
    a = act(e, "ana", 2, type=ActionType.FIND, target="Tomás")  # goes to the Iglesia
    assert a.parameters["step"] == "moved" and ag(e, "ana").location == "Iglesia"
    assert ("Ana", "found", "Iglesia") in steps(e)
    assert "You found Tomás: they are here." in e._build_observation(ag(e, "ana"), 2)


def test_without_clues_tries_their_home_then_unchecked_places_and_skips_closed_ones():
    e = make_engine()
    put(e, "tomas", "Taberna")
    put(e, "bob", "Iglesia")
    act(e, "ana", 1, type=ActionType.FIND, target="Tomás")
    assert ag(e, "ana").location == "Granja"  # where Tomás usually is
    act(e, "ana", 2, type=ActionType.FIND, target="Tomás")
    assert ag(e, "ana").location == "Iglesia"  # next unchecked place
    act(e, "ana", 3, type=ActionType.FIND, target="Tomás")  # asks Bob (no clue)
    act(e, "ana", 4, type=ActionType.FIND, target="Tomás")
    assert ag(e, "ana").location == "Taberna" and ("Ana", "found", "Taberna") in steps(e)
    assert all(place != "Cárcel" for _, _, place in steps(e))


def test_found_when_they_walk_in_and_message_is_remembered():
    e = make_engine()
    put(e, "bob", "Iglesia")
    put(e, "tomas", "Taberna")
    a = act(e, "ana", 1, type=ActionType.SPEAK, target="Tomás", content="El pozo está envenenado")
    assert a.type == ActionType.FIND  # not here: goes looking instead of waiting
    put(e, "tomas", ag(e, "ana").location)
    asyncio.run(e._searches_tick(2))
    p = e._build_observation(ag(e, "ana"), 2)
    assert (
        'You found Tomás: they are here. You wanted to tell them: "El pozo está envenenado".' in p
    )
    act(e, "ana", 2, type=ActionType.SPEAK, target="Tomás", content="El pozo está envenenado")
    assert ag(e, "ana").id not in e._searches


def test_gives_up_and_lost():
    e = make_engine()
    act(e, "ana", 1, type=ActionType.FIND, target="Lola")
    asyncio.run(e._searches_tick(1 + 8))
    assert steps(e)[-1][:2] == ("Ana", "gave_up") and ag(e, "ana").id not in e._searches
    act(e, "ana", 10, type=ActionType.FIND, target="Lola")
    asyncio.run(e.kill(ag(e, "lola"), 10, "accident"))
    asyncio.run(e._searches_tick(11))
    assert steps(e)[-1][:2] == ("Ana", "lost")


def test_state_and_parser():
    e = make_engine()
    act(e, "ana", 1, type=ActionType.FIND, target="Tomás", content="hablar")
    ana = next(a for a in e.get_state()["agents"].values() if a["name"] == "Ana")
    assert ana["search"]["target"] == "Tomás" and ana["search"]["why"] == "hablar"
    assert (
        LLMProvider()._parse_action(json.dumps({"action": "buscar", "target": "Tomás"})).type
        == ActionType.FIND
    )


def test_demo_run_searches():
    e = make_engine()
    e.tick_delay = 0
    asyncio.run(e.run())
    assert any(x.type == EventType.AGENT_SEARCH for x in e.events)
