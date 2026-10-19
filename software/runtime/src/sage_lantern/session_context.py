"""Noncredential device-session context; never a SAGE signer or consent grant.

Only the live auth broker issues this context after validating its token. It is
safe to carry beside a response so an already authenticated browser can discard
late work. Comparing this value never replaces current token authorization.
"""
from dataclasses import dataclass
import re


SCHEMA = "sage.lantern.session-context.v1"


@dataclass(frozen=True)
class SessionContext:
    epoch: str
    session_ref: str

    @classmethod
    def parse(cls, value):
        if (type(value) is not dict or set(value) != {"schema", "epoch", "session_ref"}
                or value["schema"] != SCHEMA
                or type(value["epoch"]) is not str
                or re.fullmatch(r"[0-9a-f]{32}", value["epoch"]) is None
                or type(value["session_ref"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", value["session_ref"]) is None):
            raise ValueError("session_context_unavailable")
        return cls(value["epoch"], value["session_ref"])

    def to_dict(self):
        return dict(schema=SCHEMA, epoch=self.epoch, session_ref=self.session_ref)
