"""What an LLM sees and answers when it decides: a short memory instead of old situations, the
scenario's actions in the answer format, loose names and free text read as real actions."""

import asyncio
import json

from simcore.agents.actions import Action, ActionType
from simcore.config.schema import SimulationConfig
from simcore.engine.simulation import SimulationEngine
from simcore.llm.prompts import language_directive
from simcore.llm.provider import LLMProvider

WORLD = {
    "name": "Radio",
    "duration": "12 hours",
    "rpg": True,
    "environment": {
        "global_state": {"ui_theme": "town", "radio": "rota", "luz": True},
        "locations": [{"name": "Torre"}, {"name": "Granja"}],
    },
    "conditions": {"zombi": {"appearance": "gruñe"}},
    "items": {"piezas": {}},
    "actions": {
        "reparar_radio": {
            "description": "reparar la radio",
            "target": "none",
            "who": {"items": {"piezas": 1}},
            "cost": {"piezas": 1},
            "chance": 0.6,
            "world": {"radio": "reparada"},
        },
        "golpear": {
            "description": "golpear a un zombi",
            "target_requires": {"conditions": ["zombi"]},
            "check": {"attribute": "strength", "dc": 10},
            "on_target": {"health": -0.3},
        },
    },
    "agents": [
        {"name": "Ortega", "starting_location": "Torre", "resources": {"piezas": 1}},
        {"name": "Marcos", "starting_location": "Torre", "conditions": ["zombi"]},
        {"name": "Lola", "starting_location": "Torre"},
        {"name": "Nico", "starting_location": "Granja"},
    ],
}


def make_engine() -> SimulationEngine:
    e = SimulationEngine(
        SimulationConfig.model_validate(json.loads(json.dumps(WORLD))), demo_mode=True
    )
    e.tick_delay = 0
    return e


def turn(e, name, answer: dict, tick=1):
    """One decision of `name` with the LLM answering `answer`; returns (prompt, action done)."""
    seen = {}

    async def decide(system, user):
        seen["prompt"] = user
        return LLMProvider(model="ollama/x", cache_enabled=False)._parse_action(json.dumps(answer))

    e.llm.decide = decide
    done = asyncio.run(e._agent_turn(e._find_agent_by_name(name), tick))
    return seen["prompt"], done


def test_memory_keeps_a_line_not_the_whole_old_situation():
    e = make_engine()
    turn(e, "Ortega", {"action": "wait"}, 1)
    e._find_agent_by_name("Ortega").location = "Granja"
    prompt, _ = turn(e, "Ortega", {"action": "wait"}, 2)
    # One situation only (the current one), and the past as a one-line note.
    assert prompt.count("People here:") == 1 and prompt.count("Special actions you can do now") <= 1
    assert "At Torre with Lola, Marcos." in prompt
    assert prompt.index("At Torre with") < prompt.index("Current situation:")


def test_world_context_is_readable_and_without_ui_settings():
    e = make_engine()
    prompt, _ = turn(e, "Ortega", {"action": "wait"})
    assert "World context: radio: rota; luz: yes" in prompt and "ui_theme" not in prompt


def test_ollama_gets_a_context_window_that_fits_the_prompt():
    assert (
        LLMProvider(model="ollama/qwen2.5:7b")._generation_params("ollama/qwen2.5:7b")["num_ctx"]
        == 8192
    )
    assert "num_ctx" not in LLMProvider(model="ollama/x", num_ctx=0)._generation_params("ollama/x")
    assert "num_ctx" not in LLMProvider(model="gpt-4o-mini")._generation_params("gpt-4o-mini")
    assert (
        SimulationConfig.model_validate({**WORLD, "llm": {"num_ctx": 16384}}).llm.num_ctx == 16384
    )


def test_the_answer_format_lists_the_scenario_actions_with_targets_and_odds():
    e = make_engine()
    prompt, _ = turn(e, "Ortega", {"action": "wait"})
    assert '"action": "reparar_radio|golpear|move|speak|' in prompt
    assert "- golpear (target: Marcos; success ≈" in prompt  # only the zombie, not Lola
    assert "- reparar_radio (no target; costs piezas x1; success ≈ 60%)" in prompt
    assert 'value of "action" exactly as one of the options' in language_directive("es")


def test_loose_answers_are_read():
    p = LLMProvider(model="ollama/x", cache_enabled=False)

    def read(answer):
        return p._parse_action(
            answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        )

    assert read({"action": "move|speak|whisper", "target": "Granja"}).type == ActionType.MOVE
    assert read({"action": "reparar radio"}).parameters["name"] == "reparar_radio"
    assert read({"action": "reparar_radio()"}).parameters["name"] == "reparar_radio"
    a = read({"action": {"name": "golpear", "target": "Marcos"}})
    assert a.parameters.get("alias_of") == "golpear" and a.target == "Marcos"
    b = read({"acción": "ir", "objetivo": "Granja", "razonamiento": "allí hay piezas"})
    assert b.type == ActionType.MOVE and b.target == "Granja" and b.reasoning == "allí hay piezas"
    for verb, kind in (
        ("coger", ActionType.TAKE),
        ("hablar", ActionType.SPEAK),
        ("dar", ActionType.GIVE),
        ("descansar", ActionType.REST),
        ("esperar", ActionType.WAIT),
        ("falar", ActionType.SPEAK),
    ):
        assert read({"action": verb}).type == kind
    missing = read({"target": "Marcos", "content": "¡fuera!"})
    assert missing.type == ActionType.WAIT and missing.reasoning.startswith(
        'Parse error: no "action"'
    )


def test_scenario_actions_named_loosely_are_found():
    e = make_engine()
    assert e._match_action_name("Reparar_Radio") == ("reparar_radio", "")
    assert e._match_action_name("reparár radio") == ("reparar_radio", "")
    assert e._match_action_name("golpear_a_marcos") == ("golpear", "Marcos")
    assert e._match_action_name("bailar") == ("bailar", "")
    _, done = turn(e, "Ortega", {"action": "Reparar Radio"})
    assert done.parameters.get("name") == "reparar_radio"


def test_free_text_becomes_the_action_it_names_or_says_nothing_happened():
    e = make_engine()
    ortega = e._find_agent_by_name("Ortega")
    fix = e._read_free_text(
        ortega, Action(type=ActionType.CUSTOM, content="Reparo la radio con las piezas")
    )
    assert fix.parameters == {"name": "reparar_radio"}
    hit = e._read_free_text(ortega, Action(type=ActionType.CUSTOM, content="Golpeo a Marcos"))
    assert hit.parameters == {"name": "golpear"} and hit.target == "Marcos"
    nothing = e._read_free_text(ortega, Action(type=ActionType.CUSTOM, content="Bailo una jota"))
    assert (
        nothing.type == ActionType.WAIT
        and "free text is not one of the actions" in nothing.reasoning
    )
