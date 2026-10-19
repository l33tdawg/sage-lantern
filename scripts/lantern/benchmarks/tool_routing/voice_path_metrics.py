"""Offline metrics for one authenticated voice-to-answer trace clock.

No device runner or power override. Traces must be captured by trusted runtime
instrumentation; strings/flags in a trace are not independent power/identity
attestation. Synthetic or incomplete traces can never establish RK3588 acceptance.
"""
import math
import statistics


STAGES = ("end_of_speech", "asr_final", "reviewed_submit", "session_authenticated",
          "admission", "action_dispatch", "receipt_verified", "first_visible_token",
          "first_published_text", "browser_paint")
INTERVALS = {"eos_to_asr_final": ("end_of_speech", "asr_final"),
             "review_wait": ("asr_final", "reviewed_submit"),
             "submitted_to_authenticated": ("reviewed_submit", "session_authenticated"),
             "admission": ("session_authenticated", "admission"),
             "authenticated_action": ("action_dispatch", "receipt_verified"),
             "submitted_to_first_token": ("reviewed_submit", "first_visible_token"),
             "eos_to_first_token": ("end_of_speech", "first_visible_token"),
             "eos_to_published_text": ("end_of_speech", "first_published_text"),
             "eos_to_browser_paint": ("end_of_speech", "browser_paint")}


def trace_metrics(trace):
    if type(trace) is not dict or set(trace) != {"request_id", "session_binding_sha256", "profile_sha256", "clock_id", "hardware", "temperature", "provenance", "power_gate_evidence", "events_ns"}:
        raise ValueError("trace_schema")
    if (any(type(trace[k]) is not str or not trace[k] for k in trace if k != "events_ns")
            or trace["temperature"] not in {"cold", "warm"} or trace["provenance"] not in {"observed", "synthetic"}
            or any(len(trace[k]) != 64 or any(c not in "0123456789abcdef" for c in trace[k]) for k in ("session_binding_sha256", "profile_sha256"))):
        raise ValueError("trace_bindings")
    events = trace["events_ns"]
    if type(events) is not dict or set(events) not in (set(STAGES), set(STAGES) | {"first_approved_audio"}):
        raise ValueError("full_path_stages_required")
    if any(type(n) is not int or n < 0 for n in events.values()):
        raise ValueError("strict_monotonic_nanoseconds_required")
    times = [events[s] for s in STAGES]
    if times != sorted(times):
        raise ValueError("stage_order_or_clock_mismatch")
    # Receipt verification fences publication, including audible output.
    if "first_approved_audio" in events and events["first_approved_audio"] < events["receipt_verified"]:
        raise ValueError("audio_before_verified_receipt")
    result = {name + "_ms": (events[end] - events[start]) / 1e6 for name, (start, end) in INTERVALS.items()}
    if "first_approved_audio" in events:
        result["eos_to_first_approved_audio_ms"] = (events["first_approved_audio"] - events["end_of_speech"]) / 1e6
    return result


def summarize_traces(traces):
    if type(traces) is not list or not traces:
        raise ValueError("observed_traces_required")
    if len({r["request_id"] for r in traces}) != len(traces):
        raise ValueError("duplicate_trace_request")
    rows = [(r, trace_metrics(r)) for r in traces]
    profiles = {(r["hardware"], r["profile_sha256"], r["power_gate_evidence"], r["provenance"]) for r in traces}
    if len(profiles) != 1:
        raise ValueError("hardware_profile_power_or_provenance_mixed")
    result = {}
    for temperature in ("cold", "warm"):
        selected = [m for r, m in rows if r["temperature"] == temperature]
        if not selected:
            continue
        result[temperature] = {}
        for interval in sorted({k for row in selected for k in row}):
            values = sorted(row[interval] for row in selected if interval in row)
            result[temperature][interval] = {"n": len(values), "p50": statistics.median(values),
                "p95": values[max(0, math.ceil(len(values) * .95) - 1)]}
    return {"metrics": result, "profile": list(profiles)[0], "physical_acceptance": False,
            "limitations": "Offline trace arithmetic only. Trusted owner/power/profile evidence, real microphone/ASR/action/answer/display instrumentation, memory/power/contention/cancellation checks and paired baseline are separately required."}
