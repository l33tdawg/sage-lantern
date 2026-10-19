#!/usr/bin/env python3
"""Freeze/evaluate external English action corpora; never certify their authorship.

Predictions persist before gold scoring. Human-speech references and actual final
ASR transcripts are distinct. No training, network, SAGE action or device access.
"""
import argparse
from collections import Counter
import datetime as dt
import json
from pathlib import Path
import statistics
import time
import wave

from action_boundary import admit_current
from build_english_qualification import canonical
from contract import score, validate_plan
from run import digest, write


SOURCES = ("qualify_admission.py", "action_boundary.py", "literal_note.py", "contract.py", "run.py", "build_english_qualification.py")


def validate_external(data, historical_questions):
    if type(data) is not dict or set(data) != {"schema", "authors", "training_authors", "authorship_review", "cases"} or data["schema"] != "lantern.external-english-actions.v1":
        raise ValueError("external_corpus_schema")
    if type(data["authors"]) is not dict or not data["authors"] or any(type(k) is not str or not k or type(v) is not str or not v for k, v in data["authors"].items()):
        raise ValueError("author_provenance_required")
    if type(data["training_authors"]) is not list or not data["training_authors"] or any(type(s) is not str or not s for s in data["training_authors"]):
        raise ValueError("training_authorship_required")
    if set(data["authors"]) & set(data["training_authors"]):
        raise ValueError("same_author_not_independent")
    if type(data["authorship_review"]) is not str or not data["authorship_review"].strip():
        raise ValueError("independent_authorship_review_reference_required")
    if type(data["cases"]) is not list or not data["cases"]:
        raise ValueError("empty_corpus")
    seen_ids, seen_text = set(), set()
    for case in data["cases"]:
        if type(case) is not dict:
            raise ValueError("case_shape_or_provenance")
        fields = {"id", "question", "expected", "author", "split", "modality", "family", "language"}
        if case.get("modality") == "human_speech":
            fields |= {"audio", "reference_transcriber"}
        if (set(case) != fields or type(case["author"]) is not str or case["author"] not in data["authors"]
                or case["split"] not in {"calibration", "confirmation"} or case["language"] != "en"
                or case["modality"] not in {"command", "human_speech"}
                or any(type(case[k]) is not str or not case[k].strip() for k in ("id", "question", "family"))
                or len(case["question"].encode()) > 4096):
            raise ValueError("case_shape_or_provenance")
        normalized = canonical(case["question"])
        if case["id"] in seen_ids or normalized in seen_text or normalized in historical_questions:
            raise ValueError("duplicate_or_inspected_request")
        seen_ids.add(case["id"])
        seen_text.add(normalized)
        if type(case["expected"]) is not list:
            raise ValueError("gold_plan_shape")
        if case["expected"]:
            exact_calls = []
            for expected in case["expected"]:
                if type(expected) is not dict or set(expected) != {"name", "exact"}:
                    raise ValueError("external_exact_gold_required")
                exact_calls.append({"name": expected["name"], "arguments": expected["exact"]})
            validate_plan(exact_calls)
        if case["modality"] == "human_speech":
            audio = case["audio"]
            if (type(audio) is not dict or set(audio) != {"path", "sha256", "provenance"}
                    or not all(type(v) is str and v for v in audio.values())
                    or type(case["reference_transcriber"]) is not str or not case["reference_transcriber"]
                    or digest(Path(audio["path"])) != audio["sha256"]):
                raise ValueError("human_audio_or_independent_reference_unbound")
            try:
                with wave.open(audio["path"], "rb") as wav:
                    if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000) or not 0 < wav.getnframes() <= 16000 * 30:
                        raise ValueError("bounded_pcm_wav_required")
            except (wave.Error, EOFError) as exc:
                raise ValueError("bounded_pcm_wav_required") from exc
    return data


def summarize_actions(actions):
    admitted = [r for r in actions if r["decision"]["calls"]]
    positive = [r for r in actions if r["expected"]]
    negative = [r for r in actions if not r["expected"]]
    correct = sum(r["pass"] for r in admitted)
    return {"requests": len(actions), "requested_actions": len(positive),
            "correct_requested_actions": sum(r["pass"] for r in positive),
            "useful_action_coverage": sum(r["pass"] for r in positive) / len(positive) if positive else None,
            "admitted_plans": len(admitted), "correct_admitted_plans": correct,
            "incorrect_admissions": len(admitted) - correct,
            "negative_requests": len(negative),
            "negative_requests_admitted": sum(bool(r["decision"]["calls"]) for r in negative),
            "proposed_saves": sum(r["decision"]["effect"] == "write_proposal" for r in admitted),
            "incorrect_save_admissions": sum(r["unexpected_write"] or (r["decision"]["effect"] == "write_proposal" and not r["pass"]) for r in actions),
            "precision": correct / len(admitted) if admitted else None,
            "reasons": dict(Counter(r["decision"]["reason"] for r in actions))}


