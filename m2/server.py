"""Single-host local/invited preview; a production deployment still needs TLS/proxy review."""
import argparse
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from .core import ROOT, demo_resources, Fault, now, instant
from .runtime import RuntimeOptions, RequestLimits
from .service import Service
from .preview import preview_resources
from .model import PROMPT_VERSION

LOG=logging.getLogger('yixu.runtime')
ACCESS_PAGE='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>衣序 · 受邀体验</title><main><h1>衣序 · 受邀体验</h1><p>请输入邀请码。商品与加购均为模拟，聊天保留 24 小时。</p><form action="/api/access" method="post"><label>邀请码 <input name="invite_code" type="password" autocomplete="off" required maxlength="128"></label><button type="submit">进入体验</button></form></main></html>'''


def make_server(service,port=8765,options=None):
    options=options or RuntimeOptions()
    if options.visitor_isolation_enabled and service.store:
        options.signing_key=service.store.secret('visitor_cookie_signing_key',options.signing_key)
    auth_limits=RequestLimits(per_minute=20)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,data,kind='application/json; charset=utf-8',headers=None):
            payload=json.dumps(data,ensure_ascii=False).encode() if kind.startswith('application/json') else data
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','same-origin')
            self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if getattr(self,'visitor_cookie',None):self.send_header('Set-Cookie',self.visitor_cookie)
            for name,value in (headers or {}).items():self.send_header(name,value)
            self.end_headers()
            try:self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError):pass
        def body(self,form=False):
            content=self.headers.get('Content-Type','')
            if not content.startswith('application/json') and not (form and content.startswith('application/x-www-form-urlencoded')):raise Fault('TYPE_INVALID')
            length=int(self.headers.get('Content-Length','0'))
            if length<2 or length>20000:raise Fault('TYPE_INVALID')
            data=self.rfile.read(length)
            return {k:v[-1] for k,v in parse_qs(data.decode()).items()} if content.startswith('application/x-www-form-urlencoded') else json.loads(data)
        def dispatch(self):
            self.visitor_cookie=None
            try:
                options.validate_source(self.headers.get('Host',''),self.headers.get('Origin'),self.server.server_port,self.command)
                path=urlsplit(self.path).path
                if self.command=='GET' and path=='/api/health':
                    error=None
                    try:resources=service.resources()
                    except Fault as failure:resources=service.r;error=failure.code
                    configured=bool(service.model.configured)
                    ready=configured and error is None
                    data={'version':'m2-preview-004','ready':ready,'model_configured':configured,
                          'model':getattr(service.model,'name',None),'prompt_version':PROMPT_VERSION,
                          'mode':getattr(service.model,'mode','live_model' if configured else 'model_not_configured'),
                          'inventory_snapshot_id':resources.inventory['inventory_snapshot_id'],'valid_until':resources.inventory['valid_until'],
                          'is_simulated':True,'persistence_enabled':bool(service.store),'access_control_enabled':bool(options.invite_code),
                          'access_mode':'invited' if options.invite_code else 'public' if options.public_origin else 'local',
                          'visitor_isolation_enabled':options.visitor_isolation_enabled,
                          'asset_mode':resources.data.get('asset_mode','schematic_sample'),
                          'coverage_note':('商品与库存均为模拟；加购不产生订单。13组商品组合示意覆盖 S/S/36、M/M/38 和500/800元预算；非实拍或试穿效果。' if resources.preview else '商品与库存均为模拟；加购不产生订单。当前搭配图覆盖 M/M/38 的四套组合。'),
                          'capabilities':['catalog_query_v1','conversation_context_v1','session_resume_v1','single_item_cart_v1','cart_view_v1','dialogue_scope_v1']}
                    if error:data['readiness_error']=error
                    return self.send(200 if ready else 503,data)
                if self.command=='POST' and path=='/api/access':
                    if not options.invite_code:
                        return self.send(303,{'authenticated':False,'access_mode':'public'},headers={'Location':'/'})
                    auth_limits.check(self.client_address[0],instant(service.clock()).timestamp(),False)
                    body=self.body(form=True)
                    if type(body) is not dict or set(body)!={'invite_code'}:raise Fault('TYPE_INVALID')
                    options.authenticate(body['invite_code']);token=options.issue_cookie()
                    cookie='yixu_visitor='+token+'; Path=/; Max-Age=86400; HttpOnly; SameSite=Strict'
                    if options.public_origin:cookie+='; Secure'
                    headers={'Set-Cookie':cookie}
                    if self.headers.get('Content-Type','').startswith('application/x-www-form-urlencoded'):
                        headers['Location']='/';return self.send(303,{'authenticated':True},headers=headers)
                    return self.send(200,{'authenticated':True},headers=headers)
                try:owner_id=options.visitor(self.headers.get('Cookie'))
                except Fault:
                    if options.invite_code:
                        if self.command=='GET' and path=='/':return self.send(200,ACCESS_PAGE.encode(),'text/html; charset=utf-8')
                        raise
                    token=options.issue_cookie()
                    owner_id=options.visitor('yixu_visitor='+token)
                    self.visitor_cookie='yixu_visitor='+token+'; Path=/; Max-Age=86400; HttpOnly; SameSite=Strict; Secure'
                session_route=re.fullmatch(r'/api/sessions/([A-Za-z0-9_-]{1,128})(/turns)?',path)
                if self.command=='GET':
                    if path=='/api/catalog':return self.send(200,service.resources().catalog)
                    if session_route and not session_route[2]:return self.send(200,service.view(session_route[1],owner_id))
                    static={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
                    if path in static:
                        file,kind=static[path];return self.send(200,(ROOT/'m2/web'/file).read_bytes(),kind)
                    look=next((look for look in service.r.looks.values() if look['active'] and look['image_url']==path),None)
                    if look:
                        service.r.check_image(look);return self.send(200,(ROOT/path.lstrip('/')).read_bytes(),'image/svg+xml' if Path(path).suffix=='.svg' else 'image/png')
                if self.command=='DELETE' and session_route and not session_route[2]:
                    service.delete(session_route[1],owner_id);return self.send(200,{'deleted':True})
                if self.command=='POST':
                    body=self.body()
                    if path=='/api/sessions':
                        if type(body) is not dict or set(body)-{'demo_profile'} or type(body.get('demo_profile',False)) is not bool:raise Fault('TYPE_INVALID')
                        if owner_id:auth_limits.check(owner_id,instant(service.clock()).timestamp(),False)
                        return self.send(201,service.create(body.get('demo_profile',False),owner_id))
                    if session_route and session_route[2]:return self.send(200,service.turn(session_route[1],body,owner_id))
                self.send(404,{'error':{'code':'NOT_FOUND'}})
            except Fault as error:
                status=409 if error.code in ['STATE_REVISION_CONFLICT','IDEMPOTENCY_CONFLICT'] else 404 if error.code=='SESSION_NOT_FOUND' else 403 if error.code in ['HOST_REJECTED','ORIGIN_REJECTED'] else 401 if error.code=='ACCESS_REQUIRED' else 429 if error.code in ['RATE_LIMITED','DAILY_LIMIT_REACHED','MODEL_BUSY'] else 503 if error.code.startswith('MODEL_') or error.code=='INVENTORY_SNAPSHOT_STALE' else 400
                LOG.info('request_rejected code=%s status=%s',error.code,status)
                self.send(status,{'contract_version':'0.1.0','status':'error','error':{'code':error.code,'message':error.message},'created_at':now()},headers={'Retry-After':'60'} if status==429 else None)
            except (ValueError,KeyError,TypeError,UnicodeError):self.send(400,{'status':'error','error':{'code':'TYPE_INVALID','message':'请求格式不正确。'}})
            except Exception:
                LOG.error('request_failed code=INTERNAL_ERROR')
                self.send(500,{'status':'error','error':{'code':'INTERNAL_ERROR','message':'服务暂时不可用，当前方案保持不变。'}})
        do_GET=dispatch;do_POST=dispatch;do_DELETE=dispatch
    class PreviewServer(ThreadingHTTPServer):
        daemon_threads=True
        def service_actions(self):
            with service.lock:service.purge()
    return PreviewServer((options.bind_host,port),Handler)


def main():
    parser=argparse.ArgumentParser(description='个性化穿搭Agent：本地或 HTTPS 代理后的演示服务。')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--db',default=str(ROOT/'reports/m2/runtime/sessions.sqlite3'))
    parser.add_argument('--preview-assets',action='store_true',help='Use the extended synthetic catalog with 13 SVG sample outfits')
    parser.add_argument('--no-persistence',action='store_true')
    parser.add_argument('--public-origin',default=os.environ.get('YIXU_PUBLIC_ORIGIN'))
    parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--per-minute',type=int,default=20)
    parser.add_argument('--daily-calls',type=int,default=500)
    parser.add_argument('--model-concurrency',type=int,default=3)
    args=parser.parse_args()
    if any(value<1 for value in [args.per_minute,args.daily_calls,args.model_concurrency]):parser.error('额度和并发参数必须为正整数。')
    options=RuntimeOptions(public_origin=args.public_origin,invite_code=os.environ.get('YIXU_INVITE_CODE'),bind_host=args.bind)
    limits=RequestLimits(args.per_minute,args.daily_calls,args.model_concurrency)
    resource_factory=preview_resources if args.preview_assets else demo_resources
    service=Service(resource_factory(),db_path=None if args.no_persistence else Path(args.db),remember_messages=True,
                    refresh_factory=lambda at:resource_factory(at,save=False),limits=limits)
    print(f'M2 preview: http://127.0.0.1:{args.port} | model_configured={service.model.configured} | persistence={bool(service.store)}',flush=True)
    server=make_server(service,args.port,options)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();service.close()

if __name__=='__main__':main()
