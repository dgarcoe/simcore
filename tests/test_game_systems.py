"""Game systems: stealing, items that turn up, several endings with a winner, points and
achievements, cursed items and what belonging to a group is worth."""

import asyncio
import json

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.effects import parse_effects
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine

WORLD = {
    "name": "Feria",
    "duration": "24 hours",
    "environment": {"locations": [{"name": "Plaza"}, {"name": "Iglesia"}, {"name": "Bosque"}]},
    "items": {
        "oro": {},
        "pan": {"on_use": {"energy": 0.2}},
        "cáliz": {"description": "un cáliz dorado", "visible": True, "consumable": False},
        "llave": {"consumable": False},
    },
    "actions": {
        "robar": {"description": "robar algo a alguien", "on_target": {"steal": "one"}},
        "saquear": {"on_target": {"steal": "all", "steal_except": ["llave"]}},
    },
    "agents": [
        {"name": "Ana", "starting_location": "Plaza"},
        {
            "name": "Bruno",
            "starting_location": "Plaza",
            "resources": {"oro": 3, "pan": 1, "llave": 1},
        },
        {"name": "Carla", "starting_location": "Iglesia"},
    ],
}


def make_engine(**changes) -> SimulationEngine:
    data = json.loads(json.dumps(WORLD))
    data.update(changes)
    e = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    e.tick_delay = 0
    e.events = []
    e.event_bus.on_all(lambda x: e.events.append(x))
    return e


def ag(e, name):
    return e._find_agent_by_name(name)


def do(e, name, action_name, target="", tick=1):
    a = Action(type=ActionType.CUSTOM, target=target, parameters={"name": action_name})
    return asyncio.run(e._apply_custom_action(ag(e, name), a, tick))


# ── Stealing and items that turn up ─────────────────────────────────


def test_steal_one_takes_a_single_item_at_random():
    e = make_engine()
    do(e, "Ana", "robar", "Bruno")
    ana, bruno = ag(e, "Ana"), ag(e, "Bruno")
    assert sum(ana.state.resources.values()) == 1
    (item,) = ana.state.resources
    assert bruno.state.resources.get(item, 0) == {"oro": 3, "pan": 1, "llave": 1}[item] - 1
    steals = [
        x.data for x in e.events if x.type == EventType.ITEM_TRANSFER and x.data["kind"] == "steal"
    ]
    assert steals == [
        {
            "kind": "steal",
            "item": item,
            "amount": 1,
            "from": "Bruno",
            "to": "Ana",
            "location": "Plaza",
        }
    ]


def test_steal_all_takes_everything_but_the_exceptions():
    e = make_engine()
    do(e, "Ana", "saquear", "Bruno")
    assert ag(e, "Ana").state.resources == {"oro": 3, "pan": 1}
    assert ag(e, "Bruno").state.resources == {"llave": 1}


def test_losing_items_at_random():
    e = make_engine()
    asyncio.run(e.apply_effects(parse_effects({"items": {"Bruno": {"*": -2}}}), 1, "una tormenta"))
    assert sum(ag(e, "Bruno").state.resources.values()) == 3


def test_an_item_that_turns_up_is_announced():
    e = make_engine()
    asyncio.run(
        e.apply_effects(
            parse_effects({"items": {"location:Iglesia": {"cáliz": 1, "pan": 2}}}), 2, ""
        )
    )
    carla = [str(m) for m in ag(e, "Carla").memory.short_term]
    ana = [str(m) for m in ag(e, "Ana").memory.short_term]
    assert any("New here in Iglesia: 1 cáliz (un cáliz dorado)" in m for m in carla)
    assert any(
        "1 cáliz (un cáliz dorado) has turned up in Iglesia" in m for m in ana
    )  # visible: news
    assert not any("pan" in m for m in ana)  # bread is only seen by those there


# ── Points and achievements ──────────────────────────────────────────

SCORES = {
    "achievements": {
        "explorador": {
            "description": "Visitar todos los lugares",
            "points": 10,
            "when": {"visited": "all"},
        },
        "panadero": {
            "description": "Repartir pan",
            "points": 2,
            "repeat": True,
            "when": {"action": "give", "times": 2},
        },
        "ladron": {"points": 3, "when": {"action": "robar"}},
        "rico": {
            "description": "Tener oro",
            "points": 1,
            "repeat": True,
            "when": {"carries": "oro", "every": 2},
        },
        "vago": {
            "description": "Un rato sin trabajar",
            "points": -1,
            "when": {"without": "work", "every": 3},
        },
        "vivo": {"points": 5, "when": {"alive_at_end": True}},
    }
}


