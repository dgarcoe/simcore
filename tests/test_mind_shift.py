"""Conditions that change an agent's mind while the LLM keeps deciding (a zombie that thinks)."""

import asyncio
import json

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.simulation import SimulationEngine

WORLD = {
    "name": "Mind",
    "duration": "12 hours",
    "environment": {"locations": [{"name": "Plaza"}, {"name": "Granja"}]},
    "conditions": {
        "zombi": {
            "appearance": "un cuerpo que gruñe",
            "mind": {
                "identity": "Ya no eres {name}, el {profession}: eres un muerto que camina.",
                "goals": ["Encontrar a los vivos", "Morderlos"],
                "speech": False,
                "memories": False,
                "actions": ["move", "wait", "observe", "follow", "morder"],
            },
        },
        "horda": {"controller": "rules", "rules": {"action": "morder"}},
        "poseido": {
            "mind": {"identity": "Las esporas te mueven.", "goals": ["Extender las esporas"]}
        },
    },
    "actions": {
        "morder": {
            "description": "morder",
            "who": {"any_conditions": ["zombi", "horda"]},
            "target_requires": {"not_conditions": ["zombi", "horda"]},
            "on_target": {"health": -0.1},
        },
        "golpear": {
            "description": "golpear",
            "target_requires": {"any_conditions": ["zombi", "horda"]},
            "on_target": {"health": -0.4},
        },
    },
    "agents": [
        {
            "name": "Marcos",
            "profession": "veterinario",
            "starting_location": "Plaza",
            "conditions": ["zombi"],
            "goals": ["Proteger la granja"],
            "backstory": "Atiende a los animales.",
            "relationships": {"Ana": {"type": "hermana", "sentiment": 0.8}},
            "secrets": ["Me mordió una vaca"],
        },
        {"name": "Ana", "profession": "médica", "starting_location": "Plaza"},
        {"name": "Hugo", "starting_location": "Plaza", "conditions": ["horda"]},
        {
            "name": "Sara",
            "starting_location": "Granja",
            "conditions": ["poseido"],
            "goals": ["Volver a casa"],
        },
    ],
}


def make_engine(**changes) -> SimulationEngine:
    data = json.loads(json.dumps(WORLD))
    data.update(changes)
    engine = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    engine.events = []
    engine.event_bus.on_all(lambda e: engine.events.append(e))
    return engine


def ag(e, name):
    return e._find_agent_by_name(name)


def prompts(e, name):
    agent = ag(e, name)
    found = e._mind(agent)
    mind = found[1] if found else None
    return agent.persona_prompt(mind), agent.build_prompt(e._build_observation(agent, 1), mind=mind)


def test_zombie_prompt_has_new_identity_and_no_past():
    e = make_engine()
    ag(e, "marcos").observe("I fed the cows this morning.", 0)
    system, user = prompts(e, "marcos")
    assert "Ya no eres Marcos, el veterinario: eres un muerto que camina." in system
    assert "Encontrar a los vivos, Morderlos" in system
    assert "Proteger la granja" not in system and "Atiende a los animales" not in system
    assert "fed the cows" not in user and "hermana" not in user and "Me mordió una vaca" not in user
    assert "You cannot speak or communicate" in user
    assert '"action": "wait|move|observe|follow|morder"' in user
    assert 'If you "speak" here' not in user


def test_mind_keeping_memories_and_speech():
    e = make_engine()
    system, user = prompts(e, "sara")
    assert (
        "Las esporas te mueven." in system
        and "Personality:" in system
        and "Extender las esporas" in system
    )
    assert "You cannot speak" not in user and '"action": "wait|move|speak|' in user


def test_forbidden_actions_become_wait():
    e = make_engine()
    calls = []

    async def decide(system, prompt):
        calls.append(system)
        return Action(type=ActionType.SPEAK, target="Ana", content="¡Ana, ayúdame!")

    e.llm.decide = decide
    action = asyncio.run(e._agent_turn(ag(e, "marcos"), 1))
    assert calls, "a zombie with a mind still asks the LLM"
    assert action.type == ActionType.WAIT and "Can't communicate (zombi)" in action.reasoning
    assert any("I am zombi" in m.content for m in ag(e, "marcos").memory.short_term)

    async def work(*_):
        return Action(type=ActionType.WORK, content="ordeñar")

    e.llm.decide = work
    assert asyncio.run(e._agent_turn(ag(e, "marcos"), 2)).type == ActionType.WAIT

    async def bite(*_):
        return Action(type=ActionType.CUSTOM, target="Ana", parameters={"name": "morder"})

    e.llm.decide = bite
    assert asyncio.run(e._agent_turn(ag(e, "marcos"), 3)).parameters.get("name") == "morder"


def test_rule_controlled_condition_skips_the_llm():
    e = make_engine()

    async def boom(*_):
        raise AssertionError("rules don't call the LLM")

    e.llm.decide = boom
    asyncio.run(e._agent_turn(ag(e, "hugo"), 1))


def test_any_conditions_requirement():
    e = make_engine()
    assert e._meets(ag(e, "marcos"), e.config.actions["morder"].who)
    assert e._meets(ag(e, "hugo"), e.config.actions["morder"].who)
    assert not e._meets(ag(e, "ana"), e.config.actions["morder"].who)
    assert "golpear" in [n for n, _ in e._available_actions(ag(e, "ana"))]


def test_no_plans_or_reflection_without_memories():
    e = make_engine(planning=True)
    calls = []

    async def plan(*_):
        calls.append(1)
        return ["step"]

    e.llm.plan = plan
    asyncio.run(e._maybe_plan(ag(e, "marcos"), "obs", 1, "en"))
    assert not calls
    asyncio.run(e._maybe_plan(ag(e, "ana"), "obs", 1, "en"))
    assert calls


def test_state_shows_the_mind():
    e = make_engine()
    state = e.get_state()
    marcos = next(a for a in state["agents"].values() if a["name"] == "Marcos")
    assert marcos["mind"]["condition"] == "zombi" and not marcos["mind"]["speech"]
    assert marcos["mind"]["identity"].startswith("Ya no eres Marcos")
    assert state["conditions"]["zombi"]["mind"]["goals"] == ["Encontrar a los vivos", "Morderlos"]
    assert "mind" not in next(a for a in state["agents"].values() if a["name"] == "Ana")


def test_validation():
    data = json.loads(json.dumps(WORLD))
    data["conditions"]["zombi"]["mind"]["actions"].append("volar")
    with pytest.raises(ValueError, match="allows unknown action 'volar'"):
        SimulationConfig.model_validate(data)
    data = json.loads(json.dumps(WORLD))
    data["actions"]["morder"]["who"]["any_conditions"].append("vampiro")
    with pytest.raises(ValueError, match="unknown condition 'vampiro'"):
        SimulationConfig.model_validate(data)


def test_demo_run_zombie_hunts():
    e = make_engine(duration="24 hours")
    e.tick_delay = 0
    asyncio.run(e.run())
    marcos = [
        x
        for x in e.events
        if x.source == "Marcos" and x.type.value in ("agent_action", "agent_speak")
    ]
    assert marcos and all(x.data.get("action_type") != "speak" for x in marcos)
