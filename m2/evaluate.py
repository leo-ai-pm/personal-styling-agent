"""Frozen M0 assertion runner. --mode structured is not a model evaluation."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
import json
import re
import time
from .core import ROOT, Resources, execute, Fault, instant, SLOTS, now, json_hash
from .model import CompatibleModel, PROMPT_VERSION, PROMPT

DATASET_SHA='17f9c2c4cf5ab476f3365cd862c2920404d03f932b7e6dc331e3659d15aa60e2'
MISSING=object()


def pathget(obj,path):
    parts=re.findall(r'[^.\[\]]+(?:\[[^\]]*\])?',path)
    # Dots inside filter expressions belong to the filter, not the outer path.
    parts=[];buf='';depth=0
    for ch in path:
        if ch=='.' and not depth:parts.append(buf);buf='';continue
        buf+=ch
        if ch=='[':depth+=1
        if ch==']':depth-=1
    if buf:parts.append(buf)
    def walk(value,remaining):
        if not remaining:return value
        part=remaining[0];m=re.fullmatch(r'([^\[]+)(?:\[(.*)\])?',part)
        if not m:return MISSING
        key,selector=m.groups()
        if isinstance(value,list):
            vals=[walk(v,remaining) for v in value];return [v for v in vals if v is not MISSING]
        if not isinstance(value,dict) or key not in value:return MISSING
        value=value[key]
        if selector is not None:
            if not isinstance(value,list):return MISSING
            if selector=='*':return walk(value,remaining[1:])
            if selector.startswith('id in ('):
                ids=selector[7:-1].split(',');value=[v for v in value if v.get('look_version_id') in ids]
            else:
                def match(v):
                    for expr in selector.split(','):
                        k,expected=expr.split('=',1)
                        actual=pathget(v,'look_version_id' if k=='id' else k)
                        if actual!=expected:return False
                    return True
                value=[v for v in value if match(v)]
            if selector.startswith('id=') and len(value)==1:value=value[0]
            elif remaining[1:] and len(value)==1:value=value[0]
        return walk(value,remaining[1:])
    return walk(obj,parts)


def subset(actual,expected,bindings):
    if expected==['$structured_categories_only']:
        return isinstance(actual,list) and bool(actual) and set(actual)<= {'budget','scene','size','preference','operation'}
    if isinstance(expected,str) and expected.startswith('$'):
        if expected=='$rfc3339':
            try:return isinstance(actual,str) and instant(actual).tzinfo is not None
            except (ValueError,TypeError):return False
        return expected in bindings and actual==bindings[expected]
    if isinstance(expected,dict):return isinstance(actual,dict) and all(k in actual and subset(actual[k],v,bindings) for k,v in expected.items())
    if isinstance(expected,list):return isinstance(actual,list) and all(any(subset(a,e,bindings) for a in actual) for e in expected)
    return type(actual)==type(expected) and actual==expected


def resolve(value,bindings):
    if isinstance(value,str) and value.startswith('$'):return bindings.get(value,MISSING)
    if isinstance(value,list):return [resolve(x,bindings) for x in value]
    if isinstance(value,dict):return {k:resolve(v,bindings) for k,v in value.items()}
    return value


def independent_recommendation_check(outputs,r):
    for o in outputs:
        d=o['decision']
        if d['status']!='recommendation':continue
        v=d['recommendation']['look_version'];l=r.looks.get(v['look_id']);s=o['state']
        if not l or not l['active'] or v['items']!=l['items'] or v['image_url']!=l['image_url'] or set(v['items'])!=set(SLOTS):return False
        if v['resources']!=r.binding or d['resources']!=r.binding:return False
        total=0
        for slot,sku in v['items'].items():
            item=r.skus.get(sku);stock=r.stock.get(sku)
            if not item or not stock or item['slot']!=slot or item['size']!=s['profile']['usual_sizes'].get(slot):return False
            if item['sale_status']!='sellable' or not r.products[item['product_id']]['active'] or not stock['sellable'] or stock['available_qty']<1:return False
            if item['origin']['rights_confirmed'] is not True or l['origin']['rights_confirmed'] is not True:return False
            total+=item['price_fen']
        if type(total) is not int or total!=v['total_price_fen'] or total>s['context']['budget_max_fen']:return False
        if v['budget_remaining_fen']!=s['context']['budget_max_fen']-total:return False
        for c in s['constraints']:
            if c['constraint_id'] not in d['applied_constraint_ids'] or c['strength']!='hard':continue
            slots=[c['target_slot']] if c.get('target_slot') else SLOTS;val=c['value'];kind=c['kind']
            if kind=='keep_sku' and v['items'][c['target_slot']]!=val:return False
            if kind=='forbid_sku' and val in v['items'].values():return False
            if kind=='forbid_attribute' and r.hit(l,val,slots):return False
            if kind=='scene' and not r.hit(l,val):return False
        old=o.get('state_before',{}).get('current_look_version_id')
        parent=next((x for x in o['history'] if x['look_version_id']==old),None)
        i=d['interpretation']
        for requirement in i.get('required_attributes',[]):
            if requirement.get('slot') not in SLOTS or requirement.get('attribute_token') not in r.vocab['tokens']:return False
            if not r.hit(l,requirement['attribute_token'],[requirement['slot']]):return False
        if parent and i['operation']=='modify_current':
            if v['parent_look_version_id']!=parent['look_version_id'] or v['delta_from_parent_fen']!=total-parent['total_price_fen']:return False
            comparison=parent
            if i.get('comparison_base_look_version_id'):
                ancestors={};node=parent
                while node and node['look_version_id'] not in ancestors:
                    ancestors[node['look_version_id']]=node
                    node=next((x for x in o['history'] if x['look_version_id']==node.get('parent_look_version_id')),None)
                comparison=ancestors.get(i['comparison_base_look_version_id'])
                if not comparison or not i['carry_forward_unmentioned']:return False
            if i['carry_forward_unmentioned']:
                for slot in SLOTS:
                    if (v['items'][slot]==comparison['items'][slot]) == (slot in i['replace_slots']):return False
            elif v['items']==comparison['items']:return False
    return True


EVENT_FIELDS={
 'turn_submitted':{'input_categories'},'clarification_asked':{'field','missing_slots'},
 'recommendation_shown':{'look_id','items','total_price_fen','resources'},'recommendation_accepted':{'look_id','total_price_fen'},
 'recommendation_rejected':{'look_id','reason_code','rejected_sku_ids','rejected_attribute_tokens'},
 'slot_replaced':{'from_look_version_id','to_look_version_id','slot','from_sku_id','to_sku_id','price_delta_fen','reason_tokens'},
 'constraint_changed':{'constraint_id','action','kind','scope'},'history_viewed':{'viewed_look_version_ids'},
 'history_restored':{'restored_from_look_version_id','new_look_version_id'},
 'bundle_added_to_cart':{'look_id','sku_ids','item_count','cart_value_fen','resources','price_inventory_revalidated_at'},
 'bundle_add_failed':{'target_look_version_id','reason_code','resources'},'no_match':{'no_match_id','reason_codes','blocking_constraint_ids','resources'},
 'item_add_succeeded':{'sku_ids','quantity','item_count','unit_price_fen','cart_value_fen','target_look_version_id','resources','price_inventory_revalidated_at'},
 'item_add_failed':{'sku_ids','quantity','target_look_version_id','reason_code','resources'},
 'scope_redirected':{'reason_code','supported_action'},
 'catalog_viewed':{'query_slots','query_target','query_attributes','sku_ids','resources'},
 'product_consulted':{'topic','sku_ids','look_version_id','resources'}}


def events_valid(events):
    keys={'contract_version','event_id','event_type','occurred_at','tenant_id','session_id','subject_id','turn_id','decision_id','look_version_id','is_simulated_session','payload'}
    ids=set()
    for e in events:
        if set(e)!=keys or e['event_type'] not in EVENT_FIELDS or set(e['payload'])!=EVENT_FIELDS[e['event_type']] or e['event_id'] in ids:return False
        if e['contract_version']!='0.1.0' or e['is_simulated_session'] is not True:return False
        try:
            if instant(e['occurred_at']).tzinfo is None:return False
        except ValueError:return False
        ids.add(e['event_id'])
    return True


def judge(a,actual,bindings,outputs,r):
    op=a['operator'];v=a.get('value');rv=resolve(v,bindings)
    if op=='exists':return actual is not MISSING
    if op=='absent':return actual is MISSING or actual==[]
    if actual is MISSING:return False
    if op=='eq':return actual==rv and type(actual)==type(rv)
    if op=='not_eq':return actual!=rv
    if op=='set_eq':return isinstance(actual,list) and {json.dumps(x,sort_keys=True) for x in actual}=={json.dumps(x,sort_keys=True) for x in rv}
    if op in ['contains','not_contains']:
        found=(str(rv) in actual) if isinstance(actual,str) else subset(actual,v,bindings) if isinstance(actual,dict) else any(subset(x,v,bindings) for x in actual)
        return found if op=='contains' else not found
    if op=='event_contains':return any(subset(e,v,bindings) for e in actual)
    if op=='subset':return all(x in rv for x in actual)
    if op=='gte':return type(actual) in [int,float] and actual>=rv
    if op=='lte':return type(actual) in [int,float] and actual<=rv
    if op=='sum_eq':return actual==sum(r.skus[s]['price_fen'] for s in rv)
    if op=='question_count_eq':return isinstance(actual,str) and len(re.findall('[？?]',actual))==rv
    if op=='all_recommendations_valid':return independent_recommendation_check(outputs,r)
    if op=='immutable':
        if isinstance(rv,dict):return actual==rv
        return all(any(x==wanted for x in actual) for wanted in rv)
    raise ValueError('Unsupported assertion operator '+op)


def run_case(c,mode,model):
    f=c['fixture'];r=Resources(f);state=deepcopy(f['initial_state']);history=deepcopy(f['look_versions']);outputs=[];events=[];bindings={'$fixture_resource_binding':r.binding};errors=[];pending=None;dialog=None
    if mode=='structured':
        from .tests.intent_fixtures import cases
        intents=cases()[c['case_id']]
    for idx,t in enumerate(c['turns']):
        before=deepcopy(state);turn_started=time.monotonic()
        try:
            if mode=='structured':intent=deepcopy(intents[idx]);meta={'mode':'structured_test_injection','model':None,'input_tokens':0,'output_tokens':0,'cost':0,'retries':0,'tool_calls':0}
            else:intent,meta=model.parse(t['user_text'],state,history,r,dialog)
            start=time.monotonic();o=execute(r,state,history,intent,t['turn_id'],c['case_id']+'-'+t['turn_id'],f['as_of'],pending,dialog);pending=o.pop('pending_intent',None);dialog=o.pop('dialog_context',None);meta['business_duration_ms']=round((time.monotonic()-start)*1000)
            meta['total_duration_ms']=round((time.monotonic()-turn_started)*1000);o['metrics']=meta;o['state_before']=before;o['intent']=intent
            state=o['state'];history=o['history'];events+=o['events'];outputs.append(o)
            nm=o['decision'].get('no_match')
            if nm:
                bindings['$no_match_'+t['turn_id']]=nm['no_match_id']
                if nm['blocking_constraint_ids']:bindings['$matching_blocking_constraint_id']=nm['blocking_constraint_ids'][0]
                bindings['$highest_priority_relaxable_constraint_id']=nm['suggested_relaxation']['constraint_id']
            created=[x for x in state['constraints'] if x['created_turn_id']==t['turn_id'] and x['kind'] in ['keep_sku','forbid_sku','forbid_attribute']]
            if created:bindings['$constraint_'+t['turn_id']]=created[0]['constraint_id']
        except Fault as e:
            errors.append({'turn_id':t['turn_id'],'code':e.code});outputs.append({'decision':{'status':'error','error':{'code':e.code}},'response':{'text':e.message},'state':deepcopy(state),'history':deepcopy(history),'events':[],'state_before':before,'metrics':{'mode':mode,'total_duration_ms':round((time.monotonic()-turn_started)*1000),'input_tokens':None,'output_tokens':None,'cost':None}})
    trace={'events':events,'recommendations':[o['decision']['recommendation'] for o in outputs if o['decision']['status']=='recommendation'],'look_versions':history,'selected_look_ids':[],'selected_sku_ids':[],'recommendation_shoe_skus':[],'cart_added_sku_ids':[],'requested_user_upload':False,'persisted_pii':{}}
    for o in outputs:
        if o['decision']['status']=='recommendation':
            v=o['decision']['recommendation']['look_version'];trace['selected_look_ids'].append(v['look_id']);trace['selected_sku_ids']+=list(v['items'].values());trace['recommendation_shoe_skus'].append(v['items']['shoes'])
        trace['cart_added_sku_ids']+=o.get('cart_added_sku_ids',[])
    root={'state':state,'trace':trace,**{'turn:'+t['turn_id']:o for t,o in zip(c['turns'],outputs)}}
    results=[]
    for a in c['assertions']:
        actual=pathget(root,a['target']);b=deepcopy(bindings)
        if a['target'].startswith('turn:'):
            turnkey=a['target'].split('.')[0];b['$state_revision_before']=root[turnkey]['state_before']['state_revision']
        # Text claims require an independent human/judge review. Missing labels are never an automatic pass.
        if a['level']=='soft' or '.response.claims' in a['target']:
            status='pending_review';passed=None
        else:
            passed=judge(a,actual,b,outputs,r);status='passed' if passed else 'failed'
        results.append({'assertion_id':a.get('assertion_id',a.get('id')), 'level':a['level'],'target':a['target'],'operator':a['operator'],'status':status,'actual':None if actual is MISSING else actual,'expected':a.get('value')})
    return {'case_id':c['case_id'],'tags':c['tags'],'mode':mode,'assertions':results,'outputs':outputs,'events_valid':events_valid(events),'errors':errors}


def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['structured','live'],default='live');p.add_argument('--output');args=p.parse_args()
    path=ROOT/'evals/p0_eval_cases.jsonl';raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==DATASET_SHA,'Frozen dataset changed'
    cases=[json.loads(x) for x in raw.splitlines()];model=CompatibleModel()
    output=ROOT/(args.output or 'reports/m2/'+args.mode+'_evaluation.json');output.parent.mkdir(parents=True,exist_ok=True)
    report={'created_at':now(),'mode':args.mode,'dataset_sha256':DATASET_SHA,'prompt_version':PROMPT_VERSION,'prompt_sha256':json_hash(PROMPT),'code_sha256':json_hash({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'m2').glob('*.py')}),'model':model.name if args.mode=='live' else None,'m3_gate':'not_passed','notice':'结构化注入不验证自然语言理解。软质量与文本 claims 须独立复核；未复核不计通过。'}
    if args.mode=='live':report.update(provider=model.provider,request_settings={'temperature':0,'stream':False,'response_format':model.format,'thinking':'disabled' if model.provider in ['qwen','deepseek'] else 'provider_default','wall_clock_timeout_seconds':model.timeout})
    if args.mode=='live' and not model.configured:
        report.update(status='blocked_model_configuration',cases_run=0,assertions_passed=0)
    else:
        runs=[]
        for c in cases:
            run=run_case(c,args.mode,model);runs.append(run)
            if args.mode=='live':print(json.dumps({'case_id':c['case_id'],'completed':len(runs),'total':len(cases),'errors':run['errors']},ensure_ascii=False),flush=True)
        counts=Counter(a['status'] for r in runs for a in r['assertions'])
        slices=defaultdict(lambda:Counter())
        for r in runs:
            for tag in r['tags']:slices[tag]['cases']+=1;slices[tag]['cases_without_failed_machine_assertions']+=not any(a['status']=='failed' for a in r['assertions'])
        report.update(status='evaluated',cases_run=len(runs),counts=dict(counts),slices=dict(slices),cases=runs)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['cases','slices']},ensure_ascii=False,indent=2))
    if report.get('counts',{}).get('failed') or report['status']!='evaluated':raise SystemExit(1)

if __name__=='__main__':main()
