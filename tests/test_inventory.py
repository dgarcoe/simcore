"""Items: give, take, drop, use, trade offers, production and item effects."""

import asyncio
import json

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.effects import parse_effects
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine
from simcore.llm.provider import LLMProvider


def make_engine(**extra) -> SimulationEngine:
    data = {
        "name": "Items",
        "duration": "6 hours",
        "items": {
            "medicine": {"description": "First-aid kit", "on_use": {"health": 0.4}},
            "food": {"on_use": {"energy": 0.3}},
            "radio": {"consumable": False, "on_use": {"mood": 0.1}},
        },
        "environment": {"locations": [
            {"name": "Clinic", "stock": {"medicine": 2}, "produces": {"medicine": 1}},
            {"name": "Farm", "produces": {"food": 2}},
        ]},
        "agents": [
            {"name": "Ana", "starting_location": "Clinic", "resources": {"medicine": 1, "money": 10}},
            {"name": "Bruno", "starting_location": "Clinic", "resources": {"food": 3}, "health": 0.3},
            {"name": "Carla", "starting_location": "Farm"},
        ],
    }
    data.update(extra)
    engine = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    engine.events = []
    engine.event_bus.on_all(lambda e: engine.events.append(e))
    return engine


def act(engine, name, **kw):
    async def decide(*_):
        return Action(**kw)

    engine.llm.decide = decide
    return asyncio.run(engine._agent_turn(engine.agents[name], 1))


def inv(engine, name):
    return engine.agents[name].state.resources


def transfers(engine):
    return [e.data for e in engine.events if e.type == EventType.ITEM_TRANSFER]


def test_give_moves_items_and_reports_it():
    e = make_engine()
    done = act(e, "ana", type=ActionType.GIVE, target="Bruno", item="money", amount=4)
    assert done.type == ActionType.GIVE
    assert inv(e, "ana")["money"] == 6 and inv(e, "bruno")["money"] == 4
    assert transfers(e)[-1] == {"kind": "give", "item": "money", "amount": 4, "from": "Ana", "to": "Bruno",
                                "location": "Clinic"}
    action_event = [x for x in e.events if x.type == EventType.AGENT_ACTION][-1]
    assert action_event.data["action_type"] == "give" and action_event.data["item"] == "money"


@pytest.mark.parametrize("kw, why", [
    ({"target": "Bruno", "item": "money", "amount": 50}, "don't have"),
    ({"target": "Carla", "item": "money", "amount": 1}, "not here"),
    ({"target": "Bruno", "item": "", "amount": 1}, "don't have"),
])
def test_give_refused(kw, why):
    e = make_engine()
    done = act(e, "ana", type=ActionType.GIVE, **kw)
    assert done.type == ActionType.WAIT and why in done.reasoning
    assert inv(e, "ana") == {"medicine": 1, "money": 10}


def test_take_and_drop_use_the_location_stock():
    e = make_engine()
    act(e, "ana", type=ActionType.TAKE, item="medicine", amount=2)
    assert inv(e, "ana")["medicine"] == 3 and "medicine" not in e.environment.locations["Clinic"].stock
    assert act(e, "ana", type=ActionType.TAKE, item="medicine").type == ActionType.WAIT
    act(e, "ana", type=ActionType.DROP, item="money", amount=10)
    assert "money" not in inv(e, "ana") and e.environment.locations["Clinic"].stock["money"] == 10
    assert e.get_state()["environment"]["locations"]["Clinic"]["stock"] == {"money": 10}


def test_use_on_someone_else_heals_them_and_consumes_the_item():
    e = make_engine()
    done = act(e, "ana", type=ActionType.USE, item="medicine", target="Bruno")
    assert done.type == ActionType.USE
    assert e.agents["bruno"].state.health == pytest.approx(0.7)
    assert "medicine" not in inv(e, "ana")
    assert act(e, "ana", type=ActionType.USE, item="medicine").type == ActionType.WAIT


def test_reusable_items_stay_and_items_without_use_are_refused():
    e = make_engine()
    e.agents["ana"].state.resources["radio"] = 1
    act(e, "ana", type=ActionType.USE, item="radio")
    assert inv(e, "ana")["radio"] == 1 and e.agents["ana"].state.mood == pytest.approx(0.1)
    assert "has no use" in act(e, "ana", type=ActionType.USE, item="money").reasoning


