"""Text players write (their orders, their agent's story) ends up in an LLM prompt. It must stay
their words about their character, never instructions to the model: no "ignore the rules above",
no fake system turns, no breaking the answer's format. What looks like that is cut out, control
characters go, and the quotes we wrap it in can't be closed from inside."""

from __future__ import annotations

import re

CUT = "[…]"

_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f​-‏ -‮⁠-⁯﻿]")
_INJECTION = re.compile(
    r"""
    \b(?:ignore|ignoring|disregard|forget|override|bypass|ignora(?:d|r|n)?|olvida(?:d|r|te)?|
        olv[ií]date|omite|omitid?|salt[aá]te|desobedece)\b
        [^.!?\n]{0,60}?
        \b(?:instructions?|rules?|prompts?|system|guidelines|above|previous|
            instrucci[oó]n(?:es)?|reglas?|normas?|sistema|anterior(?:es)?|arriba|todo)\b
    | \b(?:system|developer|sistema)\s*(?:prompt|message|mensaje|role|rol)\b
    | \b(?:new|updated|nuevas?)\s+(?:instructions?|instrucci[oó]n(?:es)?|reglas|rules)\b
    | \b(?:respond|reply|answer|output|responde|contesta|devuelve)\b[^.!?\n]{0,30}?
        \b(?:only|just|solo|s[oó]lo|[uú]nicamente)\b[^.!?\n]{0,20}?\b(?:with|con|json)\b
    | \byou\s+are\s+(?:now\s+)?(?:an?\s+)?(?:ai|assistant|language\s+model|chatgpt|llm)\b
    | \beres\s+(?:una?\s+)?(?:ia|asistente|modelo\s+de\s+lenguaje|llm)\b
    | `{3,}
    | </?\|?[a-z_]{2,20}\|?>
    | \[/?(?:inst|sys|system)\]
    | \b(?:assistant|system|user)\s*:
    """,
    re.IGNORECASE | re.VERBOSE,
)


def player_text(text: object, limit: int = 400) -> str:
    """A player's free text, safe to quote in a prompt."""
    s = _CONTROL.sub(" ", str(text or ""))
    s = s.replace("«", "'").replace("»", "'").replace('"', "'").replace("{", "(").replace("}", ")")
    s = re.sub(r"\s+", " ", s).strip()
    s = _INJECTION.sub(CUT, s)
    s = re.sub(r"(?:\[…\]\s*){2,}", CUT + " ", s).strip()
    return s[:limit].strip()


_NAME = re.compile(r"[^\w .'\-]", re.UNICODE)


def player_name(text: object, limit: int = 40) -> str:
    """A name: letters, digits, spaces and . ' - only."""
    return re.sub(r"\s+", " ", _NAME.sub("", re.sub(r"\s", " ", str(text or "")))).strip()[:limit]
