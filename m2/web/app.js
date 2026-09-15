'use strict';
const $=id=>document.getElementById(id);
let state=null,history=[],catalog=null,preview=null,busy=false,lastRequest=null,currentEligible=false,conversationRevision=0,failedImageUrl=null,cartRecords=[];
const money=n=>'¥'+(n/100).toLocaleString('zh-CN',{maximumFractionDigits:2});
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
async function api(path,body,method=body?'POST':'GET'){
 const r=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
 const data=await r.json();if(!r.ok&&!(path==='/api/health'&&r.status===503&&data.ready===false)){if(data.error?.code==='ACCESS_REQUIRED')location.reload();throw Object.assign(new Error(data.error?.message||'请求未完成。'),{code:data.error?.code});}return data;
}
function nearBottom(){const c=$('messages');return c.scrollHeight-c.scrollTop-c.clientHeight<90;}
function message(text,who){const follow=nearBottom()||who==='user';const node=el('div',text,'message '+who);$('messages').append(node);if(follow)$('messages').scrollTop=$('messages').scrollHeight;return node;}
function finishMessage(node,text,cls='assistant'){const follow=nearBottom();node.className='message '+cls;node.textContent=text;if(follow)$('messages').scrollTop=$('messages').scrollHeight;}
function reportError(e){$('status').textContent=e.message;message(e.message,'assistant error');}
function showLook(){if(window.innerWidth<=800){document.body.classList.add('mobile-look-open');document.querySelector('.workspace').inert=true;$('close-look').focus();}}
function hideLook(focus=true){document.body.classList.remove('mobile-look-open');document.querySelector('.workspace').inert=false;if(focus)$('show-look').focus();}
function saveSession(){if(state)sessionStorage.setItem('yixu-session',JSON.stringify({id:state.session_id,preview}));}
function applyView(d,restoreMessages=false){state=d.state;history=d.history;cartRecords=d.cart||[];conversationRevision=d.conversation_revision||0;const latest=[...(d.events||[])].reverse().find(e=>['recommendation_shown','no_match','bundle_add_failed'].includes(e.event_type));currentEligible=latest?.event_type==='recommendation_shown';if(preview&&!history.some(v=>v.look_version_id===preview))preview=null;if(restoreMessages){$('messages').replaceChildren();for(const m of d.messages||[])message(m.content,m.role==='user'?'user':'assistant');if(!d.messages?.length)message('你好，可以告诉我预算、常穿尺码和穿着场景，也可以先了解商品。','assistant');$('messages').scrollTop=$('messages').scrollHeight;}saveSession();}
function chosen(){return history.find(v=>v.look_version_id===(preview||state?.current_look_version_id));}
function renderCart(){
 const records=cartRecords||[];let count=0,total=0;$('cart-items').replaceChildren();
 for(const record of records){
  count+=record.item_count;total+=record.cart_value_fen;
  const row=el('article',undefined,'cart-record');const single=record.operation==='add_item_to_cart';
  row.append(el('h3',single?'单件加购':'整套加购'));
  for(const id of record.sku_ids){
   const sku=catalog?.skus.find(s=>s.sku_id===id);const p=catalog?.products.find(p=>p.product_id===sku?.product_id);
   row.append(el('p',`${p?.name.replace('模拟·','')||id} · ${sku?.size.value||'已选尺码'}码${single?' × '+(record.quantity||1):' × 1'}`));
  }
  row.append(el('strong',`加入金额 ${money(record.cart_value_fen)}`));
  if(single&&Number.isInteger(record.unit_price_fen))row.append(el('small',`单价 ${money(record.unit_price_fen)}`));
  $('cart-items').append(row);
 }
 if(!records.length)$('cart-items').append(el('p','购物车还是空的。可以说“把这双鞋加入购物车”，也可以在商品明细中选择单件。'));
 $('cart-count').textContent=count;$('cart-total').textContent=records.length?`共 ${count} 件商品 · 加入金额合计 ${money(total)}`:'';
}
async function showCart(){
 if(busy||!state)return;
 const session=state.session_id;
 try{const data=await api('/api/sessions/'+session);if(state?.session_id!==session||(data.conversation_revision||0)<conversationRevision)return;cartRecords=data.cart||[];renderCart();if(!$('cart-dialog').open)$('cart-dialog').showModal();}
 catch(e){if(state?.session_id===session)reportError(e);}
}
function render(){
 renderCart();$('cart-button').disabled=busy||!state;
 const v=chosen();$('products').replaceChildren();$('total').hidden=!v;$('look-image').hidden=!v;$('empty-look').hidden=!!v;$('look-actions').hidden=!v;$('show-look').disabled=!v;$('products-button').disabled=!v;
 $('cart').disabled=!v||busy||!!preview||!currentEligible||failedImageUrl===v?.image_url||$('look-image').getAttribute('src')!==v?.image_url||!$('look-image').complete||!$('look-image').naturalWidth;$('history-button').disabled=busy;$('history-count').textContent=history.length;
 $('version-label').textContent=v?`${preview?'历史预览':currentEligible?'当前搭配':'上一方案 · 请重新搭配'} / VERSION ${String(v.version_no).padStart(2,'0')}`:'搭配，从一句话开始';
 $('preview-note').hidden=!preview;
 if(preview){$('preview-note').replaceChildren(el('span','正在查看历史，当前方案没有改变。'));const b=el('button','回到当前','text-button');b.onclick=()=>{preview=null;saveSession();render();};$('preview-note').append(b);}
 const sz=state?.profile.usual_sizes||{};$('profile').textContent=['top','bottom','shoes'].map((s,i)=>['上衣','下装','鞋码'][i]+' '+(sz[s]?.value||'未提供')).join(' / ');
 if(!v)return;
 if($('look-image').getAttribute('src')!==v.image_url){failedImageUrl=null;$('look-image').src=v.image_url;}$('look-image').alt=`第 ${v.version_no} 套固定模特完整搭配`;
 for(const [n,s] of ['top','bottom','shoes'].entries()){
  const sku=catalog.skus.find(x=>x.sku_id===v.items[s]);const p=catalog.products.find(x=>x.product_id===sku.product_id);
  const row=el('article',undefined,'product');row.append(el('span',String(n+1).padStart(2,'0'),'number'));
  const info=el('div');info.append(el('h3',p.name.replace('模拟·','')),el('p',`${['上衣','下装','鞋履'][n]} / 尺码 ${sku.size.value} · ${preview?'历史库存快照':'推荐时模拟有货'}`));
  row.append(info,el('strong',money(sku.price_fen)));const details=el('details');details.append(el('summary','商品与来源'),el('p',`${sku.sku_id} · ${p.origin.source_name} · 自主合成商品 / 开源组合示意`));row.append(details);
  const add=el('button','单件加购 · 模拟','secondary item-cart');add.dataset.skuId=sku.sku_id;add.setAttribute('aria-label',`将${p.name.replace('模拟·','')}加入模拟购物车`);add.disabled=busy;
  add.onclick=()=>{$('product-dialog').close();hideLook(false);turn(request({action:{operation:'add_item_to_cart',inquiry:{topic:'purchase',reference:'history',look_version_id:v.look_version_id,sku_id:sku.sku_id,slot:s},quantity:1}}));};
  row.append(add);$('products').append(row);
 }
 $('total').replaceChildren();const left=el('div');left.append(el('small','整套合计'),el('br'),el('strong',money(v.total_price_fen)));
 const right=el('small',`预算余量 ${money(v.budget_remaining_fen)}${v.parent_look_version_id?' · 较上一套 '+(v.delta_from_parent_fen>=0?'+':'−')+money(Math.abs(v.delta_from_parent_fen)):''}`);$('total').append(left,right);
}
async function create(demo=false){if(busy)return;busy=true;$('send').disabled=true;$('reset').disabled=true;$('demo-profile').disabled=true;try{const old=state?.session_id;const data=await api('/api/sessions',{demo_profile:demo});state=data.state;conversationRevision=data.conversation_revision||0;history=[];cartRecords=data.cart||[];preview=null;lastRequest=null;currentEligible=false;hideLook(false);$('messages').replaceChildren();$('status').textContent=demo?'已加载 M / M / 38 模拟资料。告诉我预算和穿着场景。':'你好，告诉我你的预算、常穿尺码和穿着场景，我来帮你找一套完整搭配。\n有喜欢的单品，也可以直接说想保留什么。';message($('status').textContent,'assistant');$('retry').hidden=true;saveSession();render();if(old)await api('/api/sessions/'+old,undefined,'DELETE');}finally{busy=false;$('send').disabled=false;$('reset').disabled=false;$('demo-profile').disabled=false;render();}}
async function refresh(){const d=await api('/api/sessions/'+state.session_id);applyView(d);render();}
async function turn(body,retry=false){
 if(busy)return;busy=true;lastRequest=body;$('send').disabled=true;$('reset').disabled=true;$('demo-profile').disabled=true;$('retry').hidden=true;render();
 const pending=message('正在理解需求并核对搭配…','assistant pending');
 const timer=setTimeout(()=>{$('status').textContent='处理超过15秒，当前方案已保留。请等待本次请求结束后重试。';finishMessage(pending,$('status').textContent,'assistant pending');},15000);
 $('status').textContent='正在理解需求并核对完整搭配…';
 try{
  const data=await api('/api/sessions/'+state.session_id+'/turns',body);state=data.state;history=data.history;conversationRevision=data.conversation_revision;if(data.cart)cartRecords=data.cart;
  const d=data.decision;if(d.status==='recommendation')preview=null;
  if(d.status==='recommendation')currentEligible=true;
  else if(d.status==='no_match'||(data.events||[]).some(e=>e.event_type==='bundle_add_failed'))currentEligible=false;
  if(d.action?.operation==='view_history'){if(d.action.target_look_version_id)preview=d.action.target_look_version_id;else await showHistory(false);}
  saveSession();$('status').textContent=data.response.text;finishMessage(pending,data.response.text);lastRequest=null;
  if(d.action?.operation==='add_bundle_to_cart')hideLook(false);
 }catch(e){$('status').textContent=e.message;finishMessage(pending,e.message,'assistant error');$('retry').hidden=false;if(e.code==='STATE_REVISION_CONFLICT') {await refresh();lastRequest=null;$('retry').hidden=true;} }
 finally{clearTimeout(timer);busy=false;$('send').disabled=false;$('reset').disabled=false;$('demo-profile').disabled=false;render();}
}
function request(payload){const id=crypto.randomUUID();return {contract_version:'0.1.0',request_id:id,idempotency_key:id,turn_id:id,expected_state_revision:state.state_revision,expected_conversation_revision:conversationRevision,view_context:{look_version_id:preview||null},...payload};}
async function showHistory(record=true){
 if(record){await turn(request({action:{operation:'view_history'}}));return;}
 $('history-list').replaceChildren();
 if(!history.length)$('history-list').append(el('p','还没有搭配，先聊聊你的需求。'));
 for(const v of history){const c=el('article',undefined,'history-card');const img=el('img');img.loading='lazy';img.decoding='async';img.src=v.image_url;img.alt='第'+v.version_no+'套';const content=el('div');content.append(el('h3',`第 ${v.version_no} 套 · ${money(v.total_price_fen)}`),el('p',Object.values(v.items).join(' / ')));if(v.restored_from_look_version_id)content.append(el('p','恢复自 '+v.restored_from_look_version_id));const bs=el('div',undefined,'buttons');const view=el('button','查看');view.onclick=async()=>{await turn(request({action:{operation:'view_history',target_look_version_id:v.look_version_id}}));$('history-dialog').close();};const restore=el('button','恢复这套');restore.onclick=()=>{$('history-dialog').close();turn(request({action:{operation:'restore_history',target_look_version_id:v.look_version_id}}));};bs.append(view,restore);c.append(img,content,bs);$('history-list').append(c);}
 if(!$('history-dialog').open)$('history-dialog').showModal();
}
$('chat-form').onsubmit=e=>{e.preventDefault();if(!state||busy)return;const text=$('utterance').value.trim();if(!text)return;message(text,'user');$('utterance').value='';turn(request({user_utterance:text}));};
$('utterance').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('chat-form').requestSubmit();}};
$('cart').onclick=()=>turn(request({action:{operation:'add_bundle_to_cart'}}));$('history-button').onclick=()=>history.length?showHistory():showHistory(false);$('close-history').onclick=()=>$('history-dialog').close();
$('reset').onclick=()=>create().catch(reportError);$('demo-profile').onclick=()=>create(true).catch(reportError);$('retry').onclick=()=>lastRequest&&turn(lastRequest,true);
$('products-button').onclick=()=>$('product-dialog').showModal();$('close-products').onclick=()=>$('product-dialog').close();$('show-look').onclick=showLook;$('close-look').onclick=()=>hideLook();
$('cart-button').onclick=showCart;$('close-cart').onclick=()=>$('cart-dialog').close();
window.addEventListener('resize',()=>{if(window.innerWidth>800)hideLook(false);});
window.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.querySelector('dialog[open]')&&document.body.classList.contains('mobile-look-open'))hideLook();});
for(const b of document.querySelectorAll('.suggestions button'))b.onclick=()=>{$('utterance').value=b.textContent;$('utterance').focus();};
$('look-image').onload=()=>render();
$('look-image').onerror=()=>{failedImageUrl=$('look-image').getAttribute('src');$('status').textContent='搭配图片未能加载，请刷新后重试。';message($('status').textContent,'assistant error');$('cart').disabled=true;};
(async()=>{if(location.protocol==='file:'){document.body.classList.add('file-preview');$('local-file-notice').hidden=false;for(const node of document.querySelectorAll('body > header, body > main, body > dialog'))node.hidden=true;return;}try{const [h,c]=await Promise.all([api('/api/health'),api('/api/catalog')]);catalog=c;if(h.mode==='structured_test_injection'){$('config-note').hidden=false;$('config-note').textContent='测试模式：这里使用预设结构化意图验证页面，未调用真实模型。';}else if(!h.model_configured){$('config-note').hidden=false;$('config-note').textContent='对话模型尚未接入。完成本地模型配置后即可开始推荐；当前没有使用模拟对话代替模型。';}if(h.asset_mode==='schematic_sample' && h.model_configured && h.mode!=='structured_test_injection'){$('config-note').hidden=false;$('config-note').textContent='开源演示：商品与库存为模拟数据，配图为商品组合示意。';}if(h.coverage_note)document.querySelector('.footnote').textContent=h.coverage_note;let saved;try{saved=JSON.parse(sessionStorage.getItem('yixu-session')||'null');}catch{}if(saved?.id){try{preview=saved.preview;applyView(await api('/api/sessions/'+saved.id),true);render();}catch(e){if(e.code!=='SESSION_NOT_FOUND')throw e;sessionStorage.removeItem('yixu-session');await create();}}else await create();}catch(e){$('status').textContent=e.message;message(e.message,'assistant error');}})();