def points(e, name):
    return e._score_of(name)


def act(e, name, tick, **kw):
    async def decide(*_):
        return Action(**kw)

    e.llm.decide = decide
    return asyncio.run(e._agent_turn(ag(e, name), tick))


def test_achievements_for_actions_places_items_and_penalties():
    e = make_engine(scores=SCORES)
    act(e, "Ana", 1, type=ActionType.MOVE, target="Iglesia")
    act(e, "Ana", 2, type=ActionType.MOVE, target="Bosque")
    assert points(e, "Ana") == 0
    act(e, "Ana", 3, type=ActionType.MOVE, target="Plaza")  # Plaza was where she started
    assert points(e, "Ana") == 10
    act(e, "Bruno", 1, type=ActionType.GIVE, target="Ana", item="oro", amount=1)
    act(e, "Bruno", 2, type=ActionType.GIVE, target="Ana", item="oro", amount=1)
    asyncio.run(e._scores_tick(2))  # Bruno still carries gold, Ana too now
    assert points(e, "Bruno") == 2 + 1  # two gifts, and gold
    asyncio.run(e._scores_tick(3))  # three ticks without working
    assert points(e, "Carla") == -1
    asyncio.run(e._scores_tick(4))  # gold again, two ticks later
    assert points(e, "Bruno") == 2 + 1 + 1 - 1
    scored = [x.data for x in e.events if x.type == EventType.SCORE_CHANGE]
    assert scored[0] == {
        "agent": "Ana",
        "achievement": "explorador",
        "description": "Visitar todos los lugares",
        "points": 10,
        "total": 10,
    }


def test_scenario_actions_count_only_when_they_succeed():
    data = {**SCORES, "achievements": {"ladron": {"points": 3, "when": {"action": "robar"}}}}
    e = make_engine(scores=data)
    act(e, "Ana", 1, type=ActionType.CUSTOM, target="Bruno", parameters={"name": "robar"})
    assert points(e, "Ana") == 3
    e2 = make_engine(scores=data, actions={"robar": {"chance": 0.0, "on_target": {"steal": "one"}}})
    act(e2, "Ana", 1, type=ActionType.CUSTOM, target="Bruno", parameters={"name": "robar"})
    assert points(e2, "Ana") == 0


def test_agents_see_their_points_and_what_earns_them():
    e = make_engine(scores=SCORES)
    asyncio.run(e._award(ag(e, "Bruno"), "ladron", e._achievements["ladron"], 1))
    text = e._scores_prompt(ag(e, "Ana"))
    assert "you have 0, 2nd of 3. Ranking: Bruno 3, you 0, Carla 0." in text
    assert "Visitar todos los lugares +10 (still to visit: Iglesia, Bosque)" in text
    assert 'Un rato sin trabajar -1 (every 3 ticks without "work")' in text
    assert e._scores_prompt(ag(e, "Ana")) and not make_engine(
        scores={**SCORES, "visible": False}
    )._scores_prompt(ag(e, "Ana"))


def test_alive_at_end_and_the_final_points():
    e = make_engine(scores=SCORES, duration="2 hours")
    asyncio.run(e.run())
    end = [x.data for x in e.events if x.type == EventType.SIMULATION_END][0]
    assert {r["agent"] for r in end["scores"]["ranking"]} == {"Ana", "Bruno", "Carla"}
    assert all(e._earned.get(n, {}).get("vivo") == 1 for n in ("Ana", "Bruno", "Carla"))


def test_scores_are_validated():
    bad = {
        "achievements": {
            "x": {"when": {"action": "volar"}},
            "y": {"when": {"carries": "plata"}},
            "z": {"when": {}},
        }
    }
    try:
        SimulationConfig.model_validate({**WORLD, "scores": bad})
        raise AssertionError("should fail")
    except ValueError as err:
        msg = str(err)
    assert (
        "unknown action 'volar'" in msg
        and "unknown item 'plata'" in msg
        and "achievement z has no test" in msg
    )


# ── Several ways to win, and who won ─────────────────────────────────


