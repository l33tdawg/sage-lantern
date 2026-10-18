"""Bounded, process-local chat state. The caller serializes access and authorizes it.

No persistence, retrieval, photo bytes, tool execution, or model KV ownership.
Only successful completed exchanges may be committed; snapshots fence late results.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import time
import uuid
from datetime import datetime, UTC

from .conversation_branch import effective_thread_items

MAX_TURNS = 4
MAX_VISIBLE_TURNS = 40
MAX_VISIBLE_BYTES = 65536
MAX_CONTENT_BYTES = 2048
MAX_INPUT_BYTES = 32768
PHOTO_NOTE = "[Earlier photo is not attached to this turn.]\n"
ANSWER_EXCERPT = '\n[Earlier answer excerpt; remainder omitted.]'


def validate_messages(value):
    if type(value) is not list or len(value) > MAX_TURNS * 2 or len(value) % 2:
        raise ValueError('invalid_conversation')
    result = []
    size = 0
    for index, message in enumerate(value):
        if (type(message) is not dict or set(message) != {'role', 'content'}
                or message['role'] != ('user' if index % 2 == 0 else 'assistant')
                or type(message['content']) is not str or not message['content'].strip()
                or any(ord(char) < 32 and char not in '\n\t' for char in message['content'])):
            raise ValueError('invalid_conversation')
        size += len(message['content'].encode('utf-8'))
        if size > MAX_CONTENT_BYTES:
            raise ValueError('conversation_too_large')
        result.append(dict(message))
    return result


@dataclass(frozen=True)
class Exchange:
    session_id: str
    question: str
    answer: str
    created_at: str
    has_photo: bool = False
    supersedes: str | None = None

    def item(self, thread_id):
        return dict(session_id=self.session_id, created_at=self.created_at,
                    transcript=self.question, response=self.answer, has_photo=self.has_photo,
                    thread_id=thread_id, **({"supersedes": self.supersedes} if self.supersedes else {}))


@dataclass(frozen=True)
class Snapshot:
    revision: int
    owner: str
    turns: tuple[Exchange, ...]
    thread_id: str
    supersedes: str | None = None

    def messages(self):
        turns = self.turns[-MAX_TURNS:]
        while turns:
            messages = [message for turn in turns for message in (
                {'role': 'user', 'content': (PHOTO_NOTE if turn.has_photo else '') + turn.question},
                {'role': 'assistant', 'content': turn.answer})]
            try:
                return validate_messages(messages)
            except (ValueError, UnicodeError):
                # Preserve complete user questions/corrections before answer detail.
                # Excerpts are explicitly labelled, never presented as summaries.
                question_bytes = sum(len(row['content'].encode('utf-8')) for row in messages[::2])
                allowance = (MAX_CONTENT_BYTES - question_bytes) // len(turns)
                if allowance >= 128:
                    bounded = []
                    for index, row in enumerate(messages):
                        value = row['content']
                        if index % 2 and len(value.encode('utf-8')) > allowance:
                            prefix_bytes = allowance - len(ANSWER_EXCERPT.encode('utf-8'))
                            value = value.encode('utf-8')[:prefix_bytes].decode('utf-8', errors='ignore').rstrip() + ANSWER_EXCERPT
                        bounded.append({'role': row['role'], 'content': value})
                    return validate_messages(bounded)
                # Keep a contiguous suffix, never skip a correction in the middle.
                turns = turns[1:]
        return []


class ActiveConversation:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.revision = 0
        self.clear()

    def clear(self):
        self.revision += 1
        self.thread_id = str(uuid.uuid4())
        self.owner = None
        self.turns = ()
        self.updated = 0.0

    def prepare(self, token, *, replace_session=None):
        if type(token) is not str or not token:
            self.clear()
            return None
        owner = hashlib.sha256(token.encode('utf-8')).hexdigest()
        if self.owner != owner:
            self.clear()
            self.owner = owner
        turns = self.turns
        thread_id = self.thread_id
        if replace_session is not None:
            # Editing a saved question outside this chat starts a fresh branch.
            if not any(turn.session_id == replace_session for turn in turns):
                thread_id = str(uuid.uuid4())
            turns = next((turns[:i] for i, turn in enumerate(turns)
                          if turn.session_id == replace_session), ())
        return Snapshot(self.revision, owner, turns, thread_id, replace_session)

    def commit(self, snapshot, session_id, question, answer, *, has_photo=False):
        if (snapshot is None or snapshot.revision != self.revision
                or snapshot.owner != self.owner):
            return False
        try:
            # Display history and model context have separate budgets.
            for value, limit in ((question, 4096), (answer, 12000)):
                if (type(value) is not str or not value.strip() or len(value.encode('utf-8')) > limit
                        or any(ord(char) < 32 and char not in '\n\t' for char in value)):
                    raise ValueError('invalid_exchange')
            turn = Exchange(session_id, question, answer, datetime.now(UTC).isoformat(), has_photo, snapshot.supersedes)
            turns = (*snapshot.turns, turn)[-MAX_VISIBLE_TURNS:]
            while sum(len((row.question + row.answer).encode('utf-8')) for row in turns) > MAX_VISIBLE_BYTES:
                turns = turns[1:]
        except (ValueError, UnicodeError):
            self.clear()
            return False
        self.thread_id = snapshot.thread_id
        self.turns = turns
        self.updated = self.clock()
        self.revision += 1
        return True

    def restore(self, token, thread_id, items):
        self.clear()
        self.owner = hashlib.sha256(token.encode('utf-8')).hexdigest()
        self.thread_id = thread_id
        turns = tuple(Exchange(row['session_id'], row['transcript'], row['response'], row['created_at'],
                               row.get('has_photo', False), row.get('supersedes'))
                      for row in effective_thread_items(items, thread_id))[-MAX_VISIBLE_TURNS:]
        while sum(len((row.question + row.answer).encode('utf-8')) for row in turns) > MAX_VISIBLE_BYTES:
            turns = turns[1:]
        self.turns = turns
        self.updated = self.clock()
