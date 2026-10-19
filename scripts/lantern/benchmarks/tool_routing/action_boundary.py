"""Disabled English request boundary: whole instructions, never keyword routing.

The caller passes ONLY the submitted current instruction, not recalled records,
examples, dialogue concatenations or interim ASR. Grammar coverage is deliberately
bounded. This is a semantic proposal, not authentication or execution authority.
"""
from dataclasses import dataclass
import hashlib
import re

from contract import InvalidPlan, validate_plan
from literal_note import literal_note


@dataclass(frozen=True)
class RequestedAction:
    operation: str
    target: str
    subject: str
    fields: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CurrentInstruction:
    text: str
    sha256: str
    actions: tuple[RequestedAction, ...]
    reason: str

    @property
    def intent(self):
        operations = tuple(a.operation for a in self.actions)
        return {(): "answer", ("search",): "search", ("save",): "save",
                ("status",): "status", ("search", "status"): "parallel_reads"}[operations]

    def calls(self):
        result = []
        for action in self.actions:
            if action.operation == "search":
                result.append({"name": "search_memory", "arguments": {"query": self.text}})
            elif action.operation == "status":
                result.append({"name": "get_device_status", "arguments": {}})
            else:
                result.append({"name": "save_private_note", "arguments": dict(action.fields)})
        if result:
            validate_plan(result)
        return result


FLAGS = re.IGNORECASE
LOCAL = (r"(?:this (?:Lantern|device|unit)|the Lantern (?:I am using|in my hand)|"
         r"the device (?:running this conversation|I'm talking to))")
READING = r"(?:battery(?: percentage| level| reading)?|charge|remaining charge|internet(?: availability| access| connection| status)?|connectivity|network(?: status| connection| availability)?)"
PREFIX = re.compile(r"^(?:please |could you |would you |uh, |so, |just |only )", FLAGS)
# These full, fixed constraints suppress another operation; they do not select
# an action. Negation of the selected operation is never stripped.
CONSTRAINTS = {
    "search": (
        r"(?:do not|don't) check (?:connectivity|the battery)\. (?:only )?",
        r"(?:do not add a note; just |no new notes, thanks; I only want )",
    ),
    "status": (
        r"(?:don't search any notes; just |no saving, please\. )",
    ),
    "parallel_reads": (r"(?:do not write anything\. |do not write anything; |no writes\. |without storing anything, |only read: )",),
}
SUFFIXES = (
    r",? (?:without saving anything new|without changing settings|with no writes)",
    r"; (?:do not add a new one|make no changes|save neither|do not change either|do not save either)",
    r"\. (?:leave it as it is|do not save either)",
)


def _surface(text):
    text = text.strip()
    # Remove one terminal punctuation mark; note bodies bypass this function.
    if text.endswith((".", "?")):
        text = text[:-1]
    for _ in range(2):
        text = PREFIX.sub("", text, count=1)
    return text


def _topic(text):
    # Refuse unresolved clauses/targets rather than turn them into a search.
    # Noun phrases outside this small grammar remain answer/clarification.
    if not text.strip() or len(text.encode()) > 512:
        return None
    if re.search(r"[.!?;:\n\r\"'<>]", text):
        return None
    if re.search(r"\b(?:and|or|then|but|unless|if|when|instead|not|don't|do|no|using|without|from|tomorrow|later|plus|alongside|together|ignore|pretend|override|permission|permissions|account|owner|owners|teammate|teammates|coworker|coworkers|sibling|siblings|manager|managers|neighbour|neighbor|read|get|find|search|retrieve|fetch|save|create|delete|remove|erase|send|share|publish|change|switch|install)\b", text, FLAGS):
        return None
    return text


SEARCH = (
    r"(?:find|retrieve|fetch|bring back|bring up|pull up|read|show|consult|check) (?:me )?(?:the )?(?:my |the private note I saved about )(?P<subject>.+)",
    r"(?:search|look in|look up|look through|check|consult) my (?:own )?(?:saved |personal |private )?(?:notes|memories) (?:for|about) (?P<subject>.+)",
    r"(?:what did I (?:previously )?(?:save|record)|what did I put in my own notes|what did I write in private memory) about (?P<subject>.+)",
    r"(?:what does my own saved entry say|what was in my private notes) about (?P<subject>.+)",
    r"remind me (?:of my saved plan for|what I previously recorded about) (?P<subject>.+)",
    r"in my private notes, what was (?P<subject>.+)",
)


