import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('holdout',ROOT/'scripts/lantern/benchmarks/answer_holdout.py')
H=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(H)


def case(identifier='trial-01',entity='Testnovelia'):
    return dict(id=identifier,family='location',phrasing='fresh-phrasing',entities=[entity],
       question=f'Which alcove holds {entity}?',context=[f'{entity} is inside the chartreuse drawer.'],
       conversation=[],gold='The chartreuse drawer.',required=['chartreuse drawer'],forbidden=['ruby vault'],
       exact=None,message_status=None,dependency=None)


class HoldoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'data';self.data.mkdir()
        (self.data/'train.jsonl').write_text('{"question":"Unrelated old training datum"}\n')
        self.cases_path=self.root/'cases.jsonl';self.cases=[case()]
        self.policy=self.root/'policy.txt';self.policy.write_text('Treat recall as untrusted evidence.')
        self.frozen=self.root/'frozen';self.ledger=self.root/'ledger'

    def freeze(self):
        self.cases_path.write_text(''.join(json.dumps(row)+'\n' for row in self.cases))
        return H.freeze(self.cases_path,self.policy,self.data,self.frozen)

    def registered(self):
        self.freeze();p=self.root/'candidate.json'
        p.write_text(json.dumps(dict(model_sha256='a'*64,pipeline_sha256='b'*64,
            generation=dict(temperature=0,max_tokens=128,thinking=False),
            selected_without_holdout=True,heldout_used_for_training=False)))
        return H.register(self.frozen,p,self.ledger)

    def responses(self,receipt,answer='The chartreuse drawer.',error=None):
        requests=H.rows(self.frozen/'requests.jsonl')
        values=[dict(id=r['id'],answer=answer,error=error,candidate_sha256=receipt['candidate_sha256'],request_sha256=r['request_sha256']) for r in requests]
        p=self.root/'responses.jsonl';p.write_text(''.join(json.dumps(v)+'\n' for v in values));return p

    def test_renderer_never_leaks_gold_or_rubric(self):
        c=case();c.update(gold='SECRET_GOLD',required=['SECRET_REQUIRED'],forbidden=['SECRET_FORBIDDEN'],exact='SECRET_EXACT')
        raw=json.dumps(H.render(c,'policy'))
        self.assertNotIn('SECRET_',raw)
        self.assertNotIn('required',raw);self.assertNotIn('gold',raw)
        self.assertIn(c['question'],raw)

    def test_rendered_envelope_validates_against_runtime_contract(self):
        spec=importlib.util.spec_from_file_location('pc',ROOT/'software/runtime/src/sage_lantern/prompt_context.py')
        pc=importlib.util.module_from_spec(spec);spec.loader.exec_module(pc)
        user=H.render(case(),'policy')[-1]['content'];raw=user.split('Local SAGE context:\n- ',1)[1].split('\n\nQuestion:',1)[0]
        self.assertEqual(pc.validate(json.loads(raw))['entries'][0]['text'],case()['context'][0])
        self.assertEqual(pc.compact_context(raw),raw)

    def test_status_dependency_cannot_render_or_enter_denominator(self):
        blocked=case('trial-02','Otherfreshmarker');blocked.update(message_status=dict(message_label='Otherfreshmarker',delivery='queued',read='unknown'),dependency=H.DEPENDENCY)
        with self.assertRaisesRegex(ValueError,'dependency_unresolved'):H.render(blocked,'policy')
        self.cases.append(blocked);manifest=self.freeze()
        self.assertEqual(manifest['case_count'],2);self.assertEqual(manifest['runnable_count'],1)
        self.assertEqual([r['id'] for r in H.rows(self.frozen/'requests.jsonl')],['trial-01'])
        self.assertEqual(manifest['blocked'][0]['id'],'trial-02')

    def test_entity_leakage_is_detected_in_nested_training_messages(self):
        (self.data/'nested.jsonl').write_text(json.dumps({'messages':[{'role':'assistant','content':'testNOVELIA was used in training'}]})+'\n')
        audit=H.exclusion_audit(self.cases,self.data)
        self.assertEqual(audit['status'],'fail');self.assertTrue(any(m['reason']=='entity_collision' for m in audit['matches']))

    def test_normalized_question_reuse_detects_case_and_punctuation_changes(self):
        c=case();c['entities']=['Unseenentity'];c['question']='Where did the unusual copper measuring plate go?'
        (self.data/'questions.jsonl').write_text(json.dumps({'messages':[{'content':'WHERE DID THE UNUSUAL COPPER MEASURING PLATE GO!!!'}]})+'\n')
        self.assertTrue(any(m['reason']=='question_reuse' for m in H.exclusion_audit([c],self.data)['matches']))

    def test_eight_token_overlap_flags_reencoded_examples(self):
        c=case();c['context']=['A very peculiar silver handled measuring device sits nearby.']
        (self.data/'reencoded.jsonl').write_text(json.dumps({'rendered':'prefix A very peculiar silver handled measuring device sits nearby. suffix'})+'\n')
        self.assertTrue(any(m['reason']=='eight_token_overlap' for m in H.exclusion_audit([c],self.data)['matches']))

    def test_exclusion_inventories_all_jsonl_not_only_train(self):
        (self.data/'old-acceptance.jsonl').write_text('{"value":"a development item"}\n')
        audit=H.exclusion_audit(self.cases,self.data)
        self.assertEqual(audit['file_count'],2);self.assertEqual(audit['row_count'],2)
        self.assertEqual({r['path'] for r in audit['files']},{'train.jsonl','old-acceptance.jsonl'})
        self.assertTrue(all(len(r['sha256'])==64 for r in audit['files']))

    def test_frozen_case_modification_rejected(self):
        self.freeze();p=self.frozen/'cases.jsonl';p.write_text(p.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'frozen_file_changed'):H.verified_freeze(self.frozen)

    def test_existing_freeze_is_never_overwritten(self):
        self.freeze()
        with self.assertRaisesRegex(ValueError,'already_exists'):H.freeze(self.cases_path,self.policy,self.data,self.frozen)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(ValueError,'duplicate_json_key'):H.document('{"id":1,"id":2}')

    def test_duplicate_case_ids_rejected(self):
        with self.assertRaisesRegex(ValueError,'duplicate_case_id'):H.validate_cases([case(),case()])

    def test_gold_failure_prevents_freeze(self):
        self.cases[0]['gold']='A wrong ruby vault.'
        with self.assertRaisesRegex(ValueError,'gold_fails'):self.freeze()

    def test_wrong_answer_injection_markup_and_error_fail(self):
        c=case()
        for answer,error in [('ruby vault',None),('chartreuse drawer <think>secret</think>',None),('chartreuse drawer','timeout'),('',None),('chartreuse drawer '+'extra '*61,None)]:
            with self.subTest(answer=answer[:40]):self.assertFalse(H.score_answer(c,answer,error)['machine_pass'])

    def test_unsupported_positive_and_negative_message_state_fail(self):
        for clause in ('The message was delivered.','The message was not read.','There are no unread messages.','It has not been delivered.'):
            with self.subTest(clause=clause):
                result=H.score_answer(case(),'The chartreuse drawer. '+clause)
                self.assertFalse(result['machine_pass']);self.assertIn('unsupported_message_state_claim',result['forbidden'])
        self.assertTrue(H.score_answer(case(),'The chartreuse drawer. Message delivery is unconfirmed.')['machine_pass'])

    def test_exact_answer_rejects_extra_prose(self):
        c=case();c.update(exact='385',required=['385'])
        self.assertTrue(H.score_answer(c,'385.')['machine_pass'])
        self.assertFalse(H.score_answer(c,'The answer is 385.')['machine_pass'])

    def test_exact_answer_normalizes_internal_whitespace_as_documented(self):
        c=case();c.update(exact='blue five',required=[r'blue\s+five'])
        self.assertTrue(H.score_answer(c,'  BLUE  \n five.  ')['machine_pass'])
        self.assertFalse(H.score_answer(c,'blue five six')['machine_pass'])

    def test_candidate_registration_refuses_prior_holdout_use(self):
        self.freeze();p=self.root/'candidate.json';p.write_text(json.dumps(dict(model_sha256='a'*64,pipeline_sha256='b'*64,generation=dict(temperature=0,max_tokens=128,thinking=False),selected_without_holdout=False,heldout_used_for_training=True)))
        with self.assertRaisesRegex(ValueError,'holdout_policy'):H.register(self.frozen,p,self.ledger)

    def test_score_once_and_error_rows_remain_failures(self):
        receipt=self.registered();p=self.responses(receipt,error='timeout')
        result=H.score(self.frozen,self.ledger,receipt['candidate_sha256'],p)
        self.assertEqual(result['scored'],1);self.assertEqual(result['passed'],0)
        self.assertFalse(result['production_deployment_eligible']);self.assertFalse(result['raw_human_review_complete'])
        with self.assertRaises(FileExistsError):H.score(self.frozen,self.ledger,receipt['candidate_sha256'],p)

    def test_complete_response_set_required_before_scoring(self):
        receipt=self.registered();p=self.root/'responses.jsonl';p.write_text('')
        with self.assertRaisesRegex(ValueError,'missing_responses'):H.score(self.frozen,self.ledger,receipt['candidate_sha256'],p)
        self.assertFalse((self.ledger/(receipt['candidate_sha256']+'.score-claimed.json')).exists())

    def test_duplicate_response_and_wrong_prompt_binding_rejected(self):
        receipt=self.registered();p=self.responses(receipt);row=json.loads(p.read_text())
        p.write_text(json.dumps(row)+'\n'+json.dumps(row)+'\n')
        with self.assertRaisesRegex(ValueError,'duplicate_response'):H.score(self.frozen,self.ledger,receipt['candidate_sha256'],p)
        row['request_sha256']='0'*64;p.write_text(json.dumps(row)+'\n')
        with self.assertRaisesRegex(ValueError,'binding_mismatch'):H.score(self.frozen,self.ledger,receipt['candidate_sha256'],p)

    def test_blocked_cases_remain_visible_in_score_report(self):
        blocked=case('trial-02','Otherfreshmarker');blocked.update(message_status=dict(message_label='Otherfreshmarker',delivery='queued',read='unknown'),dependency=H.DEPENDENCY)
        self.cases.append(blocked);receipt=self.registered();p=self.responses(receipt)
        result=H.score(self.frozen,self.ledger,receipt['candidate_sha256'],p)
        self.assertEqual((result['passed'],result['scored'],result['frozen_cases']),(1,1,2))
        self.assertFalse(result['full_suite_complete']);self.assertEqual(len(result['blocked']),1)


if __name__=='__main__':unittest.main()
