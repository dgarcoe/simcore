"""Recipes: actions that need gathered items and a place; agents learn what is missing and where."""

import asyncio
import json

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine

WORLD = {
    "name": "Radio",
    "duration": "24 hours",
    "environment": {
        "global_state": {"radio": "rota"},
        "locations": [
            {"name": "Plaza"},
            {"name": "Cuartel", "stock": {"piezas": 1}},
            {"name": "Torre", "stock": {"piezas": 1}},
        ],
    },
    "items": {
        "piezas": {"description": "válvulas y cables"},
        "tablones": {},
        "barricada": {"consumable": False},
    },
    "actions": {
        "reparar_radio": {
            "description": "reparar la radio con tres juegos de piezas",
            "target": "none",
            "who": {"professions": ["electricist"], "items": {"piezas": 3}},
            "cost": {"piezas": 3},
            "where": ["Torre"],
            "world": {"radio": "reparada"},
            "message": "arregla la radio",
        },
        "hacer_barricada": {
            "target": "none",
            "cost": {"tablones": 2},
            "on_self": {"items": {"barricada": 1}},
        },
    },
    "agents": [
        {"name": "Sara", "profession": "electricista", "starting_location": "Plaza",
         "resources": {"piezas": 1}},
        {"name": "Ortega", "profession": "guardia", "starting_location": "Plaza",
         "resources": {"piezas": 1, "tablones": 2}},
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


def custom(name: str) -> Action:
    return Action(type=ActionType.CUSTOM, parameters={"name": name})


def test_where_must_name_a_known_place():
    data = json.loads(json.dumps(WORLD))
    data["actions"]["reparar_radio"]["where"] = ["Faro"]
    with pytest.raises(ValueError, match="unknown place 'Faro'"):
        SimulationConfig.model_validate(data)


def test_the_prompt_lists_what_a_pending_action_needs_and_where_it_lies():
    e = make_engine()
    sara = ag(e, "Sara")
    text = e._build_observation(sara, 1)
    assert "Things you could do once you have what they need" in text
    assert "- reparar_radio (needs piezas x3, you have 1; at Torre)" in text
    assert "  piezas: in Cuartel x1, in Torre x1, carried by Ortega" in text
    assert "needed for: reparar_radio" in text
    # Ortega isn't an electrician: nothing pending for him, nor the action itself.
    assert "reparar_radio (" not in e._build_observation(ag(e, "Ortega"), 1)


def test_the_action_needs_its_place_and_its_items():
    e = make_engine()
    sara = ag(e, "Sara")
    sara.state.resources["piezas"] = 3
    assert "reparar_radio" not in [n for n, _ in e._available_actions(sara)]
    done = asyncio.run(e._apply_custom_action(sara, custom("reparar_radio"), 1))
    assert done.type == ActionType.WAIT and "Torre" in done.reasoning
    assert e.environment.global_state["radio"] == "rota" and sara.state.resources["piezas"] == 3

    sara.location = "Torre"
    assert "reparar_radio" in [n for n, _ in e._available_actions(sara)]
    assert "reparar_radio (needs" not in e._build_observation(sara, 1)
    done = asyncio.run(e._apply_custom_action(sara, custom("reparar_radio"), 1))
    assert done.parameters["success"] and e.environment.global_state["radio"] == "reparada"
    assert "piezas" not in sara.state.resources


def test_a_recipe_turns_items_into_another():
    e = make_engine()
    ortega = ag(e, "Ortega")
    asyncio.run(e._apply_custom_action(ortega, custom("hacer_barricada"), 1))
    assert ortega.state.resources.get("barricada") == 1 and "tablones" not in ortega.state.resources


def test_the_demo_gathers_the_parts_and_repairs_the_radio():
    repaired = 0
    for seed in range(6):
        e = make_engine(seed=seed)
        asyncio.run(e.run())
        changes = [x.data for x in e.events if x.type == EventType.ENVIRONMENT_CHANGE
                   and x.data.get("global_state", {}).get("radio") == "reparada"]
        repaired += bool(changes)
    assert repaired >= 3
