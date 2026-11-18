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
    "items": {
        "dinero": {},
        "bate": {"consumable": False, "weapon": {"damage": 1.0, "chance": 1.0}},
    },
    "society": {
        "lethal": False,
        "knockout": {"condition": "noqueado", "place": "Hospital", "health": 0.4},
    },
    "territories": {"every": 2, "group_points": 5},
    "environment": {
        "locations": [
            {"name": "Puerto", "territory": True, "income": {"dinero": 3}},
            {"name": "Club", "territory": True},
            {"name": "Hospital"},
        ]
    },
    "factions": {"Motoristas": {"members": ["Ana", "Bea"]}, "Puerto": {"members": ["Carlos"]}},
    "scores": {
        "achievements": {
            "conquista": {
                "description": "Tomar un barrio",
                "points": 5,
                "repeat": True,
                "when": {"captures": True},
            },
            "en_casa": {
                "description": "Estar en tu barrio",
                "points": 1,
                "repeat": True,
                "when": {"in_territory": True, "every": 2},
            },
        }
    },
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
    rules = [
        {
            "when": {"territories": {"gte": 2, "ticks": 2}},
            "then": {"end": "{winner} se quedan con la ciudad. La leyenda es {mvp}."},
        }
    ]
    e = make_engine(rules=rules)
    e._running = True
    ag(e, "Carlos").location = "Hospital"
    for t in (1, 2):
        asyncio.run(e._territories_tick(t))
        asyncio.run(e._rules_tick(t))
    assert e.end_message == "Motoristas se quedan con la ciudad. La leyenda es Ana & Bea."
    assert e.end_winner == "Motoristas" and e.end_mvp == "Ana & Bea"


def test_the_best_group_wins_a_timed_ending():
    rules = [
        {"when": {"at": 3}, "winner": "group", "then": {"end": "Gana {winner}; leyenda: {mvp}"}}
    ]
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


# ── Orders from each player ──────────────────────────────────────────

ORDERS = {
    "style": "sigiloso",
    "priorities": ["ser la leyenda", "que gane mi banda"],
    "risk": "careful",
    "red_lines": [
        {"action": "attack", "target": "my_group"},
        {"action": "leave"},
        {"action": "attack", "target": "Dani"},
    ],
    "plans": [
        {"when": {"day": 2}, "then": "Ve al Club y quédate"},
        {"when": {"knocked_out": True}, "then": "Vuelve a por quien te tumbó"},
    ],
    "text": "Nada de peleas a lo loco.",
}


def with_orders(**changes):
    data = json.loads(json.dumps(CITY))
    data["agents"][0]["orders"] = ORDERS
    data.update(changes)
    return data


def test_the_agent_reads_its_orders_first_and_plans_when_they_apply():
    e = SimulationEngine(SimulationConfig.model_validate(with_orders()), demo_mode=True)
    e.events = []
    e.event_bus.on_all(lambda x: e.events.append(x))
    ana = ag(e, "Ana")
    text = asyncio.run(e._orders_prompt(ana, 1))
    assert text.startswith("Your orders from your player (follow them above everything else).")
    assert "- Style: «sigiloso» — act unseen" in text
    assert "- What matters most, in order: 1. «ser la leyenda»; 2. «que gane mi banda»." in text
    assert (
        "- Never: attack anyone in your own group." in text and "- Never: leave your group." in text
    )
    assert "- Plan for later: «Ve al Club y quédate»" in text
    assert "- In your player's words: «Nada de peleas a lo loco.»" in text
    later = asyncio.run(e._orders_prompt(ana, 25))  # day 2 with 1-hour ticks
    assert "- ACTIVE NOW (your plan applies): «Ve al Club y quédate»" in later
    asyncio.run(e._orders_prompt(ana, 26))  # still active: reported once
    plans = [x.data for x in e.events if x.type == EventType.ORDERS and x.data["kind"] == "plan"]
    assert plans == [{"agent": "Ana", "kind": "plan", "text": "Ve al Club y quédate"}]


def test_red_lines_are_enforced():
    e = SimulationEngine(SimulationConfig.model_validate(with_orders()), demo_mode=True)
    e.events = []
    e.event_bus.on_all(lambda x: e.events.append(x))
    ag(e, "Bea").location = "Puerto"

    def turn(**kw):
        async def decide(*_):
            return Action(**kw)

        e.llm.decide = decide
        return asyncio.run(e._agent_turn(ag(e, "Ana"), 1))

    for kw in (
        {"type": ActionType.ATTACK, "target": "Bea"},  # her own group
        {"type": ActionType.ATTACK, "target": "dani"},
        {"type": ActionType.LEAVE},
    ):
        done = turn(**kw)
        assert done.type == ActionType.WAIT and "red line my player set" in done.reasoning
    assert ag(e, "Bea").state.health == 1.0 and e._faction_of("Ana") == "Motoristas"
    blocked = [
        x.data["text"]
        for x in e.events
        if x.type == EventType.ORDERS and x.data["kind"] == "red_line"
    ]
    assert blocked == ["attack anyone in your own group", "attack Dani", "leave your group"]