def test_trade_offer_accepted():
    e = make_engine()
    act(e, "ana", type=ActionType.TRADE, target="Bruno", item="money", amount=5, ask_item="food", ask_amount=2)
    assert "Ana offers you 5 money for 2 food" in e._build_observation(e.agents["bruno"], 2)
    offer = [x for x in e.events if x.type == EventType.TRADE_OFFER][0]
    assert offer.data["to"] == "Bruno" and offer.data["expires"] == 3
    done = act(e, "bruno", type=ActionType.ACCEPT, target="Ana")
    assert done.type == ActionType.ACCEPT
    assert inv(e, "ana") == {"medicine": 1, "money": 5, "food": 2}
    assert inv(e, "bruno") == {"food": 1, "money": 5}
    result = [x for x in e.events if x.type == EventType.TRADE_RESULT][-1]
    assert result.data["accepted"] is True
    assert not e._offers


def test_trade_declined_expired_or_unpaid():
    e = make_engine()
    act(e, "ana", type=ActionType.TRADE, target="Bruno", item="money", amount=1, ask_item="food", ask_amount=9)
    assert act(e, "bruno", type=ActionType.ACCEPT, target="Ana").type == ActionType.WAIT  # can't pay 9 food
    assert inv(e, "ana")["money"] == 10
    act(e, "ana", type=ActionType.TRADE, target="Bruno", item="money", amount=1)
    act(e, "bruno", type=ActionType.DECLINE, target="Ana")
    act(e, "ana", type=ActionType.TRADE, target="Bruno", item="money", amount=1)
    asyncio.run(e._expire_offers(5))
    reasons = [x.data["reason"] for x in e.events if x.type == EventType.TRADE_RESULT]
    assert reasons == ["Bruno can't pay", "declined", "expired"]
    assert act(e, "bruno", type=ActionType.ACCEPT, target="Ana").type == ActionType.WAIT


def test_offers_vanish_when_someone_dies():
    e = make_engine()
    act(e, "ana", type=ActionType.TRADE, target="Bruno", item="money", amount=1)
    asyncio.run(e.kill(e.agents["ana"], 1))
    assert not e._offers


def test_work_produces_items():
    e = make_engine()
    act(e, "carla", type=ActionType.WORK, content="harvest")
    assert inv(e, "carla") == {"food": 2}
    assert transfers(e)[-1]["kind"] == "produce"


def test_items_effect_on_agents_and_locations():
    fx = parse_effects({"items": {"location:Clinic": {"medicine": 5}, "Bruno": {"food": -10}, "all": {"water": 1}}})
    e = make_engine()
    asyncio.run(e.apply_effects(fx, 1, "Supplies"))
    assert e.environment.locations["Clinic"].stock["medicine"] == 7
    assert "food" not in inv(e, "bruno")  # never below zero
    assert all(inv(e, n)["water"] == 1 for n in ("ana", "bruno", "carla"))
    with pytest.raises(ValueError):
        parse_effects({"items": {"Ana": {"food": "lots"}}})


def test_schema_validation():
    with pytest.raises(ValueError):
        make_engine(items={"x": {"on_use": {"strength": 1}}})
    with pytest.raises(ValueError):
        make_engine(environment={"locations": [{"name": "A", "stock": {"x": -1}}]})


def test_prompt_shows_inventory_stock_and_item_guide():
    e = make_engine()
    obs = e._build_observation(e.agents["ana"], 1)
    assert "Items you can take here: medicine x2." in obs
    assert "Working here produces: medicine x1." in obs
    assert "medicine (First-aid kit) — use: health +0.4" in obs
    prompt = e.agents["ana"].build_prompt(obs)
    assert "Your inventory: medicine x1, money x10." in prompt
    assert "give|take|drop|use|trade|accept|decline" in prompt and '"ask_item"' in prompt


def test_parse_item_fields():
    p = LLMProvider()
    a = p._parse_action(json.dumps({"action": "give", "target": "Bruno", "item": "food", "amount": "2"}))
    assert (a.type, a.item, a.amount) == (ActionType.GIVE, "food", 2)
    b = p._parse_action(json.dumps({"action": "trade", "target": "B", "parameters": {"item": "x", "ask_item": "y"}}))
    assert (b.item, b.amount, b.ask_item) == ("x", 1, "y")
    assert p._parse_action("[1, 2]").type == ActionType.WAIT


def test_demo_mode_exchanges_items():
    e = make_engine(duration="24 hours")
    asyncio.run(e.run())
    kinds = {t["kind"] for t in transfers(e)}
    assert {"give", "take", "use"} & kinds


def test_loaders_read_the_item_catalog(tmp_path):
    import yaml

    from simcore.config.loader import load_config
    from simcore.server.scenarios import _dict_to_config

    raw = {"simulation": {"name": "Y"}, "items": {"food": {"on_use": {"energy": 0.2}}},
           "environment": {"locations": [{"name": "A", "stock": {"food": 1}}]}, "agents": [{"name": "Z"}]}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    for cfg in (load_config(path), _dict_to_config(raw)):
        assert cfg.items["food"].on_use == {"energy": 0.2}
        assert cfg.environment.locations[0].stock == {"food": 1}
