"""Incremental sentence segmentation for streaming speech.

Pure text handling. No audio, no clock, no runtime import: the controller owns
state, this owns where a sentence ends.

The rule that matters most is what is *not* a sentence end. A split that is
merely early is recoverable; a split inside a decimal, an abbreviation, a
multi-byte character or an unfinished tag is spoken as a wrong word, so every
ambiguous case holds the text back and waits for more input. Held text is
released by the next boundary or by flush() at end of answer.

Invariant, asserted by the tests: for any sequence of push() calls followed by
flush(), "".join(emitted) reconstructs the pushed text exactly. Trailing
whitespace travels with the sentence it follows, which is what makes that true.
"""
from __future__ import annotations

import unicodedata

TERMINATORS = ".!?\u2026\u3002\uff01\uff1f"
CJK_TERMINATORS = "\u3002\uff01\uff1f"
CLOSERS = "\u300d\u300f\uff09\u3011\u300b\u201d\u2019\"'"
ENTITY_MAX = 32

# Tokens whose trailing dot does not end a sentence. Lower case, no trailing dot.
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "eg", "ie",
    "e.g", "i.e", "fig", "no", "approx", "inc", "ltd", "co", "dept", "est",
    "min", "max", "cf", "al", "ca", "vol", "pp", "ed", "eds", "esp", "resp",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    "u.s", "u.k", "a.m", "p.m",
})


def _joinable(char):
    """A character that must stay attached to the one before it.

    Combining marks and the zero-width joiner. Without this a forced byte cut
    can orphan a mark or split an emoji sequence into two spoken items.
    """
    if char == "\u200d":
        return True
    return bool(unicodedata.combining(char)) or unicodedata.category(char) in ("Mn", "Me")


def _token_before(text, index):
    """The word ending at index, keeping internal dots: "e.g." -> "e.g"."""
    start = index
    while start > 0 and (text[start - 1].isalpha() or text[start - 1] == "."):
        start -= 1
    return text[start:index].strip(".")


