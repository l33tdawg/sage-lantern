from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class DuplicateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_messages (
                source_agent_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                first_seen_unix INTEGER NOT NULL,
                PRIMARY KEY (source_agent_id, message_id)
            )
            """
        )
        self.connection.commit()

    def check_and_record(self, source_agent_id: str, message_id: str) -> bool:
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT INTO seen_messages(source_agent_id, message_id, first_seen_unix) VALUES (?, ?, ?)",
                    (source_agent_id, message_id, int(time.time())),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def close(self) -> None:
        self.connection.close()

