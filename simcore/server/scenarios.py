"""Scenario storage — builtin YAMLs + user-created JSON scenarios."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

_BUILTIN_DIR = Path(__file__).parent.parent.parent / "scenarios"
_DEFAULT_USER_DIR = Path.cwd() / "scenarios_user"


class ScenarioStore:
    def __init__(self, user_dir: Path | None = None):
        self.user_dir = Path(user_dir) if user_dir else _DEFAULT_USER_DIR
        self.user_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_all(self) -> list[dict]:
        return self._load_builtins() + self._load_user_scenarios()

    def get(self, scenario_id: str) -> dict | None:
        if scenario_id.startswith("builtin:"):
            for s in self._load_builtins():
                if s["id"] == scenario_id:
                    return s
            return None
        return self._load_user_scenario(scenario_id)

    def create(self, payload: dict) -> dict:
        scenario_id = f"user:{uuid.uuid4().hex[:12]}"
        record = {
            "id": scenario_id,
            "builtin": False,
            "meta": _extract_meta(payload, scenario_id),
            "data": payload,
        }
        safe_name = scenario_id.replace(":", "_")
        path = self.user_dir / f"{safe_name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return record

    def update(self, scenario_id: str, payload: dict) -> dict | None:
        if scenario_id.startswith("builtin:"):
            return None
        safe_name = scenario_id.replace(":", "_")
        path = self.user_dir / f"{safe_name}.json"
        if not path.exists():
            return None
        record = {
            "id": scenario_id,
            "builtin": False,
            "meta": _extract_meta(payload, scenario_id),
            "data": payload,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return record

    def delete(self, scenario_id: str) -> bool:
        if scenario_id.startswith("builtin:"):
            return False
        safe_name = scenario_id.replace(":", "_")
        path = self.user_dir / f"{safe_name}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def load_engine_config(self, scenario_id: str):
        """Return a SimulationConfig ready for SimulationEngine."""
        from simcore.config.loader import load_config
        scenario = self.get(scenario_id)
        if not scenario:
            raise ValueError(f"Scenario not found: {scenario_id}")
        if scenario.get("builtin"):
            folder = scenario_id.split(":", 1)[1]
            yaml_path = _BUILTIN_DIR / folder / "config.yaml"
            return load_config(yaml_path)
        return _dict_to_config(scenario["data"])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_builtins(self) -> list[dict]:
        result = []
        if not _BUILTIN_DIR.exists():
            return result
        import yaml as _yaml
        for yaml_path in sorted(_BUILTIN_DIR.glob("*/config.yaml")):
            folder = yaml_path.parent.name
            try:
                with open(yaml_path, encoding="utf-8") as f:
                    raw = _yaml.safe_load(f)
                if not raw:
                    continue
                sim = raw.get("simulation", {})
                result.append({
                    "id": f"builtin:{folder}",
                    "builtin": True,
                    "meta": {
                        "name": sim.get("name", folder),
                        "description": sim.get("description", ""),
                        "agents": len(raw.get("agents", [])),
                        "locations": len(raw.get("environment", {}).get("locations", [])),
                        "duration": sim.get("duration", ""),
                        "language": sim.get("language", "en"),
                        "llm_model": raw.get("llm", {}).get("model", ""),
                    },
                    "data": raw,
                })
            except Exception:
                pass
        return result

    def _load_user_scenarios(self) -> list[dict]:
        result = []
        for path in sorted(self.user_dir.glob("user_*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    record = json.load(f)
                result.append(record)
            except Exception:
                pass
        return result

    def _load_user_scenario(self, scenario_id: str) -> dict | None:
        safe_name = scenario_id.replace(":", "_")
        path = self.user_dir / f"{safe_name}.json"
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None


def _extract_meta(payload: dict, scenario_id: str) -> dict:
    sim = payload.get("simulation", {})
    return {
        "name": sim.get("name", "Untitled"),
        "description": sim.get("description", ""),
        "agents": len(payload.get("agents", [])),
        "locations": len(payload.get("environment", {}).get("locations", [])),
        "duration": sim.get("duration", ""),
        "language": sim.get("language", "en"),
        "llm_model": payload.get("llm", {}).get("model", ""),
    }


def _dict_to_config(data: dict):
    """Convert a raw YAML-structure dict to SimulationConfig."""
    from simcore.config.schema import SimulationConfig
    sim_data = dict(data.get("simulation", {}))
    sim_data["llm"] = data.get("llm", {})
    sim_data["environment"] = data.get("environment", {})
    sim_data["agents"] = data.get("agents", [])
    sim_data["events"] = data.get("events", {})
    sim_data["items"] = data.get("items", {})
    sim_data["conditions"] = data.get("conditions", {})
    sim_data["actions"] = data.get("actions", {})
    sim_data["needs"] = data.get("needs", {})
    sim_data["rules"] = data.get("rules", [])
    return SimulationConfig(**sim_data)