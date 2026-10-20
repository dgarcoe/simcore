"""Role-playing rules: attributes, skills, d20 checks, experience and levels."""

import asyncio
import json

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.agents.persona import PersonalityTraits
from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.rpg import derive_sheet, level_for, modifier, proficiency
from simcore.engine.simulation import SimulationEngine
from simcore.llm.provider import LLMProvider

WORLD = {
    "name": "RPG",
    "duration": "48 hours",
    "rpg": True,
    "environment": {"locations": [{"name": "Plaza"}, {"name": "Granja"}]},
    "items": {
        "escopeta": {
            "consumable": False,
            "weapon": {"damage": 0.4, "chance": 0.5, "ammo": "cartuchos", "ranged": True},
        },
        "cartuchos": {},
        "chaleco": {"consumable": False, "armor": 3},
        "botiquin": {"on_use": {"health": 0.4}},
    },
    "conditions": {"zombi": {"controller": "rules", "rules": {"action": "", "wander": 0}}},
    "actions": {
        "forzar": {
            "description": "forzar la puerta",
            "target": "none",
            "check": {"attribute": "strength", "skill": "athletics", "dc": 15},
            "message": "fuerza la puerta",
        },
        "morder": {
            "who": {"conditions": ["zombi"]},
            "check": {"attribute": "strength", "against": "constitution"},
            "on_target": {"health": -0.2},
        },
    },
    "agents": [
        {
            "name": "Ortega",
            "profession": "guardia",
            "starting_location": "Plaza",
            "attributes": {"strength": 16, "dexterity": 14},
            "skills": ["athletics", "intimidation"],
            "resources": {"escopeta": 1, "cartuchos": 3},
        },
        {
            "name": "Inés",
            "profession": "médica",
            "starting_location": "Plaza",
            "resources": {"botiquin": 2},
        },
        {"name": "Bob", "starting_location": "Plaza", "resources": {"chaleco": 1}},
        {"name": "Zed", "starting_location": "Plaza", "conditions": ["zombi"]},
    ],
}


def make_engine(**changes) -> SimulationEngine:
    data = json.loads(json.dumps(WORLD))
    data.update(changes)
    engine = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    engine.tick_delay = 0
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


def rolls(e):
    return [x.data for x in e.events if x.type == EventType.DICE_ROLL]


class Dice:
    """Feeds fixed d20 results to the engine's rng."""

    def __init__(self, engine, *values):
        self.values = list(values)
        engine._rng.randint = lambda a, b: self.values.pop(0)


def test_basics_and_derived_sheets():
    assert modifier(16) == 3 and modifier(9) == -1 and modifier(10) == 0
    assert level_for(0) == 1 and level_for(100) == 2 and level_for(260) == 3
    assert proficiency(1) == 2 and proficiency(5) == 3
    attrs, skills = derive_sheet(PersonalityTraits(extraversion=0.9, openness=0.2), "médica")
    assert attrs["charisma"] > 10 and attrs["wisdom"] >= 13 and "medicine" in skills
    e = make_engine()
    assert e._sheet(ag(e, "ortega"))["attributes"]["strength"] == 16
    assert e._sheet(ag(e, "ines"))["skills"] == {"medicine", "insight"}


def test_check_against_dc_critical_and_fumble():
    e = make_engine()
    Dice(e, 12, 20, 1)
    ortega = ag(e, "ortega")
    r = asyncio.run(e._check(ortega, "strength", "athletics", dc=17, label="x"))
    assert r["total"] == 12 + 3 + 2 and r["success"] is True  # 17 >= 17
    assert asyncio.run(e._check(ortega, "strength", dc=99))["critical"] is True
    assert asyncio.run(e._check(ortega, "strength", dc=1))["fumble"] is True
    assert [x["success"] for x in rolls(e)] == [True, True, False]


def test_ranged_attack_uses_dexterity_against_armor_and_crits_double():
    e = make_engine()
    Dice(e, 10, 20)
    a = act(e, "ortega", 1, type=ActionType.ATTACK, target="Bob")
    r = rolls(e)[-1]
    assert r["attribute"] == "dexterity" and r["dc"] == 10 + 0 + 3  # Bob's defense with the vest
    assert r["total"] == 10 + 2 and a.parameters["hit"] is False
    a = act(e, "ortega", 2, type=ActionType.ATTACK, target="Bob")
    assert a.parameters["critical"] and a.parameters["damage"] == pytest.approx(0.8)


