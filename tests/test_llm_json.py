"""Reading the models' JSON: repairs, retries on unreadable answers, no caching of broken ones."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import simcore.llm.provider as provider
from simcore.agents.actions import ActionType
from simcore.llm.provider import LLMProvider, _load_json_object, parse_plan


@pytest.mark.parametrize(
    "text, expected",
    [
        # the case seen in the wild: "Expecting value: line 3 column 15"
        ('{\n  "action": "speak",\n  "target": Lola,\n  "content": "hola"\n}', {"target": "Lola"}),
        ('{"action": “speak”, "target": “Lola”}', {"action": "speak", "target": "Lola"}),
        ('Claro:\n```json\n{"action": "rest", "target": "",}\n```\nListo.', {"action": "rest"}),
        (
            '{"action": "wait", // nada que hacer\n "target": None, "plan_step_done": True}',
            {"target": None, "plan_step_done": True},
        ),
        ("{'action': 'speak', 'target': 'Ana', 'content': \"I'm here\"}", {"content": "I'm here"}),
        ('{action: "move", target: Plaza Mayor}', {"action": "move", "target": "Plaza Mayor"}),
        (
            '{"action": "speak", "target": "Ana", "content": "I\'m here, l\'eau"',
            {"content": "I'm here, l'eau"},
        ),
        (
            '{"action": "speak", "target": "Bob", "content": "say \\"hi\\" {ok}"}',
            {"content": 'say "hi" {ok}'},
        ),
    ],
)
def test_repairs_common_slips(text, expected):
    data = _load_json_object(text)
    for key, value in expected.items():
        assert data[key] == value


def test_unreadable_still_raises():
    with pytest.raises(json.JSONDecodeError):
        _load_json_object('{"action": "speak", "content": "a" "b"}')
    with pytest.raises(ValueError):
        _load_json_object("no json here")


def test_parse_plan_tolerates_slips():
    assert parse_plan("{'steps': ['go', 'eat',]}") == ["go", "eat"]


def _fake_completion(answers, calls):
    async def acompletion(**kwargs):
        calls.append(kwargs)
        content = answers.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    return acompletion


_real_sleep = asyncio.sleep


async def _no_sleep(*_):
    await _real_sleep(0)


BROKEN = '{"action": "speak", "content": "a" "b"}'
GOOD = '{"action": "work", "content": "fixing the fence"}'


def test_unreadable_answer_is_retried_and_not_cached(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(provider.litellm, "acompletion", _fake_completion([BROKEN, GOOD], calls))
    monkeypatch.setattr(provider.asyncio, "sleep", _no_sleep)
    p = LLMProvider(model="ollama/qwen2.5:7b", cache_enabled=False)
    stored = {}
    p.cache = SimpleNamespace(get=stored.get, set=stored.__setitem__)
    action = asyncio.run(p.decide("sys", "user"))
    assert action.type == ActionType.WORK and len(calls) == 2
    assert list(stored.values()) == [GOOD]
    # Ollama: its own JSON mode, and no thinking (qwen3 would spend the answer thinking)
    assert calls[0]["format"] == "json" and calls[0]["think"] is False
    assert "response_format" not in calls[0] and "frequency_penalty" not in calls[0]


def test_gives_up_after_a_few_unreadable_answers(monkeypatch):
    calls = []
    monkeypatch.setattr(provider.litellm, "acompletion", _fake_completion([BROKEN] * 5, calls))
    monkeypatch.setattr(provider.asyncio, "sleep", _no_sleep)
    action = asyncio.run(LLMProvider(model="gpt-4o-mini", cache_enabled=False).decide("s", "u"))
    assert action.type == ActionType.WAIT and action.reasoning.startswith("Parse error:")
    assert len(calls) == 3
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_broken_cached_answer_is_ignored(monkeypatch):
    calls = []
    monkeypatch.setattr(provider.litellm, "acompletion", _fake_completion([GOOD], calls))
    p = LLMProvider(model="ollama/qwen2.5:7b", cache_enabled=False)
    stored = {"sys|||user": BROKEN}
    p.cache = SimpleNamespace(get=stored.get, set=stored.__setitem__)
    assert asyncio.run(p.decide("sys", "user")).type == ActionType.WORK
    assert len(calls) == 1 and stored["sys|||user"] == GOOD
