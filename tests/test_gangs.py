"""Gang wars: territories that groups take, contest and live off, knockouts instead of deaths,
a winning group and a winning person."""

import asyncio
import json

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine

CITY = {
    "name": "Los Santos",
    "duration": "24 hours",
    "conditions": {"noqueado": {"description": "te han dejado K.O.", "duration": 2}},
    "items": {"dinero": {}, "bate": {"consumable": False, "weapon": {"damage": 1.0, "chance": 1.0}}},
    "society": {"lethal": False, "knockout": {"condition": "noqueado", "place": "Hospital", "health": 0.4}},
    "territories": {"every": 2, "group_points": 5},
    "environment": {"locations": [
        {"name": "Puerto", "territory": True, "income": {"dinero": 3}},
        {"name": "Club", "territory": True},
        {"name": "Hospital"},
    ]},
    "factions": {"Motoristas": {"members": ["Ana", "Bea"]}, "Puerto": {"members": ["Carlos"]}},
    "scores": {"achievements": {
        "conquista": {"description": "Tomar un barrio", "points": 5, "repeat": True, "when": {"captures": True}},
        "en_casa": {"description": "Estar en tu barrio", "points": 1, "repeat": True,
                    "when": {"in_territory": True, "every": 2}},
    }},
    "agents": [
        {"name": "Ana", "starting_location": "Puerto", "resources": {"bate": 1}},
        {"name": "Bea", "starting_location": "Club"},
        {"name": "Carlos", "starting_location": "Club"},
        {"name": "Dani", "starting_location": "Puerto"},
    ],
}


def make_engine(**changes) -> SimulationEngine:
    data = json.loads(json.dumps(CITY))
    data.update(changes)
    e = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    e.tick_delay = 0
    e.events = []
    e.event_bus.on_all(lambda x: e.events.append(x))
    return e


def ag(e, name):
    return e._find_agent_by_name(name)


def changes(e):
    return [x.data for x in e.events if x.type == EventType.TERRITORY_CHANGE]


def test_a_place_belongs_to_the_only_group_there_and_is_contested_by_two():
    e = make_engine()
    asyncio.run(e._territories_tick(1))
    # Dani has no group: Puerto is the Motoristas'; Club has two groups: contested, nobody's.
    assert e.territories_state() == {
        "Puerto": {"owner": "Motoristas"},
        "Club": {"owner": "", "contested": ["Motoristas", "Puerto"]},
    }
    ag(e, "Bea").location = "Hospital"
    asyncio.run(e._territories_tick(2))
    assert e._owner_of("Club") == "Puerto"
    assert changes(e)[-1] == {"place": "Club", "owner": "Puerto", "previous": ""}
    # nobody there any more: it keeps its owner
    ag(e, "Carlos").location = "Hospital"
    asyncio.run(e._territories_tick(3))
    assert e._owner_of("Club") == "Puerto"


def test_territories_pay_into_the_group_stash_and_count_for_points():
    e = make_engine()
    asyncio.run(e._territories_tick(1))
    asyncio.run(e._territories_tick(2))  # every 2 ticks
    assert e._factions["Motoristas"]["stash"] == {"dinero": 3}
    assert e._score_of("Ana") == 5  # she was there when Puerto was taken
    asyncio.run(e._scores_tick(2))
    assert e._score_of("Ana") == 6  # and she stands in her group's territory
    assert e._group_ranking()[0] == ("Motoristas", 6 + 5)  # members' points + 5 per territory
    text = e._territories_prompt(ag(e, "Ana"))
    assert "Territories: Puerto: Motoristas; Club: contested (Motoristas vs Puerto)." in text
    assert "Your group Motoristas holds 1 of 2." in text and "Puerto dinero x3" in text
    assert "Groups: Motoristas (yours) 11 (1 places)" in e._scores_prompt(ag(e, "Ana"))


def test_holding_territories_long_enough_ends_the_run_with_the_group_and_the_mvp():
    rules = [{"when": {"territories": {"gte": 2, "ticks": 2}},
              "then": {"end": "{winner} se quedan con la ciudad. La leyenda es {mvp}."}}]
    e = make_engine(rules=rules)
    e._running = True
    ag(e, "Carlos").location = "Hospital"
    for t in (1, 2):
        asyncio.run(e._territories_tick(t))
        asyncio.run(e._rules_tick(t))
    assert e.end_message == "Motoristas se quedan con la ciudad. La leyenda es Ana & Bea."
    assert e.end_winner == "Motoristas" and e.end_mvp == "Ana & Bea"


def test_the_best_group_wins_a_timed_ending():
    rules = [{"when": {"at": 3}, "winner": "group", "then": {"end": "Gana {winner}; leyenda: {mvp}"}}]
    e = make_engine(rules=rules)
    e._running = True
    asyncio.run(e._award(ag(e, "Carlos"), "conquista", e._achievements["conquista"], 1))
    asyncio.run(e._rules_tick(3))
    assert e.end_message == "Gana Puerto; leyenda: Carlos"


def test_nobody_dies_they_are_knocked_out_and_wake_up_in_hospital():
    e = make_engine()

    async def decide(*_):
        return Action(type=ActionType.ATTACK, target="Dani")

    e.llm.decide = decide
    done = asyncio.run(e._agent_turn(ag(e, "Ana"), 1))
    dani = ag(e, "Dani")
    assert dani.alive and dani.location == "Hospital" and dani.state.health == 0.4
    assert "noqueado" in dani.state.conditions
    assert done.parameters.get("knocked_out") and not done.parameters.get("killed")
    ko = [x.data for x in e.events if x.type == EventType.KNOCKOUT]
    assert ko[0]["agent"] == "Dani" and ko[0]["location"] == "Puerto" and ko[0]["to"] == "Hospital"
    # a knocked-out member doesn't hold a territory
    ag(e, "Bea").location = "Hospital"
    asyncio.run(e._territories_tick(1))
    assert e._holders_here("Hospital") == {"Motoristas": [ag(e, "Bea")]}


def test_validation():
    bad = json.loads(json.dumps(CITY))
    bad["environment"]["locations"][0]["income"] = {"oro": 1}
    bad["society"]["knockout"]["place"] = "Cielo"
    bad["rules"] = [{"when": {"territories": {"gte": 1}}, "then": {"end": "x"}}]
    for loc in bad["environment"]["locations"]:
        loc.pop("territory", None)
    try:
        SimulationConfig.model_validate(bad)
        raise AssertionError("should fail")
    except ValueError as err:
        msg = str(err)
    assert "pays unknown item 'oro'" in msg and "unknown place 'Cielo'" in msg
    assert "no place is a territory" in msg
