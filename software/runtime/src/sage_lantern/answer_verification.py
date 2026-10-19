"""Optional second-model fact check of a completed answer (flag-gated).

The NPU 2B answers in seconds but occasionally states a confidently wrong
number (2026-09-20: 130 km for Tokyo-Osaka, 160 km for London-Paris). The CPU
4B - already installed as the photo fallback - corrects those when it is asked
to check the draft; this module performs that check. It is never enabled by
default: callers opt in through ``llm.verify_answers``.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

POLICY = (
    "You are a careful fact-checker. You receive a question and a draft answer. Reply with exactly one "
    "line: OK if every factual claim in the draft is correct; otherwise CORRECT: followed by a corrected "
    "answer no longer than the draft; otherwise UNSURE if you cannot tell. Never explain your choice."
)
STATUSES = ("ok", "corrected", "unsure", "unavailable")
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


@dataclass(frozen=True)
class Verdict:
    status: str
    text: str
    seconds: float


def parse_verdict(raw, original, *, max_bytes):
    """First non-empty line only: OK, UNSURE, CORRECT: <text>, anything else unavailable."""
    if type(raw) is not str:
        return "unavailable", original
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        head = line.upper()
        if head.startswith("OK") and head[2:3] in ("", ".", "!", ",", ":"):
            return "ok", original
        if head.startswith("UNSURE"):
            return "unsure", original
        if head.startswith("CORRECT:"):
            text = CONTROL.sub("", line.split(":", 1)[1]).strip()
            if not text or len(text.encode("utf-8")) > max_bytes or any(c in text for c in "<>"):
                return "unavailable", original
            return "corrected", text
        return "unavailable", original
    return "unavailable", original


class AnswerVerifier:
    def __init__(self, base_url: str, model: str, *, timeout_seconds: float = 60.0,
                 max_response_bytes: int = 4096) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        if not 1.0 <= timeout_seconds <= 600.0:
            raise ValueError("verifier timeout must be between 1 and 600 seconds")
        self.timeout_seconds = timeout_seconds
        if max_response_bytes < 128:
            raise ValueError("verifier response budget is too small")
        self.max_response_bytes = max_response_bytes

    def _request(self, question: str, answer: str) -> urllib.request.Request:
        body = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 256,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {"role": "system", "content": POLICY},
                {"role": "user", "content": f"Question:\n{question}\n\nDraft answer:\n{answer}"},
            ],
        }
        return urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def verify(self, question: str, answer: str) -> Verdict:
        """Never raises: a failed check leaves the original answer in place."""
        started = time.monotonic()
        try:
            with urllib.request.urlopen(self._request(question, answer),
                                        timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
            payload = json.loads(raw)
            text = payload["choices"][0]["message"]["content"]
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError):
            return Verdict("unavailable", answer, time.monotonic() - started)
        status, final = parse_verdict(text, answer, max_bytes=self.max_response_bytes)
        return Verdict(status, final, time.monotonic() - started)
