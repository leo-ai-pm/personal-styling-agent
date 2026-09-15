import io
import json
import unittest
from unittest.mock import patch

from m2.core import Fault, demo_resources, new_state
from m2.model import CompatibleModel


class SemanticGuardTests(unittest.TestCase):
    def setUp(self):
        self.r=demo_resources('2026-09-05T12:00:00+00:00',False)
        self.state=new_state(self.r.data['tenant_id'],True,'2026-09-05T12:00:00+00:00')
        self.model=CompatibleModel();self.model.key='test-not-a-real-credential';self.model.base='https://example.invalid/v1';self.model.name='test-model'

    def response(self,value):
        return io.BytesIO(json.dumps({'model':'test-model','choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}],
                                      'usage':{'prompt_tokens':11,'completion_tokens':7}}).encode())

    def test_independent_check_changes_misparsed_browse_to_readonly(self):
        with patch('urllib.request.OpenerDirector.open',side_effect=[self.response({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True}),self.response({'decision':'browse','slots':['shoes'],'whole_look':False})]):
            intent,meta=self.model.parse('我想先看看鞋子',self.state,[],self.r)
        self.assertEqual(intent['operation'],'browse_catalog');self.assertEqual(intent['query_slots'],['shoes'])
        self.assertEqual(meta['api_calls'],2);self.assertEqual(meta['input_tokens'],22);self.assertEqual(meta['output_tokens'],14)
        self.assertEqual(len(meta['api_usage']),2)

    def test_conflict_blocks_updates_even_if_main_parser_lost_keep(self):
        with patch('urllib.request.OpenerDirector.open',side_effect=[self.response({'operation':'modify_current','updates':{'budget_max_fen':90000},'replace_slots':['shoes']}),self.response({'decision':'clarify','slots':['shoes'],'whole_look':False})]):
            intent,_=self.model.parse('保留鞋子，但鞋子换成浅色的。',self.state,[],self.r)
        self.assertTrue(intent['ambiguous']);self.assertEqual(intent['updates'],{})

    def test_purchase_question_does_not_reach_cart_operation(self):
        with patch('urllib.request.OpenerDirector.open',side_effect=[self.response({'operation':'add_bundle_to_cart','accepted':True}),self.response({'decision':'consult_purchase','slots':[],'whole_look':True})]):
            intent,_=self.model.parse('整套能否一起加购',self.state,[],self.r)
        self.assertEqual(intent['operation'],'consult');self.assertTrue(intent['inquiry']['whole_look'])

    def test_guard_failure_does_not_return_unverified_intent(self):
        with patch('urllib.request.OpenerDirector.open',side_effect=[self.response({'operation':'modify_current'}),self.response({'unexpected':'approve'})]):
            with self.assertRaises(Fault) as error:self.model.parse('换一套',self.state,[],self.r)
        self.assertEqual(error.exception.code,'MODEL_OUTPUT_INVALID')

    def test_each_api_request_is_accounted_and_shares_deadline(self):
        calls=[]
        with patch('urllib.request.OpenerDirector.open',side_effect=[self.response({'operation':'modify_current'}),self.response({'decision':'execute','slots':[],'whole_look':False})]) as request:
            with self.model.call_accounting(lambda:calls.append('request')):
                _,meta=self.model.parse('换一套',self.state,[],self.r)
        self.assertEqual(len(calls),2);self.assertEqual(meta['api_calls'],2)
        self.assertLessEqual(request.call_args_list[1].kwargs['timeout'],request.call_args_list[0].kwargs['timeout'])

    def test_service_quota_counts_guard_and_never_commits_when_it_is_blocked(self):
        from m2.service import Service
        from m2.runtime import RequestLimits
        body={'contract_version':'0.1.0','request_id':'quota','turn_id':'quota','idempotency_key':'quota','expected_state_revision':0,'user_utterance':'预算800，日常上班穿'}
        primary={'operation':'initial_recommendation','updates':{'budget_max_fen':80000,'scene':'scene:work_commute'}}
        limits=RequestLimits(daily_calls=1);service=Service(self.r,self.model,lambda:'2026-09-05T12:00:00+00:00',limits=limits,remember_messages=True)
        sid=service.create(True)['state']['session_id'];before=service.view(sid)
        with patch('urllib.request.OpenerDirector.open',side_effect=[self.response(primary)]) as request:
            with self.assertRaises(Fault) as error:service.turn(sid,body)
        self.assertEqual(error.exception.code,'DAILY_LIMIT_REACHED');self.assertEqual(request.call_count,1)
        self.assertEqual(service.view(sid),before)
        limits=RequestLimits(daily_calls=2);service=Service(self.r,self.model,lambda:'2026-09-05T12:00:00+00:00',limits=limits)
        sid=service.create(True)['state']['session_id']
        with patch('urllib.request.OpenerDirector.open',side_effect=[self.response(primary),self.response({'decision':'execute','slots':[],'whole_look':False})]):
            result=service.turn(sid,body)
        self.assertEqual(limits.daily_count,2);self.assertEqual(result['metrics']['api_calls'],2)

    def test_guard_receives_budget_proposal_and_recent_question(self):
        dialog={'proposal':{'updates':{'budget_max_fen':73700}},'no_match':{'message':'是否提高预算到737元？','reason_codes':['BUDGET_CONFLICT'],'suggested_relaxation':{'action':'ask_user_to_raise_budget'}},'retry_intent':{'operation':'initial_recommendation'},'recent_messages':[{'role':'assistant','content':'是否提高预算到737元？'}]}
        with patch('urllib.request.OpenerDirector.open',side_effect=[self.response({'operation':'initial_recommendation','relaxation_response':'decline'}),self.response({'decision':'execute','slots':[],'whole_look':False})]) as request:
            intent,_=self.model.parse('不愿意',self.state,[],self.r,dialog)
        context=json.loads(json.loads(request.call_args_list[1].args[0].data)['messages'][1]['content'])
        self.assertEqual(context['dialog_context']['proposal'],dialog['proposal'])
        self.assertEqual(context['dialog_context']['recent_messages'],dialog['recent_messages'])
        self.assertEqual(intent['relaxation_response'],'decline')

    def test_guard_receives_size_confirmation_and_primary_sees_active_exclusions(self):
        self.state['constraints'].append({'kind':'forbid_attribute','value':'color:ivory','target_slot':'top','status':'active','strength':'hard','scope':'session'})
        dialog={'confirmation':{'field':'usual_sizes','values':self.state['profile']['usual_sizes']}}
        with patch('urllib.request.OpenerDirector.open',side_effect=[self.response({'operation':'initial_recommendation','confirmation_response':'confirm'}),self.response({'decision':'execute','slots':[],'whole_look':False})]) as request:
            intent,_=self.model.parse('是的',self.state,[],self.r,dialog)
        first=json.loads(json.loads(request.call_args_list[0].args[0].data)['messages'][1]['content'])
        checked=json.loads(json.loads(request.call_args_list[1].args[0].data)['messages'][1]['content'])
        self.assertEqual(checked['dialog_context']['confirmation'],dialog['confirmation'])
        self.assertEqual(first['active_exclusions'][0]['value'],'color:ivory')
        self.assertTrue(all(c['status']=='active' for c in first['state']['constraints']))
        self.assertEqual(intent['confirmation_response'],'confirm')


if __name__=='__main__':unittest.main()