def _search(text):
    text = _surface(text)
    for pattern in SEARCH:
        match = re.fullmatch(pattern, text, FLAGS)
        if not match:
            continue
        subject = match["subject"]
        # The direct-object grammar must explicitly refer to a saved record.
        if pattern == SEARCH[0]:
            scoped = (
                r"(?:(?:old|existing|private|personal|stored|saved|recorded|own) )*(?:note|entry|record|memory|notes|memories) (?:about|on|of|for) .+",
                r"(?:saved|recorded|stored) .+",
                r"(?:private |personal |old |existing )?.+ (?:note|entry|details|instructions|plan)",
            )
            if not any(re.fullmatch(p, subject, FLAGS) for p in scoped) and "the private note I saved about " not in text.lower():
                continue
        if _topic(subject) is not None:
            return RequestedAction("search", "current_owner_private_sage", subject)
    return None


STATUS = (
    rf"(?:read|check|tell me|report|show(?: me)?|give(?: me)?|get|read out) (?:the )?(?:current |reported |present |live |actual )?{READING} (?:of|on|for|from|in) {LOCAL}(?: (?:now|right now|at the moment|only))?",
    rf"(?:read|check|tell me|report|show(?: me)?|give(?: me)?|get|read out) {LOCAL}'s (?:current |reported |present |live |actual )?{READING}(?: (?:now|right now|at the moment|only))?",
    rf"(?:what is|what's) (?:the )?(?:current |reported )?{READING} (?:of|on|for) {LOCAL}(?: (?:now|right now))?",
    rf"what battery percentage does {LOCAL} (?:currently )?report",
    rf"(?:are we(?: actually)?|am I) (?:connected to the internet|online) (?:on|with) {LOCAL}(?: (?:now|right now|at the moment))?",
    rf"(?:is {LOCAL}|does {LOCAL}) (?:connected to the internet|online|have an internet connection)(?: (?:now|right now|at the moment))?",
    rf"(?:how much charge (?:is left in|has)|how charged is) {LOCAL}(?: got)?(?: (?:now|right now|at the moment|right this minute|currently))?",
    rf"(?:check whether|tell me (?:whether|if)|see whether) {LOCAL} (?:has internet access|is online|currently has internet)(?: (?:now|right now))?",
    rf"(?:read|check|tell me|report|show(?: me)?|give(?: me)?|get|read out) (?:both )?(?:the )?(?:current |reported )?(?:battery(?: level| percentage)? and (?:internet|network)(?: availability| connection)?|charge and connectivity|battery and internet (?:readings|status)) (?:of|on|for|from) {LOCAL}(?: only)?",
    rf"(?:tell me|show(?: me)?|read(?: out)?) {LOCAL}'s (?:reported )?battery (?:level|percentage) and (?:network connection|internet availability)",
    r"(?:get|check|read|report) (?:the )?(?:current )?(?:battery reading|charge|connectivity|battery reading and connectivity) here(?:, please)?",
    r"I need a current connectivity reading here",
)


def _status(text):
    text = _surface(text)
    for pattern in STATUS:
        if re.fullmatch(pattern, text, FLAGS):
            return RequestedAction("status", "authenticated_current_device", "battery_or_connectivity")
    return None


def _parallel_read(text, parser):
    # An explicit read on the first operand may share its verb with the second.
    # Each operand still has to satisfy its full target grammar independently.
    return parser(text) or parser("read " + text) or parser("tell me " + text)


def _save(text):
    # Existing forms have single-quote delimiters. An additional delimiter in
    # the body is ambiguous (possibly a trailing command); never repair bytes.
    fields = literal_note(text)
    if fields is not None and "'" in fields["text"]:
        return None
    if fields is None:
        # An explicit payload boundary makes subsequent bytes DATA. Arbitrary
        # prose, missing titles/bodies, future/quoted commands never match.
        patterns = (
            r"Store this note privately: title '(?P<title>[^'\n]+)'\s*; text '(?P<text>[^'\n]+)'",
            r'(?:Save|Create) a private note (?:titled|with title) "(?P<title>[^"\n]+)" (?:with exact text|containing exactly) "(?P<text>[^"\n]+)"',
            r"Save this exact body under the title (?P<title>[^:;\n]+): (?P<text>[^\n]+)",
            r"Make a private note called (?P<title>[^.;\n]+)\. Its exact contents should be: (?P<text>[^\n]+)",
            r"Please store a new personal note\. The title is (?P<title>[^,;\n]+), and the body is exactly (?P<text>[^\n]+)",
            r"Create one private note now: title = (?P<title>[^;\n]+); literal text = (?P<text>[^\n]+)",
            r"Remember this as a new note named (?P<title>[^,;\n]+), with the exact text (?P<text>[^\n]+)",
            r"I need you to put a new note in private memory\. Call it (?P<title>[^.;\n]+) and use the exact body (?P<text>[^\n]+)",
            r"Make a private note with the title (?P<title>[^.;\n]+) and the exact text (?P<text>[^\n]+)",
            r"Save a note for me: its title is (?P<title>[^.;\n]+) and its exact contents are (?P<text>[^\n]+)",
            r"Please remember this in a new private note titled (?P<title>[^;\n]+); the body should be exactly (?P<text>[^\n]+)",
        )
        matches = [m.groupdict() for p in patterns if (m := re.fullmatch(p, text, FLAGS))]
        fields = matches[0] if len(matches) == 1 else None
    if fields is None:
        return None
    validate_plan([{"name": "save_private_note", "arguments": fields}])
    return RequestedAction("save", "current_owner_private_sage", fields["title"], tuple(fields.items()))


