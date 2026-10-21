"""docs/scenario-reference.md documents every scenario field and its example loads."""

import inspect
import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from simcore.config import schema
from simcore.engine.effects import LOCATION_KEYS, RESERVED_KEYS
from simcore.engine.rpg import ATTRIBUTES, SKILLS

DOC = Path(__file__).resolve().parent.parent / "docs" / "scenario-reference.md"
TEXT = DOC.read_text(encoding="utf-8")
PROSE = re.sub(r"```.*?```", "", TEXT, flags=re.DOTALL)
CODE = set(re.findall(r"`([^`\n]+)`", PROSE))


def _documented(name: str) -> bool:
    return name in CODE or any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", c) for c in CODE)


def _models() -> list[type[BaseModel]]:
    return [
        m
        for _, m in inspect.getmembers(schema, inspect.isclass)
        if issubclass(m, BaseModel) and m is not BaseModel and m.__module__ == schema.__name__
    ]


def test_every_schema_field_is_documented():
    missing = []
    for model in _models():
        for name, field in model.model_fields.items():
            key = field.alias or name
            if not _documented(key):
                missing.append(f"{model.__name__}.{key}")
    assert not missing, f"document these in {DOC.name}: {', '.join(missing)}"


def test_effect_keys_attributes_and_skills_are_documented():
    names = [*RESERVED_KEYS, *LOCATION_KEYS, *ATTRIBUTES, *SKILLS]
    assert not [n for n in names if not _documented(n)]


def test_complete_example_loads():
    blocks = re.findall(r"```yaml\n(.*?)```", TEXT, re.DOTALL)
    example = max(blocks, key=len)
    raw = yaml.safe_load(example)
    sim = raw.pop("simulation")
    for key in ("llm", "environment", "agents", "events", "items", "conditions", "actions",
                "needs", "rules", "factions", "votes", "routines", "society"):
        if key in raw:
            sim[key] = raw.pop(key)
    assert not raw, f"unknown top-level keys: {list(raw)}"
    cfg = schema.SimulationConfig(**sim)
    assert cfg.rpg and cfg.story and len(cfg.agents) >= 4 and len(cfg.rules) >= 2


def test_every_yaml_snippet_parses():
    for block in re.findall(r"```yaml\n(.*?)```", TEXT, re.DOTALL):
        assert isinstance(yaml.safe_load(block), dict)
