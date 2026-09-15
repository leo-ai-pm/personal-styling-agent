import io
import json
import time
import unittest
import urllib.error
from unittest.mock import patch
from copy import deepcopy
from m2.core import demo_resources,new_state,Fault
from m2.model import CompatibleModel

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.r=demo_resources('2026-09-05T08:00:00+00:00',False);self.s=new_state(self.r.data['tenant_id'],True,'2026-09-05T08:00:00+00:00')
        # Transport-only tests isolate one request; execution guards have dedicated tests.
        with patch.dict('os.environ',{'YIXU_MODEL_PROVIDER':'compatible'},clear=True):self.m=CompatibleModel(semantic_guard=False)
        self.m.key='test-only-not-a-real-secret';self.m.base='https://example.invalid/v1';self.m.name='test-model'
    def response(self,intent,finish='stop'):
        return io.BytesIO(json.dumps({'model':'test-model','choices':[{'finish_reason':finish,'message':{'content':json.dumps(intent)}}],'usage':{'prompt_tokens':8,'completion_tokens':9}}).encode())
    def test_chat_request_and_usage_are_grounded(self):
        with patch('urllib.request.OpenerDirector.open',return_value=self.response({'updates':{'budget_max_fen':80000}})) as op:
            intent,meta=self.m.parse('预算800',self.s,[],self.r)
            req=op.call_args[0][0];payload=json.loads(req.data)
            self.assertEqual(req.full_url,'https://example.invalid/v1/chat/completions');self.assertEqual(payload['model'],'test-model');self.assertEqual(payload['response_format'],{'type':'json_object'});self.assertEqual(meta['input_tokens'],8);self.assertEqual(meta['output_tokens'],9);self.assertEqual(intent['updates']['budget_max_fen'],80000)
    def test_illegal_model_fields_cannot_reach_state(self):
        for intent in [{'sku_ids':['imaginary']},{'updates':{'budget_max_fen':True}},{'updates':{'usual_sizes':{'shoes':{'system':'clothing_alpha','value':'M'}}}}]:
            with patch('urllib.request.OpenerDirector.open',return_value=self.response(intent)):
                with self.assertRaises(Fault):self.m.parse('ignored',self.s,[],self.r)
    def test_truncated_output_is_error(self):
        with patch('urllib.request.OpenerDirector.open',return_value=self.response({},'length')):
            with self.assertRaises(Fault) as e:self.m.parse('ignored',self.s,[],self.r)
            self.assertEqual(e.exception.code,'MODEL_OUTPUT_INVALID')
    def test_wall_clock_timeout_including_slow_body(self):
        self.m.timeout=.02
        with patch.object(self.m,'_parse',side_effect=lambda *a:time.sleep(.1)):
            start=time.monotonic()
            with self.assertRaises(Fault) as e:self.m.parse('ignored',self.s,[],self.r)
            self.assertEqual(e.exception.code,'MODEL_UNAVAILABLE');self.assertLess(time.monotonic()-start,.09)
    def test_no_secret_in_error_response(self):
        with patch('urllib.request.OpenerDirector.open',side_effect=TimeoutError(self.m.key)):
            with self.assertRaises(Fault) as e:self.m.parse('ignored',self.s,[],self.r)
            self.assertNotIn(self.m.key,e.exception.message)

    def test_provider_json_modes(self):
        for provider in ['qwen','deepseek']:
            self.m.provider=provider
            with patch('urllib.request.OpenerDirector.open',return_value=self.response({})) as op:
                self.m.parse('ignored',self.s,[],self.r)
                payload=json.loads(op.call_args[0][0].data)
                if provider=='qwen':
                    self.assertIs(payload['enable_thinking'],False);self.assertNotIn('thinking',payload)
                else:
                    self.assertEqual(payload['thinking'],{'type':'disabled'});self.assertNotIn('enable_thinking',payload)

    def test_model_context_does_not_replay_consumed_keep_or_mutate_state(self):
        self.s['constraints'].append({'constraint_id':'old-keep','kind':'keep_sku','status':'consumed','target_slot':'shoes','scope':'next_recommendation'})
        before=deepcopy(self.s)
        with patch('urllib.request.OpenerDirector.open',return_value=self.response({})) as op:
            self.m.parse('再搭一套',self.s,[],self.r)
            context=json.loads(json.loads(op.call_args[0][0].data)['messages'][1]['content'])
            self.assertFalse(any(c['kind']=='keep_sku' for c in context['state']['constraints']))
            self.assertTrue(all('constraint_id' not in c and 'created_at' not in c for c in context['state']['constraints']))
            self.assertEqual(self.s,before)

    def test_upstream_errors_redacted_and_classified(self):
        for status,code in [(401,'MODEL_AUTH_ERROR'),(403,'MODEL_AUTH_ERROR'),(429,'MODEL_RATE_LIMITED'),(500,'MODEL_HTTP_ERROR')]:
            err=urllib.error.HTTPError(self.m.base,status,self.m.key,{},io.BytesIO(self.m.key.encode()))
            with patch('urllib.request.OpenerDirector.open',side_effect=err):
                with self.assertRaises(Fault) as caught:self.m.parse('ignored',self.s,[],self.r)
                self.assertEqual(caught.exception.code,code);self.assertNotIn(self.m.key,str(caught.exception))

if __name__=='__main__':unittest.main()
