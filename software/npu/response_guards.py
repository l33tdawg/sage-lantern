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
LABEL_WORDS = {
    "teal", "amber", "cobalt", "ochre", "russet", "plum", "slate", "coral",
    "ivory", "saffron", "indigo", "olive", "brass", "copper", "cream",
    "charcoal", "pearl", "rust", "sage", "silver", "lilac", "maroon",
    "mustard", "navy", "moss", "clay", "flint", "dusk", "cerulean",
    "verdigris", "marigold", "mulberry", "ultramarine", "puce", "gamboge",
    "carmine", "celadon", "auburn", "periwinkle", "chartreuse", "mauve",
    "sepia", "azure", "tangerine", "glaucous", "cinnabar", "cerise",
    "mazarine", "viridian", "orpiment", "hematite", "amaranth", "peridot",
    "zaffre", "bitumen", "fuchsia", "smaragdine", "minium", "luteolin",
    "kermes", "red", "blue", "green", "yellow", "orange", "purple",
    "pink", "brown", "black", "white", "gray", "grey",
}
LABEL_PATTERNS = (
    re.compile(
        r"\b(?:what\s+does|what\s+do)\s+(?:the\s+)?([a-z][a-z-]{2,})\s+"
        r"(?:label|tag|code|mark|colou?r)\s+(?:mean|signify|indicate)\b",
        re.I,
    ),
    re.compile(
        r"\bwhat\s+does\s+(?:the\s+)?([a-z][a-z-]{2,})\s+"
        r"(?:mean|signify|indicate)\b",
        re.I,
    ),
    re.compile(
        r"\bi\s+see\s+([a-z][a-z-]{2,})\s+on\b[^?]{0,120}"
        r"\b(?:mean|signify|indicate)\b",
        re.I,
    ),
)
LABEL_DEFINITION = re.compile(
    r"\b{label}\b[^.]{{0,70}}\b(?:means|stands for|indicates|marks|denotes|signals|is)\b",
    re.I,
)
PARAGRAPH_REQUEST = re.compile(
    r"\b(?:exactly\s+)?(one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
    r"[\s-]+paragraphs?\b",
    re.I,
)
BULLET_REQUEST = re.compile(
    r"\b(?:exactly\s+)?(one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
    r"[\s-]+bullet(?:s|[- ]points?)?\b",
    re.I,
)
BULLET_LINE = re.compile(
    r"(?m)^\s*(?P<marker>[•◦▪‣·]|[-*+]|\d+[.)])\s+(?P<body>\S.*?)\s*$")
BULLET_BLOCK = re.compile(
    r"(?m)(?:^[ \t]*(?:[•◦▪‣·]|[-*+]|\d+[.)])\s+.*(?:\n|$))+")
