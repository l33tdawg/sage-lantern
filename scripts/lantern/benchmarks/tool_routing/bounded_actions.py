"""Turn accepted typed intents into action proposals without text generation."""
from contract import validate_plan,InvalidPlan
from literal_note import literal_note


def propose(decision,question):
    if decision["lane"] != "specialist":
        return {**decision,"calls":[],"effect":"none"}
    intent=decision["intent"]
    # Recall uses the original request, like Lantern's current context adapter.
    # Retrieval relevance still needs real data evaluation; term coverage is not it.
    search={"name":"search_memory","arguments":{"query":question}}
    status={"name":"get_device_status","arguments":{}}
    if intent=="search":calls=[search]
    elif intent=="status":calls=[status]
    elif intent=="parallel_reads":calls=[search,status]
    elif intent=="save":
        fields=literal_note(question)
        if fields is None:
            return {**decision,"lane":"answer","calls":[],"effect":"none","reason":"literal_note_format_unrecognized"}
        calls=[{"name":"save_private_note","arguments":fields}]
    else:
        return {**decision,"lane":"answer","calls":[],"effect":"none","reason":"unknown_action_intent"}
    try:validate_plan(calls)
    except InvalidPlan as exc:
        return {**decision,"lane":"answer","calls":[],"effect":"none","reason":str(exc)}
    return {**decision,"calls":calls,"effect":"write_proposal" if intent=="save" else "read_proposal",
            "generation_tokens":0}
