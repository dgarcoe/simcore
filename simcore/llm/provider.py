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


# Common names models use for the private-talk actions.
ACTION_ALIASES = {
    "susurrar": "whisper", "sussurrar": "whisper", "whisper_to": "whisper", "murmurar": "whisper",
    "invitar": "invite", "convidar": "invite", "invite_to": "invite", "take_aside": "invite",
    "seguir": "follow", "follow_me": "follow", "follow_to": "follow",
    "buscar": "find", "search": "find", "look_for": "find", "find_person": "find", "procurar": "find",
}


_FENCE = re.compile(r"```(?:json)?\s*(.*?)(?:```|$)", re.DOTALL | re.IGNORECASE)
_SMART_QUOTES = {0x201C: '"', 0x201D: '"', 0x201E: '"', 0x00AB: '"', 0x00BB: '"'}
_LITERALS = {"True": "true", "False": "false", "None": "null"}


def _scan_object(text: str) -> str | None:
    """The first {...} in the text (braces inside strings ignored), closed if it was cut off."""
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    # Truncated answer (max tokens): close what is open.
    return text[start:] + ('"' if in_str else "") + "}" * depth


def _outside_strings(text: str, fix) -> str:
    """Apply ``fix`` to the parts of the text that are not inside double-quoted strings."""
    out, buf, in_str, esc = [], [], False, False
    for c in text:
        if in_str:
            buf.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
                out.append("".join(buf))
                buf = []
        elif c == '"':
            out.append(fix("".join(buf)))
            buf, in_str = [c], True
        else:
            buf.append(c)
    out.append("".join(buf) if in_str else fix("".join(buf)))
    return "".join(out)


def _fix_segment(seg: str) -> str:
    seg = re.sub(r"(//|#)[^\n]*", "", seg)  # comments
    seg = re.sub(r"\b(True|False|None)\b", lambda m: _LITERALS[m.group(1)], seg)
    # 'single quotes'
    seg = re.sub(r"'([^'\n]*)'", lambda m: json.dumps(m.group(1), ensure_ascii=False), seg)
    seg = re.sub(r"([{,]\s*)([A-Za-z_]\w*)(\s*:)", r'\1"\2"\3', seg)  # unquoted keys
    seg = re.sub(r",(\s*[}\]])", r"\1", seg)  # trailing commas

    def quote_value(m: re.Match) -> str:
        value = m.group(2).strip()
        if value in ("true", "false", "null"):
            return m.group(0)
        return f"{m.group(1)}{json.dumps(value, ensure_ascii=False)}{m.group(3)}"

    # "target": Lola,  →  "target": "Lola",
    return re.sub(r"(:\s*)([^\s\"{\[\d\-,}\]][^,\n}\]]*?)(\s*(?:[,\n}\]]|$))", quote_value, seg)


