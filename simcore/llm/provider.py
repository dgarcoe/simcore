"""LLM provider — unified interface via LiteLLM."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import litellm

from simcore.agents.actions import Action, ActionType
from simcore.llm.cache import ResponseCache

logger = logging.getLogger("simcore.llm")


def parse_plan(text: str) -> list[str]:
    """Steps from {"steps": [...]}, a JSON list, or numbered / bulleted lines."""
    text = text.strip()
    match = re.search(r"\{.*\}|\[.*\]", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            steps = data.get("steps", []) if isinstance(data, dict) else data
            if isinstance(steps, list):
                return [str(s).strip() for s in steps if str(s).strip()][:5]
        except json.JSONDecodeError:
            pass
    lines = [re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", line).strip() for line in text.splitlines()]
    return [line for line in lines if line][:5]


def _amount(value) -> float:
    """Item quantities: positive numbers, 1 when missing or unreadable."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 1.0
    return amount if amount > 0 else 1.0


def _strip_json_artifacts(text: str) -> str:
    """Salvage plain prose from a response that leaked JSON structure.

    Small local models sometimes ignore 'prose only' instructions and return
    { "summary": "..." } or similar. We pull the longest string value out.
    """
    stripped = text.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return stripped
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped  # not valid JSON; return as-is

    # Flatten and collect all string values
    def collect(node):
        found = []
        if isinstance(node, str):
            found.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                found.extend(collect(v))
        elif isinstance(node, list):
            for v in node:
                found.extend(collect(v))
        return found

    strings = [s for s in collect(data) if len(s) > 10]
    if not strings:
        return stripped
    # Prefer concatenating all insight/summary sentences
    return " ".join(strings)

# Suppress litellm verbose logging
litellm.suppress_debug_info = True

# Drop params that aren't supported by the active provider
# (e.g. Ollama doesn't accept presence_penalty/frequency_penalty/response_format).
# Without this, requests fail instead of degrading gracefully.
litellm.drop_params = True


