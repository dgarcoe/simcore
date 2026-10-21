"""Coveted items: visible carriers, items that fall when their carrier dies, and rules that
reward whoever keeps one long enough ({holder})."""

import asyncio
import json

import pytest

from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine

WORLD = {
    "name": "Sello",
    "duration": "48 hours",
    "environment": {"locations": [{"name": "Salón"}, {"name": "Capilla"}]},
    "items": {"sello": {"consumable": False, "visible": True, "drops": True}, "oro": {}},
    "rules": [
        {
            "description": "{holder} lleva tres horas con el sello",
            "when": {"holds": {"item": "sello", "ticks": 3}},
            "then": {"end": "{holder} es el nuevo señor", "rumor": {"all": "Manda {holder}"}},
            "once": True,
        },
    ],
    "agents": [
        {"name": "Elvira", "starting_location": "Salón", "resources": {"sello": 1, "oro": 2}},
        {"name": "Rodrigo", "starting_location": "Salón"},
        {"name": "Tobías", "starting_location": "Capilla"},
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


def test_holds_counts_ticks_in_a_row_and_restarts_when_it_changes_hands():
    e = make_engine()
    asyncio.run(e._refresh_holders(0))
    assert e.holders_state(2) == {"sello": {"agent": "Elvira", "since": 0, "ticks": 2}}
    rule = e.config.rules[0]
    assert e._hold_match(rule, 2) is None
    assert e._hold_match(rule, 3) == "Elvira"
    ag(e, "Elvira").state.resources.pop("sello")
    ag(e, "Rodrigo").state.resources["sello"] = 1
    asyncio.run(e._refresh_holders(3))
    assert e._hold_match(rule, 5) is None and e._hold_match(rule, 6) == "Rodrigo"
    change = [
        x.data for x in e.events if x.type == EventType.ITEM_TRANSFER and x.data["kind"] == "holder"
    ]
    assert change[-1] == {
        "kind": "holder",
        "item": "sello",
        "amount": 1,
        "from": "Elvira",
        "to": "Rodrigo",
        "location": "",
    }


def test_the_rule_names_the_holder_in_its_texts_and_targets():
    e = make_engine()
    asyncio.run(e._refresh_holders(0))
    asyncio.run(e._rules_tick(3))
    fired = [x.data for x in e.events if x.type == EventType.RULE_FIRED][0]
    assert (
        fired["description"] == "Elvira lleva tres horas con el sello"
        and fired["holder"] == "Elvira"
    )
    assert e.end_message == "Elvira es el nuevo señor"
    assert any("Manda Elvira" in str(b) for b in ag(e, "Rodrigo").state.beliefs)


def test_items_that_drop_fall_where_the_carrier_dies_and_can_be_taken():
    e = make_engine()
    asyncio.run(e._refresh_holders(0))
    asyncio.run(e.kill(ag(e, "Elvira"), 2, "apuñalada"))
    salon = e.environment.get_location("Salón")
    assert salon.stock["sello"] == 1 and "oro" not in salon.stock  # only items that drop
    assert "sello" not in ag(e, "Elvira").state.resources
    dropped = [
        x.data
        for x in e.events
        if x.type == EventType.ITEM_TRANSFER and x.data["kind"] == "dropped"
    ]
    assert dropped == [
        {
            "kind": "dropped",
            "item": "sello",
            "amount": 1.0,
            "from": "Elvira",
            "to": "location:Salón",
            "location": "Salón",
        }
    ]
    assert e.holders_state(2) == {}
    assert "fell from Elvira" in ag(e, "Rodrigo").memory.get_context()


def test_count_item_and_prompts_show_the_carrier_and_the_race():
    e = make_engine(
        rules=[{"when": {"count": {"item": "sello", "gte": 1}}, "then": {"hay_sello": True}}]
    )
    assert e._rule_holds(e.config.rules[0], 1)
    e = make_engine()
    asyncio.run(e._refresh_holders(0))
    rodrigo = e._build_observation(ag(e, "Rodrigo"), 2, "en")
    assert "- Elvira:" in rodrigo and "carries the sello" in rodrigo
    assert (
        "Whoever keeps the sello for 3 hours in a row wins; Elvira (at Salón) has kept it for 2."
        in rodrigo
    )
    elvira = e._build_observation(ag(e, "Elvira"), 2, "en")
    assert "You carry the sello" in elvira and "you have kept it for 2 of 3" in elvira


def test_validation_and_nothing_changes_without_the_new_fields():
    with pytest.raises(ValueError, match="holds unknown item 'corona'"):
        make_engine(rules=[{"when": {"holds": {"item": "corona"}}, "then": {"end": "x"}}])
    with pytest.raises(ValueError, match="counts unknown item 'corona'"):
        make_engine(rules=[{"when": {"count": {"item": "corona", "gte": 1}}, "then": {"x": 1}}])
    e = make_engine(items={"sello": {"consumable": False}, "oro": {}}, rules=[])
    asyncio.run(e.kill(ag(e, "Elvira"), 1))
    assert ag(e, "Elvira").state.resources["sello"] == 1
    assert "carries" not in e._build_observation(ag(e, "Rodrigo"), 1, "en")


def test_demo_run_fights_over_the_seal_until_someone_keeps_it():
    e = make_engine(duration="30 hours")
    e.config.rules[0].when.holds.ticks = 6
    asyncio.run(e.run())
    kinds = [x.type for x in e.events]
    assert EventType.RULE_FIRED in kinds or e.end_reason == "completed"


def test_everyone_learns_where_a_dropped_item_lies():
    e = make_engine()
    asyncio.run(e._refresh_holders(0))
    asyncio.run(e.kill(ag(e, "Elvira"), 2))
    assert "nobody carries it now, it lies in Salón." in e._build_observation(
        ag(e, "Tobías"), 3, "en"
    )


def test_unknown_rule_tests_are_rejected_instead_of_firing_at_once():
    with pytest.raises(ValueError, match="holdz"):
        make_engine(rules=[{"when": {"holdz": {"item": "sello"}}, "then": {"end": "x"}}])


def test_version_lists_the_features():
    from simcore.features import version

    assert {"artifacts", "contact_contagion", "rpg"} <= set(version()["features"])
