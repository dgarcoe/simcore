"""Situational awareness and private talk: whisper, invite and follow."""

import asyncio
import json

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine
from simcore.llm.provider import LLMProvider

WORLD = {
    "name": "Aware",
    "duration": "12 hours",
    "environment": {
        "locations": [
            {"name": "Plaza"},
            {"name": "Barn"},
            {"name": "Church"},
            {"name": "Jail", "closed": True},
        ]
    },
    "agents": [
        {"name": "Ana", "starting_location": "Plaza"},
        {"name": "Bob", "starting_location": "Plaza"},
        {"name": "Carla", "starting_location": "Plaza"},
        {"name": "Tomás", "starting_location": "Church"},
    ],
}


def make_engine(**changes) -> SimulationEngine:
    data = json.loads(json.dumps(WORLD))
    data.update(changes)
    engine = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    engine.events = []
    engine.event_bus.on_all(lambda e: engine.events.append(e))
    return engine


def ag(engine, name):
    return engine._find_agent_by_name(name)


def turn(engine, name, tick=1, **kw):
    async def decide(*_):
        return Action(**kw)

    engine.llm.decide = decide
    return asyncio.run(engine._agent_turn(ag(engine, name), tick))


def prompt_of(engine, name, tick):
    return engine._build_observation(ag(engine, name), tick)


def test_prompt_shows_what_people_do_who_left_and_who_hears():
    e = make_engine()
    e._see_around(1)
    turn(e, "bob", 1, type=ActionType.WORK, content="fixing the cart")
    turn(e, "carla", 1, type=ActionType.MOVE, target="Barn")
    p = prompt_of(e, "ana", 2)
    assert "- Bob: works: fixing the cart" in p
    assert "Who left: Carla went to Barn (1 tick ago)." in p
    assert 'If you "speak" here, Bob will hear it.' in p
    assert "Tomás" not in p.split("Who left")[1].split("\n")[1]  # never seen: not listed
    assert (
        "Places with nobody there (as far as you know): Barn, Church" not in p
    )  # Carla is in the Barn
    assert "Church" in p.split("Places with nobody there")[1].split("\n")[0]
    assert "Jail" not in p.split("Places with nobody there")[1].split("\n")[0]  # closed


def test_arrivals_and_full_awareness():
    e = make_engine(awareness="full")
    turn(e, "tomas", 1, type=ActionType.MOVE, target="Plaza")
    assert "- Tomás: just arrived from Church" in prompt_of(e, "ana", 2)
    turn(e, "carla", 2, type=ActionType.MOVE, target="Church")
    p = prompt_of(e, "ana", 3)
    assert "Where the others are: Carla at Church." in p
    assert "Places with nobody there (as far as you know): Barn." in p


def test_whisper_is_private():
    e = make_engine()
    a = turn(e, "ana", 1, type=ActionType.WHISPER, target="Bob", content="The key is in the well")
    assert (
        a.type == ActionType.WHISPER and a.describe() == 'whispers to Bob: "The key is in the well"'
    )
    assert "The key is in the well" not in prompt_of(e, "carla", 1)
    assert any("whisper something to Bob" in m.content for m in ag(e, "carla").memory.short_term)
    assert 'Ana → you: "The key is in the well"' in prompt_of(e, "bob", 1)


def test_invite_moves_inviter_and_waits():
    e = make_engine()
    a = turn(e, "ana", 1, type=ActionType.INVITE, target="Bob", place="Barn", content="Come, alone")
    assert a.type == ActionType.INVITE and ag(e, "ana").location == "Barn"
    ev = [x for x in e.events if x.data.get("action_type") == "invite"][-1]
    assert (
        ev.data["place"] == "Barn" and ev.data["from"] == "Plaza" and ev.data["targets"] == ["Bob"]
    )
    assert 'Ana asks you to come to Barn: "Come, alone"' in prompt_of(e, "bob", 2)
    assert "You are waiting for Bob at Barn." in prompt_of(e, "ana", 2)
    assert "Ana took Bob aside" in " ".join(m.content for m in ag(e, "carla").memory.short_term)
    turn(e, "bob", 2, type=ActionType.MOVE, target="Barn")
    assert "asks you to come" not in prompt_of(e, "bob", 3)
    assert "waiting for Bob" not in prompt_of(e, "ana", 3)


def test_invite_expires_and_needs_an_open_place():
    e = make_engine()
    assert (
        turn(e, "ana", 1, type=ActionType.INVITE, target="Bob", place="Jail").type
        == ActionType.WAIT
    )
    turn(e, "ana", 1, type=ActionType.INVITE, target="Bob", place="Barn")
    assert "asks you to come" not in prompt_of(e, "bob", 10)


def test_follow_ends_together_same_tick():
    e = make_engine()
    # Ana decides to follow Bob while he is here; Bob leaves in the same tick.
    assert turn(e, "ana", 1, type=ActionType.FOLLOW, target="Bob").type == ActionType.FOLLOW
    turn(e, "bob", 1, type=ActionType.MOVE, target="Barn")
    asyncio.run(e._resolve_follows(1))
    assert ag(e, "ana").location == ag(e, "bob").location == "Barn"
    moves = [
        x for x in e.events if x.type == EventType.AGENT_MOVE and x.data.get("follow") == "Bob"
    ]
    assert moves and moves[-1].source == "Ana"


def test_follow_into_a_closed_place_fails():
    e = make_engine()
    turn(e, "ana", 1, type=ActionType.FOLLOW, target="Bob")
    ag(e, "bob").location = "Jail"  # e.g. moved by an effect
    asyncio.run(e._resolve_follows(1))
    assert ag(e, "ana").location == "Plaza"


def test_follow_needs_the_leader_here():
    e = make_engine()
    assert turn(e, "ana", 1, type=ActionType.FOLLOW, target="Tomás").type == ActionType.WAIT


def test_parser_aliases_and_place():
    p = LLMProvider()
    a = p._parse_action(json.dumps({"action": "invitar", "target": "Bob", "place": "Barn"}))
    assert a.type == ActionType.INVITE and a.place == "Barn"
    assert (
        p._parse_action(json.dumps({"action": "susurrar", "target": "Bob"})).type
        == ActionType.WHISPER
    )
    assert (
        p._parse_action(json.dumps({"action": "seguir", "target": "Bob"})).type == ActionType.FOLLOW
    )


def test_demo_run_with_secrets_uses_private_talk():
    data = json.loads(json.dumps(WORLD))
    for a in data["agents"]:
        a["secrets"] = [f"{a['name']} hid the money"]
    e = SimulationEngine(
        SimulationConfig.model_validate({**data, "duration": "24 hours"}), demo_mode=True
    )
    events = []
    e.event_bus.on_all(events.append)
    e.tick_delay = 0
    asyncio.run(e.run())
    kinds = {x.data.get("action_type") for x in events}
    assert kinds & {"whisper", "invite", "follow"}
