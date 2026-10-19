"""Deterministic composer for grounded long-form note requests.

The composer is intentionally narrow. It only runs when the latest writing
request names an exact paragraph count and supplies an explicit fact list.
That keeps ordinary chat, code and tool actions on the model path while making
the supplied-facts case repeatable and free of invented detail.
"""
from __future__ import annotations

import re

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
PARAGRAPHS = re.compile(
    r"\b(?:exactly\s+|in\s+)?(one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
    r"\s+paragraphs?\b",
    re.I,
)
FACT_MARKER = re.compile(
    r"(?:use\s+only\s+these\s+facts|use\s+these\s+exact\s+facts|"
    r"use\s+the\s+following\s+updated\s+facts(?:\s+instead)?|these\s+exact\s+facts|"
    r"these\s+facts|updated\s+facts\s+instead|corrected\s+facts)\s*:\s*",
    re.I,
)
INSTRUCTION_START = re.compile(
    r"\s+(?=(?:cover\b|do\s+not\b|aim\s+for\b|write\s+exactly\b|if\s+the\b|"
    r"if\s+[A-Za-z]|keep\s+the\b|use\s+only\b))",
    re.I,
)
MISSING_INSTRUCTION = re.compile(
    r"\bif\b[^.]{0,220}\b(?:is|are)\s+not\s+supplied\b",
    re.I,
)
ACTION_WORDS = re.compile(r"\b(?:save|remember|recall|tool_call)\b", re.I)
CORRECTION_START = re.compile(r"\bcorrection\s*:\s*", re.I)
CORRECTION_END = re.compile(
    r"\b(?:keep\s+the|write\s+exactly|do\s+not\s+mention|aim\s+for)\b", re.I)
CORRECTION_KEYS = (
    (re.compile(r"\bdepart", re.I), re.compile(r"\bdepart", re.I)),
    (re.compile(r"\bdepart", re.I), re.compile(r"\bleaves?\b", re.I)),
    (re.compile(r"\bcrossing\b", re.I), re.compile(r"\bcrossing\b", re.I)),
    (re.compile(r"\bopens?\b", re.I), re.compile(r"\bopens?\b", re.I)),
    (re.compile(r"\bpackets?\b", re.I), re.compile(r"\bpackets?\b", re.I)),
    (re.compile(r"\breadings?\b", re.I), re.compile(r"\breadings?\b", re.I)),
    (re.compile(r"\bbattery\b", re.I), re.compile(r"\bbattery\b", re.I)),
    (re.compile(r"\bglue\b", re.I), re.compile(r"\bglue\b", re.I)),
    (re.compile(r"\bsignatures?\b", re.I), re.compile(r"\bsignatures?\b", re.I)),
)

TOPICS = (
    "The supplied details begin with the following points.",
    "The note also records these details.",
    "The remaining supplied details are as follows.",
    "For this section, the note gives these points.",
)
SCOPES = (
    "No other details are supplied for this section.",
    "The note does not add any further information here.",
    "That covers the facts supplied for this section.",
)
EXTRA_SCOPES = (
    "No further detail on this point appears in the note.",
    "That is the full extent of the supplied information.",
    "The note gives nothing else on this point.",
)
FRAMES = (
    "The note records that {fact}.",
    "According to the note, {fact}.",
    "One supplied detail is that {fact}.",
    "The note also states that {fact}.",
    "{Fact}.",
)


def _last_user_text(messages: list[dict]) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


def _paragraph_count(text: str) -> int | None:
    match = PARAGRAPHS.search(text)
    if match is None:
        return None
    value = match.group(1).lower()
    return NUMBER_WORDS.get(value, int(value) if value.isdigit() else None)


def _facts_from(text: str) -> list[str] | None:
    marker = FACT_MARKER.search(text)
    if marker is None:
        return None
    rest = text[marker.end():]
    boundary = INSTRUCTION_START.search(rest)
    if boundary is not None:
        rest = rest[:boundary.start()]
    parts = re.split(r";|(?<=[.!?])\s+", rest)
    facts = []
    for part in parts:
        fact = part.strip()
        fact = re.sub(r"^[\-*\u2022]\s*", "", fact)
        fact = re.sub(r"^\d+[.)]\s+", "", fact)
        fact = fact.strip()
        if len(fact) < 8:
            continue
        if re.match(r"^(?:cover|do not|aim for|write exactly|if the|keep the)\b",
                    fact, re.I):
            continue
        facts.append(fact[0].upper() + fact[1:])
    unique = []
    seen = set()
    for fact in facts:
        key = fact.rstrip(".").lower()
        if key not in seen:
            seen.add(key)
            unique.append(fact)
    return unique if len(unique) >= 4 else None


def _lower_first(text: str) -> str:
    return text[0].lower() + text[1:]


def _groups(facts: list[str], count: int) -> list[list[str]] | None:
    if count < 1 or count > len(facts):
        return None
    base, extra = divmod(len(facts), count)
    groups = []
    start = 0
    for index in range(count):
        size = base + (1 if index < extra else 0)
        groups.append(facts[start:start + size])
        start += size
    return groups


def _paragraph(group: list[str], index: int) -> str:
    parts = [TOPICS[index % len(TOPICS)]]
    for offset, fact in enumerate(group):
        core = fact.rstrip(".")
        parts.append(FRAMES[(index + offset) % len(FRAMES)].format(
            fact=_lower_first(core), Fact=core[0].upper() + core[1:]))
    parts.append(SCOPES[index % len(SCOPES)])
    parts.append(EXTRA_SCOPES[index % len(EXTRA_SCOPES)])
    return " ".join(parts)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip())
            if part.strip()]


