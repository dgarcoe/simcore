"""Every run started from the server is recorded, so it can be watched again later.

Each run is ``<id>.jsonl.gz`` (one event per line, after a header line with the scenario) and
``<id>.meta.json`` (what the run list shows). The format of ``RunStore.load`` is the replay file
simcore_ui plays: ``{scenario, config, events, meta}``.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from simcore.engine.events import EventType, SimEvent

logger = logging.getLogger("simcore.runs")

_ID = re.compile(r"^[\w-]{1,80}$")
MEMORY_CHARS = 280  # long memory summaries in every tick_end make files huge


def _default_dir() -> Path:
    return Path(os.environ.get("SIMCORE_RUNS_DIR") or Path.cwd() / "runs")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunRecorder:
    """Writes one run's events as they happen; `close()` writes its metadata."""

    def __init__(self, store: RunStore, run_id: str, header: dict):
        self.store = store
        self.id = run_id
        self.meta = {
            **{k: header.get(k) for k in ("name", "description", "scenario_id", "model", "demo")},
            "id": run_id,
            "started": _now(),
            "ended": None,
            "status": "running",
            "ticks": 0,
            "events": 0,
            "end_reason": "",
            "end_message": "",
            "alive": [],
        }
        self._file = gzip.open(store.events_path(run_id), "wt", encoding="utf-8")
        self._file.write(json.dumps({"header": header}, ensure_ascii=False, default=str) + "\n")
        self.store.write_meta(self.meta)

    @property
    def closed(self) -> bool:
        return self._file is None

    def handle(self, event: SimEvent) -> None:
        if self._file is None:
            return
        data = event.to_dict()
        if event.type == EventType.TICK_END:
            data = json.loads(json.dumps(data, default=str))
            for agent in data["data"].get("agents", {}).values():
                if isinstance(agent.get("memory_summary"), str):
                    agent["memory_summary"] = agent["memory_summary"][:MEMORY_CHARS]
            self.meta["ticks"] = event.tick
            self.meta["alive"] = sorted(
                a.get("name", "")
                for a in data["data"].get("agents", {}).values()
                if (a.get("status") or a.get("state", {}).get("status") or "alive") == "alive"
            )
        try:
            self._file.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        except (OSError, ValueError) as e:
            logger.warning(f"Run {self.id}: can't record ({e}); stopping the recording")
            self.close("broken")
            return
        self.meta["events"] += 1
        if event.type == EventType.SIMULATION_END:
            self.meta["end_reason"] = str(event.data.get("reason") or "completed")
            self.meta["end_message"] = str(event.data.get("message") or "")
            self.close("finished")

    def close(self, status: str = "interrupted") -> None:
        if self._file is None:
            return
        try:
            self._file.close()
        except OSError:
            pass
        self._file = None
        self.meta["status"] = status
        self.meta["ended"] = _now()
        self.store.write_meta(self.meta)
        self.store.prune()


class RunStore:
    def __init__(self, root: Path | None = None, keep: int | None = None):
        self.root = Path(root) if root else _default_dir()
        self.keep = keep if keep is not None else int(os.environ.get("SIMCORE_RUNS_KEEP", "50"))

    def _check(self, run_id: str) -> str:
        if not _ID.match(run_id or ""):
            raise ValueError(f"bad run id '{run_id}'")
        return run_id

    def events_path(self, run_id: str) -> Path:
        return self.root / f"{self._check(run_id)}.jsonl.gz"

    def meta_path(self, run_id: str) -> Path:
        return self.root / f"{self._check(run_id)}.meta.json"

    def start(
        self, engine, scenario: dict | None, scenario_id: str = "", demo: bool = False
    ) -> RunRecorder:
        """Record a run that is about to start (subscribes to its event bus)."""
        self.root.mkdir(parents=True, exist_ok=True)
        cfg = engine.config
        slug = re.sub(r"[^\w-]+", "-", cfg.name.lower()).strip("-")[:40] or "run"
        run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{slug}"
        n = 1
        while self.events_path(run_id).exists():
            n += 1
            run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{slug}-{n}"
        header = {
            "name": cfg.name,
            "description": cfg.description,
            "scenario_id": scenario_id,
            "model": "demo" if demo else cfg.llm.model,
            "demo": demo,
            "config": scenario,
        }
        recorder = RunRecorder(self, run_id, header)
        engine.event_bus.on_all(recorder.handle)
        return recorder

    def write_meta(self, meta: dict) -> None:
        path = self.meta_path(meta["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        out = []
        for path in self.root.glob("*.meta.json"):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda m: m.get("started") or "", reverse=True)

    def load(self, run_id: str) -> dict | None:
        path = self.events_path(run_id)
        if not path.exists():
            return None
        header: dict = {}
        events = []
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if "header" in item:
                        header = item["header"]
                    else:
                        events.append(item)
        except (EOFError, OSError, ValueError):
            pass  # still being written, or cut off: what was read so far
        meta_path = self.meta_path(run_id)
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        return {
            "scenario": {
                "name": header.get("name", ""),
                "description": header.get("description", ""),
            },
            "config": header.get("config"),
            "events": events,
            "meta": meta,
        }

    def delete(self, run_id: str) -> bool:
        found = False
        for path in (self.events_path(run_id), self.meta_path(run_id)):
            if path.exists():
                path.unlink()
                found = True
        return found

    def prune(self) -> None:
        if self.keep <= 0:
            return
        done = [m for m in self.list() if m.get("status") != "running"]
        for meta in done[self.keep :]:
            self.delete(meta["id"])
