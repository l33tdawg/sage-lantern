#!/usr/bin/env python3

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from uuid import uuid4

from sage_lantern.adapters import LlamaCppHTTP
from sage_lantern.config import RuntimeConfig
from sage_lantern.conversation_service import ConversationService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.directory.exists():
        raise RuntimeError("Use a new dedicated synthetic-test directory")
    config = replace(RuntimeConfig.load(args.config), conversation_dir=args.directory)
    session_id = str(uuid4())
    code = "cedar-seven-" + uuid4().hex[:8]
    service = ConversationService.from_config(config)
    try:
        ticket = service.store.approve(True)
        service.record(ticket, {"session_id": session_id,
                               "created_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
                               "question": "Synthetic test only: my fictional parcel code is " + code,
                               "answer": "You told me a fictional parcel code; this is a test, not a real-world fact.",
                               "has_photo": False})
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            service.tick()
            if all(operation["state"] == "recall_ready" for operation in service.store.operations()):
                break
            time.sleep(2)
        else:
            raise RuntimeError("Synthetic memories did not become recall-ready")
        identifiers = [operation["memory_id"] for operation in service.store.operations()]
        service.store.close()
        service = ConversationService.from_config(config)
        context = service.context("What exact fictional parcel code did I tell you earlier?")
        if code not in "\n".join(context):
            raise RuntimeError("Cross-session recall did not retrieve the synthetic code")
        answer = LlamaCppHTTP(config.llama_base_url, config.llama_model).respond(
            "What exact fictional parcel code did I tell you earlier? Repeat only that code.", context)
        if code not in answer:
            raise RuntimeError("Model did not use the retrieved synthetic code")
        service.store.suppress([session_id])
        service.tick()
        if code in "\n".join(service.context("What was my fictional parcel code?")):
            raise RuntimeError("Suppressed conversation leaked into recall")
        statuses = [str(getattr(service.backend.client.get_memory(identifier).status, "value", "")) for identifier in identifiers]
        if statuses != ["deprecated", "deprecated"]:
            raise RuntimeError("Synthetic deprecation remains incomplete")
        print(json.dumps({"passed": True, "new_session_recall": True, "three_day_old_record": True,
                          "store_reopened": True, "model_used_memory": True, "deprecation": statuses,
                          "memory_ids": identifiers}))
    finally:
        try:
            service.store.suppress([session_id])
            service.tick()
        finally:
            service.store.close()


if __name__ == "__main__":
    main()
