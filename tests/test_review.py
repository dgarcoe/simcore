"""Reviewing runs: decisions recorded as they were asked, players' private replays, and a
chronicle written for a recorded run."""

import asyncio

from fastapi.testclient import TestClient

from simcore.config.schema import SimulationConfig
from simcore.engine.events import EventType
from simcore.engine.simulation import SimulationEngine
from simcore.server.api import create_app
from simcore.server.games import private_replay
from simcore.server.runs import RunStore
from simcore.server.scenarios import ScenarioStore

SMALL = {
    "name": "Prueba",
    "duration": "4 hours",
    "environment": {"locations": [{"name": "Plaza"}]},
    "agents": [{"name": "Ana", "starting_location": "Plaza"},
               {"name": "Bea", "starting_location": "Plaza"}],
}


def run_engine(**llm):
    cfg = SimulationConfig.model_validate({**SMALL, **({"llm": llm} if llm else {})})
    e = SimulationEngine(cfg, demo_mode=True)
    e.tick_delay = 0
    seen = []
    e.event_bus.on_all(lambda x: seen.append(x))
    asyncio.run(e.run())
    return [x for x in seen if x.type == EventType.DECISION_TRACE]


def test_decisions_are_recorded_only_when_asked():
    assert run_engine() == []
    traces = run_engine(record_prompts=True)
    assert traces and {t.data["agent"] for t in traces} == {"Ana", "Bea"}
    t = traces[0].data
    assert "Ana" in t["system"] or "Bea" in t["system"]
    assert t["prompt"] and t["answer"]


def test_a_player_sees_only_their_own_orders_in_the_replay():
    run = {
        "scenario": {"name": "x"},
        "config": {"agents": [{"name": "Rata", "orders": {"style": "sigiloso"}},
                              {"name": "Toro", "orders": {"style": "agresivo"}}]},
        "events": [
            {"type": "orders", "data": {"agent": "Toro", "text": "secreto"}, "source": "Toro"},
            {"type": "orders", "data": {"agent": "Rata", "text": "mío"}, "source": "Rata"},
            {"type": "decision_trace", "data": {"agent": "Rata"}},
            {"type": "agent_action", "data": {}, "source": "Toro"},
        ],
    }
    out = private_replay(run, "Rata")
    agents = {a["name"]: a for a in out["config"]["agents"]}
    assert agents["Rata"]["orders"] == {"style": "sigiloso"} and "orders" not in agents["Toro"]
    assert [e["type"] for e in out["events"]] == ["orders", "agent_action"]
    assert out["events"][0]["data"]["text"] == "mío" and out["player"] == "Rata"
    assert "orders" in run["config"]["agents"][1]  # the recording itself is untouched


def test_a_chronicle_for_a_recorded_run_is_written_once(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMCORE_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("SIMCORE_DM_PASSWORD", raising=False)
    store = RunStore(tmp_path / "runs")
    engine = SimulationEngine(SimulationConfig.model_validate(SMALL), demo_mode=True)
    rec = store.start(engine, SMALL, demo=True)
    rec.close("finished")
    app = create_app(scenario_store=ScenarioStore(tmp_path / "u"))
    with TestClient(app) as client:
        body = {"run_id": rec.id, "ticks": 4, "moments": [{"tick": 2, "text": "Ana roba a Bea"}],
                "ending": "Fin."}
        first = client.post("/api/narrative", json=body).json()
        assert first["narrative"] and not first.get("cached")
        again = client.post("/api/narrative", json=body).json()
        assert again == {"narrative": first["narrative"], "cached": True}
        # without a run: written from what the page sends
        assert client.post("/api/narrative", json={"demo": True, "moments": []}).json()["narrative"]
