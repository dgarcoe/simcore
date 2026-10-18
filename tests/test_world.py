"""Needs, world rules, end conditions and closed / restricted / dangerous places."""

import asyncio
import json

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine

WORLD = {
    "name": "World",
    "duration": "24 hours",
    "conditions": {"panicked": {"duration": 2}, "zombie": {"controller": "rules", "rules": {"seek": True, "wander": 0}}},
    "needs": {
        "hunger": {"label": "hunger", "grows": 0.3, "relief": 0.6, "satisfied_by": {"items": ["food"]},
                   "at_max": {"health": -0.2}, "who": {"not_conditions": ["zombie"]}},
        "sleep": {"grows": 0.25, "relief": 0.5, "satisfied_by": {"places": ["Home"]}},
    },
    "items": {"food": {"on_use": {"energy": 0.1}}},
    "environment": {"global_state": {"oxygen": 1.0}, "locations": [
        {"name": "Plaza"}, {"name": "Home"},
        {"name": "Church", "access": {"professions": ["priest"]}},
        {"name": "Farm"},
    ]},
    "agents": [
        {"name": "Ana", "starting_location": "Plaza", "resources": {"food": 2}, "needs": {"hunger": 0.4}},
        {"name": "Bob", "starting_location": "Plaza", "profession": "priest"},
        {"name": "Zed", "starting_location": "Farm", "conditions": ["zombie"]},
    ],
}


def make_engine(**changes) -> SimulationEngine:
    data = json.loads(json.dumps(WORLD))
    data.update(changes)
    engine = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    engine._running = True
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


def test_needs_grow_show_in_prompt_and_hurt_at_max():
    e = make_engine()
    ana, zed = e.agents["ana"], e.agents["zed"]
    asyncio.run(e._needs_tick(1))
    assert ana.state.needs["hunger"] == pytest.approx(0.7)
    assert zed.state.needs.get("hunger", 0) == 0  # zombies are excluded (rules + who)
    assert "Your needs: hunger: high (relief: use food)" in e._build_observation(ana, 1)
    asyncio.run(e._needs_tick(2))
    assert ana.state.needs["hunger"] == 1.0 and ana.state.health == pytest.approx(0.8)
    levels = [(x.data["need"], x.data["level"]) for x in of(e, EventType.NEED_CHANGE) if x.source == "Ana"]
    assert ("hunger", "high") in levels and ("hunger", "critical") in levels


def test_needs_are_relieved_by_items_and_places():
    e = make_engine()
    ana = e.agents["ana"]
    ana.state.needs.update(hunger=0.9, sleep=0.8)
    act(e, "ana", type=ActionType.USE, item="food")
    assert ana.state.needs["hunger"] == pytest.approx(0.3)
    act(e, "ana", type=ActionType.REST)  # not at Home: no relief
    assert ana.state.needs["sleep"] == pytest.approx(0.8)
    e.environment.move_agent("ana", "Plaza", "Home")
    ana.location = "Home"
    act(e, "ana", type=ActionType.REST)
    assert ana.state.needs["sleep"] == pytest.approx(0.3)


def test_starving_to_death():
    e = make_engine()
    ana = e.agents["ana"]
    ana.state.needs["hunger"] = 1.0
    ana.state.health = 0.1
    asyncio.run(e._needs_tick(1))
    assert ana.state.status == "dead"


def test_closed_and_restricted_places():
    e = make_engine()
    assert act(e, "ana", type=ActionType.MOVE, target="Church").type == ActionType.WAIT
    assert e.agents["ana"].location == "Plaza"
    assert act(e, "bob", type=ActionType.MOVE, target="Church").type == ActionType.MOVE
    asyncio.run(e.inject_event("Lockdown", effects={"locations": {"Home": {"closed": True}}}))
    refused = act(e, "ana", type=ActionType.MOVE, target="Home")
    assert refused.type == ActionType.WAIT and "closed" in refused.reasoning
    assert "Closed places: Home." in e._build_observation(e.agents["ana"], 1)
    place = e.get_state()["environment"]["locations"]["Home"]
    assert place["closed"] is True and e.get_state()["environment"]["locations"]["Church"]["access"] == {"professions": ["priest"]}
    assert of(e, EventType.PLACE_CHANGE)[0].data["closed"] is True


