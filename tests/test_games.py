"""Games with players: DM-only API with a password, players who join with a code, configure their
own agent within the DM's template, see only names, and see results only once the DM publishes."""

import time

import pytest
from fastapi.testclient import TestClient

from simcore.server.api import create_app
from simcore.server.games import GameError, GameStore, check_agent, diary, template_of
from simcore.server.scenarios import ScenarioStore

SCENARIO = {
    "simulation": {
        "name": "Castillo",
        "description": "Secreto del DM",
        "duration": "3 hours",
        "rpg": True,
    },
    "environment": {"locations": [{"name": "Salón"}, {"name": "Capilla"}]},
    "items": {"daga": {"consumable": False, "weapon": {"damage": 0.3, "chance": 0.7}}},
    "agents": [{"name": "Conde", "starting_location": "Salón"}],
}
TEMPLATE = {
    "editable": ["name", "profession", "personality", "goals", "attributes", "skills"],
    "fixed": {"starting_location": "Salón", "resources": {"daga": 1}},
    "attribute_budget": 4,
    "max_skills": 1,
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMCORE_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("SIMCORE_GAMES_DIR", str(tmp_path / "games"))
    monkeypatch.setenv("SIMCORE_DM_PASSWORD", "dragon")
    store = ScenarioStore(tmp_path / "user")
    sid = store.create(SCENARIO)["id"]
    return create_app(scenario_store=store), sid


def login(client):
    assert client.post("/api/auth/login", json={"password": "dragon"}).status_code == 200


def test_without_a_password_everything_stays_open(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMCORE_DM_PASSWORD", raising=False)
    with TestClient(create_app(scenario_store=ScenarioStore(tmp_path / "u"))) as client:
        assert client.get("/api/scenarios").status_code == 200
        assert client.get("/api/auth/me").json() == {"required": False, "dm": True}


def test_with_a_password_only_a_dm_sees_the_simulation(env):
    app, _ = env
    with TestClient(app) as client:
        for path in ("/api/state", "/api/runs", "/api/scenarios", "/api/games", "/api/events"):
            assert client.get(path).status_code == 401, path
        assert client.get("/api/version").status_code == 200
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
        with pytest.raises(Exception), client.websocket_connect("/ws") as ws:
            ws.receive_text()
        login(client)
        assert client.get("/api/auth/me").json() == {"required": True, "dm": True}
        assert client.get("/api/scenarios").status_code == 200
        client.post("/api/auth/logout")
        assert client.get("/api/scenarios").status_code == 401


def test_a_whole_game(env):
    app, sid = env
    with TestClient(app) as dm, TestClient(app) as ana, TestClient(app) as bob:
        login(dm)
        game = dm.post(
            "/api/games",
            json={
                "scenario_id": sid,
                "title": "La sucesión",
                "briefing": "Ha muerto el conde.",
                "template": TEMPLATE,
                "max_players": 2,
            },
        ).json()
        code = game["code"]

        # Players join with the shared code and get their own token.
        view = ana.get(f"/api/play/{code}").json()
        assert view["briefing"] == "Ha muerto el conde." and "Secreto del DM" not in str(view)
        assert view["template"]["fixed"] == ["resources", "starting_location"]
        ta = ana.post(f"/api/play/{code}/join", json={"display_name": "Ana"}).json()["token"]
        tb = bob.post(f"/api/play/{code}/join", json={"display_name": "Bob"}).json()["token"]
        assert (
            bob.post(f"/api/play/{code}/join", json={"display_name": "Eve"}).status_code == 422
        )  # full
        ha, hb = {"X-Player-Token": ta}, {"X-Player-Token": tb}

        # Players never reach the DM's API, and only see names.
        assert ana.get("/api/state").status_code == 401 and ana.get("/api/runs").status_code == 401
        assert ana.get(f"/api/play/{code}").json()["players"] == ["Ana", "Bob"]
        assert ana.get(f"/api/play/{code}/me", headers={"X-Player-Token": "x"}).status_code == 403

        # Their agent, within the template.
        bad = {"name": "Ana la Roja", "starting_location": "Capilla"}
        assert (
            "lo decide el DM"
            in ana.put(f"/api/play/{code}/me", headers=ha, json={"agent": bad}).json()["detail"]
        )
        greedy = {"name": "Ana la Roja", "attributes": {"strength": 16}}
        assert (
            "Te has pasado"
            in ana.put(f"/api/play/{code}/me", headers=ha, json={"agent": greedy}).json()["detail"]
        )
        mine = {
            "name": "Ana la Roja",
            "profession": "espía",
            "goals": ["Quedarme el castillo"],
            "attributes": {"strength": 12, "dexterity": 13, "wisdom": 9},
            "skills": ["stealth"],
        }
        assert ana.put(
            f"/api/play/{code}/me", headers=ha, json={"agent": mine, "submitted": True}
        ).json()["submitted"]
        assert (
            bob.put(
                f"/api/play/{code}/me", headers=hb, json={"agent": {"name": "Ana la Roja"}}
            ).status_code
            == 422
        )
        bob.put(
            f"/api/play/{code}/me", headers=hb, json={"agent": {"name": "Bob"}, "submitted": True}
        )
        assert "Ana la Roja" not in str(bob.get(f"/api/play/{code}").json())  # only display names

        # The DM sees everything (no token hashes), closes and launches.
        full = dm.get(f"/api/games/{game['id']}").json()
        assert full["players"][0]["agent"]["profession"] == "espía" and "token_hash" not in str(
            full
        )
        dm.put(f"/api/games/{game['id']}", json={"status": "closed"})
        assert ana.put(f"/api/play/{code}/me", headers=ha, json={"agent": mine}).status_code == 422
        assert ana.post(f"/api/play/{code}/join", json={"display_name": "Late"}).status_code == 422
        run = dm.post(f"/api/games/{game['id']}/launch", json={"demo": True, "speed": 0}).json()
        assert run["status"] == "started"
        for _ in range(200):
            meta = dm.get("/api/runs").json()["runs"][0]
            if meta["status"] != "running":
                break
            time.sleep(0.05)
        assert meta["game_id"] == game["id"]
        agents = dm.get(f"/api/runs/{run['run_id']}").json()["config"]["agents"]
        ana_agent = next(a for a in agents if a["name"] == "Ana la Roja")
        assert ana_agent["starting_location"] == "Salón" and ana_agent["resources"] == {"daga": 1}

        # Nothing until the DM publishes; then each player sees only their own diary.
        assert "diary" not in ana.get(f"/api/play/{code}/results", headers=ha).json()
        dm.put(f"/api/games/{game['id']}", json={"publish": {"result": True, "diary": True}})
        res = ana.get(f"/api/play/{code}/results", headers=ha).json()
        assert res["result"]["ticks"] == 3 and "replay" not in res
        lived = res["diary"]["events"]
        for e in lived:
            if e["type"] == "tick_end":
                assert [a["name"] for a in e["data"]["agents"].values()] == ["Ana la Roja"]
            if e.get("source") and e["source"] != "Ana la Roja":
                assert "reasoning" not in e["data"]
        dm.put(
            f"/api/games/{game['id']}",
            json={"publish": {"result": True, "diary": True, "replay": True}},
        )
        assert ana.get(f"/api/play/{code}/results", headers=ha).json()["replay"]["events"]


def test_the_template_keeps_players_within_limits():
    t = template_of(TEMPLATE)
    assert t["editable"] == TEMPLATE["editable"] and t["fixed"]["starting_location"] == "Salón"
    with pytest.raises(GameError, match="Como mucho 1"):
        check_agent({"name": "X", "skills": ["stealth", "insight"]}, t, set())
    with pytest.raises(GameError, match="entre 6 y 16"):
        check_agent({"name": "X", "attributes": {"wisdom": 3}}, t, set())
    ok = check_agent({"name": " X ", "attributes": {"strength": 14, "wisdom": 8}}, t, set())
    assert ok["name"] == "X" and ok["attributes"] == {"strength": 14, "wisdom": 8}


def test_tokens_are_stored_hashed(tmp_path):
    store = GameStore(tmp_path)
    game = store.create({"scenario_id": "user:x"})
    _, player, token = store.join(game["code"], "Ana")
    raw = (tmp_path / f"{game['id']}.json").read_text()
    assert token not in raw and player["token_hash"] in raw
    assert store.player_for(game["code"].lower(), token)[1]["id"] == player["id"]
    with pytest.raises(PermissionError):
        store.player_for(game["code"], "nope")


def test_whispers_to_others_stay_secret_in_a_diary():
    events = [
        {
            "type": "tick_end",
            "tick": 1,
            "source": "",
            "data": {
                "agents": {
                    "a": {"name": "Ana", "location": "Salón"},
                    "b": {"name": "Bob", "location": "Salón"},
                }
            },
        },
        {
            "type": "agent_action",
            "tick": 2,
            "source": "Bob",
            "data": {
                "action_type": "whisper",
                "target": "Eve",
                "content": "El sello está en la cripta",
                "location": "Salón",
                "reasoning": "plan",
            },
        },
        {
            "type": "agent_speak",
            "tick": 2,
            "source": "Eve",
            "data": {"target": "Bob", "content": "hola", "location": "Capilla"},
        },
    ]
    lived = diary(events, "Ana")
    assert [e["type"] for e in lived] == ["tick_end", "agent_action"]
    assert lived[1]["data"]["content"] == "" and "reasoning" not in lived[1]["data"]
    assert list(lived[0]["data"]["agents"]) == ["a"]
