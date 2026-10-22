"""FastAPI server for simulation dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from simcore.engine.events import SimEvent
from simcore.server import auth
from simcore.server.games import GameError, GameStore, diary, dm_view
from simcore.server.scenarios import _dict_to_config
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

    app.add_middleware(GZipMiddleware, minimum_size=4096)  # recorded runs are big JSON

    @app.middleware("http")
    async def dm_only(request: Request, call_next):
        """With SIMCORE_DM_PASSWORD set, everything but logging in and the player API needs a DM."""
        if (auth.required() and not auth.is_open_path(request.url.path)
                and not auth.valid_session(request.cookies.get(auth.COOKIE))):
            return JSONResponse({"detail": "Solo para dungeon masters: inicia sesión."},
                                status_code=401)
        return await call_next(request)
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
        "runs": None,  # RunStore: every run started here is recorded
        "recorder": None,  # the current run's RunRecorder
        "games": None,  # GameStore: games with players
    }

    def _runs():
        if server_state["runs"] is None:
            from simcore.server.runs import RunStore
            server_state["runs"] = RunStore()
        return server_state["runs"]

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

    @app.get("/api/version")
    async def get_version():
        from simcore.features import version

        return version()

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

    async def _start_run(config, scenario: dict | None, scenario_id: str, demo: bool, speed: float,
                         game_id: str = "") -> None:
        """Stop the current run (it stays recorded as interrupted) and start this one."""
        old_engine = server_state.get("engine")
        if old_engine:
            old_engine.stop()
            await asyncio.sleep(0.1)  # let the old engine exit its loop
        old_recorder = server_state.get("recorder")
        if old_recorder and not old_recorder.closed:
            old_recorder.close("interrupted")

        new_engine = SimulationEngine(config, demo_mode=demo, tick_delay=speed)
        server_state["engine"] = new_engine
        try:
            server_state["recorder"] = _runs().start(
                new_engine, scenario, scenario_id=scenario_id, demo=demo, game_id=game_id)
        except Exception as e:  # never block a run because it can't be recorded
            logger.warning(f"Can't record this run: {e}")
            server_state["recorder"] = None

        srv = server_state.get("server")
        if srv:
            srv.swap_engine(new_engine)
        else:
            srv = SimulationServer(new_engine, server_state["websockets"])
            server_state["server"] = srv

        asyncio.create_task(new_engine.run())
        return server_state["recorder"]

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

        record = store.get(scenario_id) or {}
        await _start_run(config, record.get("data"), scenario_id, demo, speed)
        return {"status": "started", "scenario_id": scenario_id, "name": config.name}

    # ------------------------------------------------------------------
    # Dungeon masters and players
    # ------------------------------------------------------------------

    login_limit = auth.RateLimit()
    join_limit = auth.RateLimit(limit=30, window=60.0)

    def _games():
        if server_state.get("games") is None:
            server_state["games"] = GameStore()
        return server_state["games"]

    def _client(request: Request) -> str:
        # Behind the UI's nginx every request comes from it: it passes the real address along.
        real = request.headers.get("X-Real-IP", "").strip()
        return real or (request.client.host if request.client else "?")

    @app.get("/api/auth/me")
    async def auth_me(request: Request):
        return {"required": auth.required(),
                "dm": not auth.required() or auth.valid_session(request.cookies.get(auth.COOKIE))}

    @app.post("/api/auth/login")
    async def auth_login(request: Request, payload: dict = Body(default={})):
        if not auth.required():
            return {"dm": True}
        if not login_limit.allow(_client(request)):
            raise HTTPException(429, "Demasiados intentos: espera un minuto.")
        if not auth.check_password(str(payload.get("password", ""))):
            raise HTTPException(401, "Contraseña incorrecta.")
        response = JSONResponse({"dm": True})
        https = request.headers.get("X-Forwarded-Proto", request.url.scheme) == "https"
        response.set_cookie(auth.COOKIE, auth.make_session(), max_age=auth.SESSION_SECONDS,
                            httponly=True, samesite="strict", secure=https)
        return response

    @app.post("/api/auth/logout")
    async def auth_logout():
        response = JSONResponse({"dm": False})
        response.delete_cookie(auth.COOKIE)
        return response

    def _game_or_404(game_id: str) -> dict:
        game = _games().get(game_id)
        if game is None:
            raise HTTPException(404, "Partida desconocida.")
        return game

    @app.get("/api/games")
    async def list_games():
        games = _games().list()
        for g in games:
            _settle(g)
        return {"games": [dm_view(g) for g in games]}

    @app.post("/api/games")
    async def create_game(payload: dict = Body(default={})):
        if not _get_store().get(str(payload.get("scenario_id", ""))):
            raise HTTPException(422, "Ese escenario no existe.")
        try:
            return dm_view(_games().create(payload))
        except GameError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/games/{game_id}")
    async def get_game(game_id: str):
        return dm_view(_game_or_404(game_id))

    @app.put("/api/games/{game_id}")
    async def update_game(game_id: str, payload: dict = Body(default={})):
        _game_or_404(game_id)
        try:
            return dm_view(_games().update(game_id, payload))
        except (GameError, ValueError) as e:
            raise HTTPException(422, str(e))

    @app.delete("/api/games/{game_id}")
    async def delete_game(game_id: str):
        return {"deleted": _games().delete(game_id)}

    @app.post("/api/games/{game_id}/players/{player_id}")
    async def edit_player(game_id: str, player_id: str, payload: dict = Body(default={})):
        _game_or_404(game_id)
        try:
            return dm_view(_games().edit_player(game_id, player_id, payload))
        except (GameError, ValueError) as e:
            raise HTTPException(422, str(e))

    @app.post("/api/games/{game_id}/launch")
    async def launch_game(game_id: str, payload: dict = Body(default={})):
        game = _game_or_404(game_id)
        record = _get_store().get(game["scenario_id"])
        if not record:
            raise HTTPException(422, "El escenario de esta partida ya no existe.")
        data = _games().build_scenario(game, record["data"])
        try:
            config = _dict_to_config(data)
        except Exception as e:
            raise HTTPException(422, f"El escenario con los jugadores no es válido: {e}")
        recorder = await _start_run(config, data, game["scenario_id"], bool(payload.get("demo")),
                                    float(payload.get("speed", 1.0)), game_id=game_id)
        game["status"] = "running"
        game["run_id"] = recorder.id if recorder else None
        _games().save(game)
        return {"status": "started", "run_id": game["run_id"], "name": config.name}

    def _player(code: str, request: Request) -> tuple[dict, dict]:
        try:
            return _games().player_for(code, request.headers.get("X-Player-Token", ""))
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except GameError as e:
            raise HTTPException(404, str(e))

    def _settle(game: dict) -> bool:
        """True once the game's run is over (the game is then marked finished)."""
        if not game.get("run_id"):
            return False
        current = server_state.get("recorder")
        if current and current.id == game["run_id"] and not current.closed:
            return False
        if game["status"] == "running":
            game["status"] = "finished"
            _games().save(game)
        return True

    def _finished(game: dict) -> dict | None:
        """The run of a game, once it has finished."""
        return _runs().load(game["run_id"]) if _settle(game) else None

    @app.get("/api/play/{code}")
    async def play_view(code: str):
        game = _games().by_code(code)
        if game is None:
            raise HTTPException(404, "No hay ninguna partida con ese código.")
        _settle(game)
        return _games().public_view(game)

    @app.post("/api/play/{code}/join")
    async def play_join(code: str, request: Request, payload: dict = Body(default={})):
        if not join_limit.allow(_client(request)):
            raise HTTPException(429, "Demasiados intentos: espera un minuto.")
        try:
            _, player, token = _games().join(code, str(payload.get("display_name", "")))
        except GameError as e:
            raise HTTPException(422, str(e))
        return {"player_id": player["id"], "token": token}

    @app.get("/api/play/{code}/me")
    async def play_me(code: str, request: Request):
        game, player = _player(code, request)
        return {"display_name": player["display_name"], "agent": player["agent"],
                "submitted": player["submitted"], "status": game["status"]}

    @app.put("/api/play/{code}/me")
    async def play_save(code: str, request: Request, payload: dict = Body(default={})):
        _player(code, request)
        try:
            player = _games().save_agent(code, request.headers.get("X-Player-Token", ""),
                                         payload.get("agent") or {}, payload.get("submitted"))
        except GameError as e:
            raise HTTPException(422, str(e))
        return {"agent": player["agent"], "submitted": player["submitted"]}

    @app.get("/api/play/{code}/results")
    async def play_results(code: str, request: Request):
        game, player = _player(code, request)
        run = _finished(game)
        pub = game["publish"]
        if run is None or not any(pub.values()):
            return {"status": game["status"], "published": pub}
        name = (player.get("agent") or {}).get("name") or player["display_name"]
        out: dict = {"status": game["status"], "published": pub, "agent": name}
        meta = run.get("meta") or {}
        if pub["result"]:
            out["result"] = {
                "message": meta.get("end_message", ""),
                "reason": meta.get("end_reason", ""),
                "ticks": meta.get("ticks", 0),
                "alive": name in (meta.get("alive") or []),
            }
        if pub["diary"]:
            out["diary"] = {"scenario": run["scenario"], "events": diary(run["events"], name)}
        if pub["replay"]:
            out["replay"] = {k: run[k] for k in ("scenario", "config", "events")}
        return out

    # ------------------------------------------------------------------
    # Recorded runs
    # ------------------------------------------------------------------

    @app.get("/api/runs")
    async def list_runs():
        return {"runs": _runs().list()}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        try:
            run = _runs().load(run_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if run is None:
            raise HTTPException(404, f"Run '{run_id}' not found")
        return run

    @app.delete("/api/runs/{run_id}")
    async def delete_run(run_id: str):
        current = server_state.get("recorder")
        if current and current.id == run_id and not current.closed:
            raise HTTPException(409, "This run is still being recorded")
        try:
            return {"deleted": _runs().delete(run_id)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        if auth.required() and not auth.valid_session(websocket.cookies.get(auth.COOKIE)):
            await websocket.close(code=4401)
            return
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