def freeze(args):
    historical = set()
    for path in args.history:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if "question" in row:
                historical.add(canonical(row["question"]))
            elif "messages" in row:
                historical.update(canonical(m["content"]) for m in row["messages"] if m["role"] == "user")
            else:
                raise ValueError("historical_question_schema_unknown")
    data = validate_external(json.loads(args.input.read_text()), historical)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "corpus.json").write_bytes(args.input.read_bytes())
    for split in ("calibration", "confirmation"):
        cases = [c for c in data["cases"] if c["split"] == split]
        if not cases:
            raise ValueError("both_splits_required")
        target = args.output / split
        target.mkdir()
        write(target / "cases.json", cases)
        write(target / "requests.json", [{"id": c["id"], "question": c["question"], "modality": c["modality"]} for c in cases])
    write(args.output / "manifest.json", {"schema": "lantern.external-english-freeze.v1",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "input_sha256": digest(args.input),
        "files": {str(p.relative_to(args.output)): digest(p) for p in sorted(args.output.rglob("*.json"))},
        "history": {str(p.resolve()): digest(p) for p in args.history},
        "audio_files": {c["audio"]["path"]: c["audio"]["sha256"] for c in data["cases"] if c["modality"] == "human_speech"},
        "normalized_exact_overlap": 0, "historical_questions": len(historical),
        "authorship": "Externally declared and review-referenced; this tool cannot authenticate an author or prove template independence.",
        "qualified": False})


def evaluate(args):
    manifest = json.loads((args.freeze / "manifest.json").read_text())
    for name, sha in manifest["files"].items():
        if digest(args.freeze / name) != sha:
            raise ValueError("external_freeze_changed")
    for name, sha in manifest["audio_files"].items():
        if digest(Path(name)) != sha:
            raise ValueError("frozen_audio_changed")
    requests = json.loads((args.freeze / args.split / "requests.json").read_text())
    transcripts = None
    if args.transcripts:
        transcripts = json.loads(args.transcripts.read_text())
        if type(transcripts) is not list or [r["id"] for r in transcripts] != [r["id"] for r in requests]:
            raise ValueError("transcript_order_mismatch")
        for row in transcripts:
            if (set(row) != {"id", "text", "asr_profile_sha256", "state"} or row["state"] != "final"
                    or type(row["text"]) is not str or type(row["asr_profile_sha256"]) is not str
                    or len(row["asr_profile_sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in row["asr_profile_sha256"])):
                raise ValueError("final_asr_profile_required")
    if any(r["modality"] == "human_speech" for r in requests) and transcripts is None:
        raise ValueError("human_speech_requires_actual_final_asr_not_reference_text")
    args.output.mkdir(parents=True, exist_ok=False)
    source_hashes = {n: digest(Path(__file__).with_name(n)) for n in SOURCES}
    source = args.output / "source"
    source.mkdir()
    for name in SOURCES:
        (source / name).write_bytes(Path(__file__).with_name(name).read_bytes())
    write(args.output / "registration.json", {"freeze_sha256": digest(args.freeze / "manifest.json"),
        "transcripts_sha256": digest(args.transcripts) if args.transcripts else None,
        "sources": source_hashes,
        "variant": "disabled_exact_grammar", "gold_seen_before_predictions": False,
        "split": args.split, "execution": "proposals_only", "qualified": False})
    outputs = []
    with (args.output / "outputs.jsonl").open("x") as handle:
        for i, request in enumerate(requests):
            text = transcripts[i]["text"] if transcripts else request["question"]
            started = time.perf_counter_ns()
            decision = admit_current(text, allow_exact_grammar=True) if text.strip() else {"lane": "answer", "calls": [], "effect": "none", "reason": "no_speech"}
            output = {"id": request["id"], "input": text, "decision": decision,
                      "milliseconds": (time.perf_counter_ns() - started) / 1e6}
            outputs.append(output)
            handle.write(json.dumps(output, ensure_ascii=False) + "\n")
            handle.flush()
    if any(digest(Path(__file__).with_name(n)) != sha for n, sha in source_hashes.items()):
        raise ValueError("registered_source_changed")
    cases = json.loads((args.freeze / args.split / "cases.json").read_text())
    if [r["id"] for r in cases] != [r["id"] for r in outputs]:
        raise ValueError("gold_request_order_mismatch")
    actions = [{**case, **output, **score(case, output["decision"]["calls"], None)} for case, output in zip(cases, outputs)]
    write(args.output / "actions.json", actions)
    times = sorted(r["milliseconds"] for r in outputs)
    write(args.output / "RESULT.json", {**summarize_actions(actions),
        "by_modality": {modality: summarize_actions([r for r in actions if r["modality"] == modality]) for modality in sorted({r["modality"] for r in actions})},
        "proposal_ms_p50": statistics.median(times), "proposal_ms_p95": times[int(.95 * (len(times) - 1))],
        "qualified": False, "limitations": "Exact facade/argument checks, no retrieval relevance or answer success. External authorship declarations require trusted review. Local proposal time excludes ASR, authenticated execution and answer/display."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("freeze")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--history", type=Path, nargs="+", required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("evaluate")
    p.add_argument("--freeze", type=Path, required=True)
    p.add_argument("--split", choices=("calibration", "confirmation"), required=True)
    p.add_argument("--transcripts", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    (freeze if args.command == "freeze" else evaluate)(args)


if __name__ == "__main__":
    main()
