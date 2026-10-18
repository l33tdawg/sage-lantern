"""Local candidate controller. No network, SAGE client, or production integration."""
from dataclasses import dataclass
from intent_gate import decide, plan_matches
from contract import validate_plan, InvalidPlan
from literal_note import literal_note


@dataclass(frozen=True)
class Lane:
    name: str
    capabilities: frozenset[str]
    context_limit: int
    available: bool = True


class Router:
    def __init__(self, policy, lanes, specialist_adapter_sha256):
        if policy["adapter_sha256"] != specialist_adapter_sha256:
            raise ValueError("calibration_adapter_mismatch")
        self.policy = policy
        self.lanes = {lane.name: lane for lane in lanes}
        for name, capability in [("answer", "text"), ("specialist", "tool_proposal"), ("vision", "image")]:
            if name not in self.lanes or capability not in self.lanes[name].capabilities:
                raise ValueError("required_lane_capability: "+name)

    def route(self, judgment, *, has_image=False):
        if has_image:
            if not self.lanes["vision"].available:
                return {"lane":"unavailable","reason":"vision_unavailable","calls":[]}
            return {"lane":"vision","reason":"image_present","calls":[]}
        if not self.lanes["answer"].available:
            return {"lane":"unavailable","reason":"answer_lane_unavailable","calls":[]}
        result=decide(judgment["probabilities"],label_mass=judgment["label_mass"],order_agrees=judgment["order_agrees"],
                      policy=self.policy,available=self.lanes["specialist"].available,
                      prompt_tokens=max(o["prompt_tokens"] for o in judgment["orders"]))
        if result["lane"] == "specialist" and max(o["prompt_tokens"] for o in judgment["orders"])>self.lanes["specialist"].context_limit:
            return {"lane":"answer","reason":"specialist_context_limit","calls":[]}
        return result

    def admit(self, decision, calls, *, parse_error=None, question=None):
        """No partial calls, no fallback tool escalation after a negative intent."""
        if decision["lane"] != "specialist":
            return {**decision,"calls":[],"effect":"none"}
        try:
            if parse_error:
                raise InvalidPlan(parse_error)
            validate_plan(calls)
            if not plan_matches(decision["intent"],calls):
                raise InvalidPlan("intent_plan_disagreement")
            extracted=literal_note(question) if question and decision["intent"]=="save" else None
            changed=False
            if extracted is not None:
                changed=calls[0]["arguments"]!=extracted
                calls=[{"name":"save_private_note","arguments":extracted}]
                validate_plan(calls)
        except (InvalidPlan,ValueError) as exc:
            return {**decision,"lane":"answer","calls":[],"effect":"none","reason":str(exc),"plan_rejected":True}
        return {**decision,"calls":calls,"literal_fields_copied":extracted is not None,"literal_fields_changed":changed,
                "effect":"write_proposal" if decision["intent"]=="save" else "read_proposal"}


def reply_to_save(receipt):
    """A learned model never decides whether a write actually succeeded."""
    if receipt.get("ok") is True and isinstance(receipt.get("note_id"),str) and receipt["note_id"]:
        return "Your private note was saved."
    return "Your private note was not saved."