def test_rule_agents_avoid_places_they_cannot_enter():
    e = make_engine()
    asyncio.run(e.kill(e.agents["ana"], 1))  # only Bob is a target...
    e.environment.move_agent("bob", "Plaza", "Church")
    e.agents["bob"].location = "Church"  # ...and he hides in the church (priests only)
    action = asyncio.run(e._agent_turn(e.agents["zed"], 2))
    assert action.type != ActionType.MOVE or action.target != "Church"


def test_hazards_hurt_and_add_conditions():
    e = make_engine()
    asyncio.run(e.inject_event("Fire!", effects={"locations": {"Plaza": {
        "hazard": {"label": "on fire", "per_tick": {"health": -0.3}, "condition": "panicked"}}}}))
    asyncio.run(e._hazards_tick(2))
    assert e.agents["ana"].state.health == pytest.approx(0.7) and "panicked" in e.agents["ana"].state.conditions
    assert "Dangerous places: Plaza (on fire)." in e._build_observation(e.agents["bob"], 2)
    asyncio.run(e.inject_event("", effects={"locations": {"Plaza": {"hazard": None}}}))
    assert e.environment.locations["Plaza"].hazard is None


def test_rules_every_global_adjust_and_once():
    e = make_engine(rules=[
        {"description": "The air goes stale", "when": {"every": 2}, "then": {"adjust": {"oxygen": -0.4}}},
        {"when": {"global": {"oxygen": {"lt": 0.5}}}, "then": {"health": {"all": -0.1}}, "once": True},
    ])
    for tick in (1, 2, 3, 4, 5, 6):
        asyncio.run(e._rules_tick(tick))
    assert e.environment.global_state["oxygen"] == pytest.approx(-0.2)
    assert e.agents["ana"].state.health == pytest.approx(0.9)  # once
    fired = [x.data["rule"] for x in of(e, EventType.RULE_FIRED)]
    assert fired == [0, 0, 1, 0]
    assert any("The air goes stale" in m.content for m in e.agents["bob"].memory.short_term)


def test_rule_ends_the_run_with_its_message():
    e = make_engine(rules=[{"when": {"count": {"condition": "zombie", "gte": 2}},
                            "then": {"end": "The village has fallen"}}],
                    events={"scheduled": [{"tick": 3, "type": "x", "description": "Bite",
                                           "effects": {"add_condition": {"Ana": "zombie"}}}]})
    e._running = False
    asyncio.run(e.run())
    end = of(e, EventType.SIMULATION_END)[0]
    assert end.data["reason"] == "rule" and end.data["message"] == "The village has fallen" and end.tick == 3
    assert of(e, EventType.TICK_END)[-1].tick == 3


def test_count_alive_and_place_triggers():
    e = make_engine(rules=[
        {"when": {"alive": {"lte": 2}}, "then": {"adjust": {"alarm": 1}}, "once": True},
        {"when": {"place": {"Plaza": {"occupants": {"gte": 2}, "closed": False}}}, "then": {"adjust": {"crowd": 1}}},
    ])
    asyncio.run(e._rules_tick(1))
    assert "alarm" not in e.environment.global_state and e.environment.global_state["crowd"] == 1
    asyncio.run(e.kill(e.agents["ana"], 1))
    asyncio.run(e._rules_tick(2))
    assert e.environment.global_state["alarm"] == 1 and e.environment.global_state["crowd"] == 1


def test_needs_effect_and_validation():
    e = make_engine()
    asyncio.run(e.inject_event("Feast", effects={"needs": {"all": {"hunger": -1}}}))
    assert e.agents["ana"].state.needs["hunger"] == 0
    with pytest.raises(ValueError, match="unknown need"):
        make_engine(agents=[{"name": "A", "needs": {"thirst": 0.5}}])
    with pytest.raises(ValueError, match="unknown place"):
        make_engine(rules=[{"when": {"every": 1}, "then": {"locations": {"Mars": {"closed": True}}}}])
    with pytest.raises(ValueError):
        make_engine(rules=[{"when": {"every": 1}, "then": {"end": ""}}])
    with pytest.raises(ValueError, match="unknown action"):
        make_engine(needs={"x": {"satisfied_by": {"actions": ["dance"]}}})


def test_demo_run_handles_needs():
    e = make_engine()
    e._running = False
    asyncio.run(e.run())
    assert of(e, EventType.NEED_CHANGE)
    assert any(x.type == EventType.AGENT_ACTION and x.data.get("action_type") == "use" for x in e.events) or \
        any(x.data.get("level") == "ok" for x in of(e, EventType.NEED_CHANGE))
