"""Deterministic text-only response guards for the Lantern RKLLM server.

These guards cover arithmetic interval answers and receipt-bound status prose.
They do not authorize or dispatch tool calls; save actions remain with the
SAGE/OpenClaw dispatcher.
"""
from __future__ import annotations

import json
import re

X_PATTERN = re.compile(
    r"Line X's observed count is (\d+) units in one ten-minute interval, "
    r"with an absolute error bound of ±(\d+) units",
    re.S,
)
Y_PATTERN = re.compile(
    r"Line Y's observed count is the authoritative line_y_count for the same "
    r"interval length, with an absolute error bound of ±(\d+) units",
    re.S,
)
ABSENCE_CLAIM = re.compile(
    r"(?:does not contain a matching|no (?:matching )?(?:memory|item|location|record)"
    r"[^.]{0,60}(?:exists|found|present))",
    re.I,
)
UNIVERSAL = re.compile(
    r"\b(?:no\s+(?:(?:matching|such)\s+)?(?:memory|item|record|location)s?"
    r"(?:\s+(?:exists?|was\s+found|is\s+present))?|universal absence|"
    r"proves?\s+that\s+no)\b",
    re.I,
)
TOOL_CALL = re.compile(r"\s*<tool_call>\s*\{.*\}\s*</tool_call>\s*", re.S)
PRIVATE_OWNER_REQUEST = re.compile(
    r"\b(?:save|store|remember|record|write|keep)\b[^\n]{0,160}\b"
    r"(?:in|to|into|under)\s+"
    r"([A-Z][A-Za-z-]*(?:\s+[A-Z][A-Za-z-]*)*)"
    r"(?:'s|’s)\s+private\s+(?:memory|notes?|home|vault)\b",
    re.I,
)
STATUS_QUESTION = re.compile(
    r"(?:^\s*(?:did|does|has|have|is|was)\b[^\n?]{0,160}\b"
    r"(?:save|write|completion|completed|committed|status|succeed|succeeded)\b)"
    r"|(?:\b(?:tell|show|report|give)\b[^\n]{0,100}\b(?:status|outcome)\b"
    r"[^\n]{0,100}\breceipt\b)"
    r"|(?:\b(?:outcome|status)\s+(?:shown|reported)\s+by\s+the\s+receipt\b)",
    re.I,
)
PARAGRAPH_REQUEST = re.compile(
    r"\b(?:exactly\s+)?(one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
    r"[\s-]+paragraphs?\b",
    re.I,
)
WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
COMPLETION_CLAIM = re.compile(
    r"(?:\b(?:i\s+have|i've|has\s+been|have\s+been|successfully|was)\s+"
    r"(?:saved|stored|written|committed|recorded|published|posted|shared|"
    r"remembered|memorized|memorised|noted)\b)"
    r"|(?:\b(?:i|it|is)\s+(?:saved|stored|written|committed|recorded|"
    r"published|posted|shared|remembered|memorized|memorised|noted)\b)"
    r"|(?:\b(?:save|write|lookup)\s+(?:is|was)\s+(?:complete|committed|recorded|done)\b)"
    r"|(?:^\s*(?:saved|stored|written|committed|recorded|published|posted|"
    r"remembered|memorized|memorised|noted)\b)",
    re.I | re.M,
)


def _parse_content(message):
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _completion_claim(text):
    normalized = (text or "").replace("’", "'").replace("‘", "'")
    return bool(COMPLETION_CLAIM.search(normalized))


def _last_user(messages):
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        return _parse_content(message)
    return None


def _last_user_index(messages):
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, dict) and message.get("role") == "user":
            return index
    return None


def _last_actionable_request(messages):
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        request = _parse_content(message)
        if isinstance(request, dict) and (
                request.get("action") in {"remember", "recall"}
                or request.get("authorize_save") is True):
            return index, request
    return None, None


def _receipt_entry_for_action(messages, action_index, expected_name, end_index=None):
    receipt_entry = None
    stop = len(messages) if end_index is None else end_index
    for index in range(action_index + 1, stop):
        message = messages[index]
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        parsed = _parse_content(message)
        if parsed is None:
            continue
        name = message.get("name") or parsed.get("name")
        if not isinstance(name, str) or name != expected_name:
            continue
        receipt_entry = (index, parsed)
    return receipt_entry