def test_new_orders_during_the_run():
    e = SimulationEngine(SimulationConfig.model_validate(with_orders()), demo_mode=True)
    e.set_orders("Ana", {"style": "agresivo", "text": "A por todas"})
    text = asyncio.run(e._orders_prompt(ag(e, "Ana"), 1))
    assert "- Style: «agresivo» — fight for what you want" in text and "Never" not in text


def test_the_demo_follows_each_style():
    """Over a whole run, an aggressive agent fights more and a sneaky one steals more."""

    def run(style):
        data = json.loads(json.dumps(CITY))
        data["society"] = {"lethal": False}
        data["factions"]["Motoristas"]["rivals"] = ["Puerto"]
        data["actions"] = {
            "dar_un_palo": {"on_target": {"steal": "one"}, "description": "robar algo"}
        }
        data["agents"] = [{**a, "orders": {"style": style}} for a in data["agents"]]
        totals = {"attack": 0, "steal": 0}
        for seed in range(3):
            e = SimulationEngine(
                SimulationConfig.model_validate({**data, "seed": seed}), demo_mode=True
            )
            e.tick_delay = 0
            ev = []
            e.event_bus.on_all(lambda x: ev.append(x))
            asyncio.run(e.run())
            totals["attack"] += sum(
                1
                for x in ev
                if x.type == EventType.AGENT_ACTION and x.data.get("action_type") == "attack"
            )
            totals["steal"] += sum(
                1
                for x in ev
                if x.type == EventType.AGENT_ACTION and x.data.get("name") == "dar_un_palo"
            )
        return totals

    aggressive, sneaky = run("agresivo"), run("sigiloso")
    assert aggressive["attack"] > sneaky["attack"]
    assert sneaky["steal"] > aggressive["steal"]


def test_a_guarded_place_falls_only_to_a_siege_and_an_unguarded_one_at_once():
    e = make_engine(factions={"Motoristas": {"members": ["Ana", "Bea"]},
                              "Puerto": {"members": ["Carlos", "Dani"]}})
    ag(e, "Dani").location = "Hospital"
    asyncio.run(e._territories_tick(1))
    assert e._owner_of("Puerto") == "Motoristas"
    # two against two: contested, the owner keeps it and gets half its income
    for name in ("Bea", "Carlos", "Dani"):
        ag(e, name).location = "Puerto"
    asyncio.run(e._territories_tick(2))
    state = e.territories_state()["Puerto"]
    assert state == {"owner": "Motoristas", "contested": ["Motoristas", "Puerto"]}
    assert e._factions["Motoristas"]["stash"] == {"dinero": 1.5}
    # outnumbered: a siege that takes `siege` ticks in a row
    ag(e, "Bea").location = "Hospital"
    asyncio.run(e._territories_tick(3))
    assert e.territories_state()["Puerto"]["siege"] == {"by": "Puerto", "progress": 1, "needed": 2}
    assert e._owner_of("Puerto") == "Motoristas"
    seen = [m.content for m in ag(e, "Ana").memory.short_term]
    assert any("outnumber your group at Puerto" in m for m in seen)
    assert "besieged by Puerto (1/2)" in e._territories_prompt(ag(e, "Ana"))
    asyncio.run(e._territories_tick(4))
    assert e._owner_of("Puerto") == "Puerto"
    assert changes(e)[-1] == {"place": "Puerto", "owner": "Puerto", "previous": "Motoristas"}
    assert "contested" not in e.territories_state()["Puerto"]
    # a held place nobody guards falls at once to whoever comes
    ag(e, "Ana").location = "Club"
    asyncio.run(e._territories_tick(5))
    assert e._owner_of("Club") == "Motoristas"
    ag(e, "Ana").location = "Hospital"
    ag(e, "Carlos").location = "Club"
    asyncio.run(e._territories_tick(6))
    assert e._owner_of("Club") == "Puerto"


def test_a_siege_restarts_when_the_owner_matches_the_attackers():
    e = make_engine(factions={"Motoristas": {"members": ["Ana", "Bea"]},
                              "Puerto": {"members": ["Carlos", "Dani"]}},
                    territories={"every": 2, "siege": 3})
    ag(e, "Dani").location = "Hospital"
    asyncio.run(e._territories_tick(1))
    ag(e, "Carlos").location = ag(e, "Dani").location = "Puerto"
    asyncio.run(e._territories_tick(2))
    asyncio.run(e._territories_tick(3))
    assert e._siege["Puerto"] == ("Puerto", 2)
    ag(e, "Bea").location = "Puerto"  # reinforcements: a tie keeps the owner
    asyncio.run(e._territories_tick(4))
    assert "Puerto" not in e._siege and e._owner_of("Puerto") == "Motoristas"
    ag(e, "Bea").location = "Hospital"
    asyncio.run(e._territories_tick(5))
    assert e._siege["Puerto"] == ("Puerto", 1)
