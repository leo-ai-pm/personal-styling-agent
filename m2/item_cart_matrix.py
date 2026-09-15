"""Exhaustive SKU/quantity/stock virtual checks; never calls a model or live service."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .core import Fault, VERSION, now
from .preview import preview_resources
from .service import Service
from .evaluate import events_valid
from .model import PROMPT_VERSION


class NoModel:
    configured=True
    def parse(self,*args,**kwargs):
        raise AssertionError('Deterministic cart actions must not call a model')


def run():
    at=now();resources=preview_resources(at,save=False);results=[]
    for sid,sku in resources.skus.items():
        for quantity,force_empty in [(1,False),(2,False),(10,False),(1,True)]:
            original=deepcopy(resources.stock[sid])
            if force_empty:resources.stock[sid]['available_qty']=0
            stock=deepcopy(resources.stock[sid])
            available=bool(resources.products[sku['product_id']]['active'] and sku['sale_status']=='sellable'
                           and stock['sellable'] and stock['available_qty']>=quantity)
            service=Service(resources,NoModel(),lambda:at,remember_messages=True)
            created=service.create();session=created['state']['session_id']
            body=dict(contract_version=VERSION,request_id='matrix',turn_id='matrix',idempotency_key='matrix',
                      expected_state_revision=created['state']['state_revision'],expected_conversation_revision=0,
                      action={'operation':'add_item_to_cart','inquiry':{'topic':'purchase','reference':'catalog','sku_id':sid},'quantity':quantity})
            row={'sku_id':sid,'quantity':quantity,'force_empty':force_empty,'expected_success':available}
            try:
                result=service.turn(session,body);view=service.view(session)
                assert result['decision']['status']==('action_completed' if available else 'action_failed'),result['response']['text']
                assert view['state']==created['state'] and view['history']==created['history'],'Cart changed outfit/profile'
                assert events_valid(result['events']),'Invalid cart event contract'
                assert len(view['cart'])==int(available),'Wrong cart record count'
                if available:
                    action=view['cart'][0]
                    assert action['sku_ids']==[sid] and action['quantity']==quantity
                    assert action['item_count']==quantity and action['unit_price_fen']==sku['price_fen']
                    assert action['cart_value_fen']==sku['price_fen']*quantity
                assert service.turn(session,body)==result,'Retry is not idempotent'
                assert service.view(session)==view,'Retry duplicated cart or messages'
                assert resources.stock[sid]==stock,'Simulated cart unexpectedly decremented stock'
                row['passed']=True
            except Exception as error:
                row.update(passed=False,error=str(error),error_type=type(error).__name__)
            finally:
                service.close();resources.stock[sid].clear();resources.stock[sid].update(original)
            results.append(row)
    for product_id,product in resources.products.items():
        variants=[s for s in resources.skus.values() if s['product_id']==product_id]
        selected=next((s for s in variants if resources.stock[s['sku_id']]['sellable'] and resources.stock[s['sku_id']]['available_qty']>0),variants[0])
        other=next(s for s in resources.skus.values() if s['product_id']!=product_id)
        for conflicting in [False,True]:
            service=Service(resources,NoModel(),lambda:at)
            before=service.create();session=before['state']['session_id']
            inquiry={'topic':'purchase','reference':'catalog','product_name':product['name'].removeprefix('模拟·')}
            if conflicting:inquiry['sku_id']=other['sku_id']
            body=dict(contract_version=VERSION,request_id='name',turn_id='name',idempotency_key='name',expected_state_revision=0,
                      action={'operation':'add_item_to_cart','inquiry':inquiry,'cart_size':selected['size']})
            row={'product_id':product_id,'full_name_conflicts_with_sku':conflicting,'passed':False}
            try:
                result=service.turn(session,body)
                assert not conflicting,'Conflicting name/SKU was accepted'
                assert result['decision']['status']=='action_completed',result['response']['text']
                assert result['cart_added_sku_ids']==[selected['sku_id']],'Full name selected another product'
                view=service.view(session)
                assert view['state']==before['state'] and view['history']==before['history']
                row['passed']=True
            except Fault as error:
                if conflicting and error.code=='PRODUCT_REFERENCE_CONFLICT':
                    row['passed']=service.view(session)==before
                else:row.update(error=error.code,error_type=type(error).__name__)
            except Exception as error:row.update(error=str(error),error_type=type(error).__name__)
            finally:service.close()
            results.append(row)
    sid=next(iter(resources.skus))
    for quantity in [-1,0,11,1.5,True]:
        service=Service(resources,NoModel(),lambda:at)
        before=service.create();session=before['state']['session_id']
        body=dict(contract_version=VERSION,request_id='invalid',turn_id='invalid',idempotency_key='invalid',expected_state_revision=0,
                  action={'operation':'add_item_to_cart','inquiry':{'topic':'purchase','reference':'catalog','sku_id':sid},'quantity':quantity})
        row={'invalid_quantity':quantity,'passed':False}
        try:
            service.turn(session,body)
            row['error']='Invalid quantity accepted'
        except Fault:
            row['passed']=service.view(session)==before
        finally:service.close()
        results.append(row)
    files=['core.py','cart.py','consultation.py','service.py','model.py','dialogue.py']
    code_hash=hashlib.sha256(b''.join((Path(__file__).parent/name).read_bytes() for name in files)).hexdigest()
    return {'mode':'deterministic_virtual','api_calls':0,'prompt_version':PROMPT_VERSION,'code_sha256':code_hash,'sku_count':len(resources.skus),'total':len(results),
            'passed':sum(x['passed'] for x in results),'failed':sum(not x['passed'] for x in results),'cases':results}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    path=Path(args.output)
    if path.exists():parser.error('Use a new report path; previous results are preserved.')
    result=run();path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}))
    raise SystemExit(bool(result['failed']))


if __name__=='__main__':main()
