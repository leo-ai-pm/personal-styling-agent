import json
import http.client
import hashlib
from copy import deepcopy
from pathlib import Path
import tempfile
import sqlite3
import threading
import unittest
from unittest.mock import patch

from m2.core import Fault, VERSION, demo_resources, Resources
from m2.service import Service
from m2.runtime import RuntimeOptions, RequestLimits
from m2.server import make_server


class RecordingModel:
    configured = True
    def __init__(self): self.contexts = []
    def parse(self, utterance, state, history, resources, dialog_context=None):
        self.contexts.append(dialog_context)
        return {'operation': 'browse_catalog'}, {'mode': 'injected_test'}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.at = '2026-09-05T08:00:00+00:00'
        self.r = demo_resources(self.at, False)
        self.model = RecordingModel()

    def body(self, key='a', rev=0, text='那有什么'):
        return dict(contract_version=VERSION, request_id=key, idempotency_key=key,
                    turn_id=key, expected_state_revision=rev, user_utterance=text)

    def service(self, **kwargs):
        return Service(self.r, self.model, lambda: self.at, **kwargs)

    def test_sqlite_restart_preserves_context_idempotency_and_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'sessions.sqlite3'
            svc=self.service(db_path=path, remember_messages=True)
            sid=svc.create(owner_id='visitor-a')['state']['session_id']
            result=svc.turn(sid,self.body(),owner_id='visitor-a')
            before=svc.view(sid,owner_id='visitor-a');svc.close()
            resumed=self.service(db_path=path, remember_messages=True)
            self.assertEqual(resumed.view(sid,owner_id='visitor-a'),before)
            self.assertEqual(resumed.turn(sid,self.body(),owner_id='visitor-a'),result)
            with self.assertRaises(Fault): resumed.view(sid,owner_id='visitor-b')
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            resumed.close()

    def test_expiry_and_delete_remove_sqlite_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'sessions.sqlite3';svc=self.service(db_path=path)
            sid=svc.create()['state']['session_id'];svc.delete(sid);svc.close()
            svc=self.service(db_path=path);self.assertNotIn(sid,svc.sessions)
            sid=svc.create()['state']['session_id'];self.at='2026-09-06T08:00:01+00:00'
            with self.assertRaises(Fault):svc.view(sid)
            svc.close();svc=self.service(db_path=path);self.assertNotIn(sid,svc.sessions);svc.close()

    def test_recent_messages_are_bounded_redacted_and_sent_next_turn(self):
        svc=self.service(remember_messages=True);sid=svc.create()['state']['session_id']
        svc.turn(sid,self.body(text='那有什么 邮箱 test@example.com 手机 13812345678 sk-'+'a'*32+''))
        svc.turn(sid,self.body('b'))
        context=json.dumps(self.model.contexts[-1],ensure_ascii=False)
        self.assertIn('recent_messages',context);self.assertIn('那有什么',context)
        self.assertNotIn('test@example.com',context);self.assertNotIn('13812345678',context);self.assertNotIn('sk-abcdef',context)
        for i in range(11):svc.turn(sid,self.body(str(i)))
        self.assertEqual(len(svc.view(sid)['messages']),26)
        self.assertEqual(len(self.model.contexts[-1]['recent_messages']),20)
        before=svc.view(sid)
        with patch.object(self.model,'parse',side_effect=Fault('MODEL_UNAVAILABLE')):
            with self.assertRaises(Fault):svc.turn(sid,self.body('failed'))
        self.assertEqual(svc.view(sid),before)

    def test_read_only_concurrent_turns_cannot_overwrite_dialog(self):
        svc=self.service(remember_messages=True);sid=svc.create()['state']['session_id']
        barrier=threading.Barrier(2);original=self.model.parse;results=[]
        def parse(*args):barrier.wait(timeout=3);return original(*args)
        self.model.parse=parse
        def run(key):
            try:results.append(svc.turn(sid,self.body(key))['decision']['status'])
            except Fault as error:results.append(error.code)
        threads=[threading.Thread(target=run,args=(str(i),)) for i in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5)
        self.assertCountEqual(results,['action_completed','STATE_REVISION_CONFLICT'])
        self.assertEqual(svc.view(sid)['conversation_revision'],1)

    def test_inventory_refresh_is_independent_and_request_bound(self):
        original=self.r.inventory['inventory_snapshot_id'];calls=[]
        def refresh(at):calls.append(at);return demo_resources(at,False)
        svc=self.service(refresh_factory=refresh);sid=svc.create()['state']['session_id']
        self.at='2026-09-05T19:57:00+00:00';result=svc.turn(sid,self.body())
        self.assertEqual(len(calls),1);self.assertNotEqual(svc.r.inventory['inventory_snapshot_id'],original)
        self.assertEqual(result['decision']['action']['resources']['inventory_snapshot_id'],svc.r.inventory['inventory_snapshot_id'])

    def test_inventory_renewal_never_replenishes_stock(self):
        self.r.stock['M1V2-T102-S']['available_qty']=0
        svc=self.service(refresh_factory=lambda at:demo_resources(at,False))
        self.at='2026-09-05T19:57:00+00:00'
        self.assertEqual(svc.resources().stock['M1V2-T102-S']['available_qty'],0)

    def test_view_context_is_session_owned_and_does_not_persist(self):
        svc=self.service(remember_messages=True);sid=svc.create()['state']['session_id']
        body=self.body();body['view_context']={'look_version_id':'foreign-look'}
        with self.assertRaises(Fault):svc.turn(sid,body)
        self.assertEqual(self.model.contexts,[])
        body['view_context']={'look_version_id':None};svc.turn(sid,body)
        self.assertEqual(self.model.contexts[-1]['view_context'],{'look_version_id':None})
        self.assertNotIn('view_context',svc.sessions[sid]['dialog_context'] or {})
        body=self.body('x');body['view_context']={'look_version_id':None,'budget_max_fen':1}
        with self.assertRaises(Fault):svc.turn(sid,body)

    def test_model_limits_leave_state_unchanged(self):
        svc=self.service(limits=RequestLimits(daily_calls=1));sid=svc.create()['state']['session_id']
        svc.turn(sid,self.body());before=svc.view(sid)
        with self.assertRaises(Fault) as error:svc.turn(sid,self.body('b'))
        self.assertEqual(error.exception.code,'DAILY_LIMIT_REACHED');self.assertEqual(before,svc.view(sid))
        self.assertEqual(svc.turn(sid,self.body())['conversation_revision'],1)

    def test_daily_quota_survives_server_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runtime.sqlite3'
            svc=self.service(db_path=path,limits=RequestLimits(daily_calls=1))
            sid=svc.create()['state']['session_id'];svc.turn(sid,self.body());svc.close()
            svc=self.service(db_path=path,limits=RequestLimits(daily_calls=1))
            with self.assertRaises(Fault) as error:svc.turn(sid,self.body('second'))
            self.assertEqual(error.exception.code,'DAILY_LIMIT_REACHED');svc.close()

    def test_sqlite_cart_retry_after_restart_is_atomic(self):
        class OutfitModel(RecordingModel):
            def parse(self,*args):return {'updates':{'budget_max_fen':80000,'scene':'scene:work_commute'}},{'mode':'injected_test'}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runtime.sqlite3';self.model=OutfitModel()
            svc=self.service(db_path=path);sid=svc.create(True)['state']['session_id']
            first=svc.turn(sid,self.body());body=self.body('cart',first['state']['state_revision'])
            del body['user_utterance'];body['action']={'operation':'add_bundle_to_cart'}
            result=svc.turn(sid,body);svc.close();svc=self.service(db_path=path)
            self.assertEqual(svc.turn(sid,body),result);self.assertEqual(len(svc.view(sid)['cart']),1);svc.close()

    def test_model_concurrency_limit_is_released_after_failure(self):
        limits=RequestLimits(model_concurrency=1);limits.acquire()
        with self.assertRaises(Fault) as error:limits.acquire()
        self.assertEqual(error.exception.code,'MODEL_BUSY');limits.release()
        svc=self.service(limits=limits);sid=svc.create()['state']['session_id']
        with patch.object(self.model,'parse',side_effect=Fault('MODEL_UNAVAILABLE')):
            with self.assertRaises(Fault):svc.turn(sid,self.body())
        self.assertEqual(svc.turn(sid,self.body('retry'))['decision']['status'],'action_completed')

    def test_context_truncation_does_not_exceed_total_budget(self):
        svc=self.service(remember_messages=True);sid=svc.create()['state']['session_id']
        for index in range(12):svc.turn(sid,self.body(str(index),text='上衣'*1499))
        messages=self.model.contexts[-1]['recent_messages']
        self.assertLessEqual(sum(len(message['content']) for message in messages),16000)
        self.assertEqual(len(messages)%2,0);self.assertEqual(messages[0]['role'],'user')
        self.assertEqual(len(svc.view(sid)['messages']),24)

    def test_restart_keeps_chat_before_model_context_window(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runtime.sqlite3';svc=self.service(db_path=path,remember_messages=True)
            sid=svc.create()['state']['session_id']
            for index in range(12):svc.turn(sid,self.body(str(index),text=f'第 {index} 轮有哪些商品'))
            before=svc.view(sid)['messages'];svc.close()
            svc=self.service(db_path=path,remember_messages=True)
            self.assertEqual(svc.view(sid)['messages'],before)
            self.assertEqual(before[0]['content'],'第 0 轮有哪些商品')
            svc.turn(sid,self.body('next'))
            recent=self.model.contexts[-1]['recent_messages']
            self.assertEqual(len(recent),20);self.assertEqual(recent[0]['content'],'第 2 轮有哪些商品');svc.close()

    def test_full_session_rejects_new_turn_without_discarding_history(self):
        svc=self.service(remember_messages=True);sid=svc.create()['state']['session_id']
        svc.turn(sid,self.body());row=svc.sessions[sid]
        for index in range(199):row['cache'][f'old-{index}']=('test',{})
        before=svc.view(sid)
        with self.assertRaises(Fault) as error:svc.turn(sid,self.body('exceeds'))
        self.assertEqual(error.exception.code,'CAPACITY_EXCEEDED');self.assertIn('200',error.exception.message)
        self.assertEqual(svc.view(sid),before)
        self.assertEqual(svc.turn(sid,self.body())['conversation_revision'],1)

    def test_explicit_conversation_revision_rejects_stale_readonly_request(self):
        svc=self.service();sid=svc.create()['state']['session_id']
        svc.turn(sid,self.body());body=self.body('second');body['expected_conversation_revision']=0
        with self.assertRaises(Fault) as error:svc.turn(sid,body)
        self.assertEqual(error.exception.code,'STATE_REVISION_CONFLICT')
        body['expected_conversation_revision']=1
        self.assertEqual(svc.turn(sid,body)['conversation_revision'],2)

    def test_persisted_resource_refresh_audit_keeps_zero_stock(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runtime.sqlite3'
            svc=self.service(db_path=path,refresh_factory=lambda at:demo_resources(at,False))
            svc.r.stock['M1V2-T102-S']['available_qty']=0
            self.at='2026-09-05T19:57:00+00:00';resources=svc.resources()
            self.assertEqual(svc.store.value('inventory_snapshots')[0]['resources'],resources.binding)
            svc.close();svc=self.service(db_path=path,refresh_factory=lambda at:demo_resources(at,False))
            self.assertEqual(svc.resources().stock['M1V2-T102-S']['available_qty'],0);svc.close()

    def test_pending_request_keeps_its_resource_snapshot_when_service_renews(self):
        svc=self.service(refresh_factory=lambda at:demo_resources(at,False));sid=svc.create()['state']['session_id']
        started=self.r.binding['inventory_snapshot_id'];original=self.model.parse
        def parse(*args):
            self.assertEqual(args[3].binding['inventory_snapshot_id'],started)
            self.at='2026-09-05T19:57:00+00:00';svc.resources()
            return original(*args)
        self.model.parse=parse
        result=svc.turn(sid,self.body())
        self.assertNotEqual(svc.r.binding['inventory_snapshot_id'],started)
        self.assertEqual(result['decision']['resources']['inventory_snapshot_id'],started)

    def test_storage_failure_does_not_commit_partial_conversation(self):
        with tempfile.TemporaryDirectory() as folder:
            svc=self.service(db_path=Path(folder)/'runtime.sqlite3',remember_messages=True)
            sid=svc.create()['state']['session_id'];before=svc.view(sid)
            with patch.object(svc.store,'save',side_effect=sqlite3.OperationalError('injected disk failure')):
                with self.assertRaises(sqlite3.OperationalError):svc.turn(sid,self.body())
            self.assertEqual(svc.view(sid),before)
            self.assertEqual(svc.turn(sid,self.body())['conversation_revision'],1);svc.close()

    def test_preview_clone_preserves_restricted_asset_authority(self):
        fixture=deepcopy(self.r.data);asset_bytes=b'isolated asset-path test fixture'
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();assets=root/'data/m3_draft/assets';assets.mkdir(parents=True)
            for index,look in enumerate(fixture['look_assets']):
                look['image_url']=f'/data/m3_draft/assets/look_{index}.png'
                look['image_sha256']=hashlib.sha256(asset_bytes).hexdigest()
                (assets/f'look_{index}.png').write_bytes(asset_bytes)
            with patch('m2.core.ROOT',root):
                with self.assertRaises(Fault):Resources(fixture,display=True)
                preview=Resources(fixture,display=True,preview=True);cloned=preview.clone(fixture)
                self.assertTrue(cloned.preview);self.assertTrue(cloned.display)
                bad=deepcopy(fixture);bad['look_assets'][0]['image_url']='/data/m3_draft/assets/../../private.png'
                with self.assertRaises(Fault):preview.clone(bad)
                (assets/'look_0.png').write_bytes(b'changed contents')
                with self.assertRaises(Fault):preview.clone(fixture)

    def test_http_public_entry_is_automatic_and_visitors_stay_isolated(self):
        svc=self.service(remember_messages=True)
        options=RuntimeOptions(public_origin='https://preview.example.com')
        server=make_server(svc,0,options=options)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,method='GET',body=None,cookie=None,origin=None,host='preview.example.com'):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            headers={'Host':host}
            if cookie:headers['Cookie']=cookie
            if origin:headers['Origin']=origin
            if body is not None:headers['Content-Type']='application/json';body=json.dumps(body)
            connection.request(method,path,body=body,headers=headers)
            response=connection.getresponse();result=response.status,response.getheader('Set-Cookie'),response.read()
            connection.close();return result
        try:
            status,cookie,html=request('/')
            self.assertEqual(status,200);self.assertIn(b'app.js',html);self.assertNotIn('邀请码',html.decode())
            for flag in ['Secure','HttpOnly','SameSite=Strict']:self.assertIn(flag,cookie)
            visitor_cookie=cookie.split(';')[0]
            self.assertIsNone(request('/',cookie=visitor_cookie)[1])
            health=json.loads(request('/api/health')[2])
            self.assertEqual(health['access_mode'],'public');self.assertFalse(health['access_control_enabled'])
            self.assertTrue(health['visitor_isolation_enabled']);self.assertIsNone(request('/api/health')[1])
            self.assertEqual(request('/api/catalog')[0],200)
            status,_,data=request('/api/sessions','POST',{},visitor_cookie,'https://preview.example.com')
            self.assertEqual(status,201);sid=json.loads(data)['state']['session_id'];path='/api/sessions/'+sid
            self.assertEqual(request(path,cookie=visitor_cookie)[0],200)
            stranger=request('/')[1].split(';')[0]
            for method,suffix,body in [('GET','',None),('POST','/turns',self.body()),('DELETE','',None)]:
                self.assertEqual(request(path+suffix,method,body,stranger,'https://preview.example.com')[0],404)
            self.assertEqual(request(path,'DELETE',cookie=visitor_cookie,origin='https://evil.example')[0],403)
            self.assertEqual(request('/api/sessions','POST',{},visitor_cookie)[0],403)
            self.assertEqual(request('/api/health',host='evil.example')[0],403)
            for invalid in ['yixu_visitor=invalid','yixu_visitor='+options.issue_cookie(at=1)]:
                status,new_cookie,_=request(path,cookie=invalid)
                self.assertEqual(status,404);self.assertIsNotNone(new_cookie)
                self.assertEqual(request('/',cookie=invalid)[0],200)
            self.assertEqual(request(path,cookie=visitor_cookie)[0],200)
            # A direct client may create its first session without loading the HTML first.
            status,new_cookie,data=request('/api/sessions','POST',{},origin='https://preview.example.com')
            self.assertEqual(status,201)
            new_path='/api/sessions/'+json.loads(data)['state']['session_id']
            self.assertEqual(request(new_path,cookie=new_cookie.split(';')[0])[0],200)
        finally:server.shutdown();server.server_close();thread.join(3);svc.close()

    def test_public_restart_preserves_previously_invited_cookie_and_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'sessions.sqlite3'
            svc=self.service(db_path=path)
            old=RuntimeOptions(public_origin='https://preview.example.com',invite_code='old-invite')
            server=make_server(svc,0,old)
            cookie='yixu_visitor='+old.issue_cookie();visitor=old.visitor(cookie)
            before=svc.create(owner_id=visitor)
            server.server_close();svc.close()
            resumed=self.service(db_path=path)
            public=RuntimeOptions(public_origin='https://preview.example.com')
            server=make_server(resumed,0,public)
            try:
                self.assertEqual(public.visitor(cookie),visitor)
                self.assertEqual(resumed.view(before['state']['session_id'],public.visitor(cookie)),before)
            finally:server.server_close();resumed.close()

    def test_http_invitation_isolation_source_validation_and_health(self):
        svc=self.service(remember_messages=True)
        options=RuntimeOptions(public_origin='https://preview.example.com',invite_code='test-invitation')
        server=make_server(svc,0,options=options);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,method='GET',body=None,cookie=None,host='preview.example.com',origin=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            headers={'Host':host}
            if cookie:headers['Cookie']='yixu_visitor='+cookie
            if origin:headers['Origin']=origin
            if body is not None:headers['Content-Type']='application/json';body=json.dumps(body)
            connection.request(method,path,body=body,headers=headers);response=connection.getresponse()
            status=response.status;cookies=response.getheader('Set-Cookie');data=response.read();connection.close()
            return status,cookies,data
        try:
            self.assertEqual(request('/api/health')[0],200)
            self.assertTrue(json.loads(request('/api/health')[2])['ready'])
            self.assertEqual(request('/api/sessions','POST',{},origin='https://preview.example.com')[0],401)
            self.assertEqual(request('/api/health',host='evil.example')[0],403)
            self.assertEqual(request('/api/access','POST',{'invite_code':'wrong'},origin='https://preview.example.com')[0],401)
            status,cookie,_=request('/api/access','POST',{'invite_code':'test-invitation'},origin='https://preview.example.com')
            self.assertEqual(status,200);self.assertIn('HttpOnly',cookie);self.assertIn('Secure',cookie)
            cookie=cookie.split(';')[0].split('=',1)[1]
            status,_,data=request('/api/sessions','POST',{},cookie,origin='https://preview.example.com')
            self.assertEqual(status,201);sid=json.loads(data)['state']['session_id']
            self.assertEqual(request('/api/sessions/'+sid,cookie=cookie)[0],200)
            self.assertEqual(request('/api/sessions/'+sid,cookie=options.issue_cookie())[0],404)
            self.assertEqual(request('/api/sessions/'+sid,'DELETE',cookie=cookie,origin='https://evil.example')[0],403)
            self.assertEqual(request('/api/sessions/'+sid+'/bogus',cookie=cookie)[0],404)
        finally:server.shutdown();server.server_close();thread.join(3)


if __name__ == '__main__': unittest.main()
