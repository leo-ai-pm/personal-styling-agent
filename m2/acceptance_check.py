"""Live HTTP checks for the candidate catalog; never substitutes structured fake model output."""
import argparse,json,uuid,urllib.request,urllib.error,time,statistics
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

def journey(base,clothes,shoe,budget):
    rows=[]
    def api(path,body=None,method=None):
        req=urllib.request.Request(base+path,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers={'Content-Type':'application/json'},method=method)
        with urllib.request.urlopen(req,timeout=25) as response:return json.load(response)
    session=api('/api/sessions',{'demo_profile':False});sid=session['state']['session_id'];view=None
    def turn(text=None,action=None):
        nonlocal session
        nonce=uuid.uuid4().hex;body={'contract_version':'0.1.0','request_id':nonce,'idempotency_key':nonce,'turn_id':nonce,'expected_state_revision':session['state']['state_revision'],'expected_conversation_revision':session['conversation_revision'],'view_context':{'look_version_id':view}}
        body.update({'user_utterance':text} if text else {'action':action});started=time.monotonic()
        session=api('/api/sessions/'+sid+'/turns',body);rows.append({'input':text or action,'status':session['decision']['status'],'text':session['response']['text'],'metrics':session['metrics'],'decision':session['decision'],'duration_ms':round((time.monotonic()-started)*1000)})
        return session
    try:
        turn(f'预算{budget}元，上衣{clothes}下装{clothes}，鞋{shoe}码，日常上班通勤穿')
        assert session['decision']['status']=='recommendation';first=session['history'][-1]
        turn('鞋子保留，衣服更休闲一点');second=session['history'][-1]
        assert session['decision']['status']=='recommendation' and first['items']['shoes']==second['items']['shoes']
        turn('鞋也换掉吧，换双浅色的');third=session['history'][-1]
        assert session['decision']['status']=='recommendation' and second['items']['shoes']!=third['items']['shoes']
        assert third['items']['top']==second['items']['top'] and third['items']['bottom']==second['items']['bottom']
        for look in session['history']:
            assert look['total_price_fen']<=budget*100
            with urllib.request.urlopen(base+look['image_url'],timeout=20) as response:assert response.headers.get_content_type() in {'image/png','image/svg+xml'} and response.read(8)
        turn('单买这双鞋呢？');assert session['decision']['action']['inquiry_result']['resolved_sku_ids']==[third['items']['shoes']] and len(session['history'])==3
        turn('看看第一版');view=first['look_version_id']
        turn('这双鞋多少钱？');assert session['decision']['action']['inquiry_result']['resolved_sku_ids']==[first['items']['shoes']]
        turn(action={'operation':'restore_history','target_look_version_id':first['look_version_id']});view=None
        assert session['history'][-1]['items']==first['items'] and len(session['history'])==4
        turn(action={'operation':'add_bundle_to_cart'});assert session['decision']['status']=='action_completed' and len(session['cart_added_sku_ids'])==3
        restored=api('/api/sessions/'+sid);assert len(restored['messages'])==16 and len(restored['history'])==4
        return {'profile':[clothes,clothes,shoe],'budget':budget,'passed':True,'rows':rows}
    except Exception as e:return {'profile':[clothes,clothes,shoe],'budget':budget,'passed':False,'error':type(e).__name__+': '+str(e),'rows':rows}
    finally:api('/api/sessions/'+sid,method='DELETE')

def main():
    p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8766');p.add_argument('--output',required=True);a=p.parse_args();out=Path(a.output)
    if out.exists():raise SystemExit('Refusing to overwrite earlier result')
    jobs=[(a.base,c,s,b) for c,s in [('S','36'),('M','38')] for b in [500,800]]
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda args:journey(*args),jobs))
    times=[r['duration_ms'] for j in results for r in j['rows']]
    report={'mode':'live_candidate_http','passed':all(j['passed'] for j in results),'journeys':results,'request_count':len(times),'api_calls':sum(r['metrics'].get('api_calls',0) for j in results for r in j['rows']),'p95_ms':sorted(times)[min(len(times)-1,int(len(times)*.95))]}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='journeys'}));raise SystemExit(0 if report['passed'] else 1)

if __name__=='__main__':main()