def test_any_of_several_endings_and_the_winner():
    rules = [
        {
            "description": "Fin de la feria",
            "when": {"any": [{"score": {"gte": 10}}, {"holds": {"item": "cáliz", "ticks": 2}}]},
            "then": {"end": "{winner} gana la feria"},
        }
    ]
    e = make_engine(scores=SCORES, rules=rules)
    asyncio.run(e._rules_tick(1))
    assert e._running is not False or not e.end_message  # nothing yet
    ag(e, "Carla").state.resources["cáliz"] = 1
    asyncio.run(e._refresh_holders(1))
    e._running = True
    asyncio.run(e._rules_tick(3))
    assert e.end_message == "Carla gana la feria" and e.end_winner == "Carla"

    e2 = make_engine(scores=SCORES, rules=rules)
    e2._running = True
    asyncio.run(e2._award(ag(e2, "Bruno"), "explorador", e2._achievements["explorador"], 1))
    asyncio.run(e2._rules_tick(2))
    assert e2.end_message == "Bruno gana la feria"


def test_the_winner_of_an_ending_without_a_holder_is_the_best_score_or_the_last_alive():
    e = make_engine(scores=SCORES, rules=[{"when": {"at": 2}, "then": {"end": "Gana {winner}"}}])
    e._running = True
    asyncio.run(e._award(ag(e, "Ana"), "ladron", e._achievements["ladron"], 1))
    asyncio.run(e._rules_tick(2))
    assert e.end_message == "Gana Ana"
    e2 = make_engine(
        rules=[{"when": {"alive": {"lte": 1}}, "then": {"end": "Solo queda {winner}"}}]
    )
    e2._running = True
    for n in ("Ana", "Bruno"):
        asyncio.run(e2.kill(ag(e2, n), 1, ""))
    asyncio.run(e2._rules_tick(2))
    assert e2.end_message == "Solo queda Carla"


# ── Cursed items ─────────────────────────────────────────────────────

CURSED = {
    "conditions": {
        "poseido": {"description": "solo piensas en el anillo", "per_tick": {"mood": -0.1}}
    },
    "items": {
        **WORLD["items"],
        "anillo": {
            "consumable": False,
            "curse": {
                "condition": "poseido",
                "sticky": True,
                "broken_by": ["agua", "exorcizar"],
                "description": "susurra tu nombre",
            },
        },
        "muñeca": {"consumable": False, "curse": {"condition": "poseido"}},
        "agua": {"description": "agua bendita"},
    },
    "actions": {**WORLD["actions"], "exorcizar": {"description": "exorcizar a alguien"}},
}


def test_carrying_a_cursed_item_gives_its_condition_until_it_is_gone():
    e = make_engine(**CURSED)
    ana = ag(e, "Ana")
    ana.state.resources["muñeca"] = 1
    asyncio.run(e._curses_tick(1))
    assert "poseido" in ana.state.conditions
    assert any("The muñeca is cursed" in str(m) for m in ana.memory.short_term)
    act(e, "Ana", 2, type=ActionType.GIVE, target="Bruno", item="muñeca", amount=1)
    assert "poseido" not in ana.state.conditions and "poseido" in ag(e, "Bruno").state.conditions


def test_a_sticky_curse_wont_let_go_and_only_its_breakers_end_it():
    e = make_engine(**CURSED)
    ana, bruno = ag(e, "Ana"), ag(e, "Bruno")
    ana.state.resources.update({"anillo": 1})
    asyncio.run(e._curses_tick(1))
    for kind in (ActionType.GIVE, ActionType.DROP, ActionType.TRADE):
        done = act(e, "Ana", 2, type=kind, target="Bruno", item="anillo", amount=1)
        assert done.type == ActionType.WAIT and "won't leave my hands" in done.reasoning
    assert (
        "Your anillo is cursed; you can't give, drop or trade it; "
        "it can be broken with: agua, exorcizar."
        in e._curses_prompt(ana)
    )
    bruno.state.resources["agua"] = 1
    act(e, "Bruno", 3, type=ActionType.USE, target="Ana", item="agua")
    assert "anillo" not in ana.state.resources and "poseido" not in ana.state.conditions


