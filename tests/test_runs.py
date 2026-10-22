"""Every run started from the server is recorded and can be listed, loaded and deleted."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType, SimEvent
from simcore.engine.simulation import SimulationEngine
from simcore.server.api import create_app
from simcore.server.runs import RunStore
from simcore.server.scenarios import ScenarioStore

SCENARIO = {
    "simulation": {"name": "Plaza pequeña", "description": "Dos vecinos", "duration": "3 hours"},
    "environment": {"locations": [{"name": "Plaza"}]},
    "agents": [
        {"name": "Ana", "starting_location": "Plaza"},
        {"name": "Bob", "starting_location": "Plaza"},
    ],
}


def engine():
    cfg = {
        **SCENARIO["simulation"],
        "environment": SCENARIO["environment"],
        "agents": SCENARIO["agents"],
    }
    e = SimulationEngine(SimulationConfig.model_validate(cfg), demo_mode=True)
    e.tick_delay = 0
    return e


def test_a_finished_run_is_recorded_with_its_scenario(tmp_path):
    runs = RunStore(tmp_path)
    e = engine()
    rec = runs.start(e, SCENARIO, scenario_id="user:x", demo=True)
    asyncio.run(e.run())
    assert rec.closed
    [meta] = runs.list()
    assert (
        meta["status"] == "finished" and meta["name"] == "Plaza pequeña" and meta["model"] == "demo"
    )
    assert (
        meta["ticks"] == 3 and meta["end_reason"] == "completed" and meta["alive"] == ["Ana", "Bob"]
    )
    run = runs.load(meta["id"])
    assert run["scenario"] == {"name": "Plaza pequeña", "description": "Dos vecinos"}
    assert run["config"] == SCENARIO and run["meta"]["id"] == meta["id"]
    types = [ev["type"] for ev in run["events"]]
    assert types[0] == "simulation_start" and types[-1] == "simulation_end" and "tick_end" in types
    assert len(run["events"]) == meta["events"]


def test_an_interrupted_run_keeps_what_happened(tmp_path):
    runs = RunStore(tmp_path)
    e = engine()
    rec = runs.start(e, SCENARIO)
    asyncio.run(e.event_bus.emit(SimEvent(type=EventType.TICK_START, tick=1)))
    rec.close("interrupted")
    [meta] = runs.list()
    assert meta["status"] == "interrupted" and meta["ended"]
    assert [ev["type"] for ev in runs.load(meta["id"])["events"]] == ["tick_start"]


def test_old_runs_are_pruned_and_ids_are_checked(tmp_path):
    runs = RunStore(tmp_path, keep=2)
    for _ in range(3):
        e = engine()
        runs.start(e, SCENARIO)
        asyncio.run(e.run())
    assert len(runs.list()) == 2
    with pytest.raises(ValueError):
        runs.load("../etc/passwd")
    first = runs.list()[0]["id"]
    assert runs.delete(first) and not runs.delete(first)


def test_the_api_records_runs_started_from_the_server(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMCORE_RUNS_DIR", str(tmp_path / "runs"))
    store = ScenarioStore(tmp_path / "user")
    sid = store.create(SCENARIO)["id"]
    with TestClient(create_app(scenario_store=store)) as client:
        assert "runs" in client.get("/api/version").json()["features"]
        assert (
            client.post(f"/api/scenarios/{sid}/run", json={"demo": True, "speed": 0}).json()[
                "status"
            ]
            == "started"
        )
        for _ in range(100):
            runs = client.get("/api/runs").json()["runs"]
            if runs and runs[0]["status"] == "finished":
                break
            asyncio.run(asyncio.sleep(0.05))
        run_id = runs[0]["id"]
        assert runs[0]["status"] == "finished"
        run = client.get(f"/api/runs/{run_id}", headers={"Accept-Encoding": "gzip"}).json()
        assert run["config"]["simulation"]["name"] == "Plaza pequeña" and run["events"]
        assert client.get("/api/runs/nope").status_code == 404
        assert client.delete(f"/api/runs/{run_id}").json() == {"deleted": True}
    assert not list((tmp_path / "runs").glob("*.jsonl.gz"))
