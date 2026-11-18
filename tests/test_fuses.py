"""Items with a fuse, fists, knockout points, and where the exhausted wake up."""

import asyncio
import json

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
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