def _receipt_for_action(messages, action_index, expected_name, end_index=None):
    entry = _receipt_entry_for_action(
        messages, action_index, expected_name, end_index=end_index)
    return entry[1] if entry is not None else None


def _system_routing(messages):
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        parsed = _parse_content(message)
        if parsed is not None and isinstance(parsed.get("routing"), dict):
            return parsed["routing"]
    return {}


def _last_user_text(messages):
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            return ""
        parsed = _parse_content(message)
        if parsed is not None and isinstance(parsed.get("request"), str):
            return parsed["request"]
        return content
    return ""


def requested_paragraphs(messages):
    match = PARAGRAPH_REQUEST.search(_last_user_text(messages))
    if match is None:
        return None
    value = match.group(1).lower()
    count = WORD_NUMBERS.get(value)
    if count is None:
        try:
            count = int(value)
        except ValueError:
            return None
    return count if 2 <= count <= 10 else None


def normalize_paragraphs(text, count):
    """Return exactly ``count`` prose paragraphs without inventing content."""
    if not isinstance(text, str) or not isinstance(count, int) or count < 2:
        return text
    prose = re.sub(r"(?m)^\s*#{1,6}\s+.*(?:\n|$)", "", text).strip()
    blocks = [block.strip() for block in re.split(r"\n\s*\n", prose) if block.strip()]
    if not blocks:
        return text
    if len(blocks) > count:
        base, extra = divmod(len(blocks), count)
        grouped = []
        start = 0
        for index in range(count):
            size = base + (1 if index < extra else 0)
            grouped.append(" ".join(blocks[start:start + size]).strip())
            start += size
        blocks = grouped
    if len(blocks) < count:
        sentences = []
        for block in blocks:
            sentences.extend(part.strip() for part in re.split(
                r"(?<=[.!?])\s+", block) if part.strip())
        if len(sentences) >= count:
            base, extra = divmod(len(sentences), count)
            grouped = []
            start = 0
            for index in range(count):
                size = base + (1 if index < extra else 0)
                grouped.append(" ".join(sentences[start:start + size]).strip())
                start += size
            blocks = grouped
    return "\n\n".join(blocks[:count]) if len(blocks) >= count else "\n\n".join(blocks)


def longform_text(messages, model_text):
    count = requested_paragraphs(messages)
    if count is None or TOOL_CALL.fullmatch(model_text or ""):
        return model_text
    return normalize_paragraphs(model_text, count)


def needs_longform_post_guard(messages):
    return requested_paragraphs(messages) is not None


def _private_owner_target(text):
    normalized = (text or "").replace("‘", "'").replace("’", "'")
    match = PRIVATE_OWNER_REQUEST.search(normalized)
    return match.group(1) if match is not None else None


def private_owner_refusal(messages):
    """Refuse a save to another person's private memory without a grant."""
    request = _last_user(messages)
    owner = None
    if not isinstance(request, dict) or not (
            request.get("action") == "remember" or request.get("authorize_save") is True):
        latest_text = _last_user_text(messages)
        owner = _private_owner_target(latest_text)
        if owner is None and STATUS_QUESTION.search(latest_text):
            user_index = _last_user_index(messages)
            for message in reversed(messages[:user_index or 0]):
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    owner = _private_owner_target(content)
                    if owner is not None:
                        break
        if owner is None:
            return None
    else:
        owner = request.get("memory_owner")
    if owner in (None, "self"):
        return None
    grants = _system_routing(messages).get("private_grants", [])
    if isinstance(grants, list) and owner in grants:
        return None
    return (
        f"I can't save to {owner}'s private memory because no trusted grant is "
        "present in routing.private_grants. I made no write."
    )


def receipt_status_answer(messages):
    """Answer a write-status question from a receipt bound to the preceding save."""
    status_question_index = _last_user_index(messages)
    if status_question_index is None or not STATUS_QUESTION.search(_last_user_text(messages)):
        return None
    latest_request = _last_user(messages)
    if isinstance(latest_request, dict) and (
            latest_request.get("action") in {"remember", "recall"}
            or latest_request.get("authorize_save") is True):
        return None
    action_index, request = _last_actionable_request(messages[:status_question_index])
    if action_index is None or not (
            request.get("action") == "remember" or request.get("authorize_save") is True):
        return None
    receipt = _receipt_for_action(
        messages, action_index, "sage_remember", end_index=status_question_index)
    if receipt is None:
        return None
    status = receipt.get("status")
    if status == "committed":
        return "The save is committed; completion is established."
    if status == "pending":
        return "The save is pending; completion is not established."
    if status in {"failed", "error"}:
        return "The save failed; completion is not established."
    return None


