"""Items with a fuse, fists, knockout points, and where the exhausted wake up."""

import asyncio
import json

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import ItemConfig, SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine

BASE = {
    "name": "Mecha",
    "duration": "10 hours",
    "items": {
        "dinamita": {"description": "un cartucho", "consumable": False,
                     "fuse": {"ticks": 2, "on_holder": {"health": -0.5},
                              "on_others": {"health": -0.2},
                              "message": "¡BOOM! La dinamita estalla en {place}"}},
    },
    "environment": {"locations": [{"name": "Plaza", "stock": {"dinamita": 1}},
                                  {"name": "Bar"}, {"name": "Hospital"}]},
    "agents": [{"name": "Ana", "starting_location": "Plaza"},
               {"name": "Bea", "starting_location": "Plaza"},
               {"name": "Carla", "starting_location": "Bar"}],
}


def make(**changes):
    data = json.loads(json.dumps(BASE))
    data.update(changes)
    e = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    e.events = []
    e.event_bus.on_all(lambda x: e.events.append(x))
    return e


def ag(e, name):
    return e._find_agent_by_name(name)


def booms(e):
    return [x.data for x in e.events if x.type == EventType.FUSE]


def test_a_fuse_is_lit_when_picked_up_and_goes_off_ticks_later():
    e = make()
    asyncio.run(e._fuses_tick(1))  # on the ground: unlit
    assert e.fuses_state() == {} and booms(e) == []
    ana, bea = ag(e, "Ana"), ag(e, "Bea")
    e.environment.get_location("Plaza").stock.pop("dinamita")
    ana.state.resources["dinamita"] = 1
    asyncio.run(e._fuses_tick(2))  # Ana picks it up: lit at 2
    assert e.fuses_state() == {"dinamita": {"agent:Ana": [2]}}
    assert "DANGER: a lit dinamita you carry; it goes off in 2 ticks" in e._fuses_prompt(ana, 2)
    assert "lies here" not in e._fuses_prompt(bea, 2)
    asyncio.run(e._fuses_tick(3))
    assert booms(e) == []
    asyncio.run(e._fuses_tick(4))
    (b,) = booms(e)
    assert b["agent"] == "Ana" and b["location"] == "Plaza" and set(b["hurt"]) == {"Ana", "Bea"}
    assert b["message"] == "¡BOOM! La dinamita estalla en Plaza"
    assert ana.state.health == pytest.approx(0.5) and bea.state.health == pytest.approx(0.8)
    assert "dinamita" not in ana.state.resources and ag(e, "Carla").state.health == 1.0


def test_the_lit_fuse_follows_the_item_from_hand_to_hand_and_to_the_ground():
    e = make()
    ana, bea = ag(e, "Ana"), ag(e, "Bea")
    e.environment.get_location("Plaza").stock.pop("dinamita")
    ana.state.resources["dinamita"] = 1
    asyncio.run(e._fuses_tick(1))  # lit at 1
    ana.state.resources.pop("dinamita")
    bea.state.resources["dinamita"] = 1  # a hot potato: Ana gives it to Bea
    asyncio.run(e._fuses_tick(2))
    assert e.fuses_state() == {"dinamita": {"agent:Bea": [1]}}
    bea.state.resources.pop("dinamita")
    e.environment.get_location("Plaza").stock["dinamita"] = 1  # Bea drops it
    bea.state.location = "Bar"
    asyncio.run(e._fuses_tick(3))  # goes off on the ground: hurts whoever is still there
    (b,) = booms(e)
    assert b["agent"] == "" and b["location"] == "Plaza" and b["hurt"] == ["Ana"]
    assert "dinamita" not in e.environment.get_location("Plaza").stock


def test_fists_hurt_as_the_scenario_says_and_knockouts_score():
    e = make(society={"lethal": False, "knockout": {"place": "Hospital", "health": 0.5},
                      "fists": {"damage": 0.6, "chance": 1.0}},
             scores={"achievements": {"tumbar": {"points": 3, "repeat": True,
                                                 "when": {"knocks_out": True}}}})
    ana, bea = ag(e, "Ana"), ag(e, "Bea")
    bea.state.health = 0.5

    async def decide(*_):
        return Action(type=ActionType.ATTACK, target="Bea")

    e.llm.decide = decide
    done = asyncio.run(e._agent_turn(ana, 1))
    assert done.parameters["damage"] == pytest.approx(0.6) and done.parameters["knocked_out"]
    assert bea.location == "Hospital" and e._score_of("Ana") == 3


def test_the_exhausted_wake_up_where_the_scenario_says():
    e = make(exhaustion_place="Hospital")
    ana = ag(e, "Ana")
    ana.state.energy = 0.0
    done = asyncio.run(e._agent_turn(ana, 1))
    assert ana.location == "Hospital" and ana.state.energy >= 0.5
    assert done.describe() == "collapses from exhaustion and wakes up in Hospital"
    with pytest.raises(ValueError, match="exhaustion_place is an unknown place"):
        make(exhaustion_place="Luna")


