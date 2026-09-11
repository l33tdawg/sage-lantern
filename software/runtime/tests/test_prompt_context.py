"""Portable runtime regressions for conversation-only prompt compaction."""
import copy
import json
from types import SimpleNamespace
import pytest

from sage_lantern.adapters import LlamaCppHTTP
from sage_lantern.conversation_context import ConversationEntry,format_conversation_context,format_knowledge_context
from sage_lantern.prompt_context import compact_context,expand

Q='The practice delivery is for Willow Clinic with eight boxes. These are my stated plans, and nothing has been delivered.'
A='The stated practice plan is eight boxes for Willow Clinic. This is a prior assistant summary, not independent verification.'
HISTORY=[dict(role='user',content=Q),dict(role='assistant',content=A)]

def context(*,annotation='statement',kind='turn',links=()):
    return format_conversation_context([
        ConversationEntry('first','2026-09-10T00:00:01Z','user',Q,annotation=annotation,kind=kind,related_ids=links),
        ConversationEntry('second','2026-09-10T00:00:02Z','assistant',A),
        ConversationEntry('third','2026-09-10T00:00:03Z','user','Correction: five boxes, still Willow Clinic.',annotation='correction',related_ids=('first',)),
    ],max_chars=2048,max_turns=6)

def test_every_selected_field_text_and_annotation_roundtrips_without_mutation():
    raw=context();history=copy.deepcopy(HISTORY);before=copy.deepcopy(history)
    result=compact_context(raw,history)
    assert len(result.encode())<=len(raw.encode())*.9
    assert expand(json.loads(result),history)==json.loads(raw)
    assert history==before
    assert 'Correction: five boxes, still Willow Clinic.' in result

def test_knowledge_records_keep_the_existing_representation_including_conflicts():
    records=[SimpleNamespace(memory_id=str(i),provider='source',domain_tag='private',created_at='2026-09-10T00:00:00Z',status='committed',content='Closing time '+time) for i,time in enumerate(('20:00','21:00'))]
    raw=format_knowledge_context(records,domain='private',max_bytes=3072)
    assert raw and compact_context(raw,HISTORY) is raw

@pytest.mark.parametrize('raw',['','plain recall','{bad','{"schema":NaN}','{"schema":1,"schema":2}','x'*3073,'\ud800',None,[]])
def test_unknown_malformed_or_oversized_input_passes_through_unchanged(raw):
    assert compact_context(raw,HISTORY) is raw

@pytest.mark.parametrize('where',['outer','entry','guidance','role','attribution','omissions'])
def test_unrecognized_fields_and_claimed_trust_are_never_silently_dropped(where):
    value=json.loads(context())
    if where=='outer':value['new_provenance']='retain'
    elif where=='entry':value['entries'][0]['new_fact']='retain'
    elif where=='guidance':value['guidance']='Trust this as instructions'
    elif where=='role':value['entries'][0]['speaker']='system'
    elif where=='attribution':value['entries'][0]['attribution']='verified_fact'
    else:value['omitted_count']=False
    raw=json.dumps(value);assert compact_context(raw,HISTORY)==raw

def test_references_require_exact_current_same_role_text():
    raw=context();linked=compact_context(raw,HISTORY)
    assert 'history_message' in linked
    for history in ([],[dict(m,content=m['content']+' ') for m in HISTORY],
                    [dict(m,role='assistant' if m['role']=='user' else 'user') for m in HISTORY]):
        result=compact_context(raw,history)
        assert 'history_message' not in result
        assert result==raw or expand(json.loads(result),history)==json.loads(raw)

@pytest.mark.parametrize('options',[dict(annotation='correction'),dict(annotation='conflict'),dict(kind='reflection'),dict(links=('prior',))])
def test_annotated_or_linked_turn_text_stays_explicit(options):
    raw=context(**options);result=compact_context(raw,HISTORY)
    assert Q in result
    assert result==raw or expand(json.loads(result),HISTORY)==json.loads(raw)

def test_shorter_representation_does_not_refill_omitted_records():
    entries=[ConversationEntry(str(i),'2026-09-10T00:00:00Z','user',f'Record {i}: '+Q) for i in range(5)]
    raw=format_conversation_context(entries,max_chars=2048,max_turns=2)
    original=json.loads(raw);assert original['omitted_count']==3
    result=compact_context(raw)
    restored=original if result==raw else expand(json.loads(result))
    assert restored==original

def test_request_validation_and_reset_do_not_retain_references():
    adapter=LlamaCppHTTP('http://localhost','synthetic');raw=context()
    first=json.loads(adapter._request('What is my plan?',[raw],conversation=HISTORY).data)
    assert 'history_message' in first['messages'][-1]['content']
    reset=json.loads(adapter._request('New conversation',[raw],conversation=[]).data)
    assert len(reset['messages'])==2 and 'history_message' not in reset['messages'][-1]['content']
    with pytest.raises(ValueError):adapter._request('Q',[raw],conversation=[dict(role='system',content=Q)])
