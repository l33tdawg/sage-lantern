"""Optional, bounded question suggestions, kept out of answer text and speech."""
import json
import re

OPEN = '\n<followups>'
CLOSE = '</followups>'
LIST_ITEM = re.compile(r'^[ \t]*(?:[-*•]|\d{1,2}[.)])[ \t]+(.+?)[ \t]*$')
EMPTY_ITEM = re.compile(r'^[ \t]*(?:[-*•]|\d{1,2}[.)])[ \t]*$')
QUESTION_LINE = re.compile(r'^[ \t]*(\S.*?\?)[ \t]*$')
# The trailer opens at the start of a line, or where the tag is followed by a
# newline or a JSON bracket. An inline mention ("a separate <followups>
# section") stays ordinary text.
LINE_TAG = re.compile(r'(?m)^[ \t]*<followups>', re.IGNORECASE)
INLINE_TAG = re.compile(r'<followups>(?=\s*(?:\n|\[))', re.IGNORECASE)
TRAILING_TAG = re.compile(r'<followups>\s*$', re.IGNORECASE)
TAG = '<followups>'
LEAD_IN = {'the', 'follow-up', 'follow-ups', 'followups', 'questions',
           'suggested questions', 'suggestions', 'next', 'and'}
ECHO_MINIMUM = 40
POLICY = (
    ' After the complete answer, add a <followups> section on a new line with a JSON array of two '
    'short, specific questions the user could ask next, then </followups>. Write each question as the '
    'user asking you. The two questions must differ from each other and from the question just answered, '
    'and must stay on the subjects of the answer: for a question about the distance from Tokyo to Osaka, '
    'ask about Tokyo or Osaka themselves, how to travel, how long it takes, what it costs, when to go, '
    'or what to see. Omit the section only for a one-line arithmetic or definition answer, or when the '
    'user asked for no follow-up.'
)

# The separate, metadata-only request. POLICY above asks one completion to answer
# and annotate at once, and the small on-device model drops the annotation on
# exactly its shortest answers, so this narrower instruction is the fallback for
# those turns. It carries no answer of its own, which is why it can be bounded
# hard in both tokens and time.
REQUEST_POLICY = (
    'You suggest what a user might ask next. You are given the question that was '
    'answered and the answer it received. Reply with ONLY a JSON array of two '
    'short questions, written as the user asking, and nothing else: no tags, no '
    'explanation, no answer of your own. Both questions must be about the subject '
    'of the answer just given - never about another country, city or topic - and '
    'must differ from each other and from the question already asked.'
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
        if EMPTY_ITEM.match(line):
            # The model sometimes opens a second bullet and then closes the
            # block on the same line ("- </followups>").
            continue
        match = LIST_ITEM.match(line)
        if match is None:
            # The 2B often drops the bullets and writes one question per line.
            match = QUESTION_LINE.match(line)
        if match is None:
            return []
        items.append(match.group(1))
    return items


def _tidy_answer(value):
    """Drop a dangling lead-in line the model wrote before the tag ("The")."""
    answer = value.strip()
    lines = answer.split('\n')
    if lines:
        last = lines[-1].strip().strip('*_` :').lower()
        if last in LEAD_IN:
            lines.pop()
    return '\n'.join(lines).strip()


def _marker(text):
    """Earliest trailer opening in one completion, or None."""
    candidates = [match for match in (LINE_TAG.search(text), INLINE_TAG.search(text))
                  if match is not None]
    return min(candidates, key=lambda match: match.start()) if candidates else None


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
    if text.lstrip().lower().startswith(TAG):
        return '', []
    marker = _marker(text)
    if marker is None:
        return text.strip(), []
    close = re.search(r'</followups>', text[marker.end():], re.IGNORECASE)
    if close is None:
        return _tidy_answer(text[:marker.start()]), []
    body = text[marker.end():marker.end() + close.start()].strip()
    try:
        questions = validate_suggestions(json.loads(body))
    except (ValueError, UnicodeError, RecursionError):
        questions = validate_suggestions(parse_list(body))
    return _tidy_answer(text[:marker.start()]), questions


class AnswerStream:
    """Hold only a possible delimiter prefix; never stream suggestion metadata."""
    def __init__(self, emit):
        self.emit = emit
        self.pending = ''
        self.metadata = False
        self.at_start = True
        self.line_start = True

    def feed(self, text):
        if self.metadata:
            return
        self.pending += text
        if self.at_start:
            start = self.pending.lstrip()
            if start.lower().startswith(TAG):
                self.pending = ''
                self.metadata = True
                return
            if not start or TAG.startswith(start.lower()):
                return
            self.at_start = False
        marker = self._marker()
        if marker is not None:
            self._emit(self.pending[:marker.start()].rstrip())
            self.pending = ''
            self.metadata = True
            return
        hold = self._hold()
        ready = self.pending[:-hold] if hold else self.pending
        self.pending = self.pending[-hold:] if hold else ''
        self._emit(ready)

    def _marker(self):
        """Trailer opening in the held buffer, honouring what was already emitted."""
        candidates = []
        line = LINE_TAG.search(self.pending)
        if line is not None:
            newline = self.pending.rfind('\n', 0, line.start())
            prefix = self.pending[newline + 1:line.start()]
            if not prefix.strip() and (newline >= 0 or self.line_start):
                candidates.append(line)
        inline = INLINE_TAG.search(self.pending)
        if inline is not None:
            candidates.append(inline)
        return min(candidates, key=lambda match: match.start()) if candidates else None

    def _hold(self):
        """Length of a suffix that may still become the delimiter."""
        trailing = TRAILING_TAG.search(self.pending)
        if trailing is not None:
            start = trailing.start()
            while start > 0 and self.pending[start - 1] in ' \t\r\n' and len(self.pending) - start < 16:
                start -= 1
            return len(self.pending) - start
        stripped = self.pending.rstrip(' \t\r\n')
        if len(self.pending) > len(stripped):
            # A line break may be the one the tag opens on; hold it briefly so
            # it never lands in the streamed answer ahead of a detected marker.
            return min(len(self.pending) - len(stripped), 8)
        lowered = self.pending.lower()
        for size in range(min(len(TAG), len(lowered)), 0, -1):
            if TAG.startswith(lowered[-size:]):
                start = len(self.pending) - size
                while start > 0 and self.pending[start - 1] in ' \t\r\n' and size < 16:
                    start -= 1
                    size += 1
                return size
        return 0

    def finish(self):
        if not self.metadata:
            self._emit(self.pending)
        self.pending = ''

    def _emit(self, value):
        if value:
            self.emit(value)
            self.line_start = value.endswith('\n')