class Segmenter:
    def __init__(self, *, max_bytes):
        if type(max_bytes) is not int or max_bytes < 16:
            raise ValueError("max_bytes must be an int >= 16")
        self.max_bytes = max_bytes
        self._buffer = ""

    @property
    def pending(self):
        return self._buffer

    @property
    def pending_bytes(self):
        return len(self._buffer.encode("utf-8"))

    def push(self, text):
        """Add newly generated text and return any sentences it completed."""
        if type(text) is not str:
            raise TypeError("push expects str")
        if not text:
            return []
        self._buffer += text
        return self._drain()

    def flush(self):
        """End of answer: emit the remainder, split to the byte budget."""
        out = self._drain()
        out.extend(self._split_remainder())
        return out

    # --- internals ---------------------------------------------------------

    def _drain(self):
        out = []
        while True:
            cut = self._next_cut()
            if cut is None:
                return out
            out.append(self._buffer[:cut])
            self._buffer = self._buffer[cut:]

    def _split_remainder(self):
        out = []
        while self._buffer:
            if self.pending_bytes <= self.max_bytes:
                out.append(self._buffer)
                self._buffer = ""
                break
            cut = self._forced_cut()
            if not cut:
                out.append(self._buffer)
                self._buffer = ""
                break
            out.append(self._buffer[:cut])
            self._buffer = self._buffer[cut:]
        return out

    def _next_cut(self):
        text = self._buffer
        unsafe = self._unsafe_from()
        limit = len(text) if unsafe is None else unsafe
        index = 0
        while index < limit:
            char = text[index]
            if char in TERMINATORS:
                run_end = index
                while run_end + 1 < len(text) and text[run_end + 1] == char:
                    run_end += 1
                if char != "." or self._dot_ends_sentence(index, run_end):
                    end = self._boundary_end(run_end)
                    if end is not None and len(text[:end].encode("utf-8")) <= self.max_bytes:
                        # A natural boundary is still a segment, so it obeys the
                        # same ceiling as a forced cut. Accepting it because it
                        # *looks* like a sentence is how a 202-byte segment got
                        # emitted under a 32-byte ceiling: the boundary moved the
                        # cut, it did not license the size.
                        return end
                index = run_end + 1
                continue
            index += 1
        if self.pending_bytes > self.max_bytes:
            return self._forced_cut()
        return None

    def _dot_ends_sentence(self, index, run_end):
        if run_end > index:
            return True  # a run of dots is an ellipsis, which can end a sentence
        previous = self._buffer[index - 1] if index else ""
        following = self._buffer[index + 1] if index + 1 < len(self._buffer) else ""
        if previous.isdigit() and following.isdigit():
            return False  # decimal point
        token = _token_before(self._buffer, index)
        if token.lower() in ABBREVIATIONS:
            return False
        if len(token) == 1 and token.isalpha():
            return False  # single-letter initial
        return True

    def _boundary_end(self, run_end):
        """Index just past the sentence, or None while it is still incomplete."""
        text = self._buffer
        index = run_end + 1
        while index < len(text) and text[index] in CLOSERS:
            index += 1
        if index >= len(text):
            return None  # more may follow: "3." can still become "3.14"
        if text[index].isspace():
            while index < len(text) and text[index].isspace():
                index += 1
            return index
        if text[run_end] in CJK_TERMINATORS:
            return index  # CJK does not put a space after the terminator
        return None

    def _unsafe_from(self):
        """Index from which no boundary may be taken, or None.

        Two incomplete constructs suppress every boundary at or after them: an
        unclosed angle bracket, and an entity start with no terminator yet.
        """
        text = self._buffer
        opened = text.rfind("<")
        if opened != -1 and text.find(">", opened) == -1:
            return opened
        amp = text.rfind("&")
        if amp != -1:
            tail = text[amp:]
            if ";" not in tail and len(tail) <= ENTITY_MAX:
                return amp
        return None

    def _forced_cut(self):
        """Largest prefix inside the byte budget, on a character boundary.

        The budget is a ceiling, not a preference. An earlier version walked the
        cut *forward* over every attachment that followed it, so that a combining
        mark or a zero-width joiner could not be left at the head of the next
        segment - and in doing so emitted segments past the declared maximum
        (18 bytes under a 16-byte ceiling). Attachment is preserved the other way
        round: the cut moves *back* so the attachment goes with its base, which
        can only shrink the segment. A grapheme cluster may still straddle a
        boundary, and that is the honest outcome - the ceiling is what the caller
        asked to be held to.
        """
        data = self._buffer.encode("utf-8")
        if len(data) <= self.max_bytes:
            return None
        cut = self.max_bytes
        prefix = ""
        while cut > 0:
            try:
                prefix = data[:cut].decode("utf-8")
                break
            except UnicodeDecodeError:
                cut -= 1
        else:
            return None
        index = len(prefix)
        text = self._buffer
        # A segment must not start with an attachment: give the base character
        # back too, not just the mark. Only the mark is _joinable, so checking
        # the character before it is what keeps the pair together.
        if index < len(text) and _joinable(text[index]):
            while index > 0 and _joinable(text[index - 1]):
                index -= 1
            if index > 0:
                index -= 1
        space = text.rfind(" ", 0, index)
        if space >= 16:
            index = space + 1
        if not index:
            # The attachment walk consumed the entire prefix, which happens when
            # the buffer is one base character followed by more marks than the
            # budget can hold. Returning None here did not mean "no cut is safe":
            # the caller reads it as "this buffer cannot be split" and emits the
            # whole thing, silently over the ceiling. A prefix that decodes and
            # fits is a valid cut even when the only way to honour the ceiling is
            # to let one grapheme cluster straddle the boundary - which this
            # method already documents as the honest outcome. What is returned is
            # the decoded prefix's length in characters, because every caller
            # slices the buffer by character: handing back the byte offset would
            # slice past the whole string and emit it uncut.
            return len(prefix)
        return index