class LLMProvider:
    """Wrapper around LiteLLM for agent decision-making."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        reflection_model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        cache_enabled: bool = True,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
    ):
        self.model = model
        self.reflection_model = reflection_model or model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.frequency_penalty = frequency_penalty
        self.presence_penalty = presence_penalty
        self.cache = ResponseCache() if cache_enabled else None
        self._call_count = 0
        self._cache_hits = 0

    async def decide(self, system_prompt: str, user_prompt: str) -> Action:
        """Ask the LLM to decide an agent's next action."""

        cache_key = f"{system_prompt}|||{user_prompt}" if self.cache else None

        if self.cache and cache_key:
            cached = self.cache.get(cache_key)
            if cached:
                self._cache_hits += 1
                return self._parse_action(cached)

        max_retries = 5
        last_error = None

        for attempt in range(1, max_retries + 1):

            self._call_count += 1

            try:
                response = await litellm.acompletion(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    frequency_penalty=self.frequency_penalty,
                    presence_penalty=self.presence_penalty,
                )

                #logger.warning(
                #    "Attempt %s/%s - FULL RESPONSE=%s",
                #    attempt,
                #    max_retries,
                #    response,
                #)

                content = response.choices[0].message.content

                if content is None:
                    content = ""
                    
                content = content.strip()

                if not content:
                    last_error = "Empty content"
                    logger.warning(
                        "Attempt %s/%s returned empty content",
                        attempt,
                        max_retries,
                    )

                    await asyncio.sleep(0.5 * attempt)
                    continue

                # Validate JSON before caching
                try:
                    action = self._parse_action(content)
                except Exception as parse_error:
                    last_error = f"Parse error: {parse_error}"

                    logger.warning(
                        "Attempt %s/%s parse failed: %s",
                        attempt,
                        max_retries,
                        parse_error,
                    )

                    await asyncio.sleep(0.5 * attempt)
                    continue

                if self.cache and cache_key:
                    self.cache.set(cache_key, content)

                return action

            except Exception as e:
                last_error = str(e)

                logger.warning(
                    "Attempt %s/%s failed: %s",
                    attempt,
                    max_retries,
                    e,
                )

                await asyncio.sleep(0.5 * attempt)

        logger.warning(
            "All %s attempts failed. Returning WAIT action. Last error: %s",
            max_retries,
            last_error,
        )

        return Action(
            type=ActionType.WAIT,
            reasoning=f"LLM failed after {max_retries} attempts: {last_error}",
        )

    async def decide_old(self, system_prompt: str, user_prompt: str) -> Action:
        """Ask the LLM to decide an agent's next action."""
        cache_key = f"{system_prompt}|||{user_prompt}" if self.cache else None

        if self.cache and cache_key:
            cached = self.cache.get(cache_key)
            if cached:
                self._cache_hits += 1
                return self._parse_action(cached)

        self._call_count += 1
        try:
            response = await litellm.acompletion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                frequency_penalty=self.frequency_penalty,
                presence_penalty=self.presence_penalty,
                #response_format={"type": "json_object"},
            )
            
            logger.warning("FULL RESPONSE=%s", response)

            content = response.choices[0].message.content
                        
            if self.cache and cache_key:
                self.cache.set(cache_key, content)
                
            if not content:
                logger.warning(
                    "Empty content returned. Full response=%r",
                    response)

                return Action(
                    type=ActionType.WAIT,
                    reasoning="Empty model response")
                
            return self._parse_action(content)

        except Exception as e:
            logger.warning(f"LLM call failed: {e}. Returning WAIT action.")
            return Action(type=ActionType.WAIT, reasoning=f"LLM error: {e}")

    async def translate(self, text: str, language: str) -> str:
        """Translate text to the given language. Returns original if language is English."""
        if language == "en":
            return text
        from simcore.llm.prompts import language_directive as lang_dir, LANGUAGE_NAMES
        lang_name = LANGUAGE_NAMES.get(language, language)
        self._call_count += 1
        try:
            response = await litellm.acompletion(
                model=self.reflection_model,
                messages=[
                    {"role": "system", "content": (
                        f"You are a translator. Translate the text to {lang_name}. "
                        "Output ONLY the translated text — no quotes, no explanations, no extra content."
                    )},
                    {"role": "user", "content": text},
                ],
                temperature=0.2,
                max_tokens=200,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"Translation failed: {e}")
            return text

    async def generate_narrative(self, data: dict, language: str = "en") -> str:
        """Generate a post-mortem narrative paragraph from simulation summary data."""
        from simcore.llm.prompts import language_directive as lang_dir

        agents_lines = []
        for a in data.get("agents", []):
            line = f"- {a['name']}: mood {a['mood']:+.2f}, energy {a['energy']:.0%}"
            if a.get("reflections"):
                line += f"; reflections: {'; '.join(a['reflections'])}"
            if a.get("relationships"):
                line += f"; relationships: {', '.join(a['relationships'])}"
            agents_lines.append(line)

        events_lines = [
            f"[t{e['tick']}] {e['source'] or e['type']}: {e['summary']}"
            for e in data.get("key_events", [])
            if e.get("summary")
        ]

        system = (
            "You are a journalist writing a vivid chronicle about a social simulation.\n\n"
            "Write 2 to 3 paragraphs of engaging prose that tell the story of what happened. "
            "Highlight character arcs, key events, and how relationships evolved. "
            "Write in a vivid, literary style — like a short story or news chronicle. "
            "Reference specific agent names and concrete moments from the data. "
            "Plain prose only. No headers, no bullet points, no JSON, no markdown.\n\n"
            f"{lang_dir(language)}"
        )
        user = (
            f"The simulation ran for {data.get('ticks', 0)} ticks.\n\n"
            f"Agent final states:\n" + "\n".join(agents_lines) + "\n\n"
            + (f"Notable events:\n" + "\n".join(events_lines) + "\n\n" if events_lines else "")
            + "Write the chronicle now.\n\n"
            f"{lang_dir(language)}"
        )

        self._call_count += 1
        try:
            response = await litellm.acompletion(
                model=self.reflection_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.65,
                max_tokens=800,
            )
            text = response.choices[0].message.content.strip()
            return _strip_json_artifacts(text)
        except Exception as e:
            logger.warning(f"Narrative generation failed: {e}")
            raise

    async def plan(self, persona: str, situation: str, language: str = "en") -> list[str]:
        """A short plan (3-4 steps) for the next hours, from the agent's point of view."""
        from simcore.llm.prompts import language_directive
        self._call_count += 1
        try:
            response = await litellm.acompletion(
                model=self.reflection_model,
                messages=[
                    {"role": "system", "content": f"{persona}\n\n{language_directive(language)}"},
                    {"role": "user", "content": (
                        f"{situation}\n\nWrite your plan for the next hours: 3 or 4 short, concrete steps "
                        "(places, people, things to do), in order. Respond ONLY with JSON: "
                        '{"steps": ["...", "..."]}\n\n' + language_directive(language)
                    )},
                ],
                temperature=self.temperature,
                max_tokens=250,
            )
            return parse_plan(response.choices[0].message.content or "")
        except Exception as e:
            logger.warning(f"Plan failed: {e}")
            return []

    async def reflect(self, memories: str, language: str = "en") -> str:
        """Generate a reflection summary from recent memories."""
        from simcore.llm.prompts import language_directive
        self._call_count += 1
        try:
            response = await litellm.acompletion(
                model=self.reflection_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You write a short personal reflection from the agent's first-person point of view. "
                            "Output ONLY 2 to 3 plain sentences as prose. "
                            "NO JSON, NO braces, NO quotes, NO bullet points, NO keys like 'summary' or 'insight', "
                            "NO markdown, NO headers. Just plain natural sentences starting with 'I...' or equivalent.\n\n"
                            f"{language_directive(language)}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"These are my recent experiences:\n\n{memories}\n\n"
                            "Write 2-3 plain sentences from my point of view about what I learned or noticed. "
                            "Plain prose only, no structured data.\n\n"
                            f"{language_directive(language)}"
                        ),
                    },
                ],
                temperature=0.5,
                max_tokens=180,
            )
            text = response.choices[0].message.content.strip()
            return _strip_json_artifacts(text)
        except Exception as e:
            logger.warning(f"Reflection failed: {e}")
            return ""

    def _parse_action(self, content: str) -> Action:
        """Parse LLM JSON response into an Action."""
        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            raw_action = str(data.get("action", "wait")).strip()
            params = dict(data["parameters"]) if isinstance(data.get("parameters"), dict) else {}
            try:
                action_type = ActionType(raw_action.lower())
            except ValueError:
                if not raw_action or not raw_action.replace("_", "").replace("-", "").isalnum():
                    raise
                # Possibly a scenario action (bite, shoot…): the engine checks it exists.
                action_type = ActionType.CUSTOM
                params["name"] = raw_action.lower()

            def field(key: str, default=""):
                value = data.get(key, params.get(key))
                return default if value is None else value

            return Action(
                type=action_type,
                target=str(field("target")),
                content=str(field("content")),
                reasoning=str(field("reasoning")),
                parameters=params,
                item=str(field("item")).strip(),
                amount=_amount(field("amount", 1)),
                ask_item=str(field("ask_item")).strip(),
                ask_amount=_amount(field("ask_amount", 1)),
                share=str(field("share")).strip(),
                plan_step_done=field("plan_step_done", False) in (True, "true", "yes", 1),
            )
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse action: {e}. Content: {content[:200]}")
            return Action(type=ActionType.WAIT, reasoning=f"Parse error: {e}")

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_calls": self._call_count,
            "cache_hits": self._cache_hits,
            "cache_hit_rate": (
                self._cache_hits / max(1, self._call_count + self._cache_hits)
            ),
        }
