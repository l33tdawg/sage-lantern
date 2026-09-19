"""Optional, bounded question suggestions, kept out of answer text and speech."""
import json
import re

OPEN = '\n<followups>'
CLOSE = '</followups>'
LIST_ITEM = re.compile(r'^[ \t]*(?:[-*•]|\d{1,2}[.)])[ \t]+(.+?)[ \t]*$')
ECHO_MINIMUM = 40
POLICY = (
    ' After the complete answer, optionally append a separate <followups> section on a new line '
    'containing a JSON array of at most two short, specific questions the user could ask next, '
    'then </followups>. Write each as the user asking you, not as an offer from you. '
    'These are optional next topics, never a substitute for answering the current question. '
    'Omit the section for simple facts, explicitly brief or exact-format requests, '
    'requests without follow-up questions, or when no useful next question exists.'
)


def validate_suggestions(value):
    if type(value) is not list or len(value) > 2:
        return []
    result = []
    for question in value:
        try:
            size = len(question.encode('utf-8')) if type(question) is str else 0
        except UnicodeError:
            return []
        if (type(question) is not str or not question.strip()
                or size > 240
                or any(ord(c) < 32 or ord(c) == 127 for c in question)
                or any(c in question for c in '<>')):
            return []
        question = question.strip()
        if question.casefold() not in [item.casefold() for item in result]:
            result.append(question)
    return result


def parse_list(value):
    """Accept the bullet list a small model writes when it ignores the JSON wording."""
    if type(value) is not str or len(value.encode('utf-8', 'surrogatepass')) > 2048:
        return []
    items = []
    for line in value.splitlines():
        if not line.strip():
            continue
        match = LIST_ITEM.match(line)
        if match is None:
            return []
        items.append(match.group(1))
    return items


_WINDOW_CACHE: dict[str, frozenset] = {}


def _echo_windows(policy):
    """Every 40-character run of one instruction text, memoised."""
    if policy not in _WINDOW_CACHE:
        _WINDOW_CACHE[policy] = frozenset(
            policy[start:start + ECHO_MINIMUM]
            for start in range(max(0, len(policy) - ECHO_MINIMUM + 1)))
    return _WINDOW_CACHE[policy]


def strip_policy_echo(value, policy):
    """Drop a verbatim run of our own instruction text the model echoed back.

    The stage-5 2B sometimes continues the system prompt after finishing its
    answer; that put the policy tail (including the literal "append a
    <followups> section" sentence) on the screen and stopped the trailer from
    parsing. Only verbatim runs of >= 40 characters are treated as echo.
    """
    if type(value) is not str or not value or type(policy) is not str or not policy:
        return value
    windows = _echo_windows(policy)
    position = None
    for fragment in windows:
        found = value.find(fragment)
        if found >= 0 and (position is None or found < position):
            position = found
    if position is None:
        return value
    while position > 0 and value[position - 1:position - 1 + ECHO_MINIMUM] in windows:
        position -= 1
    return value[:position].rstrip()


class PolicyEcho:
    """Stop streaming deltas once the answer starts repeating its instructions."""

    def __init__(self, policy, *, window=512):
        self.windows = _echo_windows(policy)
        self.window = window
        self.tail = ''
        self.tripped = False

    def feed(self, text):
        if self.tripped:
            return False
        self.tail = (self.tail + text)[-self.window:]
        scan_start = max(0, len(self.tail) - (len(text) + ECHO_MINIMUM - 1))
        scan_end = max(scan_start, len(self.tail) - ECHO_MINIMUM + 1)
        for index in range(scan_start, scan_end):
            if self.tail[index:index + ECHO_MINIMUM] in self.windows:
                self.tripped = True
                return False
        return True


def split_answer(text):
    # A metadata-only completion has no answer, even if transport trimming
    # removed the leading newline. Never speak or display its JSON.
    if text.lstrip().startswith(OPEN[1:]):
        return '', []
    answer, marker, trailer = text.partition(OPEN)
    if not marker:
        return text.strip(), []
    trailer = trailer.strip()
    if not trailer.endswith(CLOSE):
        return answer.strip(), []
    body = trailer[:-len(CLOSE)].strip()
    try:
        questions = validate_suggestions(json.loads(body))
    except (ValueError, UnicodeError, RecursionError):
        questions = validate_suggestions(parse_list(body))
    return answer.strip(), questions


class AnswerStream:
    """Hold only a possible delimiter prefix; never stream suggestion metadata."""
    def __init__(self, emit):
        self.emit = emit
        self.pending = ''
        self.metadata = False
        self.at_start = True

    def feed(self, text):
        if self.metadata:
            return
        self.pending += text
        if self.at_start:
            start = self.pending.lstrip()
            if start.startswith(OPEN[1:]):
                self.pending = ''
                self.metadata = True
                return
            if not start or OPEN[1:].startswith(start):
                return
            self.at_start = False
        position = self.pending.find(OPEN)
        if position >= 0:
            self._emit(self.pending[:position])
            self.pending = ''
            self.metadata = True
            return
        hold = next((size for size in range(min(len(OPEN) - 1, len(self.pending)), 0, -1)
                     if self.pending.endswith(OPEN[:size])), 0)
        ready = self.pending[:-hold] if hold else self.pending
        self.pending = self.pending[-hold:] if hold else ''
        self._emit(ready)

    def finish(self):
        if not self.metadata:
            self._emit(self.pending)
        self.pending = ''

    def _emit(self, value):
        if value:
            self.emit(value)