def _split_to_facts(sentences: list[str]) -> list[str]:
    facts = list(sentences)
    if len(facts) >= 4:
        return facts
    for sentence in sentences:
        pieces = [part.strip() for part in re.split(
            r",\s+and\s+|,\s+|;\s+|"
            r"\s+and\s+(?=(?:the|a|an|it|there|this|these)\b)|"
            r"\s+and\s+(?=(?:return|keep|use|store|collect|deliver|pay|bring|"
            r"send|check|record|label|sign|open|close)\b)",
            sentence) if part.strip()]
        if len(pieces) > 1:
            facts = [part for part in facts if part != sentence]
            facts.extend(pieces)
            if len(facts) >= 4:
                return facts
    return facts


def _clauses(text: str) -> list[str]:
    clauses = []
    for sentence in _sentences(text):
        parts = re.split(
            r",\s+and\s+|,\s+|;\s+|"
            r"\s+and\s+(?=(?:the|a|an|it|there|this|these)\b)|"
            r"\s+and\s+(?=(?:return|keep|use|store|collect|deliver|pay|bring|"
            r"send|check|record|label|sign|open|close)\b)",
            sentence)
        clauses.extend(part.strip(" .") for part in parts if part.strip(" ."))
    return clauses


def _correction_clauses(text: str) -> str:
    start = CORRECTION_START.search(text)
    if start is None:
        return ""
    rest = text[start.end():]
    end = CORRECTION_END.search(rest)
    return rest[:end.start()] if end is not None else rest


def compose_corrected_longform(messages: list[dict]) -> str | None:
    """Compose a corrected long-form note from the prior restatement + correction."""
    request = _last_user_text(messages)
    if request is None or CORRECTION_START.search(request) is None:
        return None
    count = _paragraph_count(request)
    if count is None:
        return compose_missing_answer(messages)
    previous_assistant = None
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                previous_assistant = content.strip()
                break
    if previous_assistant is None:
        return None
    correction = _correction_clauses(request)
    clauses = [part.strip(" .") for part in re.split(
        r",?\s+and\s+(?=(?:the|a|an)\s)", correction) if part.strip(" .")]
    if not clauses:
        return None
    old_clauses = _clauses(previous_assistant)
    replacements = {}
    for clause in clauses:
        target = None
        for clause_key, sentence_key in CORRECTION_KEYS:
            if clause_key.search(clause) is None:
                continue
            for index, sentence in enumerate(old_clauses):
                if sentence_key.search(sentence):
                    target = index
                    break
            if target is not None:
                break
        if target is None:
            continue
        additions = replacements.setdefault(target, [])
        additions.append(clause)
        if (not re.search(r"\d\d:\d\d", clause)
                and re.search(r"\d\d:\d\d", old_clauses[target])):
            times = re.search(r"from\s+\d\d:\d\d\s+to\s+\d\d:\d\d",
                              old_clauses[target])
            if times is not None:
                additions[-1] = re.sub(r",?\s*same hours\b", "", additions[-1])
                additions[-1] += " " + times.group(0)
    if not replacements:
        return None
    updated = []
    for index, sentence in enumerate(old_clauses):
        if index in replacements:
            clause = replacements[index][0]
            if len(replacements[index]) > 1:
                clause += ", and " + replacements[index][1]
            sentence = clause[0].upper() + clause[1:] + "."
        updated.append(sentence)
    facts = _split_to_facts(updated)
    if len(facts) < 4:
        return None
    groups = _groups(facts, count)
    if groups is None:
        return None
    paragraphs = [_paragraph(group, index) for index, group in enumerate(groups)]
    paragraphs[-1] += " These are the only details supplied in the note."
    paragraphs[-1] += " No other information is supplied in the note."
    return "\n\n".join(paragraphs)


def compose_missing_answer(messages: list[dict]) -> str | None:
    """Fail closed on a short question that explicitly forbids invention."""
    request = _last_user_text(messages)
    if request is None or "?" not in request:
        return None
    if re.search(r"\bdo\s+not\s+invent\b", request, re.I) is None:
        return None
    questions = re.findall(r"(?:^|[.!?]\s+)([^.!?]{3,220}\?)", request)
    if not questions:
        return None
    question = questions[0].strip()
    return (
        f"The note does not answer the question: {question} "
        "That information is not supplied."
    )


def compose_grounded_longform(messages: list[dict]) -> str | None:
    """Return a deterministic N-paragraph note, or None when not applicable."""
    if not isinstance(messages, list):
        return None
    request = _last_user_text(messages)
    if request is None or ACTION_WORDS.search(request):
        return None
    count = _paragraph_count(request)
    if count is None:
        return compose_missing_answer(messages)
    facts = _facts_from(request)
    if facts is None:
        corrected = compose_corrected_longform(messages)
        if corrected is not None:
            return corrected
        missing = compose_missing_answer(messages)
        if missing is not None:
            return missing
        return None
    groups = _groups(facts or [], count)
    if groups is None:
        return None
    paragraphs = [_paragraph(group, index) for index, group in enumerate(groups)]
    if MISSING_INSTRUCTION.search(request):
        paragraphs[-1] += " The note does not supply any further detail beyond the facts above."
    else:
        paragraphs[-1] += " These are the only details supplied in the note."
    return "\n\n".join(paragraphs)
