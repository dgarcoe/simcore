"""A richer society: attack and heal, groups (configured or founded), votes, reputation,
favors, routines and emotions."""

import asyncio
import json

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine
from simcore.llm.provider import LLMProvider

WORLD = {
    "name": "Society",
    "duration": "48 hours",
    "environment": {"locations": [{"name": "Plaza"}, {"name": "Pensión"}, {"name": "Hospital"}]},
    "items": {
        "escopeta": {
            "consumable": False,
            "weapon": {"damage": 0.6, "chance": 1.0, "ammo": "cartuchos"},
        },
        "cartuchos": {},
        "botiquin": {"on_use": {"health": 0.4}},
    },
    "conditions": {"herido": {"per_tick": {"health": -0.01}}},
    "factions": {
        "Guardia": {"goal": "Proteger el pueblo", "leader": "Ortega", "members": ["Ortega", "Lola"]}
    },
    "votes": {
        "cerrar": {
            "question": "¿Cerramos la pensión?",
            "options": {"sí": {"locations": {"Pensión": {"closed": True}}}, "no": {}},
        },
    },
    "routines": {"médica": {"night": {"rest": ["Pensión"]}}},
    "agents": [
        {
            "name": "Ortega",
            "profession": "guardia",
            "starting_location": "Plaza",
            "resources": {"escopeta": 1, "cartuchos": 2},
        },
        {"name": "Lola", "starting_location": "Plaza"},
        {
            "name": "Inés",
            "profession": "médica",
            "starting_location": "Plaza",
            "resources": {"botiquin": 1},
        },
        {"name": "Bob", "starting_location": "Plaza", "conditions": ["herido"]},
        {"name": "Tomás", "starting_location": "Hospital"},
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


def of(e, kind):
    return [x.data for x in e.events if x.type == kind]


def test_attack_with_a_weapon_spends_ammo_can_kill_and_costs_reputation():
    e = make_engine()
    a = act(e, "ortega", 1, type=ActionType.ATTACK, target="Bob")
    assert (
        a.type == ActionType.ATTACK
        and a.parameters["weapon"] == "escopeta"
        and a.parameters["damage"] == 0.6
    )
    assert ag(e, "ortega").state.resources["cartuchos"] == 1
    assert e._reputation["Ortega"] < 0  # Bob was no threat
    act(e, "ortega", 2, type=ActionType.ATTACK, target="Bob")
    assert ag(e, "bob").state.status == "dead" and of(e, EventType.AGENT_DEATH)
    a = act(e, "ortega", 3, type=ActionType.ATTACK, target="Lola")  # out of ammo: fists
    assert a.parameters["weapon"] == ""
    assert "You feel" in e._build_observation(ag(e, "lola"), 3) or e._emotion(ag(e, "lola"), 3)[
        0
    ] in ("angry", "afraid")


def test_heal_with_an_item_or_skills_and_the_favor_owed():
    e = make_engine()
    ag(e, "bob").state.health = 0.3
    a = act(e, "ines", 1, type=ActionType.HEAL, target="Bob")
    assert (
        a.type == ActionType.HEAL
        and a.item == "botiquin"
        and ag(e, "bob").state.health == pytest.approx(0.7)
    )
    assert "You owe favors to: Inés (being healed)." in e._build_observation(ag(e, "bob"), 1)
    a = act(e, "ines", 2, type=ActionType.HEAL, target="Bob")  # no item left: skills
    assert a.parameters["with"] == "care" and "herido" not in ag(e, "bob").state.conditions
    assert act(e, "lola", 2, type=ActionType.HEAL, target="Bob").type == ActionType.WAIT  # no means
    act(
        e, "bob", 3, type=ActionType.GIVE, target="Inés", item="botiquin", amount=1
    )  # nothing to give: no repay
    assert e._reputation["Inés"] > 0


def test_configured_group_and_leader_succession():
    e = make_engine()
    p = e._build_observation(ag(e, "lola"), 1)
    assert "You belong to Guardia (leader: Ortega; goal: Proteger el pueblo; members: Ortega)." in p
    asyncio.run(e.kill(ag(e, "ortega"), 1, "accidente"))
    asyncio.run(e._society_tick(2))
    changes = [(d["change"], d["agent"]) for d in of(e, EventType.FACTION_CHANGE)]
    assert ("left", "Ortega") in changes
    assert e._factions["Guardia"]["leader"] == "Lola"


def test_spontaneous_group_found_invite_join_leave():
    e = make_engine()
    for a, b in (("Inés", "Bob"), ("Bob", "Inés"), ("Inés", "Tomás"), ("Tomás", "Inés")):
        ag(e, a).memory.update_relationship(b, 0.6, 0)
    e._find_agent_by_name("tomas").state.location = "Plaza"
    e.environment.move_agent(ag(e, "tomas").id, "Hospital", "Plaza")
    assert "think like you: you could form a group" in e._build_observation(ag(e, "ines"), 1)
    act(
        e,
        "ines",
        1,
        type=ActionType.FOUND_GROUP,
        content="Los del molino",
        goal="Salir juntos",
        target="Bob, Tomás",
    )
    assert e._factions["Los del molino"]["leader"] == "Inés"
    assert 'invited you to join "Los del molino"' in e._build_observation(ag(e, "bob"), 2)
    act(e, "bob", 2, type=ActionType.JOIN, target="los del molino")
    assert e._faction_of("Bob") == "Los del molino"
    act(e, "bob", 3, type=ActionType.LEAVE)
    assert "Los del molino" not in e._factions  # one member left: dissolved
    assert [d["change"] for d in of(e, EventType.FACTION_CHANGE)][-1] == "dissolved"


def test_attacks_between_groups_make_rivals():
    e = make_engine()
    act(e, "ines", 1, type=ActionType.FOUND_GROUP, content="Médicos", target="Bob")
    act(e, "bob", 1, type=ActionType.JOIN, target="Médicos")
    act(e, "lola", 2, type=ActionType.ATTACK, target="Bob")
    assert "Médicos" in e._factions["Guardia"]["rivals"]


def test_vote_with_consequences():
    e = make_engine()
    a = act(e, "lola", 1, type=ActionType.CALL_VOTE, content="cerrar")
    assert a.parameters["question"] == "¿Cerramos la pensión?"
    assert (
        'There is a vote here: "¿Cerramos la pensión?" (options: sí, no)'
        in e._build_observation(ag(e, "bob"), 1)
    )
    act(e, "bob", 2, type=ActionType.VOTE, content="Si")
    act(e, "ines", 2, type=ActionType.VOTE, content="sí")
    act(e, "ortega", 2, type=ActionType.VOTE, content="no")
    asyncio.run(e._society_tick(4))
    result = of(e, EventType.VOTE_RESULT)[-1]
    assert result["winner"] == "sí" and result["counts"] == {"sí": 2, "no": 1}
    assert e.environment.get_location("Pensión").closed


def test_routines_and_time_of_day():
    e = make_engine()
    assert "At this time you usually: rest at Pensión." in e._build_observation(ag(e, "ines"), 23)
    assert "(night): most people sleep." in e._build_observation(ag(e, "lola"), 23)


def test_emotion_from_the_answer_and_seen_by_others():
    e = make_engine()
    act(e, "lola", 1, type=ActionType.WAIT, emotion="furiosa")
    act(e, "lola", 1, type=ActionType.WAIT, emotion="ira")
    assert e._emotion(ag(e, "lola"), 1) == ("angry", "")
    assert "Lola: " in e._build_observation(
        ag(e, "bob"), 2
    ) and "looks furious" in e._build_observation(ag(e, "bob"), 2)


def test_effects_and_rules_with_groups():
    e = make_engine()
    asyncio.run(
        e.apply_effects(
            __import__("simcore.engine.effects", fromlist=["x"]).parse_effects(
                {"join": {"Tomás": "Guardia"}}
            ),
            1,
        )
    )
    assert e._faction_of("Tomás") == "Guardia"
    rule = SimulationConfig.model_validate(
        {
            **json.loads(json.dumps(WORLD)),
            "rules": [
                {
                    "when": {"count": {"faction": "Guardia", "gte": 3}},
                    "then": {"end": "La Guardia manda"},
                }
            ],
        }
    ).rules[0]
    assert e._rule_holds(rule, 1)


def test_parser_and_scenario_actions_named_like_builtins():
    p = LLMProvider()
    a = p._parse_action(json.dumps({"action": "curar", "target": "Bob"}))
    assert a.type == ActionType.HEAL and a.parameters["alias_of"] == "curar"
    v = p._parse_action(
        json.dumps(
            {"action": "call_vote", "content": "¿?", "options": ["a", "b"], "emotion": "miedo"}
        )
    )
    assert v.options == ["a", "b"] and v.emotion == "miedo"


def test_validation():
    data = json.loads(json.dumps(WORLD))
    data["factions"]["Guardia"]["members"].append("Fantasma")
    data["routines"]["*"] = {"siesta": {"rest": ["Luna"]}}
    with pytest.raises(ValueError) as err:
        SimulationConfig.model_validate(data)
    msg = str(err.value)
    assert (
        "unknown member 'Fantasma'" in msg
        and "unknown time of day 'siesta'" in msg
        and "unknown place 'Luna'" in msg
    )


def test_demo_run():
    e = make_engine()
    asyncio.run(e.run())
    kinds = {x.data.get("action_type") for x in e.events}
    assert kinds & {"heal", "vote", "join", "found_group", "attack"}
    assert "society" in e.get_state()


def test_yaml_loader_keeps_society(tmp_path):
    import yaml

    from simcore.config.loader import load_config

    raw = {"simulation": {"name": "Y", "duration": "4 hours"}, **{k: v for k, v in WORLD.items() if k not in ("name", "duration")}}
    raw["society"] = {"spontaneous_groups": False}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    cfg = load_config(path)
    assert "Guardia" in cfg.factions and "cerrar" in cfg.votes and "médica" in cfg.routines
    assert cfg.society.spontaneous_groups is False


def test_vote_options_written_as_yaml_booleans():
    import yaml

    from simcore.config.schema import VoteConfig

    raw = yaml.safe_load("question: ¿Sí?\noptions:\n  yes: {}\n  no: {depart: Bob}\n")
    assert list(VoteConfig.model_validate(raw).options) == ["yes", "no"]


def test_configured_vote_from_tick_and_once():
    e = make_engine()
    e.config.votes["cerrar"].from_tick = 3
    assert act(e, "lola", 1, type=ActionType.CALL_VOTE, content="cerrar").type == ActionType.WAIT
    act(e, "lola", 3, type=ActionType.CALL_VOTE, content="cerrar")
    act(e, "bob", 3, type=ActionType.VOTE, content="no")
    asyncio.run(e._society_tick(6))
    assert "Matters you can put" not in e._build_observation(ag(e, "lola"), 6)
    assert act(e, "lola", 6, type=ActionType.CALL_VOTE, content="cerrar").type == ActionType.WAIT