def test_items_help_the_dice():
    e = make(rpg=True, items={
        "ganzua": {"description": "ganzúa", "consumable": False,
                   "bonus": {"sleight_of_hand": 2, "robar": 1, "defense": 9}},
        "amuleto": {"description": "amuleto", "consumable": False, "bonus": {"attack": 3}},
    })
    ana = ag(e, "Ana")
    assert e._item_bonus(ana, "dexterity", "sleight_of_hand", "robar") == (0, [])
    ana.state.resources.update({"ganzua": 1, "amuleto": 1})
    assert e._item_bonus(ana, "dexterity", "sleight_of_hand", "robar") == (2, ["ganzua"])
    assert e._item_bonus(ana, "strength", "athletics", "attack") == (3, ["amuleto"])
    assert e._item_bonus(ana, "defense")[0] == 5  # capped at +5
    before = e._defense(ag(e, "Bea"))
    assert e._defense(ana) >= before + 5 - 2  # dexterity may differ by a little
    rolls = []
    e.event_bus.on(EventType.DICE_ROLL, lambda x: rolls.append(x.data))
    asyncio.run(e._check(ana, "dexterity", "sleight_of_hand", dc=10, label="robar", tick=1))
    assert rolls[-1]["item_bonus"] == 2 and rolls[-1]["items"] == ["ganzua"]
    assert "helps your rolls: sleight of hand +2, robar +1, defense +5" in e._item_guide({"ganzua"})


def test_a_knocked_out_agent_stays_in_hospital_recovering_and_a_healer_shortens_it():
    e = make(society={"lethal": False,
                      "knockout": {"place": "Hospital", "health": 0.4, "recovery": 2},
                      "fists": {"damage": 1.0, "chance": 1.0}})
    ana, bea = ag(e, "Ana"), ag(e, "Bea")
    asked = []

    async def decide(*_):
        asked.append(1)
        return Action(type=ActionType.ATTACK, target="Bea")

    e.llm.decide = decide
    done = asyncio.run(e._agent_turn(ana, 1))
    assert done.parameters["knocked_out"] and bea.location == "Hospital"
    ko = [x.data for x in e.events if x.type == EventType.KNOCKOUT][0]
    assert ko["by"] == "Ana" and ko["how"] == "attack" and ko["recovery"] == 2
    asked.clear()
    e.clock.current_tick = 2
    turn = asyncio.run(e._agent_turn(bea, 2))
    assert turn.type == ActionType.REST and turn.parameters["left"] == 2 and asked == []
    assert bea.location == "Hospital" and bea.state.health > 0.4
    assert e._agent_state(bea)["recovering"] == {"left": 2, "why": "knockout"}
    carla = ag(e, "Carla")
    carla.state.location = "Hospital"
    assert "Bea (knocked out, 2 turns left)" in e._recovery_prompt(carla, 2)
    # a nurse tends to her: one turn less
    carla.state.resources["botiquin"] = 1
    e.config.items["botiquin"] = ItemConfig(on_use={"health": 0.2})
    healed = asyncio.run(e._apply_heal(carla, Action(type=ActionType.HEAL, target="Bea"), 2))
    assert healed.parameters.get("shortened")
    e.clock.current_tick = 3
    assert asyncio.run(e._agent_turn(bea, 3)).parameters.get("recovering") is None
    assert asked  # back on her feet, deciding again


def test_the_exhausted_recover_for_some_turns_and_casualties_name_who_and_how():
    e = make(exhaustion_place="Hospital", exhaustion_recovery=1,
             society={"lethal": False, "fists": {"damage": 1.0, "chance": 1.0}})
    ana = ag(e, "Ana")
    ana.state.energy = 0.0
    first = asyncio.run(e._agent_turn(ana, 1))
    assert first.parameters["exhausted"] and first.parameters["recovery"] == 1
    second = asyncio.run(e._agent_turn(ana, 2))
    assert second.parameters["recovering"] and second.parameters["why"] == "exhaustion"
    # rivals: who downed whom
    bea = ag(e, "Bea")

    async def decide(*_):
        return Action(type=ActionType.ATTACK, target="Ana")

    e.llm.decide = decide
    ana.state.location = bea.location = "Plaza"
    e._recovering.clear()
    asyncio.run(e._agent_turn(bea, 5))
    assert e._awards.rivals() == {"Ana": {"nemesis": {"agent": "Bea", "times": 1}},
                                  "Bea": {"victim": {"agent": "Ana", "times": 1}}}


def test_forced_rests_say_why_in_the_event():
    e = make(exhaustion_place="Hospital", exhaustion_recovery=1)
    ana = ag(e, "Ana")
    ana.state.energy = 0.0
    asyncio.run(e._agent_turn(ana, 1))
    asyncio.run(e._agent_turn(ana, 2))
    rests = [x.data for x in e.events if x.type == EventType.AGENT_ACTION and x.source == "Ana"]
    assert rests[0]["exhausted"] and rests[0]["to"] == "Hospital" and rests[0]["recovery"] == 1
    assert rests[1]["recovering"] and rests[1]["left"] == 1 and rests[1]["why"] == "exhaustion"
