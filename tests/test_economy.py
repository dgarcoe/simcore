"""Markets with moving prices, opening hours, price events, wealth and money in the awards."""

import asyncio
import json

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine

BASE = {
    "name": "Mercado",
    "duration": "24 hours",
    "items": {"dinero": {}, "pan": {"on_use": {"energy": 0.2}}, "chaleco": {"consumable": False},
              "mercancia": {}},
    "environment": {"locations": [
        {"name": "Mercado", "market": {"sells": {"pan": 2, "chaleco": 5}, "buys": {"mercancia": 3},
                                       "stock": {"chaleco": 2}, "elasticity": 0.2,
                                       "recovery": 0.5}},
        {"name": "Puerto", "market": {"buys": {"mercancia": 5}, "hours": [20, 6]}},
        {"name": "Plaza"},
    ]},
    "agents": [{"name": "Ana", "starting_location": "Mercado",
                "resources": {"dinero": 20, "mercancia": 4}}],
}


def make(**changes):
    data = json.loads(json.dumps(BASE))
    data.update(changes)
    e = SimulationEngine(SimulationConfig.model_validate(data), demo_mode=True)
    e.events = []
    e.event_bus.on_all(lambda x: e.events.append(x))
    return e


def act(e, kind, item, amount=1, who="Ana", tick=1):
    async def decide(*_):
        return Action(type=kind, item=item, amount=amount)

    e.llm.decide = decide
    return asyncio.run(e._agent_turn(e._find_agent_by_name(who), tick))


def test_buying_raises_the_price_and_selling_lowers_it():
    e = make()
    ana = e._find_agent_by_name("Ana")
    assert "Market here (prices in dinero; you have 20): sells pan 2; chaleco 5 (2 left)" \
        in e._economy_prompt(ana)
    done = act(e, ActionType.BUY, "pan", 3)
    assert done.type == ActionType.BUY and done.amount == 3
    assert done.parameters["price"] == 2 + 2 + 3  # 2, 2.4→2, 2.88→3
    assert ana.state.resources["pan"] == 3 and ana.state.resources["dinero"] == 13
    assert e._price("Mercado", "pan", "buy") == 3
    done = act(e, ActionType.SELL, "mercancia", 2)
    assert done.parameters["price"] == 3 + 2 and ana.state.resources["mercancia"] == 2
    m = [x.data for x in e.events if x.type == EventType.MARKET]
    assert m[-1] == {"agent": "Ana", "place": "Mercado", "kind": "sell", "item": "mercancia",
                     "amount": 2, "price": 5, "currency": "dinero"}
    for t in (2, 3, 4):
        asyncio.run(e._economy_tick(t))
    assert e._price("Mercado", "pan", "buy") == 2  # drifting back to base


def test_limited_stock_money_and_wrong_items_are_refused():
    e = make()
    ana = e._find_agent_by_name("Ana")
    assert act(e, ActionType.BUY, "chaleco", 3).amount == 2  # only 2 in stock
    assert e.markets_state()["Mercado"]["sells"]["chaleco"]["stock"] == 0
    refused = act(e, ActionType.BUY, "chaleco")
    assert refused.type == ActionType.WAIT and "sold out" in refused.reasoning
    assert act(e, ActionType.BUY, "espada").type == ActionType.WAIT
    ana.state.resources["dinero"] = 0
    assert act(e, ActionType.BUY, "pan").type == ActionType.WAIT


def test_opening_hours_and_events_on_prices_and_markets():
    e = make()
    ana = e._find_agent_by_name("Ana")
    ana.state.location = "Puerto"
    e.clock.current_tick = 10  # 10:00
    assert not e._is_open("Puerto")
    assert act(e, ActionType.SELL, "mercancia").type == ActionType.WAIT
    e.clock.current_tick = 22
    assert e._is_open("Puerto") and act(e, ActionType.SELL, "mercancia").parameters["price"] == 5
    from simcore.engine.effects import parse_effects
    asyncio.run(e.apply_effects(parse_effects(
        {"prices": {"Mercado": {"pan": 3}}, "markets": {"Puerto": {"open": False}}}), 23))
    assert e._price("Mercado", "pan", "buy") == 6 and not e._is_open("Puerto")
    with pytest.raises(ValueError):
        parse_effects({"markets": {"Puerto": {"abierto": True}}})


def test_wealth_scores_and_awards_and_own_territory():
    e = make(
        scores={"achievements": {"rico": {"points": 4, "when": {"wealth": 25}}}},
        economy={"currency": "dinero"},
    )
    ana = e._find_agent_by_name("Ana")
    asyncio.run(e._scores_tick(1))
    assert e._score_of("Ana") == 0
    act(e, ActionType.SELL, "mercancia", 4)
    asyncio.run(e._scores_tick(2))
    assert e._score_of("Ana") == 4
    awards, _ = e.awards()
    keys = {a["key"]: a for a in awards}
    assert keys["magnate"]["agent"] == "Ana" and keys["tycoon"]["value"] >= 8
    assert ana.state.resources["dinero"] > 25
    with pytest.raises(ValueError, match="market at Mercado uses unknown item 'oro'"):
        make(economy={"currency": "oro"})
