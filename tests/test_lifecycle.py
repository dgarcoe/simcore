"""Life cycle: health, deaths, departures, arrivals and event effects."""

import asyncio

import pytest

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.effects import parse_effects
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine


def make_config(**extra) -> SimulationConfig:
    data = {
        "name": "Test",
        "duration": "6 hours",
        "time_step": "1 hour",
        "environment": {"locations": [
            {"name": "Plaza", "capacity": 10},
            {"name": "Clinic", "capacity": 1},
        ]},
        "agents": [
            {"name": "Ana", "starting_location": "Plaza"},
            {"name": "Bruno", "starting_location": "Plaza"},
            {"name": "Carla", "starting_location": "Clinic"},
        ],
    }
    data.update(extra)
    return SimulationConfig.model_validate(data)


def collect(engine: SimulationEngine) -> list:
    events = []
    engine.event_bus.on_all(lambda e: events.append(e))
    return events


def run(coro):
    return asyncio.run(coro)


def test_parse_effects_splits_world_state_and_agent_effects():
    fx = parse_effects({"school_closed": True, "kill": "Ana", "health": {"all": -0.2},
                        "spawn": {"name": "Medic", "count": 2}})
    assert fx.global_updates == {"school_closed": True}
    assert fx.kill == ["Ana"]
    assert fx.health == {"all": -0.2}
    assert fx.spawn[0].count == 2


@pytest.mark.parametrize("bad", [{"kill": 3}, {"health": {"Ana": "a lot"}}, {"health": ["Ana"]}, {"spawn": ["x"]}])
def test_parse_effects_rejects_bad_shapes(bad):
    with pytest.raises(ValueError):
        parse_effects(bad)


def test_config_validates_scheduled_effects():
    with pytest.raises(ValueError):
        make_config(events={"scheduled": [{"tick": 1, "type": "x", "description": "d", "effects": {"kill": 5}}]})


def test_kill_removes_agent_from_play_and_witnesses_see_it():
    engine = SimulationEngine(make_config(), demo_mode=True)
    events = collect(engine)
    ana, bruno, carla = engine.agents["ana"], engine.agents["bruno"], engine.agents["carla"]
    run(engine.inject_event("A gas leak", effects={"kill": "Ana"}))
    assert ana.state.status == "dead" and not ana.alive
    assert "ana" not in engine.environment.get_agents_at("Plaza")
    assert ana not in engine.living_agents
    assert any("die" in m.content for m in bruno.memory.short_term)
    assert not any("die" in m.content for m in carla.memory.short_term)
    death = [e for e in events if e.type == EventType.AGENT_DEATH]
    assert death and death[0].data["agent"] == "Ana" and death[0].data["cause"] == "A gas leak"
    # The body is visible to people at the same place, and the dead can't be talked to.
    assert "Dead bodies here: Ana" in engine._build_observation(bruno, 1)
    assert "Ana" not in engine._build_observation(bruno, 1).split("All people who exist")[1].split("\n")[0]


def test_health_damage_kills_at_zero_and_targets_locations():
    engine = SimulationEngine(make_config(), demo_mode=True)
    run(engine.inject_event("Explosion", effects={"health": {"location:Plaza": -0.6}}))
    assert engine.agents["ana"].state.health == pytest.approx(0.4)
    assert engine.agents["carla"].state.health == 1.0
    assert "Visibly hurt or sick" in engine._build_observation(engine.agents["ana"], 1)
    run(engine.inject_event("Second blast", effects={"health": {"Ana": -0.5}}))
    assert engine.agents["ana"].state.status == "dead"


def test_depart_and_spawn():
    engine = SimulationEngine(make_config(), demo_mode=True)
    events = collect(engine)
    run(engine.inject_event("", effects={
        "depart": "Bruno",
        "spawn": [{"name": "Medic", "profession": "paramedic", "starting_location": "Plaza", "count": 2},
                  {"name": "Ana", "starting_location": "Nowhere"}],
    }))
    assert engine.agents["bruno"].state.status == "departed"
    names = {a.name for a in engine.living_agents}
    assert {"Medic 1", "Medic 2", "Ana 2"} <= names
    assert engine.agents["ana_2"].location == "Plaza"  # unknown place → first physical location
    types = [e.type for e in events]
    assert types.count(EventType.AGENT_SPAWN) == 3 and EventType.AGENT_DEPART in types
    assert not any(e.type == EventType.INJECTED_EVENT for e in events)  # no description → no news item