def _load_json_object(text: str) -> dict:
    """A JSON object from a model's answer: tolerates code fences, text around it, a cut-off
    end and the usual small-model slips (unquoted values, smart quotes, True/None, comments,
    trailing commas, single quotes). Raises json.JSONDecodeError / ValueError otherwise."""
    text = (text or "").strip()
    fence = _FENCE.search(text)
    if fence and "{" in fence.group(1):
        text = fence.group(1)
    candidate = _scan_object(text) or text
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as first:
        repaired = _scan_object(_outside_strings(text.translate(_SMART_QUOTES), _fix_segment))
        try:
            data = json.loads(repaired or "")
        except json.JSONDecodeError:
            raise first from None
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def parse_plan(text: str) -> list[str]:
    """Steps from {"steps": [...]}, a JSON list, or numbered / bulleted lines."""
    text = text.strip()
    match = re.search(r"\{.*\}|\[.*\]", text, re.DOTALL)
    if match:
        try:
            found = match.group(0)
            data = _load_json_object(found) if found.startswith("{") else json.loads(found)
            steps = data.get("steps", []) if isinstance(data, dict) else data
            if isinstance(steps, list):
                return [str(s).strip() for s in steps if str(s).strip()][:5]
        except (json.JSONDecodeError, ValueError):
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
                try:
                    action = self._parse_action_strict(cached)
                    self._cache_hits += 1
                    return action
                except (json.JSONDecodeError, ValueError):
                    pass  # an unreadable answer cached by an older version: ask again

        max_retries = 5
        max_parse_failures = 3  # unreadable answers get a fresh try, but not forever
        parse_failures = 0
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
                    **self._json_mode(),
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

                # Validate JSON before caching: only readable answers are cached.
                try:
                    action = self._parse_action_strict(content)
                except (json.JSONDecodeError, ValueError) as parse_error:
                    last_error = f"Parse error: {parse_error}"
                    parse_failures += 1
                    logger.warning(
                        "Attempt %s/%s parse failed: %s. Content: %s",
                        attempt,
                        max_retries,
                        parse_error,
                        content[:200],
                    )
                    if parse_failures >= max_parse_failures:
                        break
                    await asyncio.sleep(0.2)
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

        if last_error and last_error.startswith("Parse error:"):
            return Action(type=ActionType.WAIT, reasoning=last_error)
        return Action(
            type=ActionType.WAIT,
            reasoning=f"LLM failed after {max_retries} attempts: {last_error}",
        )

    def _json_mode(self) -> dict[str, Any]:
        """Ollama can be told to answer with valid JSON only (its "format": "json")."""
        if self.model.startswith(("ollama/", "ollama_chat/")):
            return {"response_format": {"type": "json_object"}}
        return {}

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

    async def chronicle(self, moments: list[str], cast: list[str], previous: list[str],
                        language: str = "en") -> tuple[str, str]:
        """One chapter of the run's chronicle: (title, 2-4 sentences) from its key moments."""
        from simcore.llm.prompts import language_directive

        self._call_count += 1
        system = (
            "You are the narrator of a story told chapter by chapter. Write the next chapter from the "
            "moments given: 2 to 4 vivid sentences, past tense, naming the people involved, plus a short "
            'evocative title. Respond ONLY with JSON: {"title": "...", "text": "..."}\n\n'
            f"{language_directive(language)}"
        )
        user = (
            f"Characters: {'; '.join(cast)}\n"
            + (f"Previous chapters: {'; '.join(previous)}\n" if previous else "")
            + "Moments of this chapter:\n" + "\n".join(moments) + "\n\n" + language_directive(language)
        )
        response = await litellm.acompletion(
            model=self.reflection_model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.7,
            max_tokens=350,
            **self._json_mode(),
        )
        content = response.choices[0].message.content or ""
        try:
            data = _load_json_object(content)
            return str(data.get("title", "")).strip(), str(data.get("text", "")).strip()
        except (json.JSONDecodeError, ValueError):
            return "", _strip_json_artifacts(content)

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
        chapters_lines = [f"Chapter {c['n']}: {c['title']} — {c['text']}" for c in data.get("chapters", [])]

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
            + ("The story so far, chapter by chapter:\n" + "\n".join(chapters_lines) + "\n\n"
               if chapters_lines else "")
            + (f"Notable events:\n" + "\n".join(events_lines) + "\n\n" if events_lines else "")
            + (f"How it ended: {data['ending']}\n\n" if data.get("ending") else "")
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
        """Parse LLM JSON response into an Action (a WAIT with the reason if it can't)."""
        try:
            return self._parse_action_strict(content)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse action: {e}. Content: {content[:200]}")
            return Action(type=ActionType.WAIT, reasoning=f"Parse error: {e}")

    def _parse_action_strict(self, content: str) -> Action:
        """Parse LLM JSON response into an Action; raises if it can't be read."""
        data = _load_json_object(content)
        raw_action = str(data.get("action", "wait")).strip()
        params = dict(data["parameters"]) if isinstance(data.get("parameters"), dict) else {}
        raw_action = ACTION_ALIASES.get(raw_action.lower(), raw_action)
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
            target=", ".join(map(str, field("target"))) if isinstance(field("target"), list)
            else str(field("target")),
            content=str(field("content")),
            reasoning=str(field("reasoning")),
            parameters=params,
            item=str(field("item")).strip(),
            amount=_amount(field("amount", 1)),
            ask_item=str(field("ask_item")).strip(),
            ask_amount=_amount(field("ask_amount", 1)),
            share=str(field("share")).strip(),
            place=str(field("place")).strip(),
            plan_step_done=field("plan_step_done", False) in (True, "true", "yes", 1),
        )

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_calls": self._call_count,
            "cache_hits": self._cache_hits,
            "cache_hit_rate": (
                self._cache_hits / max(1, self._call_count + self._cache_hits)
            ),
        }
