"""Talking to several people, overhearing, unanswered questions and conversations in circles."""

import asyncio
import json

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine
from simcore.llm.provider import LLMProvider

TALK = {
    "name": "Talk",
    "duration": "12 hours",
    "environment": {"locations": [{"name": "Plaza"}, {"name": "Farm"}]},
    "agents": [
        {"name": "Ana", "starting_location": "Plaza"},
        {"name": "Bob", "starting_location": "Plaza"},
        {"name": "Carla", "starting_location": "Plaza"},
        {"name": "Padre Julián", "starting_location": "Plaza"},
        {"name": "Tomás", "starting_location": "Farm"},
    ],
}


def make_engine() -> SimulationEngine:
    engine = SimulationEngine(
        SimulationConfig.model_validate(json.loads(json.dumps(TALK))), demo_mode=True
    )
    engine.events = []
    engine.event_bus.on_all(lambda e: engine.events.append(e))
    return engine


def say(engine, name, target, content="Where is the key?", tick=1, kind=ActionType.SPEAK):
    async def decide(*_):
        return Action(type=kind, target=target, content=content)

    engine.llm.decide = decide
    return asyncio.run(engine._agent_turn(engine._find_agent_by_name(name), tick))


def prompt_of(engine, name, tick=1):
    return engine._build_observation(engine._find_agent_by_name(name), tick)


def test_speak_to_several_people_and_everyone():
    e = make_engine()
    a = say(e, "ana", "Bob, Carla")
    assert a.type == ActionType.SPEAK and a.parameters["targets"] == ["Bob", "Carla"]
    assert a.describe() == 'says to Bob, Carla: "Where is the key?"'
    speak = [x for x in e.events if x.type == EventType.AGENT_SPEAK][-1]
    assert speak.data["targets"] == ["Bob", "Carla"]
    a = say(e, "ana", "Bob y Tomás")  # Tomás is elsewhere: still talks to Bob
    assert a.parameters["targets"] == ["Bob"]
    a = say(e, "ana", "todos")
    assert sorted(a.parameters["targets"]) == ["Bob", "Carla", "Padre Julián"]
    assert say(e, "ana", "Tomás").type == ActionType.FIND  # not here: goes to look for him


def test_tolerant_names():
    e = make_engine()
    assert e._find_agent_by_name("padre julian").name == "Padre Julián"
    assert e._find_agent_by_name("Julián").name == "Padre Julián"
    assert e._find_agent_by_name("tomas").name == "Tomás"
    assert e._find_agent_by_name("Zoe") is None


def test_parse_target_list():
    a = LLMProvider()._parse_action(
        json.dumps({"action": "speak", "target": ["Ana", "Bob"], "content": "hi"})
    )
    assert a.type == ActionType.SPEAK and a.target == "Ana, Bob"


def test_everyone_here_hears_and_tomas_does_not():
    e = make_engine()
    say(e, "ana", "Bob", "The bridge is down")
    assert 'Ana → Bob: "The bridge is down"' in prompt_of(e, "carla")
    assert "The bridge is down" not in prompt_of(e, "tomas")
    assert any(
        "I heard Ana say to Bob" in m.content
        for m in e._find_agent_by_name("carla").memory.short_term
    )
    say(e, "bob", "Ana", "A secret", kind=ActionType.INTERACT)  # private
    assert "A secret" not in prompt_of(e, "carla")


def test_inbox_counts_and_clears_when_answered():
    e = make_engine()
    say(e, "ana", "Bob", "Where is the key?", tick=1)
    say(e, "ana", "Bob", "Where is the key??", tick=2)
    say(e, "carla", "Bob", "Can you help me?", tick=2)
    p = prompt_of(e, "bob", 2)
    assert "Waiting for your answer:" in p
    assert '- Ana: "Where is the key??" (said 2 times)' in p
    assert '"target": "Ana, Carla"' in p
    say(e, "bob", "Ana, Carla", "In the barn.", tick=3)
    assert "Waiting for your answer" not in prompt_of(e, "bob", 3)
    assert "Waiting for your answer" not in prompt_of(e, "bob", 20)


def test_going_in_circles_note():
    e = make_engine()
    say(e, "ana", "Bob", "Do you know where the key is?", tick=1)
    say(e, "bob", "Ana", "No, do you?", tick=1)
    say(e, "ana", "Bob", "Do you know where the key is?", tick=2)
    assert "going in circles" in prompt_of(e, "ana", 2)


def test_already_asked_by_someone_else():
    e = make_engine()
    say(e, "ana", "Bob", "Where is the key?", tick=1)
    say(e, "bob", "Ana", "In the barn.", tick=1)
    say(e, "carla", "Bob", "Where is the key?", tick=2)
    p = prompt_of(e, "carla", 2)
    assert 'Ana already said that ("Where is the key?"), and Bob answered: "In the barn."' in p


def test_demo_run_talks_to_several():
    e = make_engine()
    e.tick_delay = 0
    asyncio.run(e.run())
    speaks = [x for x in e.events if x.type == EventType.AGENT_SPEAK]
    assert speaks
    assert any(len(x.data.get("targets", [])) > 1 for x in speaks)


def test_ignored_after_two_turns_without_answer():
    e = make_engine()
    say(e, "ana", "Bob", "Will you help me?", tick=1)
    say(e, "bob", "Carla", "Nice weather", tick=1)  # same tick: Bob hadn't heard Ana yet
    assert not [x for x in e.events if x.type == EventType.SPEECH_IGNORED]
    say(e, "bob", "Carla", "Shall we go?", tick=2)
    say(e, "bob", "Carla", "Let's go", tick=3)
    ignored = [x for x in e.events if x.type == EventType.SPEECH_IGNORED]
    assert len(ignored) == 1 and ignored[0].data == {
        "agent": "Ana",
        "by": "Bob",
        "text": "Will you help me?",
        "turns": 2,
    }
    p = prompt_of(e, "ana", 3)
    assert "Bob has ignored you 2 times" in p and "change course" in p
    assert "Waiting for your answer" not in prompt_of(e, "bob", 3)


def test_answering_is_not_ignoring():
    e = make_engine()
    say(e, "ana", "Bob", "Will you help me?", tick=1)
    say(e, "bob", "Carla", "One moment", tick=2)
    say(e, "bob", "Ana, Carla", "Yes, both of you", tick=3)
    assert not [x for x in e.events if x.type == EventType.SPEECH_IGNORED]
