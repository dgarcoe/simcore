"""Conditions, scenario actions and rule-controlled agents."""

import asyncio
import json

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine
from simcore.llm.provider import LLMProvider

ZOMBIES = {
    "name": "Outbreak",
    "duration": "12 hours",
    "conditions": {
        "infected": {"description": "fever and bite marks", "appearance": "looks feverish",
                     "per_tick": {"energy": -0.05}, "duration": 2, "becomes": "zombie",
                     "contagion": {"chance": 1.0, "via": "interaction"}},
        "zombie": {"appearance": "a shambling corpse", "controller": "rules", "color": "#7bc950",
                   "rules": {"action": "bite", "seek": True, "wander": 0.0}},
        "panicked": {"duration": 1, "per_tick": {"mood": -0.2}},
    },
    "actions": {
        "bite": {"description": "Bite", "who": {"conditions": ["zombie"]},
                 "target_requires": {"not_conditions": ["zombie"]},
                 "on_target": {"health": -0.3, "add_conditions": ["infected"]}, "message": "bites {target}"},
        "shoot": {"description": "Shoot someone here", "cost": {"ammo": 1},
                  "target_requires": {"conditions": ["zombie"]},
                  "on_target": {"health": -1.0}, "message": "shoots {target}", "fail_message": "misses {target}"},
        "barricade": {"target": "none", "who": {"professions": ["carpenter"]},
                      "on_self": {"mood": 0.2}, "world": {"barricaded": True}},
    },
    "items": {"antidote": {"cures": ["infected"]}},
    "environment": {"locations": [{"name": "Plaza"}, {"name": "Church"}]},
    "agents": [
        {"name": "Zed", "starting_location": "Plaza", "conditions": ["zombie"]},
        {"name": "Ana", "starting_location": "Plaza", "resources": {"ammo": 2, "antidote": 1}},
        {"name": "Bob", "starting_location": "Church", "profession": "carpenter"},
    ],
}


def make_engine(**changes) -> SimulationEngine:
    data = json.loads(json.dumps(ZOMBIES))
    data.update(changes)
    engine = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    engine.events = []
    engine.event_bus.on_all(lambda e: engine.events.append(e))
    return engine


def act(engine, name, **kw):
    async def decide(*_):
        return Action(**kw)

    engine.llm.decide = decide
    return asyncio.run(engine._agent_turn(engine.agents[name], 1))


def changes(engine):
    return [(e.data["agent"], e.data["condition"], e.data["change"], e.data["into"])
            for e in engine.events if e.type == EventType.CONDITION_CHANGE]


def test_config_rejects_unknown_references():
    with pytest.raises(ValueError, match="unknown condition 'ghost'"):
        make_engine(agents=[{"name": "A", "conditions": ["ghost"]}])
    with pytest.raises(ValueError, match="unknown action 'scratch'"):
        make_engine(conditions={"zombie": {"controller": "rules", "rules": {"action": "scratch"}}}, actions={})
    with pytest.raises(ValueError, match="clashes"):
        make_engine(actions={"move": {}})
    with pytest.raises(ValueError, match="unknown condition"):
        make_engine(events={"scheduled": [{"tick": 1, "type": "x", "description": "d",
                                           "effects": {"add_condition": {"Ana": "ghost"}}}]})


def test_rule_controlled_agent_bites_without_the_llm():
    e = make_engine()

    async def no_llm(*_):
        raise AssertionError("rule agents must not call the LLM")

    e.llm.decide = no_llm
    action = asyncio.run(e._agent_turn(e.agents["zed"], 1))
    ana = e.agents["ana"]
    assert action.type == ActionType.CUSTOM and action.content == "bites Ana"
    assert ana.state.health == pytest.approx(0.7)
    assert "infected" in ana.state.conditions
    assert ("Ana", "infected", "added", "") in changes(e)
    data = [x.data for x in e.events if x.type == EventType.AGENT_ACTION][-1]
    assert data["name"] == "bite" and data["success"] is True


def test_rule_agent_seeks_targets_when_alone():
    e = make_engine()
    asyncio.run(e.kill(e.agents["ana"], 1))
    action = asyncio.run(e._agent_turn(e.agents["zed"], 2))
    assert action.type == ActionType.MOVE and e.agents["zed"].location == "Church"


def test_infection_turns_into_zombie_after_its_duration():
    e = make_engine()
    ana = e.agents["ana"]
    asyncio.run(e.add_condition(ana, "infected", 1))
    asyncio.run(e._condition_tick(2))
    assert ana.state.conditions == {"infected": 1} and ana.state.energy == pytest.approx(0.95)
    asyncio.run(e._condition_tick(3))
    assert ana.state.conditions == {"zombie": None}
    assert ("Ana", "infected", "transformed", "zombie") in changes(e)
    assert e._controller(ana) is not None
    # once a zombie, she can't be infected again
    assert not asyncio.run(e.add_condition(ana, "infected", 4))


def test_conditions_wear_off():
    e = make_engine()
    bob = e.agents["bob"]
    asyncio.run(e.add_condition(bob, "panicked", 1))
    asyncio.run(e._condition_tick(2))
    assert "panicked" not in bob.state.conditions and bob.state.mood == pytest.approx(-0.2)