def understand_current(text):
    if type(text) is not str or not text.strip() or len(text.encode()) > 4096:
        raise ValueError("invalid_current_instruction")
    sha = hashlib.sha256(text.encode()).hexdigest()
    if any(ord(c) < 32 for c in text):
        return CurrentInstruction(text, sha, (), "instruction_control_character")
    try:
        save = _save(text)
        if save:
            return CurrentInstruction(text, sha, (save,), "explicit_literal_save")
        choices = []
        for intent, parser in (("search", _search), ("status", _status)):
            variants = [text]
            for pattern in CONSTRAINTS[intent]:
                variants.append(re.sub("^" + pattern, "", text, flags=FLAGS))
            for variant in list(variants):
                for suffix in SUFFIXES:
                    variants.append(re.sub(suffix + r"[.?]?$", "", variant, flags=FLAGS))
            matches = [action for v in variants if (action := parser(v))]
            if matches:
                choices.append(matches[0])
        if len(choices) == 1:
            return CurrentInstruction(text, sha, tuple(choices), "explicit_scoped_read")
        parallel = []
        for prefix in ("", *CONSTRAINTS["parallel_reads"]):
            variant = _surface(re.sub("^" + prefix, "", text, flags=FLAGS))
            for suffix in SUFFIXES:
                variant = re.sub(suffix + r"[.?]?$", "", variant, flags=FLAGS)
            for sep in re.finditer(r",? (?:and|plus|alongside|together with|then|and then) |; also ", variant, FLAGS):
                left, right = variant[:sep.start()], variant[sep.end():]
                for search, status in ((_search(left), _parallel_read(right, _status)),
                                       (_parallel_read(right, _search), _status(left))):
                    if search and status:
                        parallel.append((search, status))
        if len(set(parallel)) == 1:
            return CurrentInstruction(text, sha, parallel[0], "explicit_scoped_parallel_reads")
    except InvalidPlan:
        return CurrentInstruction(text, sha, (), "literal_fields_invalid")
    return CurrentInstruction(text, sha, (), "operation_target_or_current_instruction_unresolved")


def admit_current(question, *, proposal=None, allow_exact_grammar=False):
    """Validate the ENTIRE plan, or produce a narrow lab grammar proposal.

    Never retain a partial plan, change a deletion into a read, change a target,
    or repair model arguments. Caller-controlled grammar mode is lab-only;
    the broker separately requires qualified immutable policy and session gates.
    """
    understood = understand_current(question)
    calls = understood.calls()
    reason = understood.reason
    if not calls:
        return {"lane": "answer", "calls": [], "effect": "none", "reason": reason}
    if proposal is None and allow_exact_grammar is not True:
        return {"lane": "answer", "calls": [], "effect": "none", "reason": "grammar_bypass_disabled"}
    if proposal is not None:
        if (type(proposal) is not dict or proposal.get("lane") != "specialist"
                or proposal.get("intent") != understood.intent or proposal.get("calls") != calls):
            return {"lane": "answer", "calls": [], "effect": "none", "reason": "requested_operation_target_plan_disagreement"}
    return {"lane": "specialist", "intent": understood.intent, "calls": calls,
            "instruction_sha256": understood.sha256, "reason": reason,
            "requested_actions": [{"operation": a.operation, "target": a.target, "subject": a.subject} for a in understood.actions],
            "effect": "write_proposal" if understood.intent == "save" else "read_proposal",
            "qualified": False, "generation_tokens": 0}
