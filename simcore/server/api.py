"""FastAPI server for simulation dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from simcore.engine.events import SimEvent
from simcore.engine.simulation import SimulationEngine

logger = logging.getLogger("simcore.server")


class SimulationServer:
    """Wraps the simulation engine and broadcasts events to WebSocket clients."""

    def __init__(self, engine: SimulationEngine, websockets: list | None = None):
        self.engine = engine
        self._websockets: list[WebSocket] = websockets if websockets is not None else []
        engine.event_bus.on_all(self._broadcast_event)

    def swap_engine(self, new_engine: SimulationEngine) -> None:
        """Hot-swap to a new engine, reusing current WebSocket connections."""
        self.engine = new_engine
        new_engine.event_bus.on_all(self._broadcast_event)

    async def _broadcast_event(self, event: SimEvent) -> None:
        if not self._websockets:
            return
        data = json.dumps(event.to_dict())
        disconnected = []
        for ws in self._websockets:
            try:
                await ws.send_text(data)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            if ws in self._websockets:
                self._websockets.remove(ws)

    def add_websocket(self, ws: WebSocket) -> None:
        if ws not in self._websockets:
            self._websockets.append(ws)

    def remove_websocket(self, ws: WebSocket) -> None:
        if ws in self._websockets:
            self._websockets.remove(ws)


def create_app(engine: SimulationEngine | None = None,
               scenario_store=None) -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(title="SimCore Dashboard", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mutable state shared across all route handlers via closure
    server_state: dict = {
        "engine": engine,
        "server": None,
        "scenario_store": scenario_store,
        "websockets": [],  # all active WS connections, regardless of sim state
    }

    @app.on_event("startup")
    async def startup():
        if engine:
            srv = SimulationServer(engine, server_state["websockets"])
            server_state["server"] = srv

    # ------------------------------------------------------------------
    # Simulation state endpoints
    # ------------------------------------------------------------------

    @app.get("/api/state")
    async def get_state():
        if not server_state["engine"]:
            return {"error": "No simulation loaded"}
        return server_state["engine"].get_state()

    @app.get("/api/agents")
    async def get_agents():
        if not server_state["engine"]:
            return {"error": "No simulation loaded"}
        return {aid: a.to_dict() for aid, a in server_state["engine"].agents.items()}

    @app.get("/api/agents/{agent_id}")
    async def get_agent(agent_id: str):
        if not server_state["engine"]:
            return {"error": "No simulation loaded"}
        agent = server_state["engine"].agents.get(agent_id)
        if not agent:
            return {"error": f"Agent '{agent_id}' not found"}
        return agent.to_dict()

    @app.get("/api/environment")
    async def get_environment():
        if not server_state["engine"]:
            return {"error": "No simulation loaded"}
        return server_state["engine"].environment.to_dict()

    @app.get("/api/clock")
    async def get_clock():
        if not server_state["engine"]:
            return {"error": "No simulation loaded"}
        return server_state["engine"].clock.to_dict()

    @app.get("/api/events")
    async def get_events(last_n: int = 50):
        if not server_state["engine"]:
            return {"error": "No simulation loaded"}
        return server_state["engine"].event_bus.get_log(last_n)

    @app.post("/api/inject")
    async def inject_event(payload: dict):
        if not server_state["engine"]:
            return {"error": "No simulation loaded"}
        description = payload.get("description", "")
        location = payload.get("location", "")
        effects = payload.get("effects") or None
        if not description and not effects:
            raise HTTPException(422, "description or effects required")
        try:
            await server_state["engine"].inject_event(description, location, effects)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"status": "ok", "description": description}

    @app.post("/api/language")
    async def set_language(payload: dict):
        if not server_state["engine"]:
            return {"status": "ok", "language": payload.get("language", "en")}
        lang = payload.get("language", "en")
        server_state["engine"].config.language = lang
        return {"status": "ok", "language": lang}

    @app.get("/api/story")
    async def get_story():
        engine = server_state["engine"]
        if not engine:
            return {"chapters": [], "moments": []}
        return engine.story()

    @app.post("/api/postmortem/narrative")
    async def postmortem_narrative():
        engine = server_state["engine"]
        if not engine:
            return {"error": "No simulation loaded"}

        lang = getattr(engine.config, "language", "en")
        state = engine.get_state()
        ticks = state.get("clock", {}).get("tick", 0)

        agents_data = []
        for agent in engine.agents.values():
            rels = sorted(
                agent.memory.relationships.values(),
                key=lambda r: r.interactions,
                reverse=True,
            )[:3]
            reflections = (agent.memory._reflection_summaries or [])[-2:]
            agents_data.append({
                "name": agent.name,
                "mood": round(agent.state.mood, 2),
                "energy": round(agent.state.energy, 2),
                "reflections": reflections,
                "relationships": [r.describe() for r in rels],
            })

        story_moments = engine.story_summary(15)
        all_events = engine.event_bus.get_log(500)
        key_events = [
            {"tick": m["tick"], "type": m["kind"], "source": ", ".join(m["agents"][:2]), "summary": m["text"][:160]}
            for m in story_moments
        ] or [
            {
                "tick": e.get("tick", 0),
                "type": e.get("type", ""),
                "source": e.get("source", ""),
                "summary": (
                    e.get("data", {}).get("description")
                    or e.get("data", {}).get("summary")
                    or e.get("data", {}).get("result")
                    or e.get("data", {}).get("action")
                    or ""
                )[:120],
            }
            for e in all_events
            if e.get("type") in ("interaction", "scheduled_event", "injected_event", "reflection")
        ][-15:]

        data = {"ticks": ticks, "agents": agents_data, "key_events": key_events,
                "chapters": engine.story()["chapters"], "ending": engine.end_message}
        try:
            narrative = await engine.llm.generate_narrative(data, lang)
            return {"narrative": narrative}
        except Exception as e:
            logger.warning(f"Narrative generation failed: {e}")
            return {"error": str(e)}

    @app.post("/api/control/{action}")
    async def control(action: str):
        if not server_state["engine"]:
            return {"error": "No simulation loaded"}
        match action:
            case "pause":
                server_state["engine"].pause()
            case "resume":
                server_state["engine"].resume()
            case "stop":
                server_state["engine"].stop()
            case _:
                return {"error": f"Unknown action: {action}"}
        return {"status": "ok", "action": action}

    # ------------------------------------------------------------------
    # Scenario endpoints
    # ------------------------------------------------------------------

    def _get_store():
        """Return the scenario store, creating one lazily if needed."""
        store = server_state.get("scenario_store")
        if not store:
            from simcore.server.scenarios import ScenarioStore
            store = ScenarioStore()
            server_state["scenario_store"] = store
        return store

    @app.get("/api/scenarios")
    async def list_scenarios():
        return {"scenarios": _get_store().list_all()}

    @app.post("/api/scenarios")
    async def create_scenario(payload: dict):
        try:
            record = _get_store().create(payload)
            return record
        except Exception as e:
            return {"error": str(e)}

    @app.get("/api/scenarios/{scenario_id}")
    async def get_scenario(scenario_id: str):
        scenario = _get_store().get(scenario_id)
        if not scenario:
            return {"error": f"Scenario '{scenario_id}' not found"}
        return scenario

    @app.put("/api/scenarios/{scenario_id}")
    async def update_scenario(scenario_id: str, payload: dict):
        updated = _get_store().update(scenario_id, payload)
        if not updated:
            return {"error": "Scenario not found or is builtin"}
        return updated

    @app.delete("/api/scenarios/{scenario_id}")
    async def delete_scenario(scenario_id: str):
        # For a built-in scenario this drops its edits and restores the original.
        deleted = _get_store().delete(scenario_id)
        return {"deleted": deleted, **({"restored": deleted} if scenario_id.startswith("builtin:") else {})}

    @app.post("/api/scenarios/{scenario_id}/run")
    async def run_scenario(scenario_id: str, payload: dict = Body(default={})):
        store = _get_store()

        demo = payload.get("demo", False)
        speed = float(payload.get("speed", 1.0))

        try:
            config = store.load_engine_config(scenario_id)
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"Failed to load scenario config: {e}")
            return {"error": f"Config error: {e}"}

        # Stop old engine if running
        old_engine = server_state.get("engine")
        if old_engine:
            old_engine.stop()
            await asyncio.sleep(0.1)  # let the old engine exit its loop

        new_engine = SimulationEngine(config, demo_mode=demo, tick_delay=speed)
        server_state["engine"] = new_engine

        srv = server_state.get("server")
        if srv:
            srv.swap_engine(new_engine)
        else:
            srv = SimulationServer(new_engine, server_state["websockets"])
            server_state["server"] = srv

        asyncio.create_task(new_engine.run())

        return {"status": "started", "scenario_id": scenario_id, "name": config.name}

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        server_state["websockets"].append(websocket)
        srv = server_state.get("server")
        if srv:
            srv.add_websocket(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    cmd = json.loads(data)
                    if cmd.get("type") == "inject" and server_state["engine"]:
                        await server_state["engine"].inject_event(
                            cmd.get("description", ""),
                            cmd.get("location", ""),
                            cmd.get("effects") or None,
                        )
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"Ignored WebSocket command: {e}")
        except WebSocketDisconnect:
            if websocket in server_state["websockets"]:
                server_state["websockets"].remove(websocket)
            if srv:
                srv.remove_websocket(websocket)

    # Serve dashboard static files if they exist
    dashboard_dist = Path(__file__).parent.parent.parent / "dashboard" / "dist"
    if dashboard_dist.exists():
        app.mount("/", StaticFiles(directory=str(dashboard_dist), html=True))

    return app
