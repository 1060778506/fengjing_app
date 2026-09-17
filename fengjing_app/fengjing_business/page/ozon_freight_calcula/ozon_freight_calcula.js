function ozfcCompetitorVersion(previous,record){
 const history=structuredClone(previous?.history||[]);
 if(previous){const snapshot=structuredClone(previous);delete snapshot.history;snapshot.version ||= 1;history.push(snapshot);}
 return {...record,version:previous?(previous.version||1)+1:1,history};
}

function ozfcCanvasCollectionTargets(nodes){
 const seen=new Set(),targets=[];
 for(const n of nodes||[]){
  if(n.manual)continue;
  for(const p of n.meta?.prices||[]){
   const store=String(p.store||''),itemId=String(p.product_id||'');
   if(!store||!/^\d+$/.test(itemId)||Number(itemId)<=0)continue;
   const key=store+'|'+itemId;if(seen.has(key))continue;
   seen.add(key);targets.push({store,itemId});
  }
 }
 return targets;
}
function ozfcCollectionCommand(products) {
 const collect=async function(products){
  if(location.hostname!=='seller.ozon.ru')throw Error('请在已登录的 seller.ozon.ru 页面 F12 控制台运行');
  if(window.__ozfcCollecting)throw Error('已有采集任务运行，请勿重复执行');
  window.__ozfcCollecting=true;
  const batch={format:'ozfc-competitors-v1',startedAt:new Date().toISOString(),collectedAt:null,intervalMs:1500,maxRetries:4,results:[]};
  window.__ozfcCollectionResult=batch;
  try{
   let queue=products,halted=false;
   for(let round=0;round<=4&&queue.length&&!halted;round++){
    if(round){console.log('开始第 '+round+'/4 轮重试，仅处理 '+queue.length+' 个未完成商品；等待 '+(round*15)+' 秒');await new Promise(resolve=>setTimeout(resolve,round*15000));}
    for(let i=0;i<queue.length;i++){
    if(i)await new Promise(resolve=>setTimeout(resolve,1500));
    const p=queue[i],previous=batch.results.find(r=>r.store===p.store&&r.itemId===p.itemId),entry={...p,collectedAt:new Date().toISOString(),ok:false,attempts:(previous?.attempts||0)+1};
    let stop=false;
    try{
     const query=new URLSearchParams({company_id:p.companyId,item_id:p.itemId,sync:'true'});
     const response=await fetch('/api/ozon-min-price-sku/v1/competitor-prices-by-item-id-with-url?'+query,{credentials:'same-origin',signal:AbortSignal.timeout(30000)});
     entry.status=response.status;
     if(!response.ok){entry.error='HTTP '+response.status;stop=[401,403,429].includes(response.status);}
     else if(!(response.headers.get('Content-Type')||'').includes('application/json')){entry.error='返回登录/验证页面而不是价格 JSON';stop=true;}
     else {
      const data=await response.json();
      if(!data||!Array.isArray(data.competitors))throw Error('接口没有返回 competitors 数组');
      entry.ok=true;entry.data=data;
     }
    }catch(e){entry.error=e.name==='TimeoutError'?'请求超时':String(e.message||e).slice(0,180);}
    entry.collectedAt=new Date().toISOString();
    entry.attemptHistory=[...(previous?.attemptHistory||[]),{attempt:entry.attempts,status:entry.status,ok:entry.ok,syncing:!!entry.data?.syncing,error:entry.error,collectedAt:entry.collectedAt}];
    if(previous)batch.results[batch.results.indexOf(previous)]=entry;else batch.results.push(entry);
    console.log((round?'重试 '+round+'/4':'首轮采集')+' · '+(i+1)+'/'+queue.length,p.itemId,entry.ok?(entry.data.syncing?'请求成功 · Ozon 报价仍在计算':'请求成功 · 报价已完成'):('请求失败 · '+entry.error));
    if(stop){halted=true;console.warn('登录、访问限制或限流：停止采集及重试，不绕过验证。');break;}
    }
    queue=batch.results.filter(r=>!r.ok||r.data?.syncing).map(r=>({store:r.store,companyId:r.companyId,itemId:r.itemId}));
   }
  }finally{window.__ozfcCollecting=false;batch.collectedAt=new Date().toISOString();}
  const text=JSON.stringify(batch);
  let copied=false;
  try{await navigator.clipboard.writeText(text);copied=true;}catch(e){}
  if(!copied&&typeof copy==='function'){try{copy(text);copied=true;}catch(e){}}
  if(!copied){
   const a=document.createElement('a'),url=URL.createObjectURL(new Blob([text],{type:'application/json'}));a.href=url;a.download='ozon-跟卖采集.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
   console.warn('浏览器禁止自动复制，已下载 JSON。也可运行 copy(JSON.stringify(window.__ozfcCollectionResult))');
  }
  const ready=batch.results.filter(r=>r.ok&&!r.data?.syncing),pending=batch.results.filter(r=>r.ok&&r.data?.syncing),failed=batch.results.filter(r=>!r.ok);
  console.log('采集结束：报价已完成 '+ready.length+'，Ozon 仍在计算 '+pending.length+'，请求失败 '+failed.length+'，未请求 '+(products.length-batch.results.length)+'，总商品 '+products.length+(copied?'；已复制到剪贴板':'；请导入下载结果'));
  const showRows=rows=>console.table?console.table(rows):console.log(rows);
  if(pending.length){console.log('仍在计算的商品不会新增报价版本，旧报价保留；稍后重新采集。');showRows(pending.map(r=>({店铺:r.store,商品内部ID:r.itemId,状态:'Ozon 仍在计算'})));}
  if(failed.length)showRows(failed.map(r=>({店铺:r.store,商品内部ID:r.itemId,状态码:r.status,错误:r.error})));
 };
 return '('+collect.toString()+')('+JSON.stringify(products)+');';
}
function ozfcParseCompetitors(input){
 const parsed=typeof input==='string'?JSON.parse(input):input;
 if(!parsed||typeof parsed!=='object')throw Error('请粘贴采集结果 JSON，不是 Cookie');
 const batch=parsed.format==='ozfc-competitors-v1'?parsed:{results:[{ok:true,data:parsed,itemId:parsed.competitors?.[0]?.itemId,collectedAt:parsed.collectedAt||null}],collectedAt:parsed.collectedAt||null};
 if(!Array.isArray(batch.results)||batch.results.length>10000)throw Error('采集结果格式不正确');
 const results=batch.results.map(r=>{
  if(!r.ok)return {...r,offers:[]};
  if(!/^\d+$/.test(String(r.itemId||''))||!Array.isArray(r.data?.competitors))throw Error('缺少商品内部 ID 或 competitors 数组；空结果请使用批量采集命令');
  if(r.data.competitors.some(q=>String(q.itemId)!==String(r.itemId)))throw Error('报价商品内部 ID 与采集记录不匹配');
  const offers=r.data.competitors.map(q=>{
   const units=Number(q.price?.units),nanos=Number(q.price?.nanos||0),currency=String(q.price?.currencyCode||'');
   if(!Number.isSafeInteger(units)||!Number.isInteger(nanos)||nanos<0||nanos>=1e9||units<0||!/^[A-Z]{3}$/.test(currency))throw Error('报价金额或币种格式不正确');
   const raw=String(q.url||''),match=raw.match(/\]\((https?:\/\/[^)]+)\)$/),url=match?match[1]:raw;
   let u;try{u=new URL(url);}catch(e){throw Error('报价链接格式不正确');}
   if(u.protocol!=='https:'||!(u.hostname==='ozon.ru'||u.hostname.endsWith('.ozon.ru')))throw Error('报价链接必须是 Ozon HTTPS 地址');
   return {price:units+nanos/1e9,currency,url:u.href,sku:String(q.sku||''),rejectionReason:Array.isArray(q.rejectionReason)?q.rejectionReason.map(String):[],downloadedAt:q.downloadedAt||null};
  });
  return {...r,itemId:String(r.itemId),collectedAt:r.collectedAt||batch.collectedAt||null,offers};
 });
 return {...batch,results};
}

/* Native draggable canvas. Tariffs are data, never executable formulas. */
function ozfcCalculate(it,r,fx=0){
 const quantity=Number(it.quantity??1),dims=[it.length,it.width,it.height].map(v=>Number(v)/10),kg=Number(it.weight)*quantity/1000,reasons=[],steps=[];
 const currency=it.value_currency||'RUB',entered=Number(it.value)*quantity;
 if(!Number.isInteger(quantity)||quantity<1||quantity>10000)reasons.push('数量需为1至10000之间的整数');
 // Prevent floating-point noise from rejecting exact tariff boundaries.
 const value=currency==='CNY'?Math.round(entered/Number(fx)*1e6)/1e6:entered;
 if(currency==='CNY'&&(!Number.isFinite(Number(fx))||Number(fx)<=0))reasons.push('人民币换算卢布需要有效汇率，请填写上方汇率');
 if(!dims.every(v=>Number.isFinite(v)&&v>0)||!Number.isFinite(kg)||kg<=0)reasons.push('请补全包装长宽高和重量');
 if(!Number.isFinite(entered)||entered<=0)reasons.push('未取得物流匹配所需货值；画板物流使用Ozon后台售价');
 if(reasons.length)return {r,reasons,steps,eligible:false};
 const sum=dims.reduce((a,b)=>a+b),volume=r.divisor>0?dims.reduce((a,b)=>a*b)*quantity/r.divisor:0,raw=Math.max(kg,volume),bill=r.step>0?Math.ceil(raw/r.step-1e-10)*r.step:raw;
 if(kg<r.min_weight||(r.min_exclusive&&kg<=r.min_weight)||kg>r.max_weight)reasons.push(`实重需 ${r.min_exclusive?'＞':'≥'}${r.min_weight} 且 ≤${r.max_weight} kg`);
 const limitCurrency=r.value_currency||'RUB',limitValue=limitCurrency===currency?entered:limitCurrency==='CNY'?value*Number(fx):value;
 if(!Number.isFinite(limitValue)||(limitCurrency!==currency&&!(Number(fx)>0)))reasons.push('货值限制换算需要有效汇率');
 if(limitValue<r.min_value||(r.value_exclusive&&limitValue<=r.min_value)||limitValue>r.max_value)reasons.push(`货值需 ${r.value_exclusive?'＞':'≥'}${r.min_value} 且 ≤${r.max_value} ${limitCurrency}`);
 if(r.max_side&&Math.max(...dims)>r.max_side)reasons.push(`最长边超过 ${r.max_side} cm`);
 if(r.max_sum&&sum>r.max_sum)reasons.push(`三边和超过 ${r.max_sum} cm`);
 if(r.sorted_sides?.length&&dims.slice().sort((a,b)=>b-a).some((v,i)=>v>r.sorted_sides[i]))reasons.push(`尺寸超过 ${r.sorted_sides.join(' × ')} cm`);
 if(r.max_billable&&bill>r.max_billable)reasons.push(`计费重量超过 ${r.max_billable} kg`);
 if(it.battery&&!r.battery)reasons.push('该渠道不接受带电货物');if(it.liquid&&!r.liquid)reasons.push('液体运输未确认，请咨询承运商');
 const price=Math.round((r.fixed+bill*r.rate+(r.surcharge||0)+Number.EPSILON)*100)/100;
 steps.push(`① 单位换算：${it.length} × ${it.width} × ${it.height} mm → ${dims.join(' × ')} cm；${it.weight} g × ${quantity}件 → ${kg.toFixed(4)} kg`);
 if(quantity>1)steps.push(`数量：${quantity}件合包；实重 ${it.weight} g × ${quantity} = ${(it.weight*quantity)} g；货值及体积累加。尺寸限制目前按单件尺寸检查，实际合箱外尺寸和额外包装重量需另行核实。`);
 steps.push(`② 货值换算：${entered} ${currency}${currency==='CNY'?' ÷ '+fx+' = '+value.toFixed(2)+' RUB':''}；三边和 ${sum.toFixed(2)} cm`);
 steps.push(r.divisor?`③ 体积重：${dims.join(' × ')} × ${quantity}件 ÷ ${r.divisor} = ${volume.toFixed(4)} kg；与实重取较大值`:'③ 按实际重量计费，不计体积重');
 steps.push(`④ 计费重量：${bill.toFixed(4)} kg${r.step?`，按 ${r.step} kg 向上进位`:'，不进位'}`);
 steps.push(`⑤ 首票 ${r.fixed} + ${bill.toFixed(4)} kg × ${r.rate} 元/kg + 附加费 ${r.surcharge||0} = ¥${price.toFixed(2)}`);
 return {r,reasons,steps,eligible:!reasons.length,price,bill};
}
function ozfcCost(unitCost,freight,marginPercent,commissionPercent,extras={}){
 const values=[unitCost,freight,marginPercent,commissionPercent];
 if(values.some(v=>v===null||v===undefined||!Number.isFinite(Number(v))||Number(v)<0))return {error:'成本、运费、目标利润率或API佣金尚未完整取得'};
 const [unit,shipping,margin,commission]=values.map(Number),quantity=Number(extras.quantity??1);
 if(!Number.isInteger(quantity)||quantity<1||quantity>10000)return {error:'数量需为1至10000之间的整数'};
 const cost=unit*quantity;
 const acquisition=extras.acquisition??0,withdrawal=extras.withdrawal??0,returns=extras.returns??0,lastmile=extras.lastmile??0,advertising=extras.advertising??0;
 if([acquisition,withdrawal,returns,lastmile,advertising].some(v=>!Number.isFinite(v)||v<0))return {error:'费用参数必须为有效非负数字'};
 const ratio=1-margin/100-commission/100-acquisition/100-withdrawal/100;
 if(ratio<=0)return {error:'目标利润率、佣金、收单及提现比例之和必须小于100%'};
 const total=cost+shipping+returns+lastmile+advertising,price=Math.ceil((total/ratio-1e-9)*100)/100;
 const fee=price*commission/100,acquisitionFee=price*acquisition/100,withdrawalFee=price*withdrawal/100,profit=price-total-fee-acquisitionFee-withdrawalFee;
 return {cost,unitCost:unit,quantity,shipping,margin,commission,acquisition,withdrawal,returns,lastmile,advertising,acquisitionFee,withdrawalFee,ratio,total,price,fee,profit,actualMargin:price?profit/price*100:0};
}
if(typeof module!=='undefined')module.exports={ozfcCalculate,ozfcCost};
if(typeof frappe!=='undefined')frappe.pages['ozon-freight-calcula'].on_page_load=function(wrapper){
 const page=frappe.ui.make_app_page({parent:wrapper,title:'Ozon 运费自由画板',single_column:true});wrapper.freightCanvas=new OzFreightCanvas(page);
};
class OzFreightCanvas{
 addBlank(){
  if(this.s.nodes.length>=300){frappe.msgprint('最多300张卡片');return;}
  this.snap();const id=crypto.randomUUID(),view=this.s.view,n={id,manual:true,manualSale:'',costEdited:true,item:{item_code:'SIM-'+id.slice(0,8),item_name:'空白物料',image:'',length:'',width:'',height:'',weight:'',value:0,value_currency:'CNY',battery:false,liquid:false},meta:{costs:[],prices:[],errors:[]},x:(60-view.x)/view.z,y:(50-view.y)/view.z};
  this.s.nodes.push(n);this.s.active=id;this.s.expanded=this.s.exclusive!==false?[id]:[...this.expanded(),id];this.change();this.paint();
 }
 product(n,raw=false){const prices=n.meta?.prices||[];if(n.manual)return n.manualSale!==''&&n.manualSale!==undefined?{store:'空白物料 · 手动估算',sku:n.item.item_code,currency:'CNY',amount:n.manualSale,cny:n.manualSale,commissions:[],source:'画布手动售价参考，不关联Ozon后台'}:null;const p=prices[n.priceIndex]||(prices.length===1?prices[0]:null);return !raw&&n.saleOverride!==undefined?{...(p||{store:'手动售价参考',sku:n.item.item_code,commissions:[]}),currency:'CNY',amount:n.saleOverride,cny:n.saleOverride}:p;}
 isTable(){return this.s.displayMode==='table';}
 dragZones(n){return `<div class="fc-drag-zones"><span data-drag-zone="single" title="只移动当前卡片">⠿ 单独拖动</span><span data-drag-zone="group" title="移动该物料全部关联卡片">⠿ 整组拖动</span></div>`;}
 estimate(n){
  const results=this.c.routes.filter(r=>r.enabled!==false).map(r=>ozfcCalculate(this.shippingItem(n),r,this.c.cny_per_rub)).filter(x=>x.eligible).sort((a,b)=>a.price-b.price);
  const route=results.find(x=>x.r.id===n.costRoute)||results[0],prices=n.meta?.prices||[],product=this.product(n);
  const commission=n.commissionOverride??product?.commissions?.find(x=>x.schema===(n.commissionSchema||'RFBS'))?.percent,params=this.feeParams(n),fx=Number(this.c.cny_per_rub);
  const costs=(n.meta?.costs||[]).filter(c=>c.currency==='CNY'),cost=n.item.value!==''&&n.item.value!==undefined?Number(n.item.value):costs.length===1?costs[0].amount:undefined;
  const x=(params.returns>0||params.lastmile>0)&&!(fx>0)?{error:'卢布费用缺少汇率'}:ozfcCost(cost,route?.price,params.margin,commission,{quantity:n.item.quantity??1,acquisition:params.acquisition,withdrawal:params.withdrawal,returns:params.returns*fx||0,lastmile:params.lastmile*fx||0,advertising:params.advertising});
  return {route,x};
 }
 brief(){
  const d=new frappe.ui.Dialog({title:'运费与售价 · 简略展示',size:'extra-large',fields:[{fieldtype:'HTML',fieldname:'brief'}]});d.$wrapper.addClass('fc-brief-dialog');
  let page=0;const host=d.fields_dict.brief.$wrapper[0];const draw=()=>{const pages=Math.max(1,Math.ceil(this.s.nodes.length/100));page=Math.max(0,Math.min(page,pages-1));host.innerHTML=`<div class="fc-brief"><div class="fc-brief-head"><span>物料</span><span>选择的物流 · 点击复制</span><span>定价计算 · 最终费用</span><span>建议售价</span></div>${this.s.nodes.slice(page*100,(page+1)*100).map(n=>{
   const {route,x}=this.estimate(n),fees=x.error?[]:[['物料成本',x.cost],['运费',x.shipping],['退货预留',x.returns],['最后一公里',x.lastmile],['广告费',x.advertising],['佣金',x.fee],['收单费',x.acquisitionFee],['提现费',x.withdrawalFee]].filter(([k,v])=>v>0);
   return `<div class="fc-brief-row"><div class="fc-brief-item"><img src="${this.image(n.item.image)}"><div class="fc-brief-identity"><strong>${this.e(n.item.item_name)}</strong><button class="fc-brief-jump" data-jump-material="${n.id}">定位到画布 <span>↗</span></button></div></div><div>${route?`<button data-copy-logistics="${this.e(route.r.provider+' · '+route.r.name)}">${this.e(route.r.provider+' · '+route.r.name)} ⧉</button>`:'暂无有效物流'}${this.briefQuoteHTML(n)}</div><div class="fc-brief-formula">${x.error?this.e(x.error):fees.map(([k,v])=>this.e(k)+' ¥'+v.toFixed(2)).join(' + ')+' · 预计利润 ¥'+x.profit.toFixed(2)+' · 利润率 '+x.actualMargin.toFixed(2)+'%'}</div><div class="fc-brief-price">${x.error?'待计算':'¥'+x.price.toFixed(2)}</div></div>`;
  }).join('')||'<p>请先添加物料</p>'}</div><div class="fc-pagination"><button data-brief-page="-1" ${page===0?'disabled':''}>上一页</button><span>${page+1} / ${pages} · 每页100个 · 共${this.s.nodes.length}个</span><button data-brief-page="1" ${page===pages-1?'disabled':''}>下一页</button></div>`;};draw();
  this.bindImageHover(host,'.fc-brief-item img');
  d.$wrapper.on?.('hide.bs.modal',()=>this.hideImageHover());
  host.addEventListener('click',ev=>{const b=ev.target.closest('[data-copy-logistics]');if(b)this.copyItem(b.dataset.copyLogistics).catch(()=>frappe.msgprint('复制失败'));const pager=ev.target.closest('[data-brief-page]');if(pager&&!pager.disabled){this.hideImageHover();page+=Number(pager.dataset.briefPage);draw();host.closest('.modal-body')?.scrollTo(0,0);}const jump=ev.target.closest('[data-jump-material]');if(jump){d.hide();this.jumpMaterial(jump.dataset.jumpMaterial);}});d.show();
 }
 competitorBrief(n){
  const products=n.competitors?.products||[],offers=products.flatMap(p=>p.offers||[]),valid=offers.filter(q=>!(q.rejectionReason||[]).length&&Number.isFinite(q.price));
  const currencies=[...new Set(valid.map(q=>q.currency))],lowest=currencies.map(c=>c+' '+Math.min(...valid.filter(q=>q.currency===c).map(q=>q.price)).toFixed(2)).join(' / ');
  return '跟卖 / 竞争报价 '+offers.length+' 条 · 最低有效价：'+(lowest||'暂无');
 }
 briefQuoteHTML(n){
  const offers=(n.competitors?.products||[]).flatMap(p=>p.offers||[]),valid=offers.filter(q=>!(q.rejectionReason||[]).length&&Number.isFinite(q.price));
  const currencies=[...new Set(valid.map(q=>q.currency))],fx=Number(this.c.cny_per_rub);
  const money=(amount,currency,cny)=>{
   const converted=currency==='CNY'?amount:currency==='RUB'&&fx>0?amount*fx:Number.isFinite(cny)?cny:null;
   return '<b>'+this.e(currency)+' '+Number(amount).toFixed(2)+'</b>'+(currency!=='CNY'?'<small>'+(converted!==null?'≈ ¥'+converted.toFixed(2):'暂无人民币汇率')+'</small>':'');
  };
  const prices=n.meta?.prices||[],current=prices[n.priceIndex]||(prices.length===1?prices[0]:null);
  const backend=current&&current.amount!==null&&current.amount!==''&&current.amount!==undefined&&Number.isFinite(Number(current.amount))?money(Number(current.amount),current.currency,current.cny):'<span class="fc-brief-unavailable">'+(prices.length>1?'请选择店铺商品':'尚未获取')+'</span>';
  return '<div class="fc-brief-quotes'+(offers.length?' fc-brief-has-rivals':'')+'"><div class="fc-brief-quote-heading"><span>跟卖 / 竞争报价</span><em>'+offers.length+' 条</em></div><div class="fc-brief-quote-line"><span>最低有效价</span><div>'+ (currencies.length?currencies.map(c=>money(Math.min(...valid.filter(q=>q.currency===c).map(q=>q.price)),c)).join(''):'<span class="fc-brief-unavailable">暂无有效报价</span>')+'</div></div><div class="fc-brief-quote-line fc-brief-backend"><span>Ozon 后台定价</span><div>'+backend+'</div></div><p>人民币按画布汇率参考换算</p></div>';
 }
 jumpMaterial(id){
  const n=this.s.nodes.find(n=>n.id===id);if(!n)return;
  this.multiSelect=false;this.selectedNodes=new Set();this.s.displayMode='canvas';this.s.expanded=[id];this.s.active=this.s.focus=id;n.rivalsHidden=false;
  if(this.stage){this.stage.scrollTop=0;this.stage.scrollLeft=0;}
  this.s.view={x:60-n.x,y:60-n.y,z:1};this.change();this.paint();
 }
 tablePageNodes(){
  const pages=Math.max(1,Math.ceil(this.s.nodes.length/50));this.tablePage=Math.max(0,Math.min(this.tablePage||0,pages-1));
  return this.s.nodes.slice(this.tablePage*50,(this.tablePage+1)*50);
 }
 tablePagination(){
  let el=this.stage.querySelector('.fc-table-pagination');if(!el){el=document.createElement('div');el.className='fc-pagination fc-table-pagination';this.stage.append(el);}
  el.hidden=!this.isTable();if(el.hidden)return;
  const pages=Math.max(1,Math.ceil(this.s.nodes.length/50)),page=this.tablePage||0;
  el.innerHTML='<button data-a="table-prev" '+(page===0?'disabled':'')+'>上一页</button><span>'+(page+1)+' / '+pages+' · 每页50个 · 共'+this.s.nodes.length+'个</span><button data-a="table-next" '+(page===pages-1?'disabled':'')+'>下一页</button>';
 }

