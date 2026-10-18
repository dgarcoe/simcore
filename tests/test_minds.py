"""Daily plans, beliefs / secrets / rumors and typed relationships."""

import asyncio
import json

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine
from simcore.llm.provider import LLMProvider, parse_plan

MINDS = {
    "name": "Minds",
    "duration": "12 hours",
    "planning": True,
    "plan_every": 4,
    "environment": {"locations": [{"name": "Plaza"}, {"name": "Farm"}]},
    "agents": [
        {"name": "Ana", "starting_location": "Plaza", "goals": ["Find out the truth"],
         "relationships": {"Tomás": {"type": "brother", "sentiment": 0.8}, "Rex": {"type": "rival", "sentiment": -0.4}},
         "beliefs": [{"text": "The well is poisoned", "true": False}],
         "secrets": ["I hid the medicine in the church"]},
        {"name": "Bob", "starting_location": "Plaza"},
        {"name": "Tomás", "starting_location": "Farm", "relationships": {"Ana": {"type": "sister", "sentiment": 0.8}}},
        {"name": "Rex", "starting_location": "Farm"},
    ],
}


def make_engine(**changes) -> SimulationEngine:
    data = json.loads(json.dumps(MINDS))
    data.update(changes)
    engine = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    engine.events = []
    engine.event_bus.on_all(lambda e: engine.events.append(e))
    return engine


def act(engine, name, tick=1, **kw):
    async def decide(*_):
        return Action(**kw)

    engine.llm.decide = decide
    return asyncio.run(engine._agent_turn(engine.agents[name], tick))


def of(engine, kind):
    return [e for e in engine.events if e.type == kind]


def test_relationships_beliefs_and_secrets_in_the_prompt():
    e = make_engine()
    ana = e.agents["ana"]
    obs = e._build_observation(ana, 1)
    prompt = ana.build_prompt(obs)
    assert "Tomás: your brother (close friend" in prompt
    assert "Rex: your rival (unfriendly" in prompt
    assert "- The well is poisoned" in obs
    assert "Your secrets (you decide whether to reveal them" in obs and "I hid the medicine" in obs
    state = e.get_state()["agents"]["ana"]
    assert state["relationship_kinds"] == {"Tomás": "brother", "Rex": "rival"}
    assert state["state"]["beliefs"][0]["truth"] is False


def test_sharing_passes_beliefs_with_their_truth_and_lies_stay_unknown():
    e = make_engine()
    act(e, "ana", type=ActionType.SPEAK, target="Bob", content="Listen", share="the well is poisoned!")
    bob = e.agents["bob"]
    assert bob.state.beliefs[-1] == {"text": "the well is poisoned!", "source": "Ana", "truth": False,
                                     "secret": False, "tick": 1}
    act(e, "ana", type=ActionType.SPEAK, target="Bob", content="Psst", share="I hid the medicine in the church")
    shared = of(e, EventType.BELIEF_SHARED)
    assert shared[-1].data["secret"] is True and shared[-1].data["truth"] is True
    act(e, "ana", type=ActionType.SPEAK, target="Bob", content="Also", share="Rex is a werewolf")
    assert bob.state.beliefs[-1]["truth"] is None
    assert "(told by Ana)" in e._build_observation(bob, 2)
    # nobody to tell: nothing happens
    act(e, "ana", type=ActionType.SPEAK, target="Tomás", content="hi", share="anything")
    assert not any(b["text"] == "anything" for b in e.agents["tomás"].state.beliefs)


def test_rumors_from_effects():
    e = make_engine()
    asyncio.run(e.inject_event("", effects={"rumor": {"location:Farm": {"text": "Ana is a thief", "true": False}}}))
    assert e.agents["rex"].state.beliefs[-1]["source"] == "rumor"
    assert "Ana is a thief (a rumor going around)" in e._build_observation(e.agents["rex"], 1)
    assert not e.agents["bob"].state.beliefs
    assert of(e, EventType.BELIEF_SHARED)[0].data["from"] == ""
    with pytest.raises(ValueError):
        asyncio.run(e.inject_event("", effects={"rumor": {"Ana": {"text": "x", "true": "maybe"}}}))


def test_family_grieves_from_afar_rivals_do_not():
    e = make_engine()
    tomas, rex = e.agents["tomás"], e.agents["rex"]
    asyncio.run(e.kill(e.agents["ana"], 2, "a fall"))
    assert tomas.state.mood == pytest.approx(-0.5)
    assert any("my sister Ana has died" in m.content for m in tomas.memory.short_term)
    assert rex.state.mood == 0
    assert tomas.state.replan is True


def test_hurting_family_turns_relatives_against_the_attacker():
    e = make_engine(actions={"punch": {"on_target": {"health": -0.2}, "message": "punches {target}"}})
    e.environment.move_agent("rex", "Farm", "Plaza")
    e.agents["rex"].location = "Plaza"
    act(e, "rex", type=ActionType.CUSTOM, target="Ana", parameters={"name": "punch"})
    assert e.agents["tomás"].memory.relationships["Rex"].sentiment == pytest.approx(-0.4)


def test_daily_plans_are_made_shown_advanced_and_redone():
    e = make_engine()
    ana = e.agents["ana"]
    act(e, "ana", tick=1, type=ActionType.REST, plan_step_done=True)
    plan = of(e, EventType.AGENT_PLAN)
    assert plan and plan[0].data["agent"] == "Ana" and len(plan[0].data["steps"]) == 4
    assert ana.state.plan_step == 1
    assert "Your plan for today: 1." in e._build_observation(ana, 2)
    act(e, "ana", tick=2, type=ActionType.REST)
    assert len(of(e, EventType.AGENT_PLAN)) == 1  # not due yet
    asyncio.run(e.inject_event("An explosion"))
    act(e, "ana", tick=3, type=ActionType.REST)
    assert of(e, EventType.AGENT_PLAN)[-1].data["reason"] == "something important happened"
    act(e, "ana", tick=7, type=ActionType.REST)
    assert of(e, EventType.AGENT_PLAN)[-1].data["reason"] == "new day"


def test_planning_is_off_by_default():
    e = make_engine(planning=False)
    act(e, "ana", type=ActionType.REST)
    assert not of(e, EventType.AGENT_PLAN)


def test_relationships_must_name_known_agents():
    with pytest.raises(ValueError, match="unknown agent 'Ghost'"):
        make_engine(agents=[{"name": "A", "relationships": {"Ghost": {"type": "friend"}}}])


def test_parsing_share_plan_flags_and_plans():
    p = LLMProvider()
    a = p._parse_action(json.dumps({"action": "speak", "target": "B", "content": "hi", "share": "a secret",
                                    "plan_step_done": True}))
    assert a.share == "a secret" and a.plan_step_done is True
    assert parse_plan('{"steps": ["go", "eat"]}') == ["go", "eat"]
    assert parse_plan("1. go to the farm\n2) talk to Ana\n- rest") == ["go to the farm", "talk to Ana", "rest"]


def test_demo_run_shares_and_plans():
    e = make_engine(duration="24 hours")
    asyncio.run(e.run())
    assert of(e, EventType.AGENT_PLAN)
    assert of(e, EventType.BELIEF_SHARED)
