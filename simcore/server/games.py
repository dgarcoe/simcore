"""Games with players: each one configures their own agent for a scenario a dungeon master set up.

A game lives in ``games/<id>.json`` (``SIMCORE_GAMES_DIR``). Players join through the game's code
and get a personal token (only its hash is stored); with it they can read the public briefing,
the names of the other players, and read and edit their own agent within the DM's template.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from simcore.config.schema import AgentConfig
from simcore.engine.rpg import ATTRIBUTES, SKILLS

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
EDITABLE = (
    "name",
    "profession",
    "age",
    "personality",
    "goals",
    "backstory",
    "secrets",
    "attributes",
    "skills",
)
FIXED = (
    "starting_location",
    "resources",
    "conditions",
    "faction",
    "relationships",
    "beliefs",
    "needs",
    "health",
    "arrives_at",
    "level",
)
DEFAULT_TEMPLATE = {
    "editable": ["name", "profession", "age", "personality", "goals", "backstory"],
    "fixed": {},
    # points above 10, summed over the six attributes (lowering one gives one back)
    "attribute_budget": 6,
    "attribute_min": 6,
    "attribute_max": 16,
    "max_skills": 2,
    "professions": [],  # allowed professions; empty = free text
    "max_text": 600,
    "max_goals": 4,
}
STATUSES = ("open", "closed", "running", "finished")
_ID = re.compile(r"^[a-f0-9]{12}$")


class GameError(ValueError):
    """Something a player or the DM can fix (shown to them as is)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _default_dir() -> Path:
    return Path(os.environ.get("SIMCORE_GAMES_DIR") or Path.cwd() / "games")


def template_of(raw: dict | None) -> dict:
    t = {**copy.deepcopy(DEFAULT_TEMPLATE), **(raw or {})}
    t["editable"] = [f for f in t.get("editable", []) if f in EDITABLE]
    t["fixed"] = {k: v for k, v in (t.get("fixed") or {}).items() if k in FIXED}
    return t


def check_agent(agent: dict, template: dict, taken: set[str]) -> dict:
    """The player's agent, keeping only what the template lets them edit, within its limits."""
    t = template_of(template)
    out: dict = {}
    for key, value in (agent or {}).items():
        if key not in t["editable"]:
            raise GameError(f"No puedes cambiar «{key}»: lo decide el DM.")
        out[key] = value
    name = str(out.get("name", "")).strip()
    if "name" in t["editable"]:
        if not name:
            raise GameError("Tu agente necesita un nombre.")
        if name.lower() in taken:
            raise GameError(f"Ya hay alguien llamado «{name}» en esta partida.")
        out["name"] = name[:40]
    limit = int(t["max_text"])
    for key in ("profession", "backstory"):
        if key in out:
            out[key] = str(out[key] or "")[:limit]
    if t["professions"] and out.get("profession") and out["profession"] not in t["professions"]:
        raise GameError(f"Profesión no permitida: elige entre {', '.join(t['professions'])}.")
    for key in ("goals", "secrets"):
        if key in out:
            items = [str(x).strip()[:limit] for x in (out[key] or []) if str(x).strip()]
            if len(items) > int(t["max_goals"]):
                raise GameError(
                    f"Como mucho {t['max_goals']} {('metas' if key == 'goals' else 'secretos')}."
                )
            out[key] = items
    if "attributes" in out:
        attrs = {k: int(v) for k, v in (out["attributes"] or {}).items()}
        for k, v in attrs.items():
            if k not in ATTRIBUTES:
                raise GameError(f"Atributo desconocido «{k}».")
            if not t["attribute_min"] <= v <= t["attribute_max"]:
                raise GameError(
                    f"{k} debe estar entre {t['attribute_min']} y {t['attribute_max']}."
                )
        spent = sum(v - 10 for v in attrs.values())
        if spent > int(t["attribute_budget"]):
            raise GameError(f"Te has pasado: {spent} puntos de {t['attribute_budget']}.")
        out["attributes"] = attrs
    if "skills" in out:
        skills = list(dict.fromkeys(out["skills"] or []))
        unknown = [s for s in skills if s not in SKILLS]
        if unknown:
            raise GameError(f"Habilidad desconocida: {', '.join(unknown)}.")
        if len(skills) > int(t["max_skills"]):
            raise GameError(f"Como mucho {t['max_skills']} habilidades.")
        out["skills"] = skills
    if "age" in out:
        out["age"] = max(14, min(99, int(out["age"] or 30)))
    # Must be a valid agent once the DM's fixed fields are added.
    try:
        AgentConfig.model_validate({"name": name or "x", **out, **t["fixed"]})
    except ValueError as e:
        raise GameError(f"Ficha no válida: {e}") from e
    return out


class GameStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else _default_dir()

    # ── Storage ────────────────────────────────────────────────────────

    def _path(self, game_id: str) -> Path:
        if not _ID.match(game_id or ""):
            raise GameError("Partida desconocida.")
        return self.root / f"{game_id}.json"

    def save(self, game: dict) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        game["updated"] = _now()
        path = self._path(game["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(game, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return game

    def get(self, game_id: str) -> dict | None:
        try:
            path = self._path(game_id)
        except GameError:
            return None
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        games = []
        for path in self.root.glob("*.json"):
            try:
                games.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(games, key=lambda g: g.get("created", ""), reverse=True)

    def by_code(self, code: str) -> dict | None:
        code = (code or "").strip().upper().replace("-", "")
        return next((g for g in self.list() if g["code"].replace("-", "") == code), None)

    def delete(self, game_id: str) -> bool:
        path = self._path(game_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # ── DM ─────────────────────────────────────────────────────────────

    def create(self, payload: dict) -> dict:
        if not payload.get("scenario_id"):
            raise GameError("Elige un escenario.")
        taken = {g["code"] for g in self.list()}
        while True:
            raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
            code = f"{raw[:4]}-{raw[4:]}"
            if code not in taken:
                break
        game = {
            "id": uuid.uuid4().hex[:12],
            "code": code,
            "title": str(payload.get("title") or "Partida").strip()[:80],
            "briefing": str(payload.get("briefing") or "").strip()[:4000],
            "scenario_id": payload["scenario_id"],
            "template": template_of(payload.get("template")),
            "max_players": max(1, min(50, int(payload.get("max_players") or 8))),
            "status": "open",
            "players": [],
            "run_id": None,
            "publish": {"result": False, "diary": False, "replay": False},
            "created": _now(),
        }
        return self.save(game)

    def update(self, game_id: str, payload: dict) -> dict:
        game = self._require(game_id)
        for key in ("title", "briefing", "scenario_id", "max_players"):
            if key in payload:
                game[key] = payload[key]
        if "template" in payload:
            game["template"] = template_of(payload["template"])
        if "status" in payload:
            if payload["status"] not in STATUSES:
                raise GameError(f"Estado desconocido «{payload['status']}».")
            game["status"] = payload["status"]
        if "publish" in payload:
            game["publish"] = {
                k: bool((payload["publish"] or {}).get(k)) for k in ("result", "diary", "replay")
            }
        return self.save(game)

    def edit_player(self, game_id: str, player_id: str, payload: dict) -> dict:
        """The DM changes a player's agent (no template limits) or removes the player."""
        game = self._require(game_id)
        player = next((p for p in game["players"] if p["id"] == player_id), None)
        if player is None:
            raise GameError("Ese jugador no está en la partida.")
        if payload.get("remove"):
            game["players"].remove(player)
        else:
            if "agent" in payload:
                AgentConfig.model_validate({"name": "x", **payload["agent"]})
                player["agent"] = payload["agent"]
            if "submitted" in payload:
                player["submitted"] = bool(payload["submitted"])
        return self.save(game)

    def build_scenario(self, game: dict, scenario: dict) -> dict:
        """The DM's scenario plus one agent per player (with the template's fixed fields)."""
        data = copy.deepcopy(scenario)
        agents = data.setdefault("agents", [])
        names = {a.get("name", "").lower() for a in agents}
        fixed = game["template"].get("fixed", {})
        for p in game["players"]:
            agent = {**copy.deepcopy(p.get("agent") or {}), **copy.deepcopy(fixed)}
            agent["name"] = (agent.get("name") or p["display_name"]).strip()
            if agent["name"].lower() in names:
                agent["name"] = f"{agent['name']} ({p['display_name']})"
            names.add(agent["name"].lower())
            agents.append(agent)
        return data

    # ── Players ────────────────────────────────────────────────────────

    def join(self, code: str, display_name: str) -> tuple[dict, dict, str]:
        game = self.by_code(code)
        if game is None:
            raise GameError("No hay ninguna partida con ese código.")
        if game["status"] != "open":
            raise GameError("Las inscripciones de esta partida están cerradas.")
        if len(game["players"]) >= int(game["max_players"]):
            raise GameError("La partida está completa.")
        display_name = str(display_name or "").strip()[:40]
        if not display_name:
            raise GameError("Escribe tu nombre.")
        if any(p["display_name"].lower() == display_name.lower() for p in game["players"]):
            raise GameError("Ya hay un jugador con ese nombre: usa otro.")
        token = secrets.token_urlsafe(24)
        player = {
            "id": uuid.uuid4().hex[:12],
            "display_name": display_name,
            "token_hash": _hash(token),
            "agent": {"name": display_name} if "name" in game["template"]["editable"] else {},
            "submitted": False,
            "joined": _now(),
        }
        game["players"].append(player)
        self.save(game)
        return game, player, token

    def player_for(self, code: str, token: str) -> tuple[dict, dict]:
        game = self.by_code(code)
        if game is None:
            raise GameError("No hay ninguna partida con ese código.")
        wanted = _hash(token or "")
        for p in game["players"]:
            if hmac.compare_digest(p["token_hash"], wanted):
                return game, p
        raise PermissionError("Tu enlace personal no es válido para esta partida.")

    def save_agent(self, code: str, token: str, agent: dict, submitted: bool | None) -> dict:
        game, player = self.player_for(code, token)
        if game["status"] != "open":
            raise GameError("La partida ya no admite cambios.")
        others = {
            str((p.get("agent") or {}).get("name") or p["display_name"]).lower()
            for p in game["players"]
            if p["id"] != player["id"]
        }
        player["agent"] = check_agent(agent, game["template"], others)
        if submitted is not None:
            player["submitted"] = bool(submitted)
        self.save(game)
        return player

    def public_view(self, game: dict) -> dict:
        """What anyone with the game's code may see: briefing, editable fields and names only."""
        t = game["template"]
        return {
            "code": game["code"],
            "title": game["title"],
            "briefing": game["briefing"],
            "status": game["status"],
            "players": [p["display_name"] for p in game["players"]],
            "max_players": game["max_players"],
            "template": {k: t[k] for k in DEFAULT_TEMPLATE if k != "fixed"}
            | {"fixed": sorted(t["fixed"])},
            "published": game["publish"],
        }

    def _require(self, game_id: str) -> dict:
        game = self.get(game_id)
        if game is None:
            raise GameError("Partida desconocida.")
        return game


def dm_view(game: dict) -> dict:
    """The whole game for the DM, without token hashes."""
    out = copy.deepcopy(game)
    for p in out["players"]:
        p.pop("token_hash", None)
    return out


# ── After the run: what a player may see of it ────────────────────────────

_PEOPLE_KEYS = ("agent", "target", "to", "from", "by", "other", "holder")
_WORLD = {
    "scheduled_event",
    "injected_event",
    "rule_fired",
    "simulation_start",
    "simulation_end",
    "story_chapter",
}


def diary(events: list[dict], name: str) -> list[dict]:
    """The events one agent lived: what it did, what was done or said to it, what happened where it
    was, and world news. Tick ends keep only its own state, so nothing else leaks."""
    where: str = ""
    out = []
    for e in events:
        data = e.get("data") or {}
        if e["type"] == "tick_end":
            agents = data.get("agents") or {}
            own = {k: a for k, a in agents.items() if a.get("name") == name}
            for a in own.values():
                where = a.get("location", where)
            out.append({**e, "data": {"clock": data.get("clock"), "agents": own}})
            continue
        if e["type"] in ("tick_start",):
            out.append(e)
            continue
        if e["type"] in _WORLD:
            out.append(e)
            continue
        mentioned = (
            e.get("source") == name
            or any(
                name in (data.get(k) if isinstance(data.get(k), list) else [data.get(k)])
                for k in _PEOPLE_KEYS
            )
            or name in (data.get("targets") or [])
        )
        here = where and (data.get("location") == where or data.get("place") == where)
        if mentioned or (
            here
            and e["type"]
            in (
                "agent_speak",
                "agent_action",
                "agent_death",
                "agent_spawn",
                "agent_depart",
                "agent_move",
            )
        ):
            if e.get("source") and e.get("source") != name:
                # Others' private thoughts never reach a player; whispers to others stay secret.
                data = {k: v for k, v in data.items() if k != "reasoning"}
                if data.get("action_type") == "whisper" and not mentioned:
                    data = {
                        **data,
                        "content": "",
                        "action": f"whispers to {data.get('target', 'someone')}",
                    }
                e = {**e, "data": data}
            out.append(e)
    return out
