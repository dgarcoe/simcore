"""Small models only fight when told this is fiction and shown whom they have reason to fight."""

import asyncio
import json

from simcore.agents.actions import ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.simulation import SimulationEngine
from simcore.llm.prompts import FICTION_FRAME
from simcore.llm.provider import LLMProvider

WORLD = {
    "name": "Castle",
    "duration": "12 hours",
    "rpg": True,
    "environment": {"locations": [{"name": "Salón"}, {"name": "Capilla"}]},
    "items": {
        "sello": {"consumable": False, "visible": True, "drops": True},
        "daga": {"consumable": False, "weapon": {"damage": 0.35, "chance": 0.75}},
        "veneno": {},
    },
    "actions": {
        "robar": {
            "target_requires": {"items": {"sello": 1}},
            "on_target": {"items": {"sello": -1}},
        },
        "envenenar": {"who": {"items": {"veneno": 1}}},
    },
    "rules": [
        {"when": {"holds": {"item": "sello", "ticks": 12}}, "then": {"end": "{holder} gana"}}
    ],
    "factions": {
        "Elvira": {"members": ["Elvira"], "rivals": ["Rodrigo"]},
        "Rodrigo": {"members": ["Rodrigo"], "rivals": ["Elvira"]},
    },
    "agents": [
        {
            "name": "Rodrigo",
            "starting_location": "Salón",
            "resources": {"daga": 1},
            "relationships": {
                "Martín": {"type": "enemigo", "sentiment": -0.2},
                "Inés": {"sentiment": 0.6},
            },
        },
        {"name": "Tobías", "starting_location": "Salón", "resources": {"sello": 1}},
        {"name": "Elvira", "starting_location": "Salón"},
        {"name": "Martín", "starting_location": "Salón"},
        {"name": "Inés", "starting_location": "Salón"},
    ],
}


def make_engine():
    e = SimulationEngine(
        SimulationConfig.model_validate(json.loads(json.dumps(WORLD))), demo_mode=True
    )
    asyncio.run(e._refresh_holders(0))
    return e


def test_hostile_options_name_the_reason_and_what_to_do():
    e = make_engine()
    text = e._hostile_prompt(e._find_agent_by_name("Rodrigo"), 1)
    lines = text.splitlines()
    assert lines[0].startswith("People here you have reason to fight")
    tobias = next(line for line in lines if line.startswith("- Tobías:"))
    assert "has the sello (keeping it wins)" in tobias and '"attack" (your daga, ≈' in tobias
    assert '"robar"' in tobias and "envenenar" not in tobias
    assert any(line.startswith("- Elvira: of Elvira, your rivals. You could") for line in lines)
    assert any(line.startswith("- Martín: your enemigo. You could") for line in lines)
    assert not any("Inés" in line for line in lines)  # a friend


def test_the_decision_prompt_says_it_is_fiction():
    e = make_engine()
    seen = {}

    async def decide(system, user):
        seen["system"] = system
        from simcore.agents.actions import Action

        return Action(type=ActionType.WAIT)

    e.llm.decide = decide
    asyncio.run(e._agent_turn(e._find_agent_by_name("Rodrigo"), 1))
    assert FICTION_FRAME in seen["system"]


def test_violent_verbs_and_a_verb_with_its_target_are_understood():
    p = LLMProvider(model="x")
    a = p._parse_action('{"action": "stab", "target": "Tobías"}')
    assert a.type == ActionType.ATTACK and a.target == "Tobías"
    b = p._parse_action('{"action": "atacar a Tobías", "content": "¡El sello es mío!"}')
    assert b.type == ActionType.ATTACK and b.target == "Tobías"
    c = p._parse_action('{"action": "apuñalar", "target": "Elvira"}')
    assert c.type == ActionType.ATTACK


def test_free_text_violence_becomes_a_real_attack():
    from simcore.agents.actions import Action

    e = make_engine()
    rodrigo = e._find_agent_by_name("Rodrigo")
    told = Action(type=ActionType.CUSTOM, content="Apuñalo a Tobías con la daga")
    a = e._read_violence(rodrigo, told)
    assert a.type == ActionType.ATTACK and a.target == "Tobías"
    talk = Action(type=ActionType.INTERACT, target="Elvira", content="le pego un empujón")
    assert e._read_violence(rodrigo, talk).type == ActionType.ATTACK
    # A scenario action named like the verb wins (robar is not violent; envenenar needs poison).
    rodrigo.state.resources["veneno"] = 1
    poison = Action(type=ActionType.CUSTOM, content="ataco a Martín")
    assert e._read_violence(rodrigo, poison).type == ActionType.ATTACK
    # Peaceful or aimless texts are left alone.
    for text in ("No quiero pelear con nadie", "Golpeo la mesa con rabia", "hablo con Elvira"):
        same = Action(type=ActionType.CUSTOM, content=text)
        assert e._read_violence(rodrigo, same) is same


def test_a_target_with_a_description_is_still_found():
    e = make_engine()
    rodrigo = e._find_agent_by_name("Rodrigo")
    assert e._present(rodrigo, "Tobías (el que tiene el sello)").name == "Tobías"
    assert e._present(rodrigo, "Elvira, la viuda").name == "Elvira"


def test_a_free_text_hit_uses_the_scenario_action_with_its_roll():
    from simcore.agents.actions import Action

    data = json.loads(json.dumps(WORLD))
    data["actions"]["golpear"] = {"check": {"attribute": "strength", "dc": 5},
                                  "on_target": {"health": -0.3}}
    e = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    rodrigo = e._find_agent_by_name("Rodrigo")
    a = e._read_violence(rodrigo, Action(type=ActionType.CUSTOM, content="Golpeo a Tobías"))
    assert a.type == ActionType.CUSTOM and a.parameters == {"name": "golpear"}
    assert a.target == "Tobías"