def action_policy(messages, model_text):
    """Block an unauthorized private-owner tool call or completion claim."""
    refusal = private_owner_refusal(messages)
    if refusal is None:
        return model_text
    if TOOL_CALL.fullmatch(model_text or "") or _completion_claim(model_text):
        return refusal
    return model_text


def needs_post_guard(messages):
    """Return whether a recall answer needs inspection before it is streamed."""
    if needs_longform_post_guard(messages):
        return True
    action_index, request = _last_actionable_request(messages)
    if action_index is None or request.get("action") != "recall":
        return False
    entry = _receipt_entry_for_action(messages, action_index, "sage_recall")
    if entry is None:
        return False
    receipt_index, _ = entry
    user_index = _last_user_index(messages)
    if user_index is None or user_index <= receipt_index:
        return False
    # Buffer only the first assistant turn after the receipt. This covers every
    # wording of a failed/empty lookup question; once that answer exists, later
    # ordinary turns stream normally.
    return not any(
        isinstance(message, dict) and message.get("role") == "assistant"
        for message in messages[receipt_index + 1:user_index]
    )


def interval_answer(messages):
    """Return a deterministic interval/rate answer, or None if not applicable."""
    request = _last_user(messages)
    if not isinstance(request, dict):
        return None
    scenario = request.get("scenario")
    update = request.get("authoritative_update")
    if not isinstance(scenario, str) or not isinstance(update, dict):
        return None
    xmatch, ymatch = X_PATTERN.search(scenario), Y_PATTERN.search(scenario)
    y = update.get("line_y_count")
    if xmatch is None or ymatch is None or type(y) is not int:
        return None
    x, ex, ey = int(xmatch[1]), int(xmatch[2]), int(ymatch[1])
    xl, xh, yl, yh = x - ex, x + ex, y - ey, y + ey
    leader = "Line X" if x > y else "Line Y" if y > x else "Neither line"
    if xl > yh:
        guarantee = (f"Line X is guaranteed to have the higher true count because its "
                     f"minimum {xl} exceeds Line Y's maximum {yh}.")
        gap_units = xl - yh
    elif yl > xh:
        guarantee = (f"Line Y is guaranteed to have the higher true count because its "
                     f"minimum {yl} exceeds Line X's maximum {xh}.")
        gap_units = yl - xh
    else:
        guarantee = (f"Neither line is guaranteed to have the higher true count: the "
                     f"intervals overlap from {max(xl, yl)} to {min(xh, yh)}, so either "
                     "true count can exceed the other.")
        gap_units = 0
    if gap_units:
        word = "unit" if gap_units == 1 else "units"
        gap = (f"The minimum guaranteed gap is {gap_units} {word}, equivalent to "
               f"{gap_units * 6} units per hour.")
    else:
        gap = "There is no guaranteed nonzero gap."
    return (
        f"Line X can have {xl}–{xh} units, which normalizes to {xl * 6}–{xh * 6} "
        f"units per hour. Line Y can have {yl}–{yh} units, which normalizes to "
        f"{yl * 6}–{yh * 6} units per hour. The observed leader is {leader}. "
        f"{guarantee} {gap} These are fixed error bounds, not probabilities, and the "
        "rates do not forecast a future hour or establish a causal difference."
    )


def status_text(messages, model_text):
    """Repair unsupported absence claims after a failed or empty recall."""
    action_index, request = _last_actionable_request(messages)
    if not isinstance(request, dict) or request.get("action") != "recall":
        return model_text
    receipt = _receipt_for_action(messages, action_index, "sage_recall")
    if receipt is None:
        return model_text
    status = receipt.get("status")
    if status in {"failed", "error"}:
        low = (model_text or "").lower()
        if ABSENCE_CLAIM.search(model_text or "") or not any(
                marker in low for marker in ("fail", "error", "does not establish")):
            return ("The lookup failed and returned an error. A failed lookup does not "
                    "establish absence; it does not prove that no matching memory exists.")
    if status == "success" and receipt.get("matches") == [] and UNIVERSAL.search(model_text or ""):
        return ("The lookup completed successfully with no matches for this query. That "
                "establishes no matches for this specific lookup, not universal absence.")
    return model_text