def test_scheduled_effects_apply_and_late_arrivals_enter():
    cfg = make_config(
        agents=[{"name": "Ana", "starting_location": "Plaza"},
                {"name": "Rescuer", "starting_location": "Plaza", "arrives_at": 2}],
        events={"scheduled": [{"tick": 1, "type": "lockdown", "description": "Lockdown",
                               "effects": {"school_closed": True, "health": {"Ana": -0.3}}}]},
    )
    engine = SimulationEngine(cfg, demo_mode=True)
    events = collect(engine)
    assert "rescuer" not in engine.agents
    assert engine.get_state()["pending_arrivals"] == ["Rescuer"]
    run(engine.run())
    assert engine.environment.global_state["school_closed"] is True
    assert engine.agents["ana"].state.health < 0.9  # -0.3, then a little rest
    assert "rescuer" in engine.agents
    spawn = [e for e in events if e.type == EventType.AGENT_SPAWN]
    assert spawn[0].tick == 2
    assert any(e.type == EventType.ENVIRONMENT_CHANGE for e in events)
    assert events[-1].data["reason"] == "completed"


def test_simulation_ends_when_nobody_is_left():
    cfg = make_config(events={"scheduled": [{"tick": 2, "type": "x", "description": "Meteor",
                                             "effects": {"kill": "all"}}]})
    engine = SimulationEngine(cfg, demo_mode=True)
    events = collect(engine)
    run(engine.run())
    end = events[-1]
    assert end.type == EventType.SIMULATION_END and end.data["reason"] == "no_agents_left" and end.tick == 2


def test_failed_move_keeps_agent_where_it_was():
    engine = SimulationEngine(make_config(), demo_mode=True)
    ana = engine.agents["ana"]

    async def decide(*_):
        return Action(type=ActionType.MOVE, target="Clinic")  # full (capacity 1, Carla inside)

    engine.llm.decide = decide
    action = run(engine._agent_turn(ana, 1))
    assert action.type == ActionType.WAIT
    assert ana.location == "Plaza" and "ana" in engine.environment.get_agents_at("Plaza")


def test_null_target_does_not_crash():
    engine = SimulationEngine(make_config(), demo_mode=True)

    async def decide(*_):
        return Action(type=ActionType.SPEAK, target=None, content="hi")

    engine.llm.decide = decide
    action = run(engine._agent_turn(engine.agents["ana"], 1))
    assert action.type == ActionType.WAIT


def test_state_exposes_health_and_status():
    engine = SimulationEngine(make_config(), demo_mode=True)
    run(engine.inject_event("", effects={"kill": "Carla"}))
    agents = engine.get_state()["agents"]
    assert agents["carla"]["status"] == "dead"
    assert agents["ana"]["state"]["health"] == 1.0


def test_stop_interrupts_a_slow_tick():
    import time

    engine = SimulationEngine(make_config(duration="2 days"), demo_mode=True, tick_delay=5)
    events = collect(engine)

    async def slow(*_):
        await asyncio.sleep(30)  # an LLM that takes forever
        return Action(type=ActionType.WAIT)

    engine.llm.decide = slow

    async def scenario():
        runner = asyncio.create_task(engine.run())
        await asyncio.sleep(0.2)
        start = time.monotonic()
        engine.stop()
        await asyncio.wait_for(runner, 2)
        return time.monotonic() - start

    took = run(scenario())
    assert took < 1
    end = events[-1]
    assert end.type == EventType.SIMULATION_END and end.data["reason"] == "stopped"


def test_stop_skips_the_wait_between_ticks():
    import time

    engine = SimulationEngine(make_config(duration="2 days"), demo_mode=True, tick_delay=30)
    events = collect(engine)

    async def scenario():
        runner = asyncio.create_task(engine.run())
        while not any(e.type == EventType.TICK_END for e in events):
            await asyncio.sleep(0.01)
        start = time.monotonic()
        engine.stop()
        await asyncio.wait_for(runner, 2)
        return time.monotonic() - start

    assert run(scenario()) < 1
    assert events[-1].data["reason"] == "stopped"
