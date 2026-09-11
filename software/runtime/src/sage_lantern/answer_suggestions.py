"""Optional, bounded question suggestions, kept out of answer text and speech."""
import json

OPEN = '\n<followups>'
CLOSE = '</followups>'
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
    try:
        questions = validate_suggestions(json.loads(trailer[:-len(CLOSE)].strip()))
    except (ValueError, UnicodeError, RecursionError):
        questions = []
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