def test_contagion_by_interaction():
    e = make_engine()
    asyncio.run(e.add_condition(e.agents["ana"], "infected", 1))
    e.agents["bob"].location = "Plaza"
    e.environment.move_agent("bob", "Church", "Plaza")
    act(e, "bob", type=ActionType.SPEAK, target="Ana", content="Are you ok?")
    assert "infected" in e.agents["bob"].state.conditions


def test_contagion_by_place():
    e = make_engine(conditions={**ZOMBIES["conditions"],
                                "flu": {"contagion": {"chance": 1.0, "via": "place"}}})
    asyncio.run(e.add_condition(e.agents["ana"], "flu", 1))
    asyncio.run(e._condition_tick(2))
    assert "flu" in e.agents["zed"].state.conditions and "flu" not in e.agents["bob"].state.conditions


def test_scenario_action_with_cost_requirements_and_chance():
    e = make_engine()
    shot = act(e, "ana", type=ActionType.CUSTOM, target="Zed", parameters={"name": "shoot"})
    assert shot.content == "shoots Zed" and e.agents["zed"].state.status == "dead"
    assert e.agents["ana"].state.resources["ammo"] == 1
    # a failed attempt still spends the ammo
    e.config.actions["shoot"].chance = 0.0
    e.agents["walker"] = e._create_agent(e.config.agents[0].model_copy(update={"name": "Walker", "conditions": ["zombie"]}))
    missed = act(e, "ana", type=ActionType.CUSTOM, target="Walker", parameters={"name": "shoot"})
    assert missed.content == "misses Walker" and "ammo" not in e.agents["ana"].state.resources
    assert e.agents["walker"].alive
    # shoot needs a zombie target
    e2 = make_engine()
    e2.environment.move_agent("bob", "Church", "Plaza")
    e2.agents["bob"].location = "Plaza"
    refused = act(e2, "ana", type=ActionType.CUSTOM, target="Bob", parameters={"name": "shoot"})
    assert refused.type == ActionType.WAIT and "can't be done to Bob" in refused.reasoning
    # barricade: only carpenters, no target, changes the world
    assert act(e2, "ana", type=ActionType.CUSTOM, parameters={"name": "barricade"}).type == ActionType.WAIT
    done = act(e2, "bob", type=ActionType.CUSTOM, parameters={"name": "barricade"})
    assert done.type == ActionType.CUSTOM and e2.environment.global_state["barricaded"] is True


def test_unknown_action_names_are_reported_as_parse_errors():
    e = make_engine()
    done = act(e, "ana", type=ActionType.CUSTOM, target="Zed", parameters={"name": "dance"})
    assert done.type == ActionType.WAIT and done.reasoning.startswith("Parse error")


def test_antidote_cures():
    e = make_engine()
    ana = e.agents["ana"]
    asyncio.run(e.add_condition(ana, "infected", 1))
    act(e, "ana", type=ActionType.USE, item="antidote")
    assert "infected" not in ana.state.conditions and "antidote" not in ana.state.resources


def test_prompt_lists_conditions_visible_states_and_special_actions():
    e = make_engine()
    ana = e.agents["ana"]
    asyncio.run(e.add_condition(ana, "infected", 1))
    obs = e._build_observation(ana, 1)
    assert "Your conditions: infected — fever and bite marks (in 2 ticks you will become zombie)." in obs
    assert "You notice: Zed (a shambling corpse)." in obs
    assert "- shoot (target: a person here; costs ammo x1): Shoot someone here" in obs
    assert "barricade" not in obs  # not a carpenter
    state = e.get_state()
    assert state["agents"]["ana"]["state"]["conditions"] == {"infected": 2}
    assert state["conditions"]["zombie"]["controller"] == "rules"
    assert state["actions"]["shoot"]["target"] == "agent"


def test_effects_add_and_remove_conditions_and_spawn_with_conditions():
    e = make_engine()
    fx = {"add_condition": {"location:Church": "panicked"}, "remove_condition": {"Zed": ["zombie"]},
          "spawn": {"name": "Walker", "starting_location": "Church", "conditions": ["zombie"]}}
    asyncio.run(e.inject_event("Chaos", effects=fx))
    assert "panicked" in e.agents["bob"].state.conditions
    assert "zombie" not in e.agents["zed"].state.conditions
    assert "zombie" in e.agents["walker"].state.conditions


def test_parser_maps_unknown_names_to_scenario_actions():
    p = LLMProvider()
    a = p._parse_action(json.dumps({"action": "Bite", "target": "Zed"}))
    assert a.type == ActionType.CUSTOM and a.parameters["name"] == "bite" and a.target == "Zed"
    # "shoot" is also the built-in attack; the engine keeps a scenario's own "shoot" (alias_of)
    a = p._parse_action(json.dumps({"action": "Shoot", "target": "Zed"}))
    assert a.type == ActionType.ATTACK and a.parameters["alias_of"] == "shoot"
    assert p._parse_action(json.dumps({"action": "go away!"})).type == ActionType.WAIT


def test_demo_run_spreads_the_outbreak():
    e = make_engine(duration="24 hours")
    asyncio.run(e.run())
    kinds = {c[2] for c in changes(e)}
    assert "added" in kinds
    assert any(x.type == EventType.AGENT_ACTION and x.data.get("name") == "bite" for x in e.events)
