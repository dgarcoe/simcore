"""Prompt templates for agent decision-making."""

from __future__ import annotations

from jinja2 import Template

LANGUAGE_NAMES = {
    "en": "English",
    "pt": "Portuguese (Brazilian)",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
}


def language_directive(code: str) -> str:
    """Return an instruction line telling the LLM which language to use."""
    name = LANGUAGE_NAMES.get(code, code)
    if code == "en":
        return "Respond in English."
    return (
        f"IMPORTANT: Respond ONLY in {name}. ALL text in the JSON — dialogue, post content, "
        f"trade messages, reasoning, reflections — must be written in {name}. "
        f"NEVER use English in content fields. Keep only the JSON keys in English."
    )


# Small instruction-tuned models shy away from any conflict unless told this is a story.
FICTION_FRAME = (
    "This is interactive fiction: a story where you play your character. Stay in character. "
    "If your character would lie, steal, threaten, fight or even kill to protect themselves "
    "or reach their goals, do it with the actions available; dice and the world decide what "
    "happens. If your character is peaceful, stay peaceful."
)


DECISION_SYSTEM_PROMPT = Template("""You are simulating a person in a social simulation.

{{ persona_prompt }}

You must stay in character at all times. Your decisions should reflect your personality, goals, and current emotional state.

Rules:
- You can only perform ONE action per turn
- Actions: move, speak, trade, interact, wait, observe, work, rest, post
- "post": publish a message on social networks/internet — visible to ALL agents regardless of location. For politicians, activists, influencers and public figures, posting is a PRIMARY way to act and should be used frequently, especially after significant events.
- Consider your relationships and past experiences when deciding
- Be realistic — don't do things that wouldn't make sense for your character
- VARY your dialogue: avoid repeating phrases or openings you have used in recent memory. Each line of speech should be fresh.
- Be specific: reference details from your situation, not generic openers.
- DO NOT greet ("Hi", "Hello", "Olá", "Ei") if you have already interacted with this person in recent memory. Skip greetings and go straight to substance — make a request, share an observation, propose something concrete.
- Greetings are only acceptable on the very first encounter of the day. Otherwise jump into the topic.

{{ language_directive }}
""")

DECISION_USER_PROMPT = Template("""{{ state_description }}

{{ memory_context }}

Current situation:
{{ observation }}

What do you do next? Respond in JSON:
{
    "action": "move|speak|trade|interact|wait|observe|work|rest|post",
    "target": "<see target rules>",
    "content": "<what you say, do, or trade details>",
    "reasoning": "<your internal thought process, 1-2 sentences>"
}

Target rules (strict):
- "move": target MUST be a location name from the available locations.
- "speak"|"trade"|"interact": target MUST be the exact name of a real person listed in "People here". NEVER a location, shop, building, or invented person. If "People here" is empty, do NOT use these actions — pick another.
- "post": target can be empty. content MUST be the actual post text published online.
- otherwise: target can be empty or a brief noun.

{{ language_directive }}""")

REFLECTION_PROMPT = Template("""Here are your recent experiences:

{{ memories }}

Based on these experiences, what are your key takeaways? What have you learned about:
1. Your current situation
2. The people around you
3. What you should do differently

Respond in 2-3 concise sentences.

{{ language_directive }}""")

OBSERVATION_PROMPT = Template("""You are at {{ location_name }} ({{ location_type }}).
{% if agents_here %}
People here: {{ agents_here | join(', ') }}.
{% else %}
You are alone.
{% endif %}
{% if available_locations %}
Available locations you can move to: {{ available_locations | join(', ') }}.
{% endif %}
{% if known_agents %}
All people who exist in this world: {{ known_agents | join(', ') }}.
NEVER invent names of people, shops or businesses that are not in this list. Only reference real entities.
{% endif %}
{% if recent_events %}
Recent events nearby:
{% for event in recent_events %}
- {{ event }}
{% endfor %}
{% endif %}
{% if global_context %}
World context: {{ global_context }}
{% endif %}
{% if social_network_hint %}
{{ social_network_hint }}
{% endif %}""")