 costCard(n,results){
  const eligible=results.filter(x=>x.eligible),route=eligible.find(x=>x.r.id===n.costRoute)||eligible[0];
  const p=n.quote||{x:n.x+370,y:n.y},pos=n.cost||{x:p.x+530,y:p.y};
  const prices=n.meta?.prices||[],product=this.product(n);
  const options=product?.commissions||[],routeSchema='RFBS';
  const chosen=options.find(c=>c.schema===(n.commissionSchema||routeSchema));
  const costs=(n.meta?.costs||[]).filter(c=>c.currency==='CNY'),stockCost=costs.length===1?costs[0]:null;
  const entered=n.item.value,cost=entered!==''&&entered!==null&&entered!==undefined&&Number.isFinite(Number(entered))&&Number(entered)>=0?{amount:Number(entered),source:n.costEdited?'画布模拟成本':stockCost?.source}:stockCost;
  const params=this.feeParams(n),commission=n.commissionOverride??chosen?.percent;
  const fx=Number(this.c.cny_per_rub),rubNeeded=params.returns>0||params.lastmile>0;
  const x=rubNeeded&&!(fx>0)?{error:'卢布费用需要有效汇率'}:ozfcCost(cost?.amount,route?.price,params.margin,commission,{quantity:n.item.quantity??1,advertising:params.advertising,acquisition:params.acquisition,withdrawal:params.withdrawal,returns:params.returns*fx||0,lastmile:params.lastmile*fx||0});
  const summary=`<div class="fc-cost-summary"><span>商品成本 ¥<input type="text" inputmode="decimal" data-edit-node="${n.id}" data-edit-key="cost" value="${this.e(n.item.value)}"></span><span>运费<b>${route?'¥'+route.price.toFixed(2):'等待物流报价'}</b></span><span>商品售价 ¥<button data-restore-sale="${n.id}" title="恢复API原始售价">↺ 恢复</button><input type="text" inputmode="decimal" data-edit-node="${n.id}" data-edit-key="sale" value="${this.e(product?.cny??'')}"></span><span class="fc-summary-margin">目标利润率<b>${params.margin}%</b></span><span class="fc-api-commission">API佣金<button class="fc-reset-commission" data-restore-commission="${n.id}" ${n.manual?'disabled':''} title="恢复API原始佣金">↺ 恢复</button><input type="text" inputmode="decimal" data-edit-node="${n.id}" data-edit-key="commission" value="${this.e(commission??'')}"></span></div>`;
  const dropdown=`<label class="fc-commission-label">佣金履约模式<select data-commission-node="${n.id}"><option value="">请选择API返回的佣金模式</option>${options.map(c=>`<option value="${this.e(c.schema)}" ${chosen?.schema===c.schema?'selected':''}>${this.e(c.schema)} · ${c.percent}%</option>`).join('')}</select></label>`;
  const steps=x.error?`<p class="fc-warning">${this.e(x.error)}${!route?'<br>请先补全包装信息和货值。':''}</p>`:`<ol class="fc-cost-steps"><li><small>① 商品成本</small><b>¥${x.unitCost.toFixed(2)} × ${x.quantity}件 = ¥${x.cost.toFixed(2)}</b></li>${x.returns>0?`<li><small>② 退货费用换算</small><b>${params.returns} RUB × ${fx} = ¥${x.returns.toFixed(2)}</b></li>`:''}<li><small>② 加上物流运费</small><b>商品 ¥${x.cost.toFixed(2)} + 运费 ¥${x.shipping.toFixed(2)}${x.returns>0?` + 退货预留 ¥${x.returns.toFixed(2)}`:''}${x.lastmile>0?` + 最后一公里预留 ¥${x.lastmile.toFixed(2)}`:''}${x.advertising>0?` + 广告费 ¥${x.advertising.toFixed(2)}`:''} = ¥${x.total.toFixed(2)}</b></li>${x.lastmile>0?`<li><small>最后一公里换算</small><b>${params.lastmile} RUB × ${fx} = ¥${x.lastmile.toFixed(2)}</b></li>`:''}${x.advertising>0?`<li><small>广告费</small><b>人民币固定费用 ¥${x.advertising.toFixed(2)}</b></li>`:''}<li><small>③ 售价中用于覆盖成本的比例</small><b>100% − 利润率${x.margin}% − 佣金${x.commission}%${x.acquisition>0?` − 收单${x.acquisition}%`:''}${x.withdrawal>0?` − 提现${x.withdrawal}%`:''} = ${(x.ratio*100).toFixed(2)}%</b></li><li><small>④ 计算建议售价</small><b>¥${x.total.toFixed(2)} ÷ ${(x.ratio*100).toFixed(2)}% → ¥${x.price.toFixed(2)}</b></li><li class="fc-step-check"><small>⑤ 佣金校验</small><b>¥${x.price.toFixed(2)} × ${x.commission}% ≈ ¥${x.fee.toFixed(2)}</b></li>${x.acquisition>0?`<li class="fc-step-check"><small>⑤ 收单费校验</small><b>¥${x.price.toFixed(2)} × ${x.acquisition}% ≈ ¥${x.acquisitionFee.toFixed(2)}</b></li>`:''}${x.withdrawal>0?`<li class="fc-step-check"><small>⑤ 提现费校验</small><b>¥${x.price.toFixed(2)} × ${x.withdrawal}% ≈ ¥${x.withdrawalFee.toFixed(2)}</b></li>`:''}<li class="fc-step-profit"><small>⑥ 利润校验</small><b>¥${x.price.toFixed(2)} − ¥${x.total.toFixed(2)} − 佣金 ¥${x.fee.toFixed(2)}${x.acquisition>0?` − 收单 ¥${x.acquisitionFee.toFixed(2)}`:''}${x.withdrawal>0?` − 提现 ¥${x.withdrawalFee.toFixed(2)}`:''} ≈ ¥${x.profit.toFixed(2)}</b><em>利润率：¥${x.profit.toFixed(2)} ÷ 售价¥${x.price.toFixed(2)} = ${x.actualMargin.toFixed(2)}%（目标利润率${x.margin}%）</em></li></ol>`;
  return `<article class="fc-card fc-cost-card" style="left:${pos.x}px;top:${pos.y}px"><header class="fc-drag" data-n="${n.id}" data-cost="1"><span>成本计算 · PROFIT STUDIO</span><button data-lookup="${n.id}" title="刷新SKU价格与佣金">刷新佣金</button></header><div class="fc-cost-layout"><section class="fc-calculation-pane"><h4>定价计算 · 分步明细</h4>${steps}</section><div class="fc-cost-content"><p class="fc-cost-channel">${route?this.e(route.r.provider+' · '+route.r.name):'在物流卡片选择有效渠道'}<br>${product?this.e(product.store+' / '+product.sku):'请选择绑定的Ozon商品'}</p>${this.priceHTML(n)}${summary}${dropdown}${this.nodeFees(n)}<details class="fc-pricing-note"><summary>计价说明 · 仅画布模拟，不修改后台</summary><div><p>${this.e(chosen?.source||'未取得佣金时不按0%计算，请先填写或查询。')}</p><ul><li>按单件估算；请核对佣金履约模式。</li><li>佣金、收单费和提现费按售价比例计算；预留费用不代表实际账单。</li><li>仅启用且非0的费用计入，不含税费及未配置费用。</li><li>建议售价可能跨物流档位，请更新售价参考后复核运费。</li></ul></div></details></div></div></article>${this.saleCard(n,x,product,pos)}`;
 }
 saleCard(n,x,product,costPos){
  const pos=n.sale||{x:costPos.x+this.costWidth(n)+40,y:costPos.y};
  return `<article class="fc-card fc-sale-card" style="left:${pos.x}px;top:${pos.y}px"><header class="fc-drag" data-n="${n.id}" data-sale="1"><span>售价建议 · PRICE STUDIO</span></header><div class="fc-cost-content">${x.error?'<p class="fc-warning">'+this.e(x.error)+'</p>':`<div class="fc-price-result"><small>建议售价 · 人民币</small><strong>¥${x.price.toFixed(2)}</strong><span>${this.c.cny_per_rub>0?'约 '+(x.price/this.c.cny_per_rub).toFixed(2)+' RUB':''}</span></div><p>预计利润 ¥${x.profit.toFixed(2)} · 利润率 ${x.actualMargin.toFixed(2)}%</p>`}<p>Ozon当前后台售价：${product?this.e(product.currency)+' '+Number(product.amount).toFixed(2):'未取得'}</p><p class="fc-source">仅建议，不修改Ozon价格。详细费用步骤见左侧成本卡片。</p></div></article>`;
 }
 shippingItem(n){
  const prices=n.meta?.prices||[],p=this.product(n);
  const value=p?.currency==='RUB'?p.amount:p?.cny;
  return {...n.item,value:value??'',value_currency:p?.currency==='RUB'?'RUB':'CNY'};
 }
 prepare(s,c){
  let converted=false;
  if(c.guoo_snapshot!==1&&Array.isArray(c.routes)&&this.bootData?.defaults?.routes){
   for(const r of this.bootData.defaults.routes.filter(r=>r.provider==='GUOO'))if(!c.routes.some(x=>x.id===r.id)){c.routes.push(structuredClone(r));converted=true;}
   c.guoo_snapshot=1;converted=true;
  }
  if(c.xy_post_snapshot!==2&&Array.isArray(c.routes)&&this.bootData?.defaults?.routes){
   for(const r of this.bootData.defaults.routes.filter(r=>r.id.startsWith('兴远-Post-'))){if(!c.routes.some(x=>x.id===r.id||(x.provider==='兴远'&&x.name===r.name&&x.destination===r.destination))){c.routes.push(structuredClone(r));converted=true;}}
   c.xy_post_snapshot=2;converted=true;
  }
  if(c.margin_pct===undefined){c.margin_pct=35;converted=true;}
  if(!Number(c.cny_per_rub)&&this.bootData?.exchange?.cny_per_rub){c.cny_per_rub=this.bootData.exchange.cny_per_rub;c.exchange_date=this.bootData.exchange.date;converted=true;}
  for(const n of s.nodes){
   if(n.item.quantity===undefined){n.item.quantity=1;converted=true;}
   if(n.manual)continue;
   const costs=(n.meta?.costs||[]).filter(c=>c.currency==='CNY');
   if(!n.costEdited&&costs.length===1){const value=Number(costs[0].amount).toFixed(2);if(n.item.value!==value||n.item.value_currency!=='CNY')converted=true;n.item.value=value;n.item.value_currency='CNY';}
   if((!n.item.value_currency||n.item.value_currency==='RUB')&&Number(c.cny_per_rub)>0){
    if(Number(n.item.value)>0)n.item.value=(Number(n.item.value)*c.cny_per_rub).toFixed(2);
    n.item.value_currency='CNY';converted=true;
   }
  }
  if(!Array.isArray(s.expanded))s.expanded=s.active?[s.active]:[];
  if(s.exclusive===undefined)s.exclusive=true;
  return converted;
 }
 expanded(){return this.multiSelect?[]:Array.isArray(this.s.expanded)?this.s.expanded:(this.s.active?[this.s.active]:[]);}
 toggleMultiSelect(){
  if(this.isTable())throw Error('请切换到画布模式后使用多选');
  this.multiSelect=!this.multiSelect;this.selectedNodes=new Set();this.paint();
 }
 minimap(){
  let box=this.stage.querySelector('.fc-minimap');
  if(!box){box=document.createElement('div');box.className='fc-minimap';this.stage.append(box);
   box.addEventListener('click',ev=>{const svg=ev.target.closest('svg');if(!svg||!this.mapBounds)return;const r=svg.getBoundingClientRect(),b=this.mapBounds,v=this.s.view;
    v.x=this.stage.clientWidth/2-(b.x+(ev.clientX-r.left)/r.width*b.w)*v.z;
    v.y=this.stage.clientHeight/2-(b.y+(ev.clientY-r.top)/r.height*b.h)*v.z;this.transform();this.change();
   });
  }
  box.hidden=this.isTable();if(box.hidden)return;
  const v=this.s.view,nodes=this.s.nodes,view={x:-v.x/v.z,y:-v.y/v.z,w:this.stage.clientWidth/v.z,h:this.stage.clientHeight/v.z};
  const x=Math.min(view.x,0,...nodes.map(n=>n.x))-80,y=Math.min(view.y,0,...nodes.map(n=>n.y))-80;
  const w=Math.max(view.x+view.w,350,...nodes.map(n=>n.x+330))-x+80,h=Math.max(view.y+view.h,600,...nodes.map(n=>n.y+600))-y+80;
  this.mapBounds={x,y,w,h};
  box.innerHTML='<div>画布导航 <span>'+nodes.length+' 张物料</span></div><svg viewBox="'+[x,y,w,h].join(' ')+'" preserveAspectRatio="none">'+nodes.map(n=>'<rect x="'+n.x+'" y="'+n.y+'" width="330" height="600" rx="20" fill="'+(this.selectedNodes?.has(n.id)?'#17b8a7':n.id===this.s.active?'#6172f6':'#aebbd6')+'"/>').join('')+'<rect x="'+view.x+'" y="'+view.y+'" width="'+view.w+'" height="'+view.h+'" fill="#6172f61a" stroke="#6172f6" stroke-width="'+Math.max(w/180,3)+'"/></svg>';
 }
 multiPointer(ev){
  if(ev.button!==0||ev.target.closest('input,button,select,a,.fc-controls,.fc-minimap'))return;
  const card=ev.target.closest('.fc-item'),id=card?.dataset.id,drag=card&&ev.target.closest('.fc-drag');
  if(card&&!drag)return;
  ev.preventDefault();const origin={x:ev.clientX,y:ev.clientY},v={...this.s.view};let moved=false,rect;
  if(id&&!this.selectedNodes.has(id)){this.selectedNodes.add(id);this.paint();}
  const selected=this.s.nodes.filter(n=>this.selectedNodes.has(n.id)),starts=selected.map(n=>({n,x:n.x,y:n.y,positions:Object.fromEntries(['quote','cost','sale','rivals'].filter(k=>n[k]).map(k=>[k,{...n[k]}]))}));
  const move=e=>{
   const dx=e.clientX-origin.x,dy=e.clientY-origin.y;if(!moved&&Math.hypot(dx,dy)<4)return;
   if(!moved){moved=true;if(drag)this.snap();else{rect=document.createElement('div');rect.className='fc-selection-box';this.stage.append(rect);}this.stage.classList.add('fc-panning');}
   window.getSelection()?.removeAllRanges();
   if(drag){for(const s of starts){s.n.x=s.x+dx/v.z;s.n.y=s.y+dy/v.z;for(const [k,pos] of Object.entries(s.positions)){s.n[k].x=pos.x+dx/v.z;s.n[k].y=pos.y+dy/v.z;}this.positionCards(s.n);}this.minimap();}
   else{const r=this.stage.getBoundingClientRect(),left=Math.min(origin.x,e.clientX)-r.left,top=Math.min(origin.y,e.clientY)-r.top,width=Math.abs(dx),height=Math.abs(dy);
    Object.assign(rect.style,{left:left+'px',top:top+'px',width:width+'px',height:height+'px'});
    const area={x:(left-v.x)/v.z,y:(top-v.y)/v.z,w:width/v.z,h:height/v.z};this.selectedNodes=new Set(this.s.nodes.filter(n=>n.x<area.x+area.w&&n.x+330>area.x&&n.y<area.y+area.h&&n.y+600>area.y).map(n=>n.id));this.paint();
   }
  };
  const up=()=>{rect?.remove();this.stage.classList.remove('fc-panning');window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);if(moved){this.suppress=true;setTimeout(()=>this.suppress=false,100);if(drag)this.change();}this.paint();};
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up,{once:true});
 }
 applyCardFocus(){
  const expanded=this.expanded(),focus=this.s.focus&&expanded.includes(this.s.focus)?this.s.focus:null;
  this.root.querySelectorAll('.fc-nodes .fc-card').forEach(card=>{
   const owner=card.dataset.id||card.querySelector('.fc-drag[data-n]')?.dataset.n;
   const selected=!!focus&&owner===focus,open=expanded.includes(owner);
   card.classList.toggle('fc-selected-group',selected);
   card.classList.toggle('fc-multi-selected',!!this.multiSelect&&this.selectedNodes?.has(owner));
   card.classList.toggle('fc-muted-group',!this.isTable()&&!!focus&&!selected);
   card.style.zIndex=this.isTable()?'':selected?'30':open?'20':'1';
  });
 }
 activate(id){
  this.snap();const open=this.expanded().includes(id);
  this.s.expanded=this.s.exclusive!==false?(open?[]:[id]):(open?this.expanded().filter(x=>x!==id):[...this.expanded(),id]);
  this.s.focus=open?null:id;
  if(!open){const node=this.s.nodes.find(n=>n.id===id);if(node)node.rivalsHidden=false;}
  this.s.active=open?(this.s.expanded.at(-1)||null):id;this.change();this.paint();
  const n=this.s.nodes.find(n=>n.id===id);if(!open&&n&&(!n.meta||n.meta.prices?.some(p=>!Array.isArray(p.commissions)||!Array.isArray(p.ozon_sku_ids))))this.enrich(id,true);
 }
 moneybar(){
  this.root.querySelectorAll('[data-fee-toggle]').forEach(el=>el.checked=el.dataset.feeToggle==='lastmile'?this.c.lastmile_enabled===true:el.dataset.feeToggle==='advertising'?this.c.advertising_enabled===true:this.c[el.dataset.feeToggle+'_enabled']!==false);
  this.root.querySelectorAll('[data-fee-value]').forEach(el=>{const k=el.dataset.feeValue;el.value=this.c[k+'_'+el.dataset.suffix]??({margin:35,acquisition:2,withdrawal:2,returns:15,lastmile:500,advertising:''}[k]);el.disabled=['lastmile','advertising'].includes(k)?this.c[k+'_enabled']!==true:this.c[k+'_enabled']===false;});
  this.root.querySelector('.fc-exclusive').checked=this.s.exclusive!==false;
  this.root.querySelector('.fc-fx').value=this.c.cny_per_rub||'';
  const rate=Number(this.c.cny_per_rub),date=this.c.exchange_date||'日期未记录';
  this.root.querySelector('.fc-threshold').textContent=rate>0?
   `1500 RUB ≈ ¥${(1500*rate).toFixed(2)} · 7000 RUB ≈ ¥${(7000*rate).toFixed(2)} · 参考汇率：${date}`:
   '尚无有效汇率：人民币货值暂时不能判断渠道';
 }
 drawLines(){
  const ids=this.expanded();this.root.querySelector('.fc-lines').innerHTML=this.s.nodes.filter(n=>ids.includes(n.id)).map(n=>{
   const p=n.quote||{x:n.x+370,y:n.y},x=n.x+330,y=n.y+170,x2=p.x,y2=p.y+100;
   const c=n.cost||{x:p.x+530,y:p.y},cx=p.x+490,cy=p.y+170,tx=c.x,ty=c.y+100;
   const sale=n.sale||{x:c.x+this.costWidth(n)+40,y:c.y};
   if(this.isTable())return '';
   return `<path d="M${x} ${y} C${x+80} ${y} ${x2-80} ${y2} ${x2} ${y2}" fill="none" stroke="#6b7bf5" stroke-width="3" stroke-dasharray="7 5"/><circle cx="${x}" cy="${y}" r="5" fill="#6b7bf5"/><circle cx="${x2}" cy="${y2}" r="5" fill="#19b8a7"/><path d="M${cx} ${cy} C${cx+70} ${cy} ${tx-70} ${ty} ${tx} ${ty}" fill="none" stroke="#b68bdc" stroke-width="3" stroke-dasharray="7 5"/><circle cx="${tx}" cy="${ty}" r="5" fill="#b68bdc"/><path d="M${c.x+this.costWidth(n)} ${c.y+100} C${c.x+this.costWidth(n)+20} ${c.y+100} ${sale.x-20} ${sale.y+100} ${sale.x} ${sale.y+100}" fill="none" stroke="#e5a346" stroke-width="3" stroke-dasharray="7 5"/>`;
  }).join('')+this.s.nodes.filter(n=>n.competitors&&!n.rivalsHidden&&ids.includes(n.id)&&!this.isTable()).map(n=>{const x=n.x+330,y=n.y+250,x2=n.rivals?.x??n.x+370,y2=(n.rivals?.y??n.y+600)+100;return `<path d="M${x} ${y} C${x+60} ${y} ${x2-60} ${y2} ${x2} ${y2}" fill="none" stroke="#19b8a7" stroke-width="3" stroke-dasharray="7 5"/>`;}).join('');
 }
 ozonIdentifiers(n){
  if(n.manual)return '';
  const prices=n.meta?.prices||[];
  const copy=(value,label)=>value?`<button type="button" class="fc-copy-ozon" data-copy-item="${this.e(value)}" title="点击复制${label}"><small>${label}</small><b>${this.e(value)}</b><span>⧉</span></button>`:`<span class="fc-id-missing">${label} · 未取得</span>`;
  return `<div class="fc-ozon-identifiers">${prices.length?prices.map(p=>`<section><small class="fc-id-store">${this.e(p.store)}</small><div>${p.ozon_sku_ids?.length?p.ozon_sku_ids.map(sku=>copy(sku,'Ozon SKU ID')).join(''):copy(null,'Ozon SKU ID')}${copy(p.offer_id,'Ozon货号')}${copy(p.product_id,'商品内部 ID')}</div></section>`).join(''):'<small>Ozon标识 · 尚未查询或未绑定商品</small>'}${!prices.length||prices.some(p=>!Array.isArray(p.ozon_sku_ids))?`<button type="button" class="fc-id-refresh" data-lookup="${n.id}">${this.pending?.has(n.id)?'查询中…':'查询标识'}</button>`:''}</div>`;
 }
 metaHTML(n){
  const m=n.meta||{},costs=m.costs||[],prices=m.prices||[];
  if(n.manual)return `<div class="fc-manual-note"><label>物料名称<input data-manual-name="${n.id}" value="${this.e(n.item.item_name)}"></label><small>仅用于画布估算，不会新建ERPNext物料。</small></div>`;
  const costsHTML=costs.length?costs.map(c=>`<span title="${this.e((c.company||'')+' · '+c.source)}">${c.currency==='CNY'?'¥':this.e(c.currency||'币种待核对 ')} ${Number(c.amount).toFixed(2)}<small>${this.e(c.source)}</small>${c.components?.length?'<ul class="fc-bundle-parts">'+c.components.map(p=>'<li>'+this.e(p.item_name)+' · ¥'+Number(p.unit_cost).toFixed(2)+' × '+p.qty+' = ¥'+Number(p.amount).toFixed(2)+'</li>').join('')+'</ul>':''}</span>`).join(''):'<span>暂无完整成本数据，请核对物料或套件组件</span>';
  return `<div class="fc-item-meta">${this.ozonIdentifiers(n)}<div class="fc-cost"><small>ERPNext 成本价格 · 只读</small>${costsHTML}</div>${Number(n.item.weight)>0&&Number(n.item.weight)<1?'<p class="fc-unit-alert">当前重量不足1克，请核对重量单位。</p>':''}</div>`;
 }
 priceHTML(n){
  if(n.manual)return `<div class="fc-manual-prices"><label>商品售价参考 ¥<input type="number" min="0" step="0.01" data-manual-sale="${n.id}" value="${this.e(n.manualSale)}" placeholder="物流档位需要售价"></label><label>佣金 %<input type="number" min="0" max="99.99" step="0.01" data-manual-commission="${n.id}" value="${this.e(n.commissionOverride)}" placeholder="请填写佣金率"></label></div>`;
  const m=n.meta||{},prices=m.prices||[],selected=prices[n.priceIndex]||(prices.length===1?prices[0]:null);
  return `<div class="fc-ozon-price"><small>Ozon 后台售价 · 只读</small><button data-lookup="${n.id}">${this.pending?.has(n.id)?'查询中…':'刷新售价'}</button><select data-price-node="${n.id}"><option value="" ${!selected?'selected':''}>请选择绑定商品</option>${prices.map((q,i)=>`<option value="${i}" ${q===selected?'selected':''}>${this.e(q.store+' / '+q.sku)}</option>`).join('')}</select><p>${selected?this.e(selected.currency)+' '+Number(selected.amount).toFixed(2)+(selected.currency!=='CNY'&&selected.cny!=null?' ≈ ¥'+Number(selected.cny).toFixed(2):'')+'<br>'+this.e(selected.source)+' · '+this.e(selected.checked_at?.slice(0,19)):this.e(m.errors?.join('；')||'等待后台售价')}</p></div>`;
 }
 feeParams(n){
  const c={...this.c,...n?.fees};
  return {margin:c.margin_enabled===false?0:c.margin_pct??35,acquisition:c.acquisition_enabled===false?0:c.acquisition_pct??2,withdrawal:c.withdrawal_enabled===false?0:c.withdrawal_pct??2,returns:c.returns_enabled===false?0:c.returns_rub??15,lastmile:c.lastmile_enabled===true?c.lastmile_rub??500:0,advertising:c.advertising_enabled===true?Number(c.advertising_cny||0):0};
 }
 resetFee(key){
  const defaults={acquisition:['pct',2],withdrawal:['pct',2],returns:['rub',15],lastmile:['rub',500]},entry=defaults[key];if(!entry)return;
  this.snap();this.c[key+'_'+entry[0]]=entry[1];this.change();this.paint();
 }
 feeControls(){
  return [['margin','目标利润率',35,'%','pct'],['acquisition','收单费',2,'%','pct'],['withdrawal','提现费',2,'%','pct'],['returns','退货预留',15,'RUB/单','rub'],['lastmile','最后一公里预留',500,'RUB/单','rub'],['advertising','广告费','','¥ CNY','cny']].map(([key,label,value,unit,suffix])=>`<label class="fc-fee-control"><input type="checkbox" data-fee-toggle="${key}" ${['lastmile','advertising'].includes(key)?'':'checked'}>${label}<input class="${key==='margin'?'fc-margin':''}" type="number" min="0" ${suffix==='pct'?'max="99.99"':'max="500"'.repeat(key==='lastmile'?1:0)} step="0.01" data-fee-value="${key}" data-suffix="${suffix}" value="${value}"><span>${unit}</span>${['acquisition','withdrawal','returns','lastmile'].includes(key)?`<button type="button" class="fc-fee-reset" data-reset-fee="${key}" title="恢复默认值 ${value}${unit}" aria-label="重置${label}">↺</button>`:''}</label>`).join('');
 }


 matrixRowHeight(){
  const cards=this.root?.querySelectorAll('.fc-item')||[];
  return Math.max(600,...Array.from(cards,el=>el.scrollHeight))+20;
 }
 arrangeCards(nodes,originX=0,originY=0){
  const rowHeight=this.matrixRowHeight();
  nodes.forEach((n,i)=>{
   const x=originX+(i%10)*350,y=originY+Math.floor(i/10)*rowHeight,dx=x-n.x,dy=y-n.y;
   for(const key of ['quote','cost','sale','rivals'])if(n[key]){n[key].x+=dx;n[key].y+=dy;}
   n.x=x;n.y=y;
  });
 }
 arrangeSystemCards(originX){this.arrangeCards(this.s.nodes.filter(n=>n.systemGenerated),originX);}
 sortCards(){
  if(this.skuRefreshing||this.saving)throw Error('请等待刷新或保存完成后再排序');
  this.snap();const manual=this.s.nodes.filter(n=>!n.systemGenerated),system=this.s.nodes.filter(n=>n.systemGenerated);
  this.arrangeCards(manual);
  this.arrangeCards(system,0,manual.length?Math.ceil(manual.length/10)*this.matrixRowHeight()+40:0);
  this.change();this.paint();this.status('已分别排列手工与系统卡片 · 每排10个 · 请保存画布');
 }
 skuMatrixPosition(originX){
  // Fill free cells in ten columns; use full card height plus a small gap.
  const rowHeight=this.matrixRowHeight();
  for(let i=0;i<10000;i++){
   const x=originX+(i%10)*350,y=Math.floor(i/10)*rowHeight;
   if(!this.s.nodes.some(n=>Math.abs(n.x-x)<340&&Math.abs(n.y-y)<rowHeight-10))return {x,y};
  }
  throw Error('无法找到空闲的商品排列位置');
 }
 async refreshSkus(){
  if(this.skuRefreshing)return;this.skuRefreshing=true;this.status('正在读取 Ozon 全部商品…');
  let added=0,merged=0;this.snap();const columnX=Math.max(0,...this.s.nodes.filter(n=>!n.systemGenerated).map(n=>Math.max(n.x+330,(n.sale?.x||n.cost?.x||n.quote?.x||n.x)+800)))+220;
  try{
   const {stores}=await this.api('list_ozon_canvas_products');
   for(const store of stores){
    let cursor='',seen=new Set();
    do{
     const page=await this.api('list_ozon_canvas_products',{store,last_id:cursor});
     for(const p of page.products){
      const matches=this.s.nodes.filter(n=>(p.item&&n.item.item_code===p.item.item_code)||(n.meta?.prices||[]).some(q=>q.store===p.store&&(q.product_id===p.product_id||(q.ozon_sku_ids||[]).some(s=>p.ozon_sku_ids.includes(s)))));
      const existing=matches.find(n=>!n.systemGenerated)||matches[0];
      const record={...p,sku:p.ozon_sku_ids[0]||p.offer_id,commissions:p.commissions||[]};
      delete record.item;delete record.costs;
      if(existing){
       existing.meta ||= {costs:[],prices:[],errors:[]};existing.meta.prices ||= [];
       const index=existing.meta.prices.findIndex(q=>q.store===p.store&&q.product_id===p.product_id);
       if(index>=0)existing.meta.prices[index]={...existing.meta.prices[index],...record,commissions:record.commissions.length?record.commissions:existing.meta.prices[index].commissions||[]};
       else existing.meta.prices.push(record);
       // Only remove duplicate auto-generated cards; never discard a user's simulations.
       this.s.nodes=this.s.nodes.filter(n=>n===existing||!n.systemGenerated||!matches.includes(n));merged++;continue;
      }
      if(this.s.nodes.length>=300)throw Error('画布达到300张上限，已读取的商品保留，请分画布处理');
      const {x:right,y}=this.skuMatrixPosition(columnX);
      const item=p.item?this.normalize(p.item):{item_code:'ozon-'+p.store+'-'+p.product_id,item_name:p.name||p.offer_id,image:p.image,length:'',width:'',height:'',weight:'',value:'',value_currency:'CNY'};
      const cnyCosts=(p.costs||[]).filter(c=>c.currency==='CNY');if(cnyCosts.length===1)item.value=Number(cnyCosts[0].amount).toFixed(2);
      const n={id:crypto.randomUUID(),item,x:right,y,systemGenerated:true,ozonOnly:!p.item,meta:{costs:p.costs||[],prices:[record],errors:p.binding_ambiguous?['SKU 绑定多件物料，未自动选用']:[]},priceIndex:0};
      this.s.nodes.push(n);added++;
     }
     this.change();this.paint();this.status('已新增 '+added+' 张 · 已匹配 '+merged+' 个商品');
     if(!page.more)break;if(!page.last_id||seen.has(page.last_id))throw Error('商品分页没有推进，请重试');seen.add(page.last_id);cursor=page.last_id;
    }while(true);
   }
   this.arrangeSystemCards(columnX);this.change();this.paint();
   this.status('SKU 刷新完成：新增 '+added+' 张，匹配 '+merged+' 个 · 请保存画布');
  }finally{this.skuRefreshing=false;}
 }

 async collectionDialog(nodeId){
  const d=new frappe.ui.Dialog({title:'抓取跟卖 · 浏览器采集与导入',size:'large',fields:[{fieldtype:'HTML',fieldname:'help'},{fieldtype:'Small Text',fieldname:'result',label:'粘贴 F12 采集结果 JSON',reqd:1}],primary_action_label:'保存报价与画布',primary_action:async v=>{
   if(d._saving)return;d._saving=true;d.get_primary_btn().prop('disabled',true);
   try{
    ozfcParseCompetitors(v.result);
    if(d._lastInput!==v.result){d._lastInput=v.result;d._batchId=crypto.randomUUID();}
    const saved=await this.save({batch_id:d._batchId,payload:JSON.parse(v.result)});
    const summary=saved.quote_summary;
    d.hide();frappe.show_alert({message:'已保存：匹配 '+summary.matched+' 张卡片，未匹配 '+summary.unmatched+'，失败/未完成 '+summary.failed,indicator:'green'});
   }catch(e){frappe.msgprint(this.e(e.message));}finally{d._saving=false;d.get_primary_btn().prop('disabled',false);}
  }});
  d.fields_dict.help.$wrapper.html('<div class="fc-collection-help"><p>① 复制命令 → ② 在正常登录的 Ozon 页面 F12 运行 → ③ 等待完成，结果自动复制 → ④ 粘贴到下面并保存。</p><button type="button" class="btn btn-primary">复制F12命令</button><small>命令只采集当前画布卡片关联的 Ozon 商品内部 ID。想采集全部商品，请先点击“刷新 SKU”。逐个请求，间隔1.5秒。登录失败、访问拒绝或限流时停止；不会复制 Cookie，也不会修改售价。多个店铺需有相应登录权限。</small><small>保存会连同当前画布更改一起写入数据库；报价独立存入“Ozon 跟卖价格历史”，每次保存追加新版本，单次采集上限20MB。失败、仍在计算的结果不会覆盖已保存报价；找不到卡片的商品请先刷新 SKU。</small></div>');
  const button=d.fields_dict.help.$wrapper.find('button');
  button.on('click',async()=>{
   button.prop('disabled',true).text('读取当前画布商品 ID…');
   try{
    const products=ozfcCanvasCollectionTargets(this.s.nodes);
    if(!products.length)throw Error('当前画布没有可采集的 Ozon 商品内部 ID，请先选择物料并查询标识，或点击刷新 SKU');
    const manifest=await this.api('competitor_collection_manifest',{products:JSON.stringify(products)});
    if(!manifest.products?.length)throw Error('没有可采集的 Ozon 商品');
    await this.copyItem(ozfcCollectionCommand(manifest.products));
   }catch(e){frappe.msgprint(this.e(e.message));}finally{button.prop('disabled',false).text('复制F12命令');}
  });d.show();
 }
 applyCompetitors(input,nodeId){
  if(typeof input==='string'&&new TextEncoder().encode(input).length>20000000)throw Error('采集 JSON 不能超过20MB');
  const batch=ozfcParseCompetitors(input),updates=new Map(),touched=new Set(),importedAt=new Date().toISOString();let failed=0,unmatched=0;
  for(const r of batch.results){
   if(!r.ok||r.data.syncing){failed++;continue;}
   const candidates=this.s.nodes.filter(n=>(n.meta?.prices||[]).some(p=>String(p.product_id)===r.itemId&&(!r.store||p.store===r.store)));
   const stores=new Set(candidates.flatMap(n=>(n.meta?.prices||[]).filter(p=>String(p.product_id)===r.itemId&&(!r.store||p.store===r.store)).map(p=>p.store)));
   if(!candidates.length||stores.size!==1){unmatched++;continue;}
   for(const n of candidates){
    const data=updates.get(n)||structuredClone(n.competitors||{products:[]});
    data.products ||= [];
    const store=[...stores][0],key=store+'|'+r.itemId;
    const record={key,store,itemId:r.itemId,companyId:r.companyId||null,offers:r.offers,raw:r.data,collectedAt:r.collectedAt,importedAt};
    const index=data.products.findIndex(p=>p.key===key),touch=n.id+'|'+key;
    if(index<0)data.products.push(ozfcCompetitorVersion(null,record));
    else if(touched.has(touch))data.products[index]={...record,version:data.products[index].version,history:data.products[index].history};
    else data.products[index]=ozfcCompetitorVersion(data.products[index],record);
    touched.add(touch);
    data.message='浏览器采集结果';data.checked_at=r.collectedAt;updates.set(n,data);
   }
  }
  if(updates.size){
   const prospective=structuredClone(this.s);
   prospective.nodes.forEach(n=>{const source=this.s.nodes.find(q=>q.id===n.id);if(updates.has(source))n.competitors=updates.get(source);});
   if(new TextEncoder().encode(JSON.stringify(prospective)).length>2000000)throw Error('导入后画布超过2MB，请分画布保存或减少采集范围');
   this.snap();for(const [n,data] of updates){n.competitors=data;n.rivalsHidden=false;for(const product of data.products)this.rivalVersions?.delete(n.id+'|'+product.key);}this.change();this.paint();
  }
  return {matched:updates.size,unmatched,failed};
 }
 arrangeMaterialCards(id){
  const n=this.s.nodes.find(n=>n.id===id);if(!n)return;
  this.snap();
  const quoteCard=this.root?.querySelector?.('.fc-drag[data-n="'+id+'"][data-q]')?.closest?.('.fc-card');
  n.quote={x:n.x+370,y:n.y};
  n.cost={x:n.quote.x+530,y:n.y};
  n.sale={x:n.cost.x+this.costWidth(n)+40,y:n.y};
  n.rivals={x:n.quote.x,y:n.y+(quoteCard?.offsetHeight||580)+30};
  this.change();this.paint();this.status('已整理当前物料的关联卡片 · 物料位置保持不变 · 请保存画布');
 }
 async refreshCompetitors(id){const n=this.s.nodes.find(n=>n.id===id);if(n?.rivalsHidden){n.rivalsHidden=false;this.change();this.paint();}return this.collectionDialog(id);}
 competitorCountHTML(n){
  const products=n.competitors?.products||[],count=products.reduce((sum,p)=>sum+(p.offers||[]).length,0);
  return '<div class="fc-rival-count" style="padding:0 16px 10px;font-size:11px;color:#258d86">跟卖 / 竞争报价：<b>'+count+'</b> 条'+(!products.length?' · 尚未导入':'')+'</div>';
 }
 competitorRmb(price,currency){
  if(currency==='CNY')return '<small class="fc-rival-rmb">人民币 ¥'+Number(price).toFixed(2)+'</small>';
  const fx=Number(this.c?.cny_per_rub);
  return '<small class="fc-rival-rmb">'+(currency==='RUB'&&fx>0?'≈ 人民币 ¥'+(price*fx).toFixed(2)+' · 参考换算':'暂无人民币换算汇率')+'</small>';
 }
 competitorManagerLink(id){
  return /^\d+$/.test(String(id))?'<a class="btn btn-xs btn-default" style="display:inline-block;margin:8px 0;color:#258d86" href="https://seller.ozon.ru/app/prices/manager/'+id+'/prices" target="_blank" rel="noopener noreferrer">查看后台竞争报价 ↗</a>':'';
 }
 competitorHTML(n){
  const data=n.competitors;if(!data||(!this.isTable()&&(n.rivalsHidden||!this.expanded().includes(n.id))))return '';const pos=n.rivals||{x:n.x+370,y:n.y+600};
  const date=t=>t&&!Number.isNaN(Date.parse(t))?new Date(t).toLocaleString('zh-CN',{hour12:false}):'未提供';
  const products=data.products||[];
  return '<article class="fc-card fc-competitors" style="left:'+pos.x+'px;top:'+pos.y+'px"><header class="fc-drag" data-n="'+n.id+'" data-rivals="true"><span>跟卖报价 · 浏览器采集</span><button data-close-rivals="'+n.id+'">×</button></header><div class="fc-competitor-content">'+(products.length?products.map(current=>{
   const selection=this.rivalVersions?.get(n.id+'|'+current.key)||'current';
   const p=selection==='current'?current:(current.history||[]).find(v=>String(v.version)===selection)||current;
const versions='<label class="fc-rival-version-label">报价版本<select data-rival-version="'+n.id+'" data-rival-key="'+this.e(current.key)+'"><option value="current" '+(p===current?'selected':'')+'>最新 · 第'+(current.version||1)+'版</option>'+(current.history||[]).slice().sort((a,b)=>b.version-a.version).map(v=>'<option value="'+v.version+'" '+(p===v?'selected':'')+'>历史 · 第'+v.version+'版 · '+this.e(date(v.collectedAt||v.importedAt))+'</option>').join('')+'</select></label>'+(current.historyMore?'<button class="fc-history-more" data-rival-more="'+n.id+'" data-rival-key="'+this.e(current.key)+'">加载更多历史版本</button>':'');
   const valid=p.offers.filter(q=>!q.rejectionReason.length),currencies=[...new Set(valid.map(q=>q.currency))];
   return '<section><small>'+this.e(p.store)+' · 商品 '+this.e(p.itemId)+'</small>'+this.competitorManagerLink(p.itemId)+versions+'<p>导入时间：'+this.e(date(p.importedAt))+'</p><p>采集时间：'+this.e(date(p.collectedAt))+'</p><div class="fc-competitor-summary">'+p.offers.length+' 个报价 · '+valid.length+' 个未标记拒绝'+(currencies.length===1&&valid.length?' · 最低 '+Math.min(...valid.map(q=>q.price)).toFixed(2)+' '+this.e(currencies[0]):'')+'</div>'+p.offers.map((q,i)=>'<div class="fc-competitor-offer '+(q.rejectionReason.length?'rejected':'')+'"><b>'+q.price.toFixed(2)+' '+this.e(q.currency)+'</b>'+this.competitorRmb(q.price,q.currency)+'<a href="'+this.image(q.url)+'" target="_blank" rel="noopener noreferrer">报价商品 '+(i+1)+' ↗</a><small>Ozon 数据时间：'+this.e(date(q.downloadedAt))+'</small>'+(q.rejectionReason.length?'<em>接口拒绝标记：'+this.e(q.rejectionReason.map(s=>s==='bad-offer-price'?'报价价格异常（bad-offer-price）':s).join('、'))+'</em>':'<small>接口未标记拒绝；仍需核对是否同款</small>')+'</div>').join('')+(!p.offers.length?'<p>接口本次没有返回竞争报价。</p>':'')+'</section>';
  }).join(''):'<p>'+this.e(data.message||'请通过“抓取跟卖”导入新结果')+'</p>')+'</div></article>';
 }



 canvasForStorage(){
  const canvas=structuredClone(this.s);
  for(const node of canvas.nodes)if(node.competitors?.fromHistoryStore)node.competitors={external:true};
  return canvas;
 }
 async hydrateHistory(){
  if(!this.name)return;
  const canvas=this.name,state=this.s;
  const catalog=await this.api('competitor_catalog',{canvas});
  if(this.name!==canvas||this.s!==state)return;
  for(const node of this.s.nodes){
   const keys=new Set((node.meta?.prices||[]).map(p=>p.store+'|'+p.product_id));
   const products=catalog.products.filter(p=>keys.has(p.key));
   if(products.length)node.competitors={fromHistoryStore:true,products,message:'独立报价历史'};
  }
  this.paint();
 }
 async chooseQuoteVersion(el){
  const node=this.s.nodes.find(n=>n.id===el.dataset.rivalVersion),key=el.dataset.rivalKey,value=el.value;
  const product=node?.competitors?.products?.find(p=>p.key===key);
  const record=value==='current'?product:product?.history?.find(v=>String(v.version)===value);
  if(!record)return;
  if(record.record_name&&!record.loaded){
   el.disabled=true;
   try{const detail=await this.api('competitor_record',{name:record.record_name});Object.assign(record,detail);}
   finally{el.disabled=false;}
  }
  this.rivalVersions ||= new Map();this.rivalVersions.set(node.id+'|'+key,value);this.paint();
 }
 async moreQuoteVersions(el){
  const node=this.s.nodes.find(n=>n.id===el.dataset.rivalMore),key=el.dataset.rivalKey;
  const product=node?.competitors?.products?.find(p=>p.key===key);if(!product)return;
  el.disabled=true;
  try{
   const result=await this.api('competitor_versions',{canvas:this.name,store:product.store,product_id:product.itemId,before_version:product.version,start:product.history.length});
   const known=new Set(product.history.map(v=>v.record_name));
   product.history.push(...result.versions.filter(v=>!known.has(v.record_name)));product.historyMore=result.more;this.paint();
  }finally{el.disabled=false;}
 }

 async enrich(id,force=false){
  const n=this.s.nodes.find(n=>n.id===id);this.pending ||= new Set();if(!n||n.manual||this.pending.has(id))return;this.pending.add(id);this.paint();
  try{
   const m=n.ozonOnly?{costs:[],prices:n.meta?.prices||[],errors:['未绑定 ERPNext 物料；尺寸、重量和成本需手动填写']} : await this.api('item_details',{item_code:n.item.item_code,force:force?1:0});
   if(!this.s.nodes.includes(n))return;
   // Capture a value still being typed before replacing the card DOM.
   this.root.querySelectorAll('input[data-n]').forEach(input=>{
    if(input.dataset.n===id)n.item[input.dataset.f]=input.type==='checkbox'?input.checked:input.value;
   });
   this.snap();n.meta=m;
   // Never overwrite a value entered while the background query was running.
   const costs=m.costs.filter(c=>c.currency==='CNY');
   if(!n.costEdited)n.item.value=costs.length===1?Number(costs[0].amount).toFixed(2):'';n.item.value_currency='CNY';
   if(m.prices.length===1)n.priceIndex=0;
   this.change();
  }catch(e){if(this.s.nodes.includes(n)){n.meta={...(n.meta||{}),errors:['后台查询未完成，请稍后重试']};}}
  finally{this.pending.delete(id);if(this.s.nodes.includes(n))this.paint();}
 }
 constructor(page){this.s={version:2,nodes:[],active:null,expanded:[],exclusive:true,view:{x:60,y:50,z:1}};this.undo=[];this.redo=[];this.name=null;this.modified=null;this.dirty=false;this.root=document.createElement('div');this.root.className='ozfc';page.main.append(this.root);this.shell();this.bind();this.boot();}
 e(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
 image(s){return /^(\/[^/]|https?:\/\/)/i.test(s||'')?this.e(s):'';}
 async api(method,args={}){const x=await frappe.call({method:'fengjing_app.fengjing_business.page.ozon_freight_calcula.ozon_freight_calcula.'+method,args});if(x.exc)throw Error('请求失败');return x.message;}
 status(t){this.root.querySelector('.fc-status').textContent=t;}
 change(){this.changeId=(this.changeId||0)+1;this.dirty=true;this.status('● 有未保存的更改');}
 snap(){this.undo.push(JSON.stringify({s:this.s,c:this.c}));if(this.undo.length>50)this.undo.shift();this.redo=[];}
 history(back){const a=back?this.undo:this.redo,b=back?this.redo:this.undo;if(!a.length)return;b.push(JSON.stringify({s:this.s,c:this.c}));const x=JSON.parse(a.pop());this.s=x.s;this.c=x.c;this.change();this.paint();}
 shell(){this.root.innerHTML=`<style>${OzFreightCanvas.css}</style><div class="fc-hero"><div><small>OZON · FREIGHT STUDIO</small><h2>让每一笔运费，算得明明白白</h2><p>物料 → 渠道筛选 → 计费过程 · 自由拖拽的运费分析画板</p></div><b class="fc-count">0 张物料卡片</b></div><div class="fc-tools"><input class="fc-title" value="我的运费画板" aria-label="画布名称"><button data-a="blank">＋ 空白物料</button><button data-a="add" class="primary">＋ 选择物料</button><button data-a="config">⚙ 物流配置</button><button data-a="save" class="primary">保存画布</button><button data-a="open">打开</button><button data-a="new">新画布</button><button data-a="export">导出</button><button data-a="import">导入画布</button><label class="fc-utility"><input type="checkbox" class="fc-exclusive" checked> 单物料展开</label><div class="fc-mode-switch"><button data-a="mode-canvas" class="active">画布</button><button data-a="mode-table">横向表格</button></div><button data-a="brief">简略展示</button><button data-a="collect">抓取跟卖</button><button data-a="sku-refresh">刷新 SKU</button><button data-a="sort-cards">排序</button><label class="fc-utility">1 RUB = ¥ <input class="fc-fx" type="number" min="0" step="0.000001" placeholder="汇率"></label><button data-a="exchange">刷新汇率</button><span class="fc-threshold"></span><span class="fc-status">读取中…</span></div><div class="fc-moneybar">${this.feeControls()}<label class="fc-fee-control">佣金 <input class="fc-top-commission" type="number" min="0" max="99.99" step="0.01" placeholder="API"><span>%</span></label></div><div class="fc-menu-switch"><button data-a="menu-toggle" aria-label="收起顶部菜单" aria-expanded="true"><span class="fc-menu-grip"></span><span>收起工具栏</span><span class="fc-menu-chevron"></span></button></div><div class="fc-stage" tabindex="0"><div class="fc-world"><svg class="fc-lines" width="1" height="1"></svg><div class="fc-nodes"></div></div><div class="fc-empty"><span>◇</span><h3>从一张物料卡片开始</h3><p>选择物料，点击卡片展开报价和计算过程</p><button data-a="add" class="primary">＋ 添加物料</button></div><div class="fc-controls"><button data-a="multi-select" title="框选物料，拖动标题栏集体移动">多选</button><button data-a="undo">↶</button><button data-a="redo">↷</button><button data-a="minus">−</button><span class="fc-zoom">100%</span><button data-a="plus">＋</button><button data-a="fit">居中</button><button data-a="fullscreen">全屏</button></div><small class="fc-help">空白处拖拽平移 · 滚轮缩放 · 拖动卡片顶部移动 · Ctrl+Z 撤销</small></div>`;this.stage=this.root.querySelector('.fc-stage');this.world=this.root.querySelector('.fc-world');this.root.querySelector('.fc-title').oninput=()=>this.change();this.layoutObserver=new ResizeObserver(()=>this.layout());this.layoutObserver.observe(this.root);window.addEventListener('resize',()=>this.layout());}
 layout(){if(!this.stage)return;const rect=this.root.getBoundingClientRect(),height=Math.max(220,window.innerHeight-Math.max(0,rect.top)-16);this.root.style.height=height+'px';this.root.style.setProperty('--fc-card-height',Math.max(140,this.stage.clientHeight-16)+'px');}
 logisticsHeight(){this.root.querySelectorAll('.fc-results').forEach(el=>{const rows=[...el.querySelectorAll('.fc-route')].filter(row=>!row.hidden).slice(0,this.isTable()?1:3);if(rows.length){const last=rows.at(-1);el.style.height=(last.offsetTop+last.offsetHeight-rows[0].offsetTop+24)+'px';}});}
 topCommission(){const n=this.s.nodes.find(n=>n.id===(this.s.focus||this.s.active))||this.s.nodes[0],el=this.root.querySelector('.fc-top-commission');if(!el)return;const prices=n?.meta?.prices||[],product=prices[n?.priceIndex]||(prices.length===1?prices[0]:null),api=product?.commissions?.find(c=>c.schema===(n.commissionSchema||'RFBS'));el.value=n?.commissionOverride??api?.percent??'';el.dataset.node=n?.id||'';el.disabled=!n;}
 async boot(){
  try{this.bootData=await this.api('bootstrap');this.c=this.bootData.defaults;
   this.c.cny_per_rub=this.bootData.exchange.cny_per_rub;this.c.exchange_date=this.bootData.exchange.date;
   this.c.margin_pct=35;
   Object.assign(this.c,{margin_enabled:true,acquisition_pct:2,acquisition_enabled:true,withdrawal_pct:2,withdrawal_enabled:true,returns_rub:15,returns_enabled:true,lastmile_rub:500,lastmile_enabled:false});
   this.bootData.defaults=structuredClone(this.c);
   this.paint();this.status('初始报价快照 · 使用前请核对');
   if(this.bootData.canvases.length){const x=await this.api('load_canvas',{name:this.bootData.canvases[0].name});const parse=v=>typeof v==='string'?JSON.parse(v):v;this.s=parse(x.canvas);this.c=parse(x.config);this.validateCanvas(this.s);this.validateConfig(this.c);const migrated=this.prepare(this.s,this.c);this.s.focus=null;this.name=x.name;this.modified=x.modified;this.root.querySelector('.fc-title').value=x.title;this.dirty=migrated;this.paint();this.status('✓ 默认打开第一个画布'+(migrated?' · 请保存更新':''));try{await this.hydrateHistory();}catch(e){this.status('画布已打开，独立报价历史加载失败');}}
  }catch(e){this.status('读取失败，请检查画布单据读取权限');}
 }
 async copyItem(code){
  if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(code);
  else{const el=document.createElement('textarea');el.value=code;el.style.position='fixed';el.style.opacity='0';document.body.append(el);el.select();const ok=document.execCommand('copy');el.remove();if(!ok)throw Error('Clipboard blocked');}
  frappe.show_alert({message:'已复制',indicator:'green'});
 }
 hideImageHover(){this.hoverImage?.remove();this.hoverImage=null;}
 bindImageHover(host,selector,allowed=()=>true){
  host.addEventListener('mouseover',ev=>{
   const img=ev.target.closest(selector);if(!img||!allowed()||img.contains(ev.relatedTarget))return;
   this.hideImageHover();const box=document.createElement('div');box.className='fc-original-image-preview';
   const full=document.createElement('img');full.src=img.currentSrc||img.src;full.alt=img.alt||'物料原图';box.append(full);
   document.body.append(box);this.hoverImage=box;
   const rect=img.getBoundingClientRect(),width=box.offsetWidth,height=box.offsetHeight;
   let left=rect.right+16;if(left+width>window.innerWidth-12)left=rect.left-width-16;
   box.style.left=Math.max(12,Math.min(left,window.innerWidth-width-12))+'px';
   box.style.top=Math.max(12,Math.min(rect.top,window.innerHeight-height-12))+'px';
  });
  host.addEventListener('mouseout',ev=>{if(ev.target.closest(selector))this.hideImageHover();});
  host.addEventListener('scroll',()=>this.hideImageHover(),true);
  host.addEventListener('pointerdown',()=>this.hideImageHover());
 }
 costWidth(n){return this.root?.querySelector?.('.fc-drag[data-n="'+n.id+'"][data-cost]')?.closest?.('.fc-card')?.offsetWidth||1000;}
 imageCard(){
  if(!this.stage?.querySelector)return;
  this.stage.querySelector('.fc-full-image-card')?.remove();this.stage.querySelector('.fc-image-link')?.remove();
  if(this.multiSelect)return;
  const n=this.s.nodes.find(n=>n.id===this.imageNode);
  const item=n&&this.root.querySelector('.fc-item[data-id="'+n.id+'"]');
  if(!item)return;
  const box=document.createElement('article');box.className='fc-card fc-full-image-card';
  box.innerHTML=`<header><span>${this.e(n.item.item_name)} · 完整图片</span><button data-close-image="1" aria-label="关闭图片卡片">×</button></header><div><img src="${this.image(n.item.image)}" alt="${this.e(n.item.item_name)}"></div>`;
  const host=this.isTable()?this.stage:this.world;host.append(box);
  const full=box.querySelector?.('img'),thumbnail=item.querySelector?.('.fc-product img'),source=this.image(n.item.image);
  this.imageRatios ||= {};
  const resize=()=>{
   const ratio=full?.naturalHeight>0?full.naturalWidth/full.naturalHeight:this.imageRatios[source]|| (thumbnail?.naturalHeight>0?thumbnail.naturalWidth/thumbnail.naturalHeight:1);
   this.imageRatios[source]=ratio;
   const available=Math.max(1,(box.clientHeight||500)-(box.querySelector?.('header')?.offsetHeight||42)-32);
   box.style.width=Math.max(100,Math.ceil(available*ratio+34))+'px';
  };
  resize();
  const rect=item.getBoundingClientRect(),stage=this.stage.getBoundingClientRect();
  const left=this.isTable()?rect.left-stage.left-box.offsetWidth-30+(this.stage.scrollLeft||0):n.x-box.offsetWidth-30;
  const top=this.isTable()?rect.top-stage.top+(this.stage.scrollTop||0):n.y;
  box.style.left=left+'px';box.style.top=top+'px';
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('fc-image-link');
  const path=document.createElementNS('http://www.w3.org/2000/svg','path'),x=left+box.offsetWidth,y=top+100,end=this.isTable()?rect.left-stage.left+(this.stage.scrollLeft||0):n.x;
  path.setAttribute('d',`M${x} ${y} C${x+15} ${y} ${end-15} ${y} ${end} ${y}`);path.setAttribute('fill','none');path.setAttribute('stroke','#94a7e6');path.setAttribute('stroke-width','3');path.setAttribute('stroke-dasharray','6 4');svg.append(path);host.append(svg);
  full?.addEventListener('load',()=>{if(!box.isConnected)return;resize();box.style.left=(this.isTable()?rect.left-stage.left-box.offsetWidth-30+(this.stage.scrollLeft||0):n.x-box.offsetWidth-30)+'px';});
 }
 positionCards(n){
  const quote=n.quote||{x:n.x+370,y:n.y},cost=n.cost||{x:quote.x+530,y:quote.y},sale=n.sale||{x:cost.x+this.costWidth(n)+40,y:cost.y};
  this.root.querySelectorAll('.fc-drag[data-n="'+n.id+'"]').forEach(header=>{const pos=header.dataset.rivals?(n.rivals||{x:n.x+370,y:n.y+600}):header.dataset.sale?sale:header.dataset.cost?cost:header.dataset.q?quote:n;const card=header.closest('.fc-card');card.style.left=pos.x+'px';card.style.top=pos.y+'px';});
  this.drawLines();this.imageCard();
 }
 tableRows(){
  this.root.querySelectorAll('.fc-table-row').forEach(row=>{
   const item=row.querySelector('.fc-item'),quotes=row.querySelector('.fc-quotes'),cost=row.querySelector('.fc-cost-card'),sale=row.querySelector('.fc-sale-card');
   if(!item||!quotes||!cost||!sale)return;
   row.classList.add('fc-editor-row');item.classList.add('fc-fixed-item');sale.classList.add('fc-fixed-sale');
   const scroll=document.createElement('div');scroll.className='fc-row-scroll';const content=document.createElement('div');content.className='fc-row-content';
   const rivals=row.querySelector('.fc-competitors');
   const quotePanel=document.createElement('div');quotePanel.className='fc-table-rivals-panel';
   if(rivals)quotePanel.append(rivals);else quotePanel.innerHTML='<div class="fc-table-rivals-empty">跟卖 / 竞争报价<br><small>尚未导入采集结果</small></div>';
   content.append(quotes,cost,quotePanel);scroll.append(content);row.replaceChildren(item,scroll,sale);
   const title=document.createElement('div');title.className='fc-editor-row-title';title.textContent=item.querySelector('.fc-product h3').textContent;row.prepend(title);
   scroll.addEventListener('scroll',()=>{if(this.scrollSyncing)return;this.tableScroll=scroll.scrollLeft;this.scrollSyncing=true;this.root.querySelectorAll('.fc-row-scroll').forEach(other=>{if(other!==scroll&&Math.abs(other.scrollLeft-scroll.scrollLeft)>1)other.scrollLeft=scroll.scrollLeft;});requestAnimationFrame(()=>this.scrollSyncing=false);});
  });
  const contents=[...this.root.querySelectorAll('.fc-row-content')],width=Math.max(0,...contents.map(el=>el.scrollWidth));
  contents.forEach(el=>el.style.width=width+'px');
 }
 restoreTableScroll(){if(this.isTable())this.root.querySelectorAll('.fc-row-scroll').forEach(el=>el.scrollLeft=this.tableScroll||0);}
 filterLogistics(){
  this.root.querySelectorAll('[data-logistics-search]').forEach(input=>{
   const query=input.value.trim().toLowerCase(),list=this.root.querySelector('[data-logistics-list="'+input.dataset.logisticsSearch+'"]');if(!list)return;let count=0;
   list.querySelectorAll('.fc-route').forEach(row=>{row.hidden=!row.dataset.logisticsText.includes(query);if(!row.hidden)count++;});
   input.parentElement.querySelector('.fc-search-count').textContent=count+' 条物流';
   if(!count)list.style.height='80px';
  });
 }
 feeDiff(n,key){
  const defaults={margin:['pct',35],acquisition:['pct',2],withdrawal:['pct',2],returns:['rub',15],lastmile:['rub',500],advertising:['cny','']},entry=defaults[key];if(!entry)return false;
  const field=key+'_'+entry[0],global=this.c[field]??entry[1],local=n.fees?.[field]??global;
  const flag=key+'_enabled',base=['lastmile','advertising'].includes(key)?this.c[flag]===true:this.c[flag]!==false;
  const enabled=n.fees?.[flag]??base;
  return Number(local||0)!==Number(global||0)||enabled!==base;
 }
 feeDot(n,key){return this.feeDiff(n,key)?'<i class="fc-override-dot" title="当前物料设置与顶部公共值不同" role="img" aria-label="已单独修改，与公共值不同"></i>':'';}
 nodeFees(n){
  const c={...this.c,...n.fees};
  return '<div class="fc-node-fees">'+[['margin','目标利润率','pct',35],['acquisition','收单费','pct',2],['withdrawal','提现费','pct',2],['returns','退货预留','rub',15],['lastmile','最后一公里','rub',500],['advertising','广告费','cny','']].map(([k,label,suffix,d])=>{
   const enabled=['lastmile','advertising'].includes(k)?c[k+'_enabled']===true:c[k+'_enabled']!==false;
   return `<label class="${this.feeDiff(n,k)?'fc-fee-overridden':''}"><span><input type="checkbox" data-node-toggle="${n.id}" data-key="${k}" ${enabled?'checked':''}> ${label}${this.feeDot(n,k)}</span><div><input type="text" inputmode="decimal" data-edit-node="${n.id}" data-edit-key="${k+'_'+suffix}" value="${this.e(c[k+'_'+suffix]??d)}"><small>${suffix==='pct'?'%':suffix==='rub'?'RUB/单':'¥'}</small></div></label>`;
  }).join('')+'</div>';
 }
 liveEdit(el){
  const id=el.dataset.editNode||el.dataset.n,n=this.s.nodes.find(n=>n.id===id),key=el.dataset.editKey||(el.dataset.f==='value'?'cost':null);
  if(!n||!key)return false;
  const v=el.value===''?'':Number(el.value);
  if(v!==''&&(!Number.isFinite(v)||v<0||((key==='commission'||key.endsWith('_pct'))&&v>=100)||(key==='lastmile_rub'&&v>500)))return true;
  if(this.editSession!==id+key){this.snap();this.editSession=id+key;}
  if(key==='cost'){n.item.value=el.value;n.item.value_currency='CNY';n.costEdited=true;}
  else if(key==='sale'){if(n.manual)n.manualSale=v;else if(v==='')delete n.saleOverride;else n.saleOverride=v;}
  else if(key==='commission'){if(v==='')delete n.commissionOverride;else n.commissionOverride=v;}
  else{n.fees ||= {};n.fees[key]=v;}
  this.change();
  const selector=el.dataset.editNode?'[data-edit-node="'+id+'"][data-edit-key="'+key+'"]':'input[data-n="'+id+'"][data-f="value"]',value=el.value;
  const start=el.selectionStart,end=el.selectionEnd,direction=el.selectionDirection,session=this.editSession;
  this.paint();this.restoreTableScroll();this.editSession=session;
  const next=this.root.querySelector(selector);if(next){next.value=value;next.focus({preventScroll:true});if(start!==null&&start!==undefined)next.setSelectionRange?.(start,end,direction||'none');}
  return true;
 }
 bind(){
 this.bindImageHover(this.root,'.fc-item .fc-product img',()=>this.isTable());
 this.root.addEventListener('focusout',()=>{this.editSession=null;});
 this.root.addEventListener('click',ev=>{
  const b=ev.target.closest('[data-reset-fee]');if(!b)return;ev.preventDefault();ev.stopImmediatePropagation();
  this.resetFee(b.dataset.resetFee);
 });
 this.root.addEventListener('click',ev=>{
  const img=ev.target.closest('.fc-item .fc-product img'),close=ev.target.closest('[data-close-image]');
  if(!img&&!close)return;
  ev.stopImmediatePropagation();
  const id=img?.closest('.fc-item').dataset.id;
  this.imageNode=close||this.imageNode===id?null:id;
  this.imageCard();
 });
 this.root.addEventListener('click',ev=>{
  const reset=ev.target.closest('[data-restore-sale]');if(reset){const n=this.s.nodes.find(n=>n.id===reset.dataset.restoreSale);if(n){this.snap();if(n.manual)n.manualSale='';else delete n.saleOverride;this.change();this.paint();}}
 });
 this.root.addEventListener('change',ev=>{
  if(ev.target.dataset.rivalVersion){this.chooseQuoteVersion(ev.target).catch(e=>{frappe.msgprint(this.e(e.message));this.paint();});return;}
  const el=ev.target,n=this.s.nodes.find(n=>n.id===el.dataset.nodeToggle);if(n){this.snap();n.fees ||= {};n.fees[el.dataset.key+'_enabled']=el.checked;this.change();this.paint();}
 });

 this.root.addEventListener('input',ev=>{if(this.liveEdit(ev.target))return;if(ev.target.dataset.logisticsSearch){this.logisticsQueries ||= {};this.logisticsQueries[ev.target.dataset.logisticsSearch]=ev.target.value;this.filterLogistics();this.logisticsHeight();this.restoreTableScroll();}});

 this.root.addEventListener('toggle',ev=>{if(ev.target.matches('.fc-route'))this.filterLogistics();this.logisticsHeight();this.restoreTableScroll();},true);
 this.root.addEventListener('click',ev=>{if(this.suppress)return;if(ev.target.closest('.fc-minimap'))return;if(this.multiSelect){const item=ev.target.closest('.fc-item');if(item&&!ev.target.closest('input,select,button,a')){this.selectedNodes ||= new Set();if(this.selectedNodes.has(item.dataset.id))this.selectedNodes.delete(item.dataset.id);else this.selectedNodes.add(item.dataset.id);this.paint();return;}}const arrange=ev.target.closest('[data-arrange-material]');if(arrange){this.arrangeMaterialCards(arrange.dataset.arrangeMaterial);return;}const copy=ev.target.closest('[data-copy-item]');if(copy){this.copyItem(copy.dataset.copyItem).catch(()=>frappe.msgprint('复制失败，请允许剪贴板访问'));return;}const more=ev.target.closest('[data-rival-more]');if(more){this.moreQuoteVersions(more).catch(e=>frappe.msgprint(this.e(e.message)));return;}const rivals=ev.target.closest('[data-competitors]');if(rivals){this.refreshCompetitors(rivals.dataset.competitors).catch(e=>frappe.msgprint(this.e(e.message)));return;}const closeRivals=ev.target.closest('[data-close-rivals]');if(closeRivals){const n=this.s.nodes.find(n=>n.id===closeRivals.dataset.closeRivals);if(n){n.rivalsHidden=true;this.change();this.paint();}return;}const restore=ev.target.closest('[data-restore-commission]');if(restore){const n=this.s.nodes.find(n=>n.id===restore.dataset.restoreCommission);if(n){this.snap();delete n.commissionOverride;this.change();this.paint();}return;}if(ev.target.closest('.fc-stage')&&!ev.target.closest('.fc-card,.fc-controls,button,input,select')){this.s.focus=null;this.paint();return;}const route=ev.target.closest('[data-cost-route]');if(route){const n=this.s.nodes.find(n=>n.id===route.dataset.node);if(n){this.snap();n.costRoute=route.dataset.costRoute;this.change();this.paint();}return;}const lookup=ev.target.closest('[data-lookup]');if(lookup){this.enrich(lookup.dataset.lookup,true);return;}const a=ev.target.closest('[data-a]');if(a){this.action(a.dataset.a).catch(e=>frappe.msgprint(this.e(e.message)));return;}const del=ev.target.closest('[data-del]');if(del){this.snap();this.s.nodes=this.s.nodes.filter(n=>n.id!==del.dataset.del);this.change();this.paint();return;}const refresh=ev.target.closest('[data-refresh]');if(refresh){this.refresh(refresh.dataset.refresh).catch(e=>frappe.msgprint(this.e(e.message)));return;}if(ev.target.closest('input,select,button,details,a'))return;const card=ev.target.closest('.fc-item');if(card){if(this.isTable()){this.s.active=card.dataset.id;this.topCommission();}else this.activate(card.dataset.id);}});
 this.root.addEventListener('change',ev=>{
 const manualKey=ev.target.dataset.manualName?'manualName':ev.target.dataset.manualSale?'manualSale':ev.target.dataset.manualCommission?'manualCommission':null;
 if(manualKey){const n=this.s.nodes.find(n=>n.id===ev.target.dataset[manualKey]);if(!n?.manual)return;const value=ev.target.value,v=Number(value);if(manualKey!=='manualName'&&value!==''&&(!Number.isFinite(v)||v<0||(manualKey==='manualCommission'&&v>=100))){frappe.msgprint('请填写有效的非负金额，佣金需小于100%');this.paint();return;}this.snap();if(manualKey==='manualName')n.item.item_name=value||'空白物料';else if(manualKey==='manualSale')n.manualSale=value===''?'':v;else if(value==='')delete n.commissionOverride;else n.commissionOverride=v;this.change();this.paint();return;}
 if(ev.target.matches('.fc-top-commission')){const n=this.s.nodes.find(n=>n.id===ev.target.dataset.node),v=Number(ev.target.value);if(!n)return;if(ev.target.value!==''&&(!Number.isFinite(v)||v<0||v>=100)){frappe.msgprint('佣金需在0至100%之间');this.topCommission();return;}this.snap();if(ev.target.value==='')delete n.commissionOverride;else n.commissionOverride=v;this.change();this.paint();return;}
 if(ev.target.dataset.feeToggle){this.snap();this.c[ev.target.dataset.feeToggle+'_enabled']=ev.target.checked;this.change();this.paint();return;}
 if(ev.target.dataset.feeValue){const v=Number(ev.target.value),key=ev.target.dataset.feeValue,suffix=ev.target.dataset.suffix;if(!Number.isFinite(v)||v<0||(suffix==='pct'&&v>=100)||(key==='lastmile'&&v>500)){frappe.msgprint('费用参数无效，百分比需小于100%，最后一公里不超过500卢布');this.moneybar();return;}this.snap();this.c[key+'_'+suffix]=key==='advertising'&&ev.target.value===''?'':v;this.change();this.paint();return;}
 if(ev.target.dataset.commissionNode){const n=this.s.nodes.find(n=>n.id===ev.target.dataset.commissionNode);if(n){this.snap();delete n.commissionOverride;n.commissionSchema=ev.target.value||'UNSELECTED';this.change();this.paint();}return;}
 if(ev.target.matches('.fc-exclusive')){this.snap();this.s.exclusive=ev.target.checked;if(this.s.exclusive)this.s.expanded=this.s.active?[this.s.active]:[];this.change();this.paint();return;}
 if(ev.target.matches('.fc-fx')){this.snap();this.c.cny_per_rub=Number(ev.target.value);this.c.exchange_date='手动设置';this.change();this.paint();return;}
 if(ev.target.dataset.priceNode){const n=this.s.nodes.find(n=>n.id===ev.target.dataset.priceNode),p=n?.meta?.prices?.[Number(ev.target.value)];if(ev.target.value!==''&&p&&p.cny!==null){this.snap();n.priceIndex=Number(ev.target.value);this.change();this.paint();}return;}
 const f=ev.target.dataset.f,n=this.s.nodes.find(n=>n.id===ev.target.dataset.n);if(!f||!n)return;if(f==='quantity'&&(!Number.isInteger(Number(ev.target.value))||Number(ev.target.value)<1||Number(ev.target.value)>10000)){frappe.msgprint('数量需为1至10000之间的整数');this.paint();return;}if(f==='value'&&(ev.target.value===''||!Number.isFinite(Number(ev.target.value))||Number(ev.target.value)<0)){frappe.msgprint('模拟成本需填写非负数字');this.paint();return;}this.snap();if(f==='value')n.costEdited=true;n.item[f]=f==='quantity'?Number(ev.target.value):ev.target.type==='checkbox'?ev.target.checked:ev.target.value;this.change();this.paint();});
 this.stage.addEventListener('wheel',ev=>{if(this.isTable())return;if(ev.target.closest('.fc-results,.fc-cost-content,.fc-competitors'))return;ev.preventDefault();this.zoom(this.s.view.z*(ev.deltaY<0?1.1:1/1.1),ev.clientX,ev.clientY);},{passive:false});
 this.stage.addEventListener('pointerdown',ev=>{
  if(this.multiSelect){this.multiPointer(ev);return;}
  if(this.isTable()||ev.button!==0||ev.target.closest('input,button,select,details,a,.fc-controls,.fc-minimap,.fc-pagination'))return;
  const zone=ev.target.closest('[data-drag-zone]'),card=ev.target.closest('.fc-card'),header=card?.querySelector('.fc-drag');if(card&&!zone)return;
  const n=header?this.s.nodes.find(n=>n.id===header.dataset.n):null;
  const quote=n?(n.quote||{x:n.x+370,y:n.y}):null,cost=n?(n.cost||{x:quote.x+530,y:quote.y}):null,sale=n?(n.sale||{x:cost.x+this.costWidth(n)+40,y:cost.y}):null;
  const starts=n?{item:{x:n.x,y:n.y},quote:{...quote},cost:{...cost},sale:{...sale},rivals:{...(n.rivals||{x:n.x+370,y:n.y+600})}}:{view:{...this.s.view}},key=n?(header.dataset.rivals?'rivals':header.dataset.sale?'sale':header.dataset.cost?'cost':header.dataset.q?'quote':'item'):'view';
  const targets=n?(zone.dataset.dragZone==='group'?['item','quote','cost','sale','rivals']:[key]):['view'];const orig={cx:ev.clientX,cy:ev.clientY};let moved=false;
  const move=e=>{const dx=e.clientX-orig.cx,dy=e.clientY-orig.cy;if(!moved&&Math.hypot(dx,dy)<4)return;if(!moved){this.snap();moved=true;if(n){n.quote={...quote};n.cost={...cost};n.sale={...sale};n.rivals={...starts.rivals};}this.stage.classList.add('fc-panning');window.getSelection()?.removeAllRanges();}const z=n?this.s.view.z:1;
   for(const k of targets){const pos=k==='view'?this.s.view:k==='item'?n:n[k];pos.x=starts[k].x+dx/z;pos.y=starts[k].y+dy/z;}
   if(n)this.positionCards(n);else this.transform();
  };
  const up=()=>{this.stage.classList.remove('fc-panning');window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);if(moved){this.suppress=true;setTimeout(()=>this.suppress=false,100);this.change();}};
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up,{once:true});
 });
 this.stage.addEventListener('keydown',ev=>{if(ev.target.matches('input,textarea,select'))return;if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==='z'){ev.preventDefault();this.history(!ev.shiftKey);}if(ev.key==='Escape'){this.s.focus=null;this.s.active=null;this.s.expanded=[];this.paint();}});
 window.addEventListener('beforeunload',ev=>{if(this.dirty){ev.preventDefault();ev.returnValue='';}});
 }
 async action(a){if(!this.c)return;if(a==='table-prev'||a==='table-next'){this.tablePage=(this.tablePage||0)+(a==='table-next'?1:-1);this.paint();this.stage.scrollTop=0;return;}if(a==='multi-select')return this.toggleMultiSelect();if(a==='mode-canvas'||a==='mode-table'){this.snap();this.s.displayMode=a==='mode-table'?'table':'canvas';this.multiSelect=false;this.selectedNodes=new Set();this.tablePage=0;this.change();this.paint();return;}if(a==='collect')return this.collectionDialog();if(a==='sku-refresh')return this.refreshSkus();if(a==='sort-cards')return this.sortCards();if(a==='brief')return this.brief();if(a==='menu-toggle'){this.menuCollapsed=!this.menuCollapsed;this.paint();return;}if(a==='exchange'){const x=await this.api('exchange_info');this.snap();this.c.cny_per_rub=x.cny_per_rub;this.c.exchange_date=x.date;this.change();this.paint();return;}if(a==='blank')return this.addBlank();if(a==='add')return this.picker();if(a==='config')return this.editor();if(a==='save')return this.save();if(a==='open')return this.open();if(a==='undo'||a==='redo')return this.history(a==='undo');if(a==='plus'||a==='minus')return this.zoom(this.s.view.z*(a==='plus'?1.2:1/1.2));if(a==='fullscreen')return document.fullscreenElement?document.exitFullscreen():this.stage.requestFullscreen();if(a==='fit'){this.s.view={x:50-Math.min(0,...this.s.nodes.map(n=>n.x)),y:50-Math.min(0,...this.s.nodes.map(n=>n.y)),z:1};this.transform();this.change();}if(a==='export')this.download({title:this.root.querySelector('.fc-title').value,canvas:this.s,config:this.c},'ozon-freight-canvas.json');if(a==='new'){if(this.dirty&&!confirm('有未保存内容，仍然新建？'))return;this.s={version:2,nodes:[],active:null,expanded:[],exclusive:true,view:{x:60,y:50,z:1}};this.c=structuredClone(this.bootData.defaults);this.name=this.modified=null;this.undo=[];this.redo=[];this.root.querySelector('.fc-title').value='新运费画板';this.change();this.paint();}if(a==='import')this.file(x=>{this.validateCanvas(x.canvas);this.validateConfig(x.config);if(this.dirty&&!confirm('替换当前画布？'))return;this.snap();this.s=x.canvas;this.c=x.config;this.prepare(this.s,this.c);this.name=this.modified=null;this.root.querySelector('.fc-title').value=x.title||'导入画布';this.change();this.paint();});}
 zoom(z,cx,cy){const r=this.stage.getBoundingClientRect(),v=this.s.view;cx=(cx??r.left+r.width/2)-r.left;cy=(cy??r.top+r.height/2)-r.top;z=Math.max(.05,Math.min(5,z));v.x=cx-(cx-v.x)*z/v.z;v.y=cy-(cy-v.y)*z/v.z;v.z=z;this.transform();this.change();}
 transform(){const v=this.s.view;if(this.isTable()){this.world.style.transform='none';this.root.querySelector('.fc-zoom').textContent='表格';this.imageCard();this.minimap();return;}this.world.style.transform=`translate(${v.x}px,${v.y}px) scale(${v.z})`;this.root.querySelector('.fc-zoom').textContent=Math.round(v.z*100)+'%';this.imageCard();this.minimap();}
 normalize(r){return {item_code:r.item_code,item_name:r.item_name,image:r.image,length:r.custom_带包装长度mm||'',width:r.custom_带包装宽度mm||'',height:r.custom_带包装高度mm||'',weight:r.custom_带包装重量g||'',value:'',value_currency:'CNY',quantity:1,battery:false,liquid:false};}
 picker(){const d=new frappe.ui.Dialog({title:'选择物料 · 包装信息来自 ERPNext',size:'large',fields:[{fieldtype:'HTML',fieldname:'picker'}]}),host=d.fields_dict.picker.$wrapper[0];host.innerHTML='<div class="ozfc"><input class="fc-search" placeholder="搜索物料号或名称"><div class="fc-list"></div><button class="fc-more">加载更多</button></div>';let rows=[],start=0,seq=0,timer;const input=host.querySelector('input'),list=host.querySelector('.fc-list');const load=async(more=false)=>{const token=++seq;if(!more){rows=[];start=0;}try{const found=await this.api('search_items',{query:input.value,start});if(token!==seq)return;rows=rows.concat(found);start+=found.length;list.innerHTML=rows.map((r,i)=>`<button class="fc-pick" data-i="${i}"><img src="${this.image(r.image)}" onerror="this.style.visibility='hidden'"><span><b>${this.e(r.item_name)}</b><small>${this.e(r.item_code)}</small><small>${this.e(r.custom_带包装长度mm||'—')} × ${this.e(r.custom_带包装宽度mm||'—')} × ${this.e(r.custom_带包装高度mm||'—')} mm · ${this.e(r.custom_带包装重量g||'—')} g</small></span><strong>＋</strong></button>`).join('')||'没有找到物料';host.querySelector('.fc-more').disabled=found.length<30;}catch(e){list.textContent='读取物料失败，请检查权限';}};input.oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>load(),250);};host.querySelector('.fc-more').onclick=()=>load(true);list.onclick=ev=>{const b=ev.target.closest('[data-i]');if(!b)return;if(this.s.nodes.length>=300){frappe.msgprint('最多300张卡片');return;}this.snap();const i=this.s.nodes.length,v=this.s.view,n={id:crypto.randomUUID(),item:this.normalize(rows[Number(b.dataset.i)]),x:(60-v.x)/v.z+(i%3)*355,y:(50-v.y)/v.z+Math.floor(i/3)*380};this.s.nodes.push(n);this.s.expanded=this.s.exclusive!==false?[n.id]:[...this.expanded(),n.id];this.s.active=n.id;this.change();this.paint();d.hide();this.enrich(n.id);};d.show();load();}
 async refresh(id){const n=this.s.nodes.find(n=>n.id===id);if(!n||n.manual)return;if(n.ozonOnly)return this.refreshSkus();const row=await frappe.db.get_doc('Item',n.item.item_code);this.snap();n.item={...this.normalize(row),quantity:n.item.quantity??1,value:n.item.value,value_currency:n.item.value_currency||'RUB',battery:n.item.battery,liquid:n.item.liquid};this.change();this.paint();await this.enrich(id,true);}
 paint(){if(!this.c)return;if(!this.isTable()&&this.root.classList.contains('fc-table-mode')){this.stage.scrollTop=0;this.stage.scrollLeft=0;}this.root.querySelector('.fc-nodes').innerHTML=(this.isTable()?this.tablePageNodes():this.s.nodes).map(n=>{const it=n.item,active=this.isTable()||this.expanded().includes(n.id);return `${this.isTable()?'<section class="fc-table-row">':''}<article class="fc-card fc-item ${active?'active':''}" data-id="${n.id}" style="left:${n.x}px;top:${n.y}px"><header class="fc-drag" data-n="${n.id}"><span>物料 · PACKAGED ITEM</span><button data-del="${n.id}" title="移除卡片，不删除物料">×</button></header><div class="fc-product"><img src="${this.image(it.image)}" onerror="this.style.visibility='hidden'"><div><h3>${this.e(it.item_name)}</h3><button class="fc-copy-item" data-copy-item="${this.e(it.item_code)}" title="点击复制物料ID">${this.e(it.item_code)} <span>⧉</span></button></div></div>${this.metaHTML(n)}${this.competitorCountHTML(n)}<div class="fc-inputs">${[['length','长 mm'],['width','宽 mm'],['height','高 mm'],['weight','重量 g'],['value',it.value_currency==='CNY'?'货值 / 成本 ¥ CNY':'货值 RUB（旧画布）'],['quantity','数量 / 件']].map(([k,l])=>`<label>${l}<input type="${k==='value'?'text':'number'}" inputmode="decimal" min="${k==='quantity'?1:0}" step="${k==='quantity'?1:'any'}" data-n="${n.id}" data-f="${k}" value="${this.e(k==='quantity'?(it[k]??1):it[k])}" ${k==='value'?'title="仅修改画布模拟成本，不修改ERPNext物料数据"':''} placeholder="待填写"></label>`).join('')}</div><div class="fc-checks"><label><input type="checkbox" data-n="${n.id}" data-f="battery" ${it.battery?'checked':''}> 带电</label><label><input type="checkbox" data-n="${n.id}" data-f="liquid" ${it.liquid?'checked':''}> 液体</label><button data-refresh="${n.id}">刷新物料</button><button data-arrange-material="${n.id}" title="仅整理关联卡片，不移动当前物料">整理卡片</button></div><footer>${active?'再次点击卡片收起计算':'点击卡片展开物流计算'} →</footer></article>${active?this.quotes(n):''}${this.competitorHTML(n)}${this.isTable()?'</section>':''}`;}).join('');this.root.querySelector('.fc-empty').hidden=!!this.s.nodes.length;this.root.querySelector('.fc-count').textContent=this.s.nodes.length+' 张物料卡片';this.root.classList.toggle('fc-table-mode',this.isTable());this.root.querySelectorAll('.fc-mode-switch button').forEach(b=>{const on=b.dataset.a===(this.isTable()?'mode-table':'mode-canvas');b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});if(this.isTable())this.tableRows();else this.root.querySelectorAll('.fc-card>.fc-drag').forEach(header=>{const children=[...header.children],single=document.createElement('div'),group=document.createElement('div');single.dataset.dragZone='group';single.title='拖动全部关联卡片';group.dataset.dragZone='single';group.title='只拖动当前卡片';single.append(children[0]);children.slice(1).forEach(el=>group.append(el));header.append(single,group);});this.root.classList.toggle('fc-immersive',!!this.menuCollapsed);const toggle=this.root.querySelector('[data-a="menu-toggle"]');toggle.innerHTML='<span class="fc-menu-grip"></span><span>'+(this.menuCollapsed?'展开工具栏':'收起工具栏')+'</span><span class="fc-menu-chevron"></span>';toggle.setAttribute('aria-expanded',String(!this.menuCollapsed));toggle.setAttribute('aria-label',this.menuCollapsed?'展开顶部菜单':'收起顶部菜单');this.tablePagination();this.applyCardFocus();const multiButton=this.root.querySelector('[data-a="multi-select"]');if(multiButton){multiButton.classList.toggle('primary',!!this.multiSelect);multiButton.textContent=this.multiSelect?'退出多选 ('+(this.selectedNodes?.size||0)+')':'多选';}this.root.classList.toggle('fc-multi-mode',!!this.multiSelect);this.hideImageHover();this.moneybar();this.topCommission();this.drawLines();this.transform();this.layout();this.filterLogistics();this.logisticsHeight();if(!this.isTable())this.s.nodes.filter(n=>!n.sale).forEach(n=>this.positionCards(n));}
 quotes(n){const p=n.quote||{x:n.x+370,y:n.y},rs=this.c.routes.filter(r=>r.enabled!==false).map(r=>ozfcCalculate(this.shippingItem(n),r,this.c.cny_per_rub)).sort((a,b)=>Number(b.r.id===n.costRoute)-Number(a.r.id===n.costRoute)||Number(b.eligible)-Number(a.eligible)||(a.price||0)-(b.price||0)),best=rs.filter(x=>x.eligible).sort((a,b)=>a.price-b.price)[0];return `<article class="fc-card fc-quotes" style="left:${p.x}px;top:${p.y}px"><header class="fc-drag" data-n="${n.id}" data-q="1"><span>物流计算 · SHIPPING OPTIONS</span><span>${rs.filter(x=>x.eligible).length}/${rs.length} 可用</span></header><div class="fc-best"><small>按Ozon后台售价匹配档位 · 最低估算运费</small><strong>${best?'¥'+best.price.toFixed(2):'等待完整条件'}</strong><p>${best?this.e(best.r.provider+' · '+best.r.name):'补全包装信息并取得Ozon后台售价，自动检查配送限制'}</p></div><div class="fc-logistics-search"><input data-logistics-search="${n.id}" value="${this.e(this.logisticsQueries?.[n.id]||'')}" placeholder="搜索承运商、物流名称、履约模式"><small class="fc-search-count"></small></div><div class="fc-results" data-logistics-list="${n.id}">${rs.map((x,i)=>`<details data-logistics-text="${this.e([x.r.provider,x.r.name,x.r.mode,x.r.destination].join(' ').toLowerCase())}" ${this.isTable()||i===0?'open':''} class="fc-route ${x.eligible?'':'invalid'}"><summary><div><b>${this.e(x.r.provider)} · ${this.e(x.r.name)}</b><small>${this.e(x.r.mode)} · ${this.e(x.r.destination)} · ${this.e(x.r.days)}</small></div><strong>${x.eligible?'¥'+x.price.toFixed(2):'不适用'}</strong></summary>${x.reasons.length?'<p class="fc-warning">'+x.reasons.map(v=>this.e(v)).join('<br>')+'</p>':''}<div class="fc-route-choice">${x.eligible?`<button data-cost-route="${this.e(x.r.id)}" data-node="${n.id}">${n.costRoute===x.r.id?'✓ 已用于成本计算':'用此运费计算售价'}</button>`:''}</div><ol>${x.steps.map(v=>'<li>'+this.e(v)+'</li>').join('')}</ol><p class="fc-source">来源：${this.e(x.r.source)}<br>${this.e(x.r.note)}</p></details>`).join('')||'<p>请先启用物流渠道</p>'}</div><footer>人民币估算 · 不含平台佣金、税费和未配置附加费</footer></article>${this.costCard(n,rs)}`;}
 async save(quoteBatch=null){if(this.saving)throw Error('正在保存，请等待完成');this.validateCanvas(this.s);this.validateConfig(this.c);const revision=this.changeId||0;this.saving=true;this.status('保存中…');try{const x=await this.api('save_canvas',{title:this.root.querySelector('.fc-title').value,name:this.name,modified:this.modified,canvas:JSON.stringify(this.canvasForStorage()),config:JSON.stringify(this.c),quote_batch:quoteBatch?JSON.stringify(quoteBatch):undefined});this.name=x.name;this.modified=x.modified;this.dirty=(this.changeId||0)!==revision;this.status(this.dirty?'已保存之前内容 · 仍有新的更改未保存':'✓ 已保存到 ERPNext 数据库');frappe.show_alert({message:'画布与物流配置已保存',indicator:'green'});
if(quoteBatch)this.rivalVersions?.clear();
try{await this.hydrateHistory();}catch(e){this.status('数据已保存，报价显示加载失败，请重新打开画布');}
return x;}catch(e){this.status('保存失败 · 更改仍保留在当前画布');throw e;}finally{this.saving=false;}}
 async open(){const b=await this.api('bootstrap');if(!b.canvases.length){frappe.msgprint('还没有已保存的画布');return;}const d=new frappe.ui.Dialog({title:'打开画布',fields:[{fieldname:'name',label:'已保存画布',fieldtype:'Select',options:b.canvases.map(x=>({label:x.canvas_title||x.name,value:x.name})),reqd:1}],primary_action_label:'打开',primary_action:async v=>{try{if(this.dirty&&!confirm('放弃未保存更改并打开？'))return;const x=await this.api('load_canvas',{name:v.name}),s=typeof x.canvas==='string'?JSON.parse(x.canvas):x.canvas,c=typeof x.config==='string'?JSON.parse(x.config):x.config;this.validateCanvas(s);this.validateConfig(c);const migrated=this.prepare(s,c);this.s=s;this.c=c;this.name=x.name;this.modified=x.modified;this.undo=[];this.redo=[];this.dirty=false;this.root.querySelector('.fc-title').value=x.title;this.paint();this.status('✓ 已恢复保存的画布');try{await this.hydrateHistory();}catch(e){this.status('画布已打开，独立报价历史加载失败');}if(migrated){this.change();this.status('旧画布货值已从卢布换算成人民币 · 请保存');}d.hide();}catch(e){frappe.msgprint(this.e(e.message));}}});d.show();}
 validateFees(c){for(const [k,v] of Object.entries(c||{})){if(/^(margin|acquisition|withdrawal|returns|lastmile|advertising)_(pct|rub|cny)$/.test(k)&&!(k==='advertising_cny'&&v==='')&&(!Number.isFinite(v)||v<0||(k.endsWith('_pct')&&v>=100)||(k==='lastmile_rub'&&v>500)))throw Error('费用参数无效：'+k);if(/^(margin|acquisition|withdrawal|returns|lastmile|advertising)_enabled$/.test(k)&&typeof v!=='boolean')throw Error('费用开关必须为布尔值');}}
 validateCanvas(s){if(!s||!Array.isArray(s.nodes)||s.nodes.length>300||!s.view||![s.view.x,s.view.y,s.view.z].every(Number.isFinite)||s.view.z<.05||s.view.z>5)throw Error('画布格式错误');const ids=new Set();s.nodes.forEach(n=>{if(!n.item?.item_code||!Number.isFinite(n.x)||!Number.isFinite(n.y)||!n.id||!/^[A-Za-z0-9-]+$/.test(n.id)||ids.has(n.id))throw Error('物料卡片格式错误');ids.add(n.id);for(const p of [n.quote,n.cost,n.sale,n.rivals].filter(Boolean))if(![p.x,p.y].every(Number.isFinite))throw Error('计算卡片位置错误');});}
 validateConfig(c){this.validateFees(c);if(!c||!Array.isArray(c.routes)||c.routes.length>500)throw Error('需要含 routes 数组的配置对象');if(c.cny_per_rub!==undefined&&(!Number.isFinite(c.cny_per_rub)||c.cny_per_rub<0))throw Error('汇率必须是非负数字');const ids=new Set();c.routes.forEach(r=>{if(!r||!r.id||ids.has(r.id)||!r.name||!r.provider)throw Error('渠道ID重复或名称缺失');ids.add(r.id);if(!['RUB','CNY'].includes(r.value_currency||'RUB'))throw Error('货值币种需为RUB或CNY');['fixed','rate','min_weight','max_weight','min_value','max_value'].forEach(k=>{if(r[k]===undefined)throw Error('缺少必填参数 '+k);});['fixed','rate','min_weight','max_weight','min_value','max_value','max_side','max_sum','divisor','max_billable','step','surcharge'].forEach(k=>{if(r[k]!==undefined&&(!Number.isFinite(r[k])||r[k]<0))throw Error(k+' 必须是非负数字');});['enabled','battery','liquid','min_exclusive','value_exclusive'].forEach(k=>{if(r[k]!==undefined&&typeof r[k]!=='boolean')throw Error(k+' 必须是 true 或 false');});if(r.sorted_sides&&(!Array.isArray(r.sorted_sides)||![0,3].includes(r.sorted_sides.length)||r.sorted_sides.some(v=>!Number.isFinite(v)||v<=0)))throw Error('sorted_sides 应为空数组或三个正数');if(r.max_weight<r.min_weight||r.max_value<r.min_value)throw Error('上下限顺序错误');});}
 file(callback){const i=document.createElement('input');i.type='file';i.accept='.json,application/json';i.onchange=async()=>{try{if(!i.files.length)return;if(i.files[0].size>2000000)throw Error('文件不能超过2MB');await callback(JSON.parse(await i.files[0].text()));}catch(e){frappe.msgprint(this.e(e.message));}};i.click();}
 download(x,name){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(x,null,2)],{type:'application/json'}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 editor(){let draft=structuredClone(this.c),mode='table';const d=new frappe.ui.Dialog({title:'物流配置 · 编辑 / AI JSON 导入',size:'extra-large',fields:[{fieldtype:'HTML',fieldname:'editor'}],primary_action_label:'应用到画布',primary_action:()=>{try{if(mode==='json')draft=JSON.parse(host.querySelector('textarea').value);this.validateConfig(draft);this.snap();this.c=draft;this.change();this.paint();d.hide();frappe.show_alert({message:'配置已应用，请保存画布以写入数据库',indicator:'blue'});}catch(e){frappe.msgprint(this.e(e.message));}}});d.$wrapper.addClass('fc-tall-dialog');const host=d.fields_dict.editor.$wrapper[0],cols=[['enabled','启用','checkbox'],['provider','承运商','text'],['name','渠道','text'],['destination','国家','text'],['mode','模式','text'],['fixed','首票元','number'],['rate','元/kg','number'],['min_weight','实重下限kg','number'],['max_weight','实重上限kg','number'],['min_exclusive','下限不含','checkbox'],['value_currency','货值币种RUB/CNY','text'],['min_value','货值下限','number'],['max_value','货值上限','number'],['value_exclusive','货值下限不含','checkbox'],['max_side','最长边cm','number'],['max_sum','三边和cm','number'],['divisor','体积除数','number'],['max_billable','计费重上限kg','number'],['step','进位kg','number'],['surcharge','附加费元','number'],['battery','带电','checkbox'],['liquid','液体','checkbox'],['days','时效','text'],['source','来源','text'],['note','备注','text']];
 const draw=()=>{host.innerHTML=`<div class="ozfc fc-editor"><div class="fc-note"><b>历史运价快照，请确认承运商当前报价后使用。</b><p>统一单位：cm / kg / RUB / CNY。体积除数0=不计抛；进位0=不进位；计费重上限0=未设限制。导入只接受JSON，不执行Excel公式或AI代码。</p></div><div class="fc-tools"><button data-c="table">表格编辑</button><button data-c="json">AI / JSON编辑</button><button data-c="import">导入配置JSON</button><button data-c="export">导出配置</button><button data-c="add">＋ 渠道</button><button data-c="reset">恢复初始快照</button></div>${mode==='json'?`<p>让AI按现有结构生成运价JSON，再粘贴或导入。sorted_sides 是按长边排序的三个尺寸上限。</p><textarea spellcheck="false">${this.e(JSON.stringify(draft,null,2))}</textarea>`:`<div class="fc-configtable"><table><thead><tr>${cols.map(x=>'<th>'+x[1]+'</th>').join('')}<th>操作</th></tr></thead><tbody>${draft.routes.map((r,i)=>`<tr>${cols.map(([k,l,t])=>`<td><input data-row="${i}" data-key="${k}" type="${t}" ${t==='checkbox'?(r[k]?'checked':''):`value="${this.e(r[k]??'')}"`} ${t==='number'?'min="0" step="any"':''} aria-label="${l}"></td>`).join('')}<td><button data-remove="${i}">移除</button></td></tr>`).join('')}</tbody></table></div>`}</div>`;
 host.querySelectorAll('[data-c]').forEach(b=>b.onclick=()=>{try{if(mode==='json')draft=JSON.parse(host.querySelector('textarea').value);const a=b.dataset.c;if(a==='table'||a==='json'){mode=a;draw();}if(a==='export')this.download(draft,'ozon-freight-config.json');if(a==='import')this.file(x=>{this.validateConfig(x);draft=x;draw();});if(a==='reset'&&confirm('替换为初始报价快照？')){draft=structuredClone(this.bootData.defaults);draw();}if(a==='add'){draft.routes.push({...structuredClone(this.bootData.defaults.routes[0]),id:crypto.randomUUID(),provider:'新承运商',name:'新渠道',enabled:false,source:'手动配置',note:'请核对'});draw();}}catch(e){frappe.msgprint(this.e(e.message));}});host.querySelectorAll('[data-key]').forEach(i=>i.onchange=()=>draft.routes[Number(i.dataset.row)][i.dataset.key]=i.type==='checkbox'?i.checked:i.type==='number'?Number(i.value):i.value);host.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{draft.routes.splice(Number(b.dataset.remove),1);draw();});};draw();d.show();}
}
if(typeof module!=='undefined')module.exports.OzFreightCanvas=OzFreightCanvas;
if(typeof module!=='undefined')Object.assign(module.exports,{ozfcCompetitorVersion,ozfcCanvasCollectionTargets,ozfcCollectionCommand,ozfcParseCompetitors});
OzFreightCanvas.css=`
.ozfc{font-family:var(--font-stack,sans-serif);color:#233654}.ozfc *{box-sizing:border-box}.ozfc button{border:1px solid #dce3f0;border-radius:9px;background:#fff;color:#334463;padding:8px 12px;cursor:pointer;font-size:12px}.ozfc button:hover{border-color:#7783f4;background:#f1f3ff}.ozfc button:disabled{opacity:.4;cursor:not-allowed}.ozfc .primary{background:#606df5;color:white;border-color:#606df5}.fc-hero{background:linear-gradient(115deg,#162b50,#384f99 65%,#1199a1);border-radius:18px;padding:24px 28px;display:flex;justify-content:space-between;gap:20px;color:#fff;margin:12px 0}.fc-hero small{letter-spacing:3px;color:#a7d7ff;font-size:10px}.fc-hero h2{color:white;font-size:24px;margin:8px 0}.fc-hero p{color:#d6e3ff;font-size:12px;margin:0}.fc-count{align-self:center;border:1px solid #ffffff40;border-radius:12px;padding:14px;white-space:nowrap}.fc-tools{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:12px 0}.fc-title{border:1px solid #dce3f0;border-radius:9px;padding:8px;width:175px;font-size:13px;background:white;color:#18243c}.fc-status{font-size:11px;color:#687795;margin-left:auto}.fc-stage{height:calc(100vh - 310px);min-height:450px;position:relative;overflow:hidden;border:1px solid #e0e6f3;border-radius:18px;background-color:#f5f7fc;background-image:radial-gradient(#cdd6e7 1px,transparent 1px);background-size:22px 22px;touch-action:none;outline:none}.fc-stage:fullscreen{height:100vh;border-radius:0}.fc-world{position:absolute;transform-origin:0 0;width:0;height:0}.fc-lines{position:absolute;overflow:visible;pointer-events:none}.fc-card{position:absolute;background:#fff;border:1px solid #dce4f3;border-radius:16px;box-shadow:0 10px 28px #1c315814;overflow:hidden;width:330px}.fc-item.active{border-color:#7783f4;box-shadow:0 10px 30px #596ce533}.fc-drag{padding:12px 16px;background:#edf1fc;display:flex;align-items:center;justify-content:space-between;cursor:grab;user-select:none;color:#6b7c9e;font-size:10px;letter-spacing:.8px}.fc-drag button{padding:0 7px;font-size:18px;background:transparent;border:0}.fc-product{padding:18px 16px;display:flex;gap:13px;align-items:center}.fc-product img{width:76px;height:76px;object-fit:contain;background:#f8f9fc;border-radius:12px}.fc-product h3{font-size:14px;line-height:1.5;margin:0;overflow-wrap:anywhere}.fc-product small{font-size:10px;color:#8390a8;display:block;margin-top:6px;word-break:break-all}.fc-inputs{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;padding:0 16px 14px}.fc-inputs label{color:#8490a6;font-size:10px;margin:0}.fc-inputs input{width:100%;border:1px solid #dce3f0;border-radius:7px;padding:7px;margin-top:4px;color:#243751;font-size:12px;background:#fafbfe}.fc-inputs label:last-child{grid-column:span 2}.fc-checks{display:flex;align-items:center;gap:12px;padding:0 16px 14px;font-size:11px}.fc-checks label{margin:0}.fc-checks button{margin-left:auto;padding:4px 6px}.fc-card footer{border-top:1px solid #edf0f6;padding:12px 16px;color:#8592aa;font-size:10px}.fc-quotes{width:490px}.fc-quotes header{background:#e9f8f6;color:#258d86}.fc-best{background:linear-gradient(120deg,#fafbff,#eef8fb);padding:20px}.fc-best small{color:#718099;font-size:11px}.fc-best strong{display:block;font-size:30px;color:#213d6b;margin:5px 0}.fc-best p{font-size:12px;color:#6d83a4;margin:0}.fc-results{max-height:440px;overflow-y:auto;padding:8px 16px;overscroll-behavior:contain}.fc-route{border:1px solid #e0e8f2;border-radius:10px;margin:8px 0;overflow:hidden}.fc-route summary{padding:13px;display:flex;justify-content:space-between;align-items:center;gap:10px;cursor:pointer;list-style:none}.fc-route summary b{font-size:12px}.fc-route summary small{display:block;font-size:10px;color:#8491a5;margin-top:4px}.fc-route summary strong{color:#16948d;font-size:17px;white-space:nowrap}.fc-route ol{list-style:none;padding:0 13px;margin:0}.fc-route li{font-size:11px;line-height:1.8;border-top:1px dashed #e5eaf2;padding:7px 0;color:#465d7b}.fc-route.invalid summary strong{font-size:11px;color:#a8aebd}.fc-route.invalid{background:#fafbfc}.fc-warning{padding:10px 13px;background:#fff4ee;color:#b7754b;font-size:11px;line-height:1.7}.fc-source{font-size:9px;line-height:1.7;color:#97a1b3;padding:0 13px}.fc-empty{position:absolute;left:50%;top:42%;transform:translate(-50%,-50%);text-align:center;color:#8795b0;pointer-events:none}.fc-empty[hidden]{display:none}.fc-empty span{font-size:70px;color:#b1bef3}.fc-empty h3{font-size:20px;color:#465c86}.fc-empty p{font-size:12px}.fc-empty button{pointer-events:auto}.fc-controls{position:absolute;bottom:18px;right:18px;display:flex;gap:6px;align-items:center;background:#fffffff0;padding:7px;border-radius:13px;box-shadow:0 5px 20px #21344b15}.fc-zoom{font-size:11px;width:40px;text-align:center}.fc-help{position:absolute;bottom:25px;left:20px;font-size:10px;color:#96a0b5;pointer-events:none}.fc-search{width:100%;padding:12px;border-radius:9px;border:1px solid #dce3f0;margin-bottom:12px}.fc-list{max-height:480px;overflow:auto}.fc-pick{width:100%;display:flex;align-items:center;text-align:left;gap:16px;margin:5px 0}.fc-pick img{width:58px;height:58px;object-fit:contain;background:#f8f9fc;border-radius:9px}.fc-pick span{flex:1}.fc-pick b{font-size:13px}.fc-pick small{display:block;font-size:10px;color:#8190a6;margin-top:4px}.fc-pick strong{font-size:22px;color:#606df5}.fc-more{width:100%;margin-top:10px}.fc-note{padding:16px;background:#f0f4ff;border-radius:12px;font-size:12px;line-height:1.7}.fc-note p{margin:8px 0 0;color:#73829e}.fc-configtable{max-height:470px;overflow:auto;border:1px solid #e2e8f3;border-radius:10px}.fc-configtable table{border-collapse:collapse;min-width:2300px}.fc-configtable th{position:sticky;top:0;background:#eef2fc;z-index:1;white-space:nowrap;font-size:10px;padding:10px}.fc-configtable td{padding:5px;border-bottom:1px solid #edf1f7}.fc-configtable input{width:100px;border:1px solid #e0e6f0;border-radius:5px;padding:7px;font-size:11px}.fc-configtable input[type=checkbox]{width:18px}.fc-editor textarea{height:430px;width:100%;font-family:monospace;border:1px solid #d9e1ef;border-radius:9px;padding:15px;color:#334765;background:#fafbff;tab-size:2}@media(max-width:900px){.fc-help{display:none}.fc-hero h2{font-size:19px}.fc-count{display:none}.fc-stage{height:65vh}.fc-status{width:100%;margin:0}.fc-controls{right:8px;bottom:8px;gap:3px}.fc-controls button{padding:7px}}`;