def test_a_ritual_action_breaks_the_curse_and_a_thief_takes_the_curse_along():
    e = make_engine(**CURSED)
    ana, carla = ag(e, "Ana"), ag(e, "Carla")
    ana.state.resources["anillo"] = 1
    asyncio.run(e._curses_tick(1))
    act(e, "Bruno", 2, type=ActionType.CUSTOM, target="Ana", parameters={"name": "saquear"})
    assert (
        "anillo" in ag(e, "Bruno").state.resources and "poseido" in ag(e, "Bruno").state.conditions
    )
    assert "poseido" not in ana.state.conditions
    carla.location = "Plaza"
    act(e, "Carla", 3, type=ActionType.CUSTOM, target="Bruno", parameters={"name": "exorcizar"})
    assert (
        "anillo" not in ag(e, "Bruno").state.resources
        and "poseido" not in ag(e, "Bruno").state.conditions
    )


def test_curses_are_validated():
    bad = json.loads(json.dumps(CURSED))
    bad["items"]["anillo"]["curse"] = {"condition": "loco", "broken_by": ["sal"]}
    try:
        SimulationConfig.model_validate({**WORLD, **bad})
        raise AssertionError("should fail")
    except ValueError as err:
        assert "unknown condition 'loco'" in str(err) and "unknown item or action 'sal'" in str(err)


# ── What belonging to a group is worth ───────────────────────────────

GROUPS = {
    "rpg": True,
    "factions": {"Gremio": {"members": ["Ana", "Bruno"]}},
    "agents": [
        {"name": "Ana", "starting_location": "Plaza", "resources": {"pan": 2}},
        {"name": "Bruno", "starting_location": "Plaza", "resources": {"oro": 3}},
        {"name": "Carla", "starting_location": "Plaza"},
        {"name": "Dani", "starting_location": "Plaza"},
    ],
}


def test_members_here_help_each_others_rolls_and_defense():
    e = make_engine(**GROUPS)
    ana, carla = ag(e, "Ana"), ag(e, "Carla")
    assert e._ally_bonus(ana) == 1 and e._ally_bonus(carla) == 0
    assert e._defense(ana) == e._defense(carla) + 1  # same dexterity by default
    res = asyncio.run(e._check(ana, "strength", dc=10, label="x", tick=1))
    assert res["allies"] == 1 and res["modifier"] == e._bonus(ana, "strength", "") + 1
    off = make_engine(**GROUPS, society={"group_perks": False})
    assert off._ally_bonus(ag(off, "Ana")) == 0


def test_the_group_stands_by_a_member_who_is_attacked():
    e = make_engine(**GROUPS)
    act(e, "Carla", 1, type=ActionType.ATTACK, target="Ana")
    rel = ag(e, "Bruno").memory.relationships["Carla"]
    assert rel.sentiment <= -0.4
    assert any(
        "one of your group: stand by them" in str(m) for m in ag(e, "Bruno").memory.short_term
    )


def test_a_common_stash_from_anywhere():
    e = make_engine(**GROUPS)
    act(e, "Ana", 1, type=ActionType.GIVE, target="Gremio", item="pan", amount=2)
    assert "pan" not in ag(e, "Ana").state.resources
    ag(e, "Bruno").location = "Bosque"
    got = act(e, "Bruno", 2, type=ActionType.TAKE, target="gremio", item="Pan", amount=1)
    assert got.type == ActionType.TAKE and ag(e, "Bruno").state.resources["pan"] == 1
    assert e.society_state()["factions"]["Gremio"]["stash"] == {"pan": 1}
    no = act(e, "Carla", 3, type=ActionType.TAKE, target="Gremio", item="pan", amount=1)
    assert no.type == ActionType.WAIT  # not a member: Gremio is just a word for her
    assert "Common stash of Gremio: pan x1" in e._society_prompt(ag(e, "Ana"), 3)


def test_people_are_nudged_out_of_their_group_and_into_others():
    e = make_engine(**GROUPS)
    ana, carla, dani = ag(e, "Ana"), ag(e, "Carla"), ag(e, "Dani")
    for a, b in ((ana, carla), (carla, ana), (ana, dani), (dani, ana)):
        a.memory.update_relationship(b.name, 0.6, 1, "friends")
    text = e._society_prompt(ana, 1)
    assert (
        "Carla and Dani think like you: you could form a group together (you would leave Gremio)"
        in text
    )
    assert (
        'People you like belong to Gremio, and some are here: you could "join" it'
        in e._society_prompt(carla, 1)
    )
    asyncio.run(e._apply_leave(ana, Action(type=ActionType.LEAVE), 1))
    assert e._faction_of("Ana") == "" and e._factions["Gremio"]["members"] == [
        "Bruno"
    ]  # configured: it stays