def test_heal_degrees():
    e = make_engine()
    bob = ag(e, "bob")
    bob.state.health = 0.2
    Dice(e, 1)
    a = act(e, "ines", 1, type=ActionType.HEAL, target="Bob")
    assert a.parameters["fumble"] and bob.state.health == pytest.approx(0.2)
    Dice(e, 3)  # 3 + 1 (WIS) + 2 = 6 < 10: half
    act(e, "ines", 2, type=ActionType.HEAL, target="Bob")
    assert bob.state.health == pytest.approx(0.4)


def test_scenario_action_check_and_opposed_bite():
    e = make_engine()
    Dice(e, 10)  # 10 + 3 + 2 = 15 >= 15
    a = act(e, "ortega", 1, type=ActionType.CUSTOM, parameters={"name": "forzar"})
    assert a.parameters["success"] is True and rolls(e)[-1]["dc"] == 15
    e2 = make_engine(rpg=False)
    act(e2, "ortega", 1, type=ActionType.CUSTOM, parameters={"name": "forzar"})
    assert not rolls(e2)  # without rpg: plain chance, no dice
    Dice(e, 15, 2)
    zed = ag(e, "zed")
    ok = asyncio.run(e._action_roll(zed, ag(e, "bob"), e.config.actions["morder"], "morder", 3))
    assert ok and rolls(e)[-1]["against"]["agent"] == "Bob"


def test_deceive_caught_and_persuade():
    e = make_engine()
    Dice(e, 2, 18)  # Inés lies badly, Bob sees through it
    act(
        e,
        "ines",
        1,
        type=ActionType.SPEAK,
        target="Bob",
        content="El pozo es seguro",
        intent="deceive",
        share="El pozo es seguro",
    )
    bob = ag(e, "bob")
    assert not any("pozo" in b["text"] for b in bob.state.beliefs)
    assert any("lying" in m.content for m in bob.memory.short_term)
    Dice(e, 19, 2)
    act(
        e,
        "ines",
        2,
        type=ActionType.SPEAK,
        target="Bob",
        content="Ven al hospital",
        intent="persuade",
    )
    assert any("convinced me" in m.content for m in bob.memory.short_term)


def test_xp_and_level_up_improves_most_used_attribute_and_learns_skills():
    e = make_engine()
    ortega = ag(e, "ortega")
    sheet = e._sheet(ortega)
    sheet["uses"]["strength"] = 5
    sheet["skill_uses"]["stealth"] = 3
    asyncio.run(e._gain_xp(ortega, 260, 4))
    ups = [x.data for x in e.events if x.type == EventType.LEVEL_UP]
    assert [u["level"] for u in ups] == [2, 3]
    assert sheet["attributes"]["strength"] == 18 and "stealth" in sheet["skills"]
    assert ups[-1]["improved"] == {"attribute": "strength", "skill": "stealth"}


def test_prompt_state_and_parser():
    e = make_engine()
    p = e._build_observation(ag(e, "ortega"), 1)
    assert "Your character: level 1 (0/100 XP). STR 16 (+3), DEX 14 (+2)" in p
    assert "Your odds (dice decide): attack ≈" in p and "(with your escopeta)" in p
    state = next(a for a in e.get_state()["agents"].values() if a["name"] == "Ortega")
    assert state["rpg"]["level"] == 1 and state["rpg"]["proficiency"] == 2
    a = LLMProvider()._parse_action(
        json.dumps({"action": "speak", "target": "Bob", "intent": "Deceive"})
    )
    assert a.intent == "deceive"
    assert "Your character" not in make_engine(rpg=False)._build_observation(
        ag(make_engine(rpg=False), "ortega"), 1
    )


def test_validation():
    data = json.loads(json.dumps(WORLD))
    data["agents"][0]["attributes"]["luck"] = 30
    with pytest.raises(ValueError, match="unknown attribute 'luck'"):
        SimulationConfig.model_validate(data)
    data = json.loads(json.dumps(WORLD))
    data["actions"]["forzar"]["check"]["skill"] = "flying"
    with pytest.raises(ValueError, match="unknown skill 'flying'"):
        SimulationConfig.model_validate(data)


def test_demo_run_rolls_dice():
    e = make_engine()
    asyncio.run(e.run())
    assert rolls(e)