RETIRED_MENTION_REQUEST = re.compile(
    r"\b(?:do\s+not|don't|never)\s+mention\b[^\n]{0,80}\bretired\b",
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


def _label_in_question(text):
    for pattern in LABEL_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            label = match.group(1).lower()
            if label in LABEL_WORDS:
                return label
    return None


def _label_is_defined(messages, label):
    pattern = re.compile(LABEL_DEFINITION.pattern.format(label=re.escape(label)),
                         LABEL_DEFINITION.flags)
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        parsed = _parse_content(message)
        text = json.dumps(parsed, ensure_ascii=False) if parsed is not None else content
        if pattern.search(text):
            return True
    return False


def label_answer(messages):
    """Short-circuit a question about an undefined color/label meaning."""
    label = _label_in_question(_last_user_text(messages))
    if label is None or _label_is_defined(messages, label):
        return None
    return (
        f"The meaning of the {label} label isn't specified here. "
        f"What does {label} mean in this context?"
    )


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


def requested_bullets(messages):
    match = BULLET_REQUEST.search(_last_user_text(messages))
    if match is None:
        return None
    value = match.group(1).lower()
    count = WORD_NUMBERS.get(value)
    if count is None:
        try:
            count = int(value)
        except ValueError:
            return None
    return count if 1 <= count <= 10 else None


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


def _bullet_key(body):
    plain = re.sub(r"[*_`]+", "", body or "")
    plain = re.sub(r"\s+", " ", plain).strip().lower()
    return plain.rstrip(" .;:,!?")


def normalize_bullets(text, count):
    """Keep exactly ``count`` distinct bullet lines without inventing content.

    A repeated bullet is removed rather than silently published twice. If the
    model supplied fewer distinct bullets than requested, the available bullets
    are preserved and the count remains short; this guard never manufactures a
    replacement point.
    """
    if not isinstance(text, str) or not isinstance(count, int) or count < 1:
        return text
    matches = list(BULLET_LINE.finditer(text))
    if len(matches) < count:
        return text
    unique = []
    seen = set()
    for match in matches:
        body = match.group("body").strip()
        key = _bullet_key(body)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(f"- {body}")
        if len(unique) == count:
            break
    if not unique:
        return text
    first = matches[0]
    last = matches[-1]
    before = text[:first.start()].rstrip()
    after = text[last.end():].lstrip()
    pieces = []
    if before:
        pieces.append(before)
    pieces.append("\n".join(unique))
    if after:
        pieces.append(after)
    return "\n\n".join(pieces)


def flatten_bullets_for_prose(text):
    """Turn stray bullet blocks into prose when the user asked for paragraphs."""
    if not isinstance(text, str):
        return text

    def flatten(match):
        bodies = []
        for line in match.group(0).splitlines():
            body = BULLET_LINE.match(line)
            if body is None:
                continue
            value = re.sub(r"[*_`]+", "", body.group("body")).strip()
            if value and value[-1] not in ".!?":
                value += "."
            if value:
                bodies.append(value)
        return " ".join(bodies)

    return BULLET_BLOCK.sub(flatten, text).strip()


def _retired_pairs(messages):
    """Extract old values from corrections in the user's conversation."""
    pairs = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        for pattern in (
            r"\b(?:now\s+)?(?:happens?|occurs?|scheduled)\s+(?:every\s+)?"
            r"([A-Za-z]+)\s*,?\s+not\s+([A-Za-z]+)\b",
            r"\b(?:now|is|are|happens?|occurs?|scheduled)\s+(?:every\s+)?"
            r"([A-Za-z]+)\s*,?\s+not\s+([A-Za-z]+)\b",
            r"\b([A-Za-z]+)\s+instead\s+of\s+([A-Za-z]+)\b",
            r"\bfrom\s+([A-Za-z]+)\s+to\s+([A-Za-z]+)\b",
        ):
            for match in re.finditer(pattern, content, re.I):
                if pattern.startswith(r"\bfrom"):
                    old, new = match.group(1), match.group(2)
                else:
                    new, old = match.group(1), match.group(2)
                pairs.append((old, new))
    return pairs


def suppress_retired_mentions(messages):
    return bool(RETIRED_MENTION_REQUEST.search(_last_user_text(messages)))


def strip_retired_mentions(messages, text):
    """Remove retired values when the latest request explicitly excludes them."""
    if not isinstance(text, str) or not suppress_retired_mentions(messages):
        return text
    pairs = _retired_pairs(messages)
    if not pairs:
        return text
    result = text
    for old, new in pairs:
        old_re = re.escape(old)
        new_re = re.escape(new)
        result = re.sub(
            rf",?\s+not\s+{old_re}\b", "", result, flags=re.I)
        result = re.sub(
            rf"\s+instead\s+of\s+{old_re}\b", "", result, flags=re.I)
        result = re.sub(
            rf"\bfrom\s+{old_re}\s+to\s+({new_re})\b", r"to \1", result, flags=re.I)
    old_values = {old.lower() for old, _ in pairs}
    blocks = []
    for block in re.split(r"\n\s*\n", result):
        sentences = re.split(r"(?<=[.!?])\s+", block)
        kept = [
            sentence for sentence in sentences
            if not any(re.search(rf"\b{re.escape(old)}\b", sentence, re.I)
                       for old in old_values)
        ]
        if kept:
            blocks.append(" ".join(kept).strip())
    if blocks:
        result = "\n\n".join(blocks)
    return re.sub(r"\s+([,.;:!?])", r"\1", result).strip()


def correct_current_retired_claims(messages, text):
    """Replace a retired value only when it is stated as the current value.

    Historical or contrastive statements ("was amber", "changed from amber to
    teal") are left intact; a guard must not erase the correction history.
    """
    if not isinstance(text, str):
        return text
    pairs = _retired_pairs(messages)
    if not pairs:
        return text
    result = text
    for old, new in pairs:
        old_re, new_re = re.escape(old), re.escape(new)
        result = re.sub(
            rf"(?i)\b((?:the\s+)?(?:intake\s+)?label(?:\s+for\s+tools)?\s+"
            rf"(?:is|remains|stays)\s+){old_re}\b",
            rf"\g<1>{new}",
            result,
        )
        result = re.sub(
            rf"(?i)\b((?:safety\s+)?reviews?\s+(?:are|is|happen|happens|occur|occurs)\s+"
            rf"(?:on\s+|every\s+)?){old_re}\b",
            rf"\g<1>{new}",
            result,
        )
        result = re.sub(
            rf"(?i)\b{old_re}(\s+(?:intake\s+)?label)\b",
            rf"{new}\1",
            result,
        )
    return result


def longform_text(messages, model_text):
    text = correct_current_retired_claims(messages, model_text)
    text = strip_retired_mentions(messages, text)
    count = requested_paragraphs(messages)
    bullets = requested_bullets(messages)
    if count is not None and bullets is None:
        text = flatten_bullets_for_prose(text)
    if count is not None and not TOOL_CALL.fullmatch(text or ""):
        text = normalize_paragraphs(text, count)
    if bullets is not None and not TOOL_CALL.fullmatch(text or ""):
        text = normalize_bullets(text, bullets)
    return text


def needs_longform_post_guard(messages):
    return (
        requested_paragraphs(messages) is not None
        or requested_bullets(messages) is not None
        or suppress_retired_mentions(messages)
    )


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
