frappe.pages['amazon-finance'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({ parent: wrapper, title: 'Amazon 财务交易中心', single_column: true });
    wrapper.amazon_finance = new AmazonFinancePage(page);
};
frappe.pages['amazon-finance'].on_page_show = wrapper => wrapper.amazon_finance?.load();

class AmazonFinancePage {
    constructor(page) {
        this.page=page; this.pageNo=1; this.pageSize=300; this.returnPage=1; this.detailGroup=''; this.charts=[]; this.loading=false; this.summaryOpen=false; this.collapsedGroups=new Set();
        this.selectedMonths=new Set([this.currentMonthKey()]); this.monthSelectionStarted=false; this.selectedCountry=''; this.summaryCostCenters=new Map(); this.summaryAccounts=new Map();
        this.renderShell(); this.renderMonthButtons(); this.renderCountryButtons(); this.bindEvents();
    }
    renderShell() {
        const {from,to}=this.monthBounds(this.currentMonthKey());
        $(this.page.main).html(`<div class="af-root">
          <section class="af-hero"><div><span>AMAZON FINANCE INTELLIGENCE</span><h1>财务交易中心</h1><p>销售、退款、税费、平台费用与结算批次，穿透至店铺、SKU和ERPNext物料。</p></div><div><button class="btn af-refresh-bind">重新匹配物料</button><button class="btn btn-primary af-refresh">刷新数据</button></div></section>
          <section class="af-filter-panel"><div class="af-ranges"><span>月份</span><div class="af-month-range"></div></div><div class="af-country-ranges"><span>国家</span><div class="af-country-range"></div></div><div class="af-filters">
            ${this.input('date_from','开始日期（美西）','date',from)}${this.input('date_to','结束日期（美西）','date',to)}${this.select('store','店铺')}${this.select('marketplace_id','站点')}${this.select('currency_code','币种')}${this.select('transaction_type','交易类型')}${this.select('transaction_status','状态')}
            <label>交易归属<select data-filter="scope"><option value="">全部</option><option value="product">商品级交易</option><option value="store">店铺级费用</option></select></label><label>物料绑定<select data-filter="binding"><option value="">全部</option><option value="bound">已绑定</option><option value="unbound">未绑定</option></select></label><label class="af-search-label">搜索<input data-filter="search" placeholder="订单号 / SKU / ASIN / 费用内容 / 物料 / 结算ID"></label></div></section>
          <div class="af-loading"><i></i>正在分析财务交易…</div><main class="af-content"><section class="af-kpis"></section>
          <section class="af-card af-table-card"><header><div><h2>完整交易明细</h2><span class="af-table-count"></span></div><div class="af-table-actions"><a class="af-profit-link" href="https://sellercentral.amazon.com/manage/profit-analytics?" target="_blank" rel="noopener noreferrer">费用分配</a><a class="af-profit-link" href="https://sellercentral.amazon.com/manage/shipments" target="_blank" rel="noopener noreferrer">管理货件</a><a class="af-profit-link" href="https://sellercentral.amazon.com/awd/reports/fee?reportType=AwdOutboundTransportationFee" target="_blank" rel="noopener noreferrer">AWD费用</a><a class="af-profit-link af-return-links" href="https://sellercentral.amazon.com/reportcentral/CUSTOMER_RETURNS/0" target="_blank" rel="noopener noreferrer">退货管理</a><button class="af-summary-toggle" type="button">汇总明细</button><div class="af-pages"></div><button class="af-table-expand" type="button" title="放大交易明细" aria-label="放大交易明细"><svg class="af-expand-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/></svg><svg class="af-compress-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5"/></svg></button></div></header><div class="af-detail-tabs"></div><div class="af-table-wrap"><table><thead><tr><th>店铺 / 站点</th><th>交易 / 订单</th><th>交易内容</th><th class="af-manual-items-head" hidden>关联物料</th><th>类型 / 状态</th><th>净额</th><th>费用</th><th>时间（美国太平洋）</th><th>物料 / 费用归属</th><th>币种</th><th>销售额</th><th>结算ID</th></tr></thead><tbody></tbody></table></div><div class="af-summary-overlay" hidden><div class="af-summary-backdrop" data-summary-close></div><aside class="af-summary-drawer" role="dialog" aria-label="汇总明细"><header><div><h3>汇总明细</h3><span class="af-summary-count"></span></div><button type="button" data-summary-close aria-label="关闭汇总明细">×</button></header><p>按交易类型、交易内容和币种汇总当前筛选范围，包含所有分页。</p><div class="af-summary-body"></div></aside></div></section>
          <section class="af-card af-returns-card"><header><div><h2>FBA 客户退货</h2><span class="af-return-count"></span></div><a href="/app/amazon_fba_return_analysis">查看完整退货分析 ↗</a></header><p class="af-returns-note">按上方日期范围展示实际收到的退货，已合并四站请求返回的重复记录。源报告没有国家字段，因此不按请求国家归类；退货数量也不计入财务金额。</p><div class="af-return-metrics"></div><div class="af-returns-table-wrap"><table><thead><tr><th>退货时间（美西）</th><th>订单</th><th>商品 / SKU</th><th>商品状态</th><th>处理状态</th><th>数量</th><th>原记录</th></tr></thead><tbody><tr><td colspan="7" class="af-empty">正在加载退货记录…</td></tr></tbody></table></div><footer class="af-return-pages"></footer></section>
          <section class="af-currencies"></section>
          <section class="af-grid af-grid-2"><article class="af-card af-wide"><header><h2>净额与费用趋势</h2><span>按美西记账日期与币种</span></header><div id="af-trend" class="af-chart"></div></article><article class="af-card"><header><h2>费用构成</h2><span>负值代表扣费</span></header><div id="af-fees" class="af-chart"></div></article></section>
          <section class="af-grid af-grid-2"><article class="af-card"><header><h2>交易类型</h2><span>金额与笔数</span></header><div id="af-types" class="af-chart"></div></article><article class="af-card"><header><h2>店铺贡献</h2><span>店铺净额</span></header><div id="af-stores" class="af-chart"></div></article></section>
          <section class="af-grid af-grid-2"><article class="af-card"><header><h2>SKU 与物料表现</h2><span>未绑定商品可直接绑定</span></header><div class="af-products"></div></article><article class="af-card"><header><h2>结算批次</h2><span>最近200个批次</span></header><div class="af-settlements"></div></article></section></main></div>`);
        this.$=$(this.page.main).find('.af-root');
    }
    input(n,l,t,v){return `<label>${l}<input type="${t}" data-filter="${n}" value="${v}"></label>`}
    select(n,l){return `<label>${l}<select data-filter="${n}"><option value="">全部</option></select></label>`}
    currentMonthKey(){const parts=new Intl.DateTimeFormat('en-US',{timeZone:'America/Los_Angeles',year:'numeric',month:'2-digit'}).formatToParts(new Date());return `${parts.find(x=>x.type==='year').value}-${parts.find(x=>x.type==='month').value}`}
    availableMonths(){const months=this.data?.available_months||[];return months.length?months:[this.currentMonthKey()]}
    monthBounds(month){const [year,value]=String(month).split('-').map(Number),lastDay=new Date(year,value,0).getDate();return {from:`${year}-${String(value).padStart(2,'0')}-01`,to:`${year}-${String(value).padStart(2,'0')}-${String(lastDay).padStart(2,'0')}`}}
    renderMonthButtons(){const html=this.availableMonths().map(month=>`<button type="button" data-month="${this.esc(month)}" class="af-month ${this.selectedMonths.has(month)?'active':''}">${Number(month.slice(5))}月</button>`).join('');this.$.find('.af-month-range').html(html)}
    renderCountryButtons(){
        const order=['United States','Canada','Mexico','Brazil'];
        const countries=[...(this.data?.options?.country||[])];
        if(this.selectedCountry&&!countries.includes(this.selectedCountry))countries.push(this.selectedCountry);
        countries.sort((a,b)=>{const ai=order.indexOf(a),bi=order.indexOf(b);return (ai<0?99:ai)-(bi<0?99:bi)||a.localeCompare(b)});
        const buttons=['',...countries].map(country=>`<button type="button" data-country="${this.esc(country)}" class="af-country ${country===this.selectedCountry?'active':''}">${country?`${this.esc(this.translate(country))} ${this.countryFlag(country)}`:'全部 🌐'}</button>`).join('');
        this.$.find('.af-country-range').html(buttons);
    }
    countryFlag(country){return ({'United States':'🇺🇸',Canada:'🇨🇦',Mexico:'🇲🇽',Brazil:'🇧🇷'})[country]||'🌐'}
    selectMonth(month){const months=this.availableMonths();if(!months.includes(month))return;if(!this.monthSelectionStarted||!this.selectedMonths.size){this.selectedMonths=new Set([month]);this.monthSelectionStarted=true}else if(this.selectedMonths.has(month)){if(this.selectedMonths.size>1)this.selectedMonths.delete(month)}else this.selectedMonths.add(month);const indexes=months.map((value,index)=>this.selectedMonths.has(value)?index:-1).filter(index=>index>=0);if(indexes.length)this.selectedMonths=new Set(months.slice(Math.min(...indexes),Math.max(...indexes)+1));this.applyMonthSelection()}
    applyMonthSelection(){const months=[...this.selectedMonths].sort();if(!months.length)return;const first=this.monthBounds(months[0]),last=this.monthBounds(months[months.length-1]);this.$.find('[data-filter=date_from]').val(first.from);this.$.find('[data-filter=date_to]').val(last.to);this.renderMonthButtons();this.pageNo=1;this.returnPage=1;this.load()}
    bindEvents(){
        this.$.on('click','.af-refresh',()=>this.load()).on('click','.af-refresh-bind',()=>this.refreshBindings());
        this.$.on('click','.af-return-links',()=>window.open('https://sellercentral.amazon.com/fba-return','_blank','noopener,noreferrer'));
        this.$.on('click','[data-month]',e=>this.selectMonth(e.currentTarget.dataset.month));
        this.$.on('click','[data-country]',e=>{const country=e.currentTarget.dataset.country||'';if(country===this.selectedCountry)return;this.selectedCountry=country;this.pageNo=1;this.returnPage=1;this.renderCountryButtons();this.load()});
        let timer;this.$.on('change','[data-filter]:not([data-filter=search])',e=>{if(['date_from','date_to'].includes(e.currentTarget.dataset.filter)){this.selectedMonths.clear();this.monthSelectionStarted=false;this.renderMonthButtons()}this.pageNo=1;this.returnPage=1;this.load()}).on('input','[data-filter=search]',()=>{clearTimeout(timer);timer=setTimeout(()=>{this.pageNo=1;this.returnPage=1;this.load()},400)});
        this.$.on('click','[data-page]',e=>{this.pageNo=+e.currentTarget.dataset.page;this.load()}).on('click','.af-bind',e=>this.bindItem($(e.currentTarget).data())).on('click','[data-copy]',e=>{frappe.utils.copy_to_clipboard(String($(e.currentTarget).data('copy')||''));frappe.show_alert({message:'已复制',indicator:'green'})});
        this.$.on('click','[data-return-page]',e=>{this.returnPage=+e.currentTarget.dataset.returnPage;this.loadReturnCard()});
        this.$.on('click','[data-detail-group]',e=>{const group=e.currentTarget.dataset.detailGroup||'';if(group===this.detailGroup)return;this.detailGroup=group;if(!group)this.toggleSummary(false);this.pageNo=1;this.load()});
        this.$.on('click','.af-manual-select',e=>this.openManualItems(e.currentTarget.dataset.manualRow,e.currentTarget.dataset.childKey||''));
        this.$.on('click','.af-release-toggle',e=>{const key=e.currentTarget.dataset.releaseToggle;if(this.collapsedGroups.has(key))this.collapsedGroups.delete(key);else this.collapsedGroups.add(key);this.renderTable()});
        this.$.on('click','.af-summary-toggle',()=>this.toggleSummary()).on('click','[data-summary-close]',()=>this.toggleSummary(false));
        this.$.on('click','[data-order-summary-retry]',()=>{
            this.orderSummary=null;
            this.renderSummary();
        });
        this.$.on('click','[data-summary-copy]',e=>this.copyLogisticsSummaryColumn(e.currentTarget.dataset.summaryCopy));
        this.$.on('change','[data-summary-cost-key]',e=>this.summaryCostCenters.set(e.currentTarget.dataset.summaryCostKey,e.currentTarget.value));
        this.$.on('change','[data-summary-account-key]',e=>{
            const input=e.currentTarget,key=input.dataset.summaryAccountKey,value=input.value.trim();
            if(!(this.data?.account_options||[]).includes(value)){
                input.value=input.dataset.previousAccount||'';
                frappe.show_alert({message:'请从搜索结果中选择有效的费用科目',indicator:'orange'});
                return;
            }
            this.summaryAccounts.set(key,value);
            input.dataset.previousAccount=value;
        });
        this.$.on('click','.af-table-expand',()=>this.toggleTableExpand());
        $(document).off('keydown.amazon-finance-table').on('keydown.amazon-finance-table',e=>{if(e.key==='Escape'){if(this.summaryOpen)this.toggleSummary(false);else this.toggleTableExpand(false)}});
        $(window).on('resize.amazon-finance',()=>this.charts.forEach(c=>c?.resize()));
    }
    filters(){const r={};this.$.find('[data-filter]').each((_,e)=>{if(e.value)r[e.dataset.filter]=e.value});if(this.selectedCountry)r.country=this.selectedCountry;if(this.detailGroup)r.detail_group=this.detailGroup;return r}
    toggleTableExpand(force){const card=this.$.find('.af-table-card'),expand=typeof force==='boolean'?force:!card.hasClass('is-expanded');card.toggleClass('is-expanded',expand);$('body').toggleClass('af-table-expanded',expand);card.find('.af-table-expand').attr({'title':expand?'还原交易明细':'放大交易明细','aria-label':expand?'还原交易明细':'放大交易明细'})}
    toggleSummary(force){const open=typeof force==='boolean'?force:!this.summaryOpen;if(open&&!this.detailGroup)return;this.summaryOpen=open;const card=this.$.find('.af-table-card');card.toggleClass('af-summary-visible',this.summaryOpen);card.find('.af-summary-overlay').prop('hidden',!this.summaryOpen);card.find('.af-summary-toggle').toggleClass('active',this.summaryOpen);if(this.summaryOpen)this.renderSummary()}
    summaryCostCenterKey(row){return JSON.stringify([row.store,row.marketplace_id,row.currency,row.transaction_type,row.description,row.sku,row.item_id,row.shipment_id||''])}
    logisticsSummaryDescription(row){
        const description=this.translate(row.description||'—');
        return row.shipment_label&&['AWDTransportationFee','AWDProcessingFee','AGSGlobalInboundTransportation'].includes(row.description)
            ?`${description}_货件 ${row.shipment_label}`:description;
    }
    summaryBalance(){
        const summary=this.data?.detail_summary||{},source=new Map(),grouped=new Map(),issues=[];
        const cents=value=>Math.round(Number(value||0)*100);
        for(const total of summary.currencies||[])source.set(total.currency,cents(total.net));
        for(const row of summary.rows||[])grouped.set(row.currency,(grouped.get(row.currency)||0)+cents(row.net));
        for(const currency of new Set([...source.keys(),...grouped.keys()])){
            const original=source.get(currency)||0,detail=grouped.get(currency)||0;
            if(!Number.isFinite(original)||!Number.isFinite(detail)||original!==detail)
                issues.push(`${currency} 原交易 ${this.money(original/100,currency)}，汇总行 ${this.money(detail/100,currency)}`);
        }
        if(summary.row_mismatch_count)issues.push(`${summary.row_mismatch_count} 笔原交易的拆分金额不一致，首笔 ${summary.row_mismatches?.[0]?.transaction||'请查看源数据'}`);
        if((summary.reconciliation||[]).some(row=>!row.matched)&&!issues.length)issues.push('服务器金额核对未通过');
        return {ok:!issues.length,issues,hasCredits:(summary.rows||[]).some(row=>Number(row.net)>0)};
    }
    copyLogisticsSummaryColumn(column){
        if(this.detailGroup!=='logistics')return;
        if(!this.summaryBalance().ok){frappe.msgprint({title:'汇总金额不一致',message:'原交易与汇总行金额不一致，请先核对数据，暂时不能复制整列。',indicator:'red'});return}
        if(column==='net'&&this.summaryBalance().hasCredits){frappe.msgprint({title:'包含正向交易',message:'当前明细含正向净额，不能全部作为借方金额复制。请逐行核对借贷方向。',indicator:'orange'});return}
        const rows=this.data?.detail_summary?.rows||[];
        const labels={cost_center:'成本中心',account:'科目表',net:'净额(借)',item:'关联物料',description:'交易内容'};
        if(!labels[column]||!rows.length){frappe.show_alert({message:'当前没有可复制的汇总数据',indicator:'orange'});return}
        const values=rows.map(row=>({
            cost_center:()=>this.summaryCostCenters.get(this.summaryCostCenterKey(row))||row.cost_center||'',
            account:()=>this.summaryAccounts.get(this.summaryCostCenterKey(row))||row.account||'',
            net:()=>Math.abs(Number(row.net||0)).toFixed(2),
            item:()=>row.item_id||row.manual_items?.[0]?.name||'',
            description:()=>this.logisticsSummaryDescription(row),
        })[column]());
        frappe.utils.copy_to_clipboard(values.join(column==='description'?';':'\n'));
        frappe.show_alert({message:`已复制${labels[column]}整列，共 ${rows.length} 行`,indicator:'green'});
    }
    async load(){if(this.loading){this.reloadPending=true;return}this.loading=true;this.$.addClass('is-loading');try{const r=await frappe.call({method:'fengjing_app.fengjing_business.page.amazon_finance.amazon_finance.get_finance_dashboard_data',args:{filters:this.filters(),page:this.pageNo,page_size:this.pageSize}});this.data=r.message||{};this.orderSummary=null;if(this.detailGroup&&!(this.data.detail_groups||[]).some(group=>group.key===this.detailGroup)){this.detailGroup='';this.pageNo=1;this.reloadPending=true;return}this.render()}catch(e){console.error(e);frappe.msgprint({title:'财务页面加载失败',message:e.message||'请检查后台日志',indicator:'red'})}finally{this.loading=false;this.$.removeClass('is-loading');if(this.reloadPending){this.reloadPending=false;this.load()}}}
    render(){this.renderMonthButtons();this.renderCountryButtons();this.fillOptions();this.renderKpis();this.renderCurrencies();this.renderCharts();this.renderProducts();this.renderSettlements();this.renderTable();this.loadReturnCard();if(this.summaryOpen)this.renderSummary()}
    fillOptions(){['store','marketplace_id','currency_code','transaction_type','transaction_status'].forEach(k=>{const e=this.$.find(`[data-filter=${k}]`),v=e.val(),values=[...(this.data.options?.[k]||[])];if(v&&!values.includes(v))values.unshift(v);const o=values.map(x=>`<option value="${this.esc(x)}">${this.esc(this.translate(x))}</option>`).join('');e.html(`<option value="">全部</option>${o}`).val(v||'')})}
    renderKpis(){const s=this.data.summary||{},a=[['本期交易明细',s.report_row_count??s.row_count,`${this.num(s.row_count)} 组原交易，按报告商品行口径`],['关联释放',s.release_count,'随原交易展示，不重复计入金额'],['商品级交易',s.product_count,'带SKU、ASIN或订单商品ID'],['店铺级费用',s.store_fee_count,'仓储、订阅、运输等'],['已绑定物料',s.bound_count,`${s.binding_rate||0}% 商品交易已识别`],['未绑定商品',s.unbound_count,'仅商品交易需要绑定']];this.$.find('.af-kpis').html(a.map((x,i)=>`<article class="af-kpi c${i}"><span>${x[0]}</span><strong>${this.num(x[1])}</strong><small>${x[2]}</small></article>`).join('')+(s.truncated?'<div class="af-warning">数据达到10万行，请缩小日期范围。</div>':''))}
    renderCurrencies(){const a=Object.entries(this.data.summary?.currencies||{});this.$.find('.af-currencies').html(`<div class="af-section-title"><h2>多币种交易总览</h2><span>关联的资金释放记录只展示，不重复计入；不同币种不混加</span></div><div class="af-currency-grid">${a.map(([c,v])=>`<article class="af-currency"><div class="af-currency-head"><span>${this.flag(c)}</span><strong>${c}</strong></div><div class="af-net ${v.net>=0?'positive':'negative'}">${this.money(v.net,c)}</div><div class="af-money-grid"><span>流入<b>${this.money(v.inflow,c)}</b></span><span>流出<b>${this.money(v.outflow,c)}</b></span><span>销售<b>${this.money(v.sales,c)}</b></span><span>平台费用<b>${this.money(v.fees,c)}</b></span></div></article>`).join('')||'<div class="af-empty">暂无金额数据</div>'}</div>`)}
    async renderCharts(){if(typeof echarts==='undefined'){await this.loadEcharts();return this.renderCharts()}this.charts.forEach(c=>c?.dispose());this.charts=[];const d=this.data.daily||{},dates=Object.keys(d),cs=[...new Set(dates.flatMap(x=>Object.keys(d[x])))];this.chart('af-trend',{tooltip:{trigger:'axis',appendToBody:true},legend:{top:4},grid:{left:65,right:20,top:50,bottom:50},xAxis:{type:'category',data:dates},yAxis:{type:'value'},dataZoom:[{type:'inside'},{type:'slider',height:17,bottom:8}],series:cs.map(c=>({name:`${c} 净额`,type:'line',smooth:true,showSymbol:false,data:dates.map(x=>d[x][c]?.net||0),lineStyle:{width:3},areaStyle:{opacity:.06}}))});const c=this.$.find('[data-filter=currency_code]').val()||Object.keys(this.data.fees||{})[0],fees=this.data.fees?.[c]||{};this.chart('af-fees',{tooltip:{trigger:'item',appendToBody:true},legend:{bottom:0,type:'scroll'},series:[{type:'pie',radius:['40%','68%'],center:['50%','43%'],data:Object.entries(fees).map(([name,value])=>({name,value:Math.abs(value)}))}]});this.bar('af-types',this.data.types?.[c]||{},x=>x.amount,'#377cf5');this.bar('af-stores',this.data.stores?.[c]||{},x=>x.amount,'#16bba4')}
    bar(id,obj,value,color){const a=Object.entries(obj).sort((x,y)=>Math.abs(value(y[1]))-Math.abs(value(x[1]))).slice(0,15);this.chart(id,{tooltip:{trigger:'axis',appendToBody:true},grid:{left:165,right:25,top:15,bottom:25},xAxis:{type:'value'},yAxis:{type:'category',data:a.map(x=>this.translate(x[0])),axisLabel:{width:150,overflow:'truncate'}},series:[{type:'bar',data:a.map(x=>value(x[1])),itemStyle:{borderRadius:[0,7,7,0],color}}]})}
    chart(id,o){const e=this.$.find('#'+id)[0];if(e){const c=echarts.init(e);c.setOption(o);this.charts.push(c)}}
    loadEcharts(){if(!this.echartsPromise)this.echartsPromise=new Promise((ok,bad)=>frappe.require('/assets/fengjing_app/js/图表-echarts.js',ok,bad));return this.echartsPromise}
    renderProducts(){this.$.find('.af-products').html(`<div class="af-scroll-list">${(this.data.products||[]).slice(0,100).map((p,i)=>`<div class="af-product"><span class="af-rank">${i+1}</span><img src="${this.image(p.image)}"><div class="af-product-main"><b>${this.esc(p.item_name||p.product_name||p.sku||'未命名商品')}</b><small><button data-copy="${this.esc(p.asin||'')}">${this.esc(p.asin||'无ASIN')}</button> · <button data-copy="${this.esc(p.sku||'')}">${this.esc(p.sku||'无SKU')}</button></small><small>${this.esc(p.store||'未归属')} · 数量 ${this.num(p.quantity)} · ${p.transactions}条</small></div><div class="af-product-money">${Object.entries(p.amounts||{}).map(([c,v])=>`<b class="${v>=0?'positive':'negative'}">${this.money(v,c)}</b>`).join('')}</div>${p.item?`<a class="af-bound" href="/app/item/${encodeURIComponent(p.item)}">${this.esc(p.item)}</a>`:`<button class="btn btn-xs btn-primary af-bind" ${this.bindData(p)}>绑定物料</button>`}</div>`).join('')||'<div class="af-empty">暂无商品交易</div>'}</div>`)}
    renderSettlements(){this.$.find('.af-settlements').html(`<div class="af-scroll-list">${(this.data.settlements||[]).map(s=>`<div class="af-settlement"><i>结</i><div><b>${this.esc(s.settlement_id)}</b><small>${this.esc(s.store||'未归属')} · ${s.count}条 · ${this.date(s.last_date)}</small></div><strong class="${s.amount>=0?'positive':'negative'}">${this.money(s.amount,s.currency)}</strong></div>`).join('')||'<div class="af-empty">暂无结算批次</div>'}</div>`)}
    renderSummary(){
        const orderSummary=this.detailGroup==='order';
        this.$.find('.af-summary-drawer h3').text(orderSummary?'订单汇总明细':'汇总明细');
        if(orderSummary)return this.renderOrderSummary();
        const summary=this.data?.detail_summary||{},totals=summary.currencies||[],rows=summary.rows||[];
        const logistics=this.detailGroup==='logistics';
        const balance=logistics?this.summaryBalance():null;
        const balanceHtml=logistics?`<div class="af-summary-balance ${balance.ok?'is-matched':'is-mismatched'}">${balance.ok?'✓ 原交易净额与汇总行净额一致（按币种及正负号核对）':`⚠ 金额核对未通过，已暂停整列复制。${balance.issues.map(issue=>`<span>${this.esc(issue)}</span>`).join('')}`}</div>${balance.hasCredits?'<div class="af-summary-balance is-mismatched">含正向交易；净额(借)显示绝对值，不能将整列直接记入借方。</div>':''}`:'';
        this.$.find('.af-summary-count').text(`共 ${this.num(summary.count)} 组原交易 · ${this.num(rows.length)} 个汇总分类`);
        this.$.find('.af-summary-drawer>p').text(logistics?'按店铺、站点、SKU、交易内容和货件汇总当前筛选范围，包含所有分页。':'按 SKU、交易类型、交易内容和币种汇总当前筛选范围，包含所有分页。');
        const totalsHtml=`<div class="af-summary-totals">${totals.map(x=>`<article><header><b>${this.esc(x.currency)}</b><span>${this.num(x.count)} 组</span></header><div><span>净额<strong>${this.money(x.net,x.currency)}</strong></span><span>销售额<strong>${this.money(x.sales,x.currency)}</strong></span><span>费用<strong>${this.money(x.fees,x.currency)}</strong></span></div></article>`).join('')}</div>`;
        const itemHtml=(items,currency)=>(items||[]).map(item=>`<span class="af-manual-chip" title="${this.esc(item.name)} · ${this.esc(item.item_name||'')}"><img src="${this.esc(this.image(item.image))}" alt=""><span>${this.esc(item.item_name||item.name)}</span>${item.amount!=null?`<strong>${this.money(Math.abs(item.amount),currency)}</strong>`:''}</span>`).join('')||'—';
        const costCenterCell=x=>{
            const key=this.summaryCostCenterKey(x);
            const selected=this.summaryCostCenters.get(key)||x.cost_center||'';
            const options=[...(this.data?.cost_center_options||[])];
            if(selected&&!options.includes(selected))options.unshift(selected);
            return `<select class="af-summary-cost-center" data-summary-cost-key="${this.esc(key)}" title="${this.esc(selected)}">${options.map(value=>`<option value="${this.esc(value)}" ${value===selected?'selected':''}>${this.esc(value)}</option>`).join('')}</select>`;
        };
        const accountCell=x=>{
            const key=this.summaryCostCenterKey(x),selected=this.summaryAccounts.get(key)||x.account||'';
            return `<input class="af-summary-account" data-summary-account-key="${this.esc(key)}" data-previous-account="${this.esc(selected)}" list="af-summary-account-options" value="${this.esc(selected)}" placeholder="搜索科目" title="搜索并选择费用科目" aria-label="费用科目">`;
        };
        const accountOptions=`<datalist id="af-summary-account-options">${(this.data?.account_options||[]).map(value=>`<option value="${this.esc(value)}"></option>`).join('')}</datalist>`;
        const body=logistics
            ? `<div class="af-summary-table-wrap af-summary-logistics">${accountOptions}<table><thead><tr><th>店铺 / 站点</th><th class="af-summary-copy-head" data-summary-copy="cost_center" title="点击复制成本中心整列">成本中心 ⧉</th><th class="af-summary-copy-head" data-summary-copy="item" title="点击复制关联物料整列">关联物料 ⧉</th><th class="af-summary-copy-head" data-summary-copy="account" title="点击复制科目表整列">科目表 ⧉</th><th class="af-summary-copy-head" data-summary-copy="net" title="点击复制净额(借)整列">净额(借) ⧉</th><th class="af-summary-copy-head" data-summary-copy="description" title="点击复制交易内容整列">交易内容 ⧉</th></tr></thead><tbody>${rows.map(x=>`<tr><td><b>${this.esc(x.store||'—')}</b><small>${this.esc(this.translate(x.marketplace_id||''))}</small></td><td>${costCenterCell(x)}</td><td><div class="af-manual-items">${itemHtml(x.manual_items,x.currency)}</div></td><td>${accountCell(x)}</td><td class="num">${this.money(Math.abs(x.net),x.currency)}</td><td title="${this.esc(this.logisticsSummaryDescription(x))}">${this.esc(this.logisticsSummaryDescription(x))}</td></tr>`).join('')||'<tr><td colspan="6" class="af-empty">当前筛选范围没有交易明细</td></tr>'}</tbody></table></div>`
            : `<div class="af-summary-table-wrap"><table><thead><tr><th>交易类型</th><th>交易内容</th><th>币种</th><th>SKU</th><th>笔数</th><th>净额</th><th>销售额</th><th>费用</th></tr></thead><tbody>${rows.map(x=>`<tr><td>${this.esc(this.translate(x.transaction_type))}</td><td title="${this.esc(this.translate(x.description||''))}">${this.esc(this.translate(x.description||'—'))}</td><td>${this.esc(x.currency)}</td><td>${this.esc(x.sku||'—')}</td><td class="num">${this.num(x.count)}</td><td class="num">${this.money(x.net,x.currency)}</td><td class="num">${this.money(x.sales,x.currency)}</td><td class="num">${this.money(x.fees,x.currency)}</td></tr>`).join('')||'<tr><td colspan="8" class="af-empty">当前筛选范围没有交易明细</td></tr>'}</tbody></table></div>`;
        this.$.find('.af-summary-body').html(balanceHtml+totalsHtml+body);
    }
    async renderOrderSummary(){
        const filters=this.filters(), key=JSON.stringify(filters);
        const body=this.$.find('.af-summary-body');
        this.$.find('.af-summary-drawer>p').text('财务按当前筛选汇总全部分页。库存按美西日期、SKU 和履约中心核对当天总量。');
        if(this.orderSummary?.key===key){
            const current=this.orderSummary;
            if(current.data)return this.renderOrderReconciliation(current.data);
            if(current.error){
                this.$.find('.af-summary-count').text('加载失败');
                body.html('<div class="af-empty">订单汇总读取失败。<button class="btn btn-default btn-xs" type="button" data-order-summary-retry>重试</button></div>');
                return;
            }
        }
        this.$.find('.af-summary-count').text('正在读取财务、发货和库存记录…');
        body.html('<div class="af-empty" role="status">正在核对订单明细…</div>');
        if(this.orderSummary?.key===key)return;
        const request=this.orderSummary={key};
        try{
            const response=await frappe.call({
                method:'fengjing_app.fengjing_business.page.amazon_finance.amazon_finance.get_finance_dashboard_data',
                args:{filters,order_reconciliation:1},
            });
            if(!Array.isArray(response.message?.products))throw new Error('订单汇总数据格式错误');
            request.data=response.message;
        }catch(error){
            console.error('订单汇总加载失败',error);
            request.error=true;
        }
        if(this.orderSummary===request&&this.summaryOpen&&this.detailGroup==='order'
            &&JSON.stringify(this.filters())===key)this.renderOrderSummary();
    }
    orderSummaryAmounts(entries,field){
        return entries.map(entry=>`<span>${this.esc(this.money(entry[field],entry.currency))}</span>`).join('')||'—';
    }
    orderSummaryStatus(entries){
        const state=entries.some(entry=>entry.status==='difference')?'difference'
            :entries.some(entry=>entry.status==='missing')?'missing':'matched';
        const label={matched:'数量一致',missing:'待核对',difference:'数量有差异'}[state];
        return `<span class="af-order-status ${state}">${label}</span>`;
    }
    orderSummaryCount(value){
        return value==null?'<small>无记录</small>':`${this.num(value)} 件`;
    }
    orderSummaryCenter(entry){
        const counts=[
            ['库存分类账',entry.ledger,null],
            ['已配送',entry.fulfilled,entry.selected_fulfilled],
            ['已完成',entry.completed,entry.selected_completed],
        ];
        const shipments=lines=>lines.slice().sort((a,b)=>a.time.localeCompare(b.time))
            .map(line=>`<span>${this.esc(line.time)} · ${this.num(line.quantity)} 件</span>`).join('')||'<small>无记录</small>';
        const quantity=entry.orders.length>1
            ?`当天关联 ${this.num(entry.orders.length)} 个订单，库存按仓库合计核对。`
            :'库存分类账没有订单号，按仓库合计核对。';
        return `<section class="af-order-center">
            <header><b>${this.esc(entry.center||'履约中心缺失')}</b>
                <span>${this.esc(entry.countries.map(country=>this.translate(country)).join(' / '))}</span>${this.orderSummaryStatus([entry])}</header>
            <div class="af-order-center-counts">${counts.map(([label,value,selected])=>`
                <div><span>${label} · 当天</span><b>${this.orderSummaryCount(value)}</b>
                ${selected!=null&&selected!==value?`<small>筛选订单 ${this.num(selected)} 件</small>`:''}</div>`).join('')}</div>
            <div class="af-order-table"><table><thead><tr><th>当前筛选订单</th><th>已配送 · 美西</th><th>已完成 · 美西</th></tr></thead>
                <tbody>${entry.orders.map(order=>`<tr><td><button type="button" data-copy="${this.esc(order.id)}" title="复制订单号">${this.esc(order.id)}</button></td>
                    <td>${shipments(order.fulfilled)}</td><td>${shipments(order.completed)}</td></tr>`).join('')}</tbody></table></div>
            <small class="af-order-note">${quantity}</small>
        </section>`;
    }
    orderSummaryDay(day,open){
        const counts=['ledger','fulfilled','completed'].map(key=>day.centers.every(center=>center[key]!=null)
            ?day.centers.reduce((total,center)=>total+center[key],0):null);
        return `<details class="af-order-day"${open?' open':''}><summary>
            <span class="af-order-day-title"><b>${this.esc(day.date)}</b>
                <small>${this.num(day.centers.length)} 个履约中心${day.outside_finance_period?' · 跨财务期间':''}</small></span>
            <span class="af-order-day-count"><small>库存出库</small><b>${this.orderSummaryCount(counts[0])}</b></span>
            <span class="af-order-day-count"><small>已配送</small><b>${this.orderSummaryCount(counts[1])}</b></span>
            <span class="af-order-day-count"><small>已完成</small><b>${this.orderSummaryCount(counts[2])}</b></span>
            ${this.orderSummaryStatus(day.centers)}
        </summary><div class="af-order-centers">${day.centers.map(center=>this.orderSummaryCenter(center)).join('')}</div></details>`;
    }
    orderSummaryProduct(product,index){
        const items=product.items||[],first=items[0];
        const unlinked=product.orders.filter(order=>!order.days.length).length;
        const financial=`<details class="af-order-finance"><summary>财务订单明细 · ${this.num(product.orders.length)} 行
            ${unlinked?`<small>${this.num(unlinked)} 行未找到关联发货</small>`:''}</summary>
            <div class="af-order-table"><table><thead><tr><th>订单号</th><th>财务日期（美西）</th><th>数量</th><th>销售额</th><th>费用</th><th>净额</th><th>关联发货日</th></tr></thead>
                <tbody>${product.orders.map(order=>`<tr>
                    <td>${order.id?`<button type="button" data-copy="${this.esc(order.id)}" title="复制订单号">${this.esc(order.id)}</button>`:'—'}</td>
                    <td>${this.esc(order.dates.join('、')||'—')}</td><td>${this.num(order.quantity)}</td>
                    ${['sales','fees','net'].map(field=>`<td>${this.orderSummaryAmounts(order.amounts,field)}</td>`).join('')}
                    <td>${this.esc(order.days.join('、')||'未找到关联发货')}</td></tr>`).join('')}</tbody></table></div></details>`;
        return `<details class="af-order-product"${index===0?' open':''}><summary>
            ${first?.image?`<img src="${this.esc(this.image(first.image))}" alt="">`:''}
            <span class="af-order-product-name"><b>${this.esc(product.sku||'无 SKU')}</b>
                <small>${this.esc(first?.label||product.product_name||'')}</small></span>
            <span class="af-order-product-quantity"><b>${this.num(product.quantity)} 件</b><small>${this.num(product.order_count)} 个订单</small></span>
            <span class="af-order-product-net"><small>财务净额</small><b>${this.orderSummaryAmounts(product.totals,'net')}</b></span>
        </summary><div class="af-order-product-body">
            <div class="af-order-product-meta"><span>${this.esc(product.stores.join(' / '))}</span>
                ${items.map(item=>`<a href="/app/item/${encodeURIComponent(item.name)}" target="_blank" rel="noopener noreferrer">${this.esc(item.name)}</a>`).join('')}</div>
            ${financial}
            ${product.days.map((day,dayIndex)=>this.orderSummaryDay(day,index===0&&dayIndex===0)).join('')
            ||'<div class="af-empty">当前订单未找到对应发货记录</div>'}
        </div></details>`;
    }
    renderOrderReconciliation(data){
        const metrics=[['财务订单',data.order_count],['履约中心分组一致',data.matched_count],['数量有差异',data.difference_count],['分组待核对',data.missing_count]];
        const unlinked=data.products.reduce((count,product)=>count+product.orders.filter(order=>!order.days.length).length,0);
        const unlinkedLabel=unlinked?` · ${this.num(unlinked)} 条订单商品未关联发货`:'';
        this.$.find('.af-summary-count').text(`${this.num(data.products.length)} 个 SKU 分组 · ${this.num(data.finance_rows)} 条财务记录 · ${this.num(data.center_count)} 个日期和履约中心分组${unlinkedLabel}`);
        const totals=`<div class="af-summary-totals">${data.totals.map(entry=>`<article>
            <header><b>${this.esc(entry.currency)}</b><span>财务交易</span></header>
            <div>${[['销售额','sales'],['费用','fees'],['净额','net']].map(([label,field])=>`
                <span>${label}<strong>${this.esc(this.money(entry[field],entry.currency))}</strong></span>`).join('')}</div>
        </article>`).join('')}</div>`;
        this.$.find('.af-summary-body').html(`
            <div class="af-order-metrics">${metrics.map(([label,value])=>`<div><b>${this.num(value)}</b><span>${label}</span></div>`).join('')}</div>
            ${data.truncated?'<div class="af-summary-balance is-mismatched">财务记录达到查询上限，请缩小日期范围后重新核对。</div>':''}
            ${totals}
            <nav class="af-order-sources" aria-label="原始报告">
                <a href="/app/amazon-ledger" target="_blank" rel="noopener noreferrer">库存分类账 ↗</a>
                <a href="/app/amazon_fba_shipment_analysis" target="_blank" rel="noopener noreferrer">FBA 已配送货件 ↗</a>
                <a href="/app/amazon_fba_customer_sales_analysis" target="_blank" rel="noopener noreferrer">FBA 已完成订单销售 ↗</a>
            </nav>
            ${data.products.map((product,index)=>this.orderSummaryProduct(product,index)).join('')
            ||'<div class="af-empty">当前筛选范围没有订单</div>'}`);
    }
    async loadReturnCard(){
        const requestId=this.returnRequestId=(this.returnRequestId||0)+1;
        const filters=this.filters();
        this.$.find('.af-returns-table-wrap tbody').html('<tr><td colspan="7" class="af-empty">正在加载退货记录…</td></tr>');
        try{
            const response=await frappe.call({method:'fengjing_app.fengjing_business.page.amazon_fba_return_analysis.amazon_fba_return_analysis.get_finance_return_data',args:{date_from:filters.date_from,date_to:filters.date_to,search:filters.search,page:this.returnPage,page_size:20}});
            if(requestId!==this.returnRequestId)return;
            this.returnData=response.message||{};
            this.returnPage=this.returnData.pagination?.page||1;
            this.renderReturnCard();
        }catch(error){
            if(requestId!==this.returnRequestId)return;
            console.error(error);
            this.$.find('.af-returns-table-wrap tbody').html('<tr><td colspan="7" class="af-empty">退货记录加载失败，请稍后刷新。</td></tr>');
        }
    }
    renderReturnCard(){
        const data=this.returnData||{},summary=data.summary||{},pagination=data.pagination||{};
        this.$.find('.af-return-count').text(`${this.num(summary.records)} 条记录 · ${this.num(summary.orders)} 个订单 · ${this.num(summary.units)} 件退货`);
        const metrics=[['退货件数',summary.units],['可售',summary.sellable],['不可售',summary.unsellable],['已退回库存',summary.returned_to_inventory],['立即清算',summary.liquidation]];
        this.$.find('.af-return-metrics').html(metrics.map(([label,value])=>`<div><strong>${this.num(value)}</strong><span>${label}</span></div>`).join('')+(data.truncated?'<p>退货原始记录超过处理上限，请缩小日期范围。</p>':''));
        this.$.find('.af-returns-table-wrap tbody').html((data.rows||[]).map(row=>{
            const status=({IMMEDIATE_LIQUIDATION:'立即清算',IMMEDIATE_DISPOSAL:'立即处置','Unit returned to inventory':'已退回库存'})[row.status]||row.status||'未提供';
            const condition=row.detailed_disposition?this.translate(row.detailed_disposition):'未判定';
            return `<tr><td>${this.date(row.report_date)}<small>${this.esc(row.report_timezone||'')}</small></td><td>${this.esc(row.order_id||'—')}<small class="af-order-month">${row.order_month?`原订单：${this.esc(row.order_month.slice(0,4))}年${this.esc(String(Number(row.order_month.slice(5))))}月`:'原订单月份待核实'}</small></td><td><div class="af-return-product"><img src="${this.esc(this.image(row.item_image))}" alt=""><span><b title="${this.esc(row.item_name||row.product_name||'')}">${this.esc(row.item_name||row.product_name||row.seller_sku||'未命名商品')}</b><small>${this.esc(row.seller_sku||'无 SKU')} · ${this.esc(row.asin||'无 ASIN')}${row.item_id?` · ${this.esc(row.item_id)}`:''}</small></span></div></td><td><span class="af-return-condition ${row.detailed_disposition==='SELLABLE'?'sellable':row.detailed_disposition?'unsellable':'unknown'}">${this.esc(condition)}</span><small>${this.esc(this.translate(row.reason||'—'))}</small></td><td>${this.esc(status)}<small>${this.esc(row.fulfillment_center_id||'—')}</small></td><td class="num">${this.num(row.quantity)}</td><td><a href="/app/amazon-fba-customer-return/${encodeURIComponent(row.name)}">查看 ↗</a></td></tr>`;
        }).join('')||'<tr><td colspan="7" class="af-empty">当前日期范围没有 FBA 客户退货。</td></tr>');
        this.$.find('.af-return-pages').html(`<span>第 ${pagination.page||1} / ${pagination.pages||1} 页</span><button type="button" data-return-page="${(pagination.page||1)-1}" ${(pagination.page||1)<=1?'disabled':''}>上一页</button><button type="button" data-return-page="${(pagination.page||1)+1}" ${(pagination.page||1)>=(pagination.pages||1)?'disabled':''}>下一页</button>`);
    }
    renderTable(){
        const p=this.data.pagination||{};
        const refundOnly=this.detailGroup==='refund';
        this.$.find('.af-summary-toggle').prop('disabled',!this.detailGroup).attr('title',this.detailGroup?'汇总当前分类':'请先选择一个交易分类');
        this.$.find('.af-manual-items-head').prop('hidden',this.detailGroup!=='logistics');
        this.$.find('.af-detail-tabs').html((this.data.detail_groups||[]).map(group=>`<button type="button" class="af-detail-tab ${group.key===this.detailGroup?'active':''}" data-detail-group="${this.esc(group.key)}"><span>${this.esc(group.label)}</span><b>${this.num(group.count)}</b></button>`).join(''));
        const releaseCount=(this.data.rows||[]).reduce((count,row)=>count+(row.related_releases||[]).length,0);
        const itemCount=refundOnly?0:(this.data.rows||[]).reduce((count,row)=>count+(row.item_lines||[]).length,0);
        const awdCount=refundOnly?0:(this.data.rows||[]).reduce((count,row)=>count+(row.awd_fee_details||[]).length,0);
        this.$.find('.af-table-count').text(refundOnly?`共 ${this.num(p.total)} 条退款订单 · 当前页展开 ${this.num(releaseCount)} 条资金释放 · 第 ${p.page}/${p.pages} 页`:`共 ${this.num(p.display_total??p.total)} 条交易明细（${this.num(p.total)} 组）· 当前页展开 ${this.num(itemCount)} 条商品明细、${this.num(releaseCount)} 条释放记录、${this.num(awdCount)} 条 AWD 费用明细 · 第 ${p.page}/${p.pages} 页`);
        this.$.find('.af-pages').html(`<button class="btn btn-xs" data-page="${p.page-1}" ${p.page<=1?'disabled':''}>上一页</button><button class="btn btn-xs" data-page="${p.page+1}" ${p.page>=p.pages?'disabled':''}>下一页</button>`);
        this.$.find('.af-table-card tbody').html((this.data.rows||[]).flatMap(r=>[
            this.transactionRow(r),
            ...(refundOnly?[]:(r.item_lines||[]).map(line=>this.transactionRow(r,false,r.name,line))),
            ...(refundOnly?[]:(r.awd_fee_details||[]).map(detail=>this.awdFeeRow(r,detail))),
            ...(r.related_releases||[]).map(child=>this.transactionRow(child,true,r.name)),
        ]).join('')||`<tr><td colspan="${this.detailGroup==='logistics'?12:11}" class="af-empty">暂无交易</td></tr>`)
    }
    transactionRow(r,isRelease=false,parentName='',itemLine=null){
            const isItem=Boolean(itemLine);
            const detail=itemLine||r;
            const fees=detail.display_fees??((detail.selling_fees||0)+(detail.fba_fees||0)+(detail.other_transaction_fees||0));
            const net=isItem?detail.net_amount:(r.display_net_amount??r.net_amount);
            const product=Boolean(r.is_product);
            const releases=r.related_releases||[];
            const refundOnly=this.detailGroup==='refund'&&!isRelease;
            const itemLines=refundOnly?[]:(r.item_lines||[]);
            const awdDetails=refundOnly?[]:(r.awd_fee_details||[]);
            const collapsed=this.collapsedGroups.has(r.name);
            const toggle=!isRelease&&!isItem&&(itemLines.length||releases.length||awdDetails.length)?`<button type="button" class="af-release-toggle" data-release-toggle="${this.esc(r.name)}" aria-expanded="${!collapsed}" title="${collapsed?'展开':'收起'}关联明细">${collapsed?'▸':'▾'}</button>`:'';
            const relation=isItem?`<span class="af-item-label">↳ 商品明细 ${this.num(detail.line_no)}/${this.num(detail.line_count)}</span>`:isRelease?`<span class="af-release-label">↳ 资金释放${r.context_only?' · 范围外':''}</span>`:itemLines.length||releases.length||awdDetails.length?`<span class="af-parent-label">原交易${itemLines.length?` · ${this.num(itemLines.length)} 条商品`:''}${awdDetails.length?` · ${this.num(awdDetails.length)} 条 AWD 明细`:''}${releases.length?` · ${this.num(releases.length)} 条释放`:''}</span>`:'';
            const content=product
                ? `<div class="af-table-product"><img src="${this.image(r.item_image)}"><span><b>${this.esc((isItem&&detail.description)||r.product_name||r.sku||'未命名商品')}</b><small>${this.esc(detail.asin||r.asin||'无ASIN')} · ${r.transaction_type==='RemovalShipment'?'FNSKU ':''}${this.esc(detail.sku||r.sku||'无SKU')}${isItem?` · 数量 ${this.num(detail.quantity)}`:''}</small></span></div>`
                : `<div class="af-fee-content"><span class="af-fee-icon">${this.feeIcon(r.description,r.transaction_type)}</span><span><b>${this.esc(this.translate(r.description||r.transaction_type||'店铺级费用'))}</b><small>${this.esc(this.translate(r.transaction_type||'未分类'))} · 不对应具体SKU</small></span></div>`;
            const assignment=product
                ? (r.corresponding_item?`<a href="/app/item/${encodeURIComponent(r.corresponding_item)}">${this.esc(r.item_name||r.corresponding_item)}</a><small class="af-scope-tag product">商品级交易</small>`:`<button class="btn btn-xs btn-primary af-bind" ${this.bindData(r)}>绑定物料</button><small class="af-scope-tag product">商品级交易</small>`)
                : `<span class="af-no-item">${this.esc(this.feeGroup(r.description,r.transaction_type))}</span><small class="af-scope-tag store">店铺级费用 · 无需绑定物料</small>`;
            const hasChildren=itemLines.length||awdDetails.length;
            const manualCell=this.detailGroup==='logistics'
                ?this.manualItemsCell(r,Boolean(isRelease||(!isItem&&hasChildren)),isItem?detail.manual_key:'',isItem?detail.manual_items:undefined):'';
            const rowClass=isItem?'af-item-child':isRelease?'af-release-child':`${product?'af-product-row':'af-fee-row'}${this.detailGroup==='refund'?' af-refund-parent':''}`;
            const groupAttr=(isItem||isRelease)?` data-release-parent="${this.esc(parentName)}"${this.collapsedGroups.has(parentName)?' hidden':''}`:'';
            const taxNote=isItem&&((detail.product_sales_tax||0)||(detail.marketplace_withheld_tax||0))?`<small>销售税 ${this.money(detail.product_sales_tax,r.currency_code)} · 平台代扣税 ${this.money(detail.marketplace_withheld_tax,r.currency_code)}</small>`:'';
            const feeComponents=isItem?detail:(r.display_fee_categories||r);
            const feeDetails=this.detailGroup==='order'&&!isRelease
                ?[['佣金',feeComponents.selling_fees],['FBA履约费',feeComponents.fba_fees],['其他交易费',feeComponents.other_transaction_fees]]
                    .filter(([,amount])=>Math.abs(Number(amount||0))>=0.005)
                    .map(([label,amount])=>`<small>${label} ${this.money(amount,r.currency_code)}</small>`).join('')
                :'';
            const shipmentTags=this.detailGroup==='order'&&!isItem&&!isRelease
                ?[
                    ...(r.shipment_dates||[]).filter(date=>/^\d{4}-\d{2}-\d{2}$/.test(date)).map(date=>`<span class="af-shipment-month" title="数据来自亚马逊FBA已配送货件分析 · 美国太平洋时间 ${this.esc(date)}">${Number(date.slice(5,7))}月${Number(date.slice(8,10))}日已配送</span>`),
                    ...(r.completed_dates||[]).filter(date=>/^\d{4}-\d{2}-\d{2}$/.test(date)).map(date=>`<span class="af-shipment-month completed" title="数据来自亚马逊FBA已完成订单销售分析 · 美国太平洋时间 ${this.esc(date)}">${Number(date.slice(5,7))}月${Number(date.slice(8,10))}日已完成</span>`),
                ].join('')
                :'';
            return `<tr class="${rowClass}"${groupAttr}><td><b>${this.esc(r.store||'—')}</b><small>${this.esc(this.translate(r.country||''))} · ${this.esc(this.translate(r.marketplace_id||''))}</small></td><td><div class="af-transaction-id">${toggle}${relation}<button data-copy="${this.esc(r.transaction_id||'')}">${this.esc(r.transaction_id||'—')}</button></div><small>${this.esc(r.amazon_order_id||'无订单号')}</small>${shipmentTags?`<div class="af-shipment-months">${shipmentTags}</div>`:''}</td><td>${content}</td>${manualCell}<td><b>${this.esc(this.translate(r.transaction_type||'未分类'))}</b><small>${this.esc(this.translate(r.transaction_status||''))}</small></td><td class="num ${net>=0?'positive':'negative'}">${this.money(net,r.currency_code)}${isRelease?'<small>资金流转，不重复计入汇总</small>':isItem?'<small>已计入原交易，不重复汇总</small>':''}${taxNote}</td><td class="num ${fees<0?'negative':''}">${isRelease?'—':this.money(fees,r.currency_code)}${feeDetails}</td><td>${this.date(r.report_posted_date||r.posted_date)}<small>${this.esc(r.report_timezone||'')}</small></td><td>${assignment}</td><td>${this.esc(r.currency_code||'—')}</td><td class="num">${isRelease?'—':this.money(detail.product_sales,r.currency_code)}</td><td>${this.esc(r.settlement_id||'—')}</td></tr>`;
    }
    manualItemsCell(row,hide=false,childKey='',childItems){
        if(hide)return '<td class="af-manual-items-column"></td>';
        const items=childKey?(childItems||[]):(row.manual_items||[]);
        return `<td class="af-manual-items-column"><div class="af-manual-items">${items.map(item=>`<span class="af-manual-chip" title="${this.esc(item.name)} · ${this.esc(item.item_name||'')}"><img src="${this.esc(this.image(item.image))}" alt=""><span>${this.esc(item.item_name||item.name)}</span>${item.amount!=null?`<strong>${this.money(Math.abs(item.amount),row.currency_code)}</strong>`:''}</span>`).join('')}<button type="button" class="af-manual-select" data-manual-row="${this.esc(row.name)}" data-child-key="${this.esc(childKey)}">${items.length?'编辑':'选择物料'}</button></div></td>`;
    }
    async openManualItems(name,childKey=''){
        const row=(this.data.rows||[]).find(item=>item.name===name);
        if(!row)return;
        const child=childKey?[...(row.awd_fee_details||[]),...(row.item_lines||[])].find(item=>item.manual_key===childKey):null;
        if(childKey&&!child){frappe.msgprint('费用明细已变化，请刷新页面');return}
        const currency=row.currency_code||'USD';
        const targetCents=Math.round(Math.abs(Number(childKey?(child.amount??child.net_amount):(row.display_net_amount??row.net_amount))) * 100);
        if(!Number.isFinite(targetCents)){frappe.msgprint('无法确定本条净额，请刷新页面');return}
        const dialog=new frappe.ui.Dialog({title:'关联物料与金额',size:'large',fields:[{fieldtype:'HTML',fieldname:'manual_items_html'}]});
        dialog.show();
        const wrapper=dialog.fields_dict.manual_items_html.$wrapper;
        wrapper.html(`<div class="af-manual-picker"><input class="af-manual-search" type="search" placeholder="搜索物料 ID 或名称"><div class="af-manual-target">需分配金额（填正数）：<b>${this.money(targetCents/100,currency)}</b></div><div class="af-manual-results">正在加载物料…</div><div class="af-manual-footer"><span class="af-manual-status">选择物料并填写正数金额</span><button type="button" class="af-manual-save" disabled>保存分配</button></div></div>`);
        const currentItems=()=>childKey?(row.manual_child_items?.[childKey]||[]):(row.manual_items||[]);
        let selected=new Set(childKey?(row.manual_child_item_ids?.[childKey]||[]):(row.manual_item_ids||[]));
        const savedAmounts=childKey?(row.manual_child_item_amounts?.[childKey]||{}):(row.manual_item_amounts||{});
        const amounts=new Map(Object.entries(savedAmounts).map(([id,value])=>[id,String(value)]));
        const knownItems=new Map(currentItems().map(item=>[item.name,item]));
        let candidates=[],busy=false,requestId=0,searchTimer;
        const cents=value=>{
            const raw=String(value??'').trim();
            return /^\d+(?:\.\d{1,2})?$/.test(raw)?Math.round(Number(raw)*100):null;
        };
        const validate=()=>{
            const values=[...selected].map(id=>cents(amounts.get(id)));
            const complete=selected.size>0&&values.every(value=>value!==null&&Number.isFinite(value));
            const sum=values.reduce((total,value)=>total+(value||0),0);
            const match=complete&&sum===targetCents;
            wrapper.find('.af-manual-status').text(complete
                ?`已分配 ${this.money(sum/100,currency)} / 目标 ${this.money(targetCents/100,currency)}${match?' · 金额相符':` · 差额 ${this.money((targetCents-sum)/100,currency)}`}`
                :'请勾选物料，并为每个物料填写最多两位小数的正数金额');
            wrapper.find('.af-manual-save').prop('disabled',busy||!match);
            return match;
        };
        const draw=()=>{
            const displayed=[...candidates],present=new Set(displayed.map(item=>item.name));
            for(const [id,item] of knownItems){if(selected.has(id)&&!present.has(id)){displayed.push(item);present.add(id)}}
            wrapper.find('.af-manual-results').html(displayed.map(item=>`<div class="af-manual-option"><input type="checkbox" data-item-id="${this.esc(item.name)}" ${selected.has(item.name)?'checked':''} ${busy?'disabled':''} aria-label="选择 ${this.esc(item.item_name||item.name)}"><input type="number" class="af-manual-amount" data-item-id="${this.esc(item.name)}" min="0" step="0.01" value="${this.esc(amounts.get(item.name)||'')}" placeholder="金额" ${selected.has(item.name)&&!busy?'':'disabled'} aria-label="${this.esc(item.item_name||item.name)}的分配金额"><img src="${this.esc(this.image(item.image))}" alt=""><span><b>${this.esc(item.item_name||item.name)}</b><small>${this.esc(item.name)}</small></span></div>`).join('')||'<p class="af-manual-none">没有找到物料</p>');
            validate();
        };
        const search=async()=>{
            const id=++requestId,query=wrapper.find('.af-manual-search').val();
            try{
                const response=await frappe.call({method:'fengjing_app.fengjing_business.page.amazon_finance.amazon_finance.search_logistics_items',args:{query}});
                if(id!==requestId)return;
                candidates=response.message||[];
                candidates.forEach(item=>knownItems.set(item.name,item));
                draw();
            }catch(error){if(id===requestId){console.error(error);wrapper.find('.af-manual-results').html('<p class="af-manual-none">物料加载失败，请重试</p>')}}
        };
        wrapper.on('input','.af-manual-search',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(search,250)});
        wrapper.on('change','.af-manual-option input[type=checkbox]',event=>{
            if(busy)return;
            const itemId=event.currentTarget.dataset.itemId;
            if(event.currentTarget.checked){
                selected.add(itemId);
                if(selected.size===1&&!amounts.has(itemId))amounts.set(itemId,(targetCents/100).toFixed(2));
            }else{selected.delete(itemId);amounts.delete(itemId)}
            draw();
        });
        wrapper.on('input','.af-manual-amount',event=>{
            amounts.set(event.currentTarget.dataset.itemId,event.currentTarget.value);
            validate();
        });
        wrapper.on('click','.af-manual-save',async()=>{
            if(busy||!validate())return;
            busy=true;wrapper.find('.af-manual-save').prop('disabled',true);
            wrapper.find('.af-manual-status').text('正在保存…');
            const itemAmounts=Object.fromEntries([...selected].map(id=>[id,(cents(amounts.get(id))/100).toFixed(2)]));
            let saved=false;
            try{
                const response=await frappe.call({method:'fengjing_app.fengjing_business.page.amazon_finance.amazon_finance.save_logistics_items',args:{transaction_name:name,item_ids:[...selected],item_amounts:itemAmounts,child_key:childKey||undefined}});
                if(childKey){
                    row.manual_child_item_ids=row.manual_child_item_ids||{};
                    row.manual_child_item_amounts=row.manual_child_item_amounts||{};
                    row.manual_child_items=row.manual_child_items||{};
                    row.manual_child_item_ids[childKey]=response.message.manual_item_ids||[];
                    row.manual_child_item_amounts[childKey]=response.message.manual_item_amounts||{};
                    row.manual_child_items[childKey]=response.message.manual_items||[];
                    for(const entry of [...(row.awd_fee_details||[]),...(row.item_lines||[])]){
                        if(entry.manual_key===childKey)entry.manual_items=row.manual_child_items[childKey];
                    }
                }else Object.assign(row,response.message||{});
                const items=childKey?(row.manual_child_items?.[childKey]||[]):row.manual_items;
                this.$.find('.af-manual-select').filter((_,element)=>element.dataset.manualRow===name&&(element.dataset.childKey||'')===childKey)
                    .each((_,element)=>$(element).closest('td').replaceWith(this.manualItemsCell(row,false,childKey,items)));
                saved=true;
                dialog.hide();
                this.refreshSummarySilently();
            }catch(error){
                console.error(error);
                frappe.msgprint({title:'关联物料保存失败',message:error.message||'请稍后重试',indicator:'red'});
            }finally{busy=false;if(!saved)draw()}
        });
        search();
    }
    async refreshSummarySilently(){
        const refreshId=this.summaryRefreshId=(this.summaryRefreshId||0)+1;
        const args={filters:this.filters(),page:this.pageNo,page_size:this.pageSize};
        const signature=JSON.stringify(args);
        try{
            const response=await frappe.call({method:'fengjing_app.fengjing_business.page.amazon_finance.amazon_finance.get_finance_dashboard_data',args});
            if(refreshId!==this.summaryRefreshId||signature!==JSON.stringify({filters:this.filters(),page:this.pageNo,page_size:this.pageSize}))return;
            this.data.detail_summary=response.message?.detail_summary||this.data.detail_summary;
            if(this.summaryOpen)this.renderSummary();
        }catch(error){console.error('汇总明细更新失败',error)}
    }
    awdFeeRow(parent,detail){
        const hidden=this.collapsedGroups.has(parent.name)?' hidden':'';
        const storage=detail.source_type==='Storage';
        const tracking=storage?(parent.amazon_order_id||detail.billing_order_id||detail.asin||'未提供计费编号'):(detail.tracking_id||detail.shipment_id||'未提供货件编号');
        const sourceId=storage&&detail.billing_order_id&&detail.billing_order_id!==parent.amazon_order_id
            ?`报表账单编号：${detail.billing_order_id}`:(parent.amazon_order_id||'—');
        const type=storage?'AWD仓储费':parent.description==='AWDTransportationFee'?'AWD运输费':'AWD处理费';
        const detailNote=storage?`计费体积 ${this.num(detail.billed_volume)} ${this.esc(detail.volume_unit||'')} · ${this.esc(detail.storage_region||'—')}`:`${this.num(detail.box_count)} 箱`;
        return `<tr class="af-awd-child" data-release-parent="${this.esc(parent.name)}"${hidden}>
            <td><b>${this.esc(parent.store||'—')}</b></td>
            <td><div class="af-transaction-id"><span class="af-awd-label">↳ AWD${storage?'仓储':'货件'}</span><b>${this.esc(tracking)}</b></div><small>${this.esc(sourceId)}</small></td>
            <td><b>${this.esc(detail.msku||detail.fnsku||detail.asin||'—')}</b><small>${detailNote}${detail.rate!=null?` · 费率 ${this.esc(detail.rate)} ${this.esc(detail.rate_unit||'')}`:''}</small></td>
            ${this.detailGroup==='logistics'?this.manualItemsCell(parent,false,detail.manual_key,detail.manual_items):''}
            <td><b>${type}</b><small>${storage?'仓储费用拆分':'货件费用拆分'}</small></td>
            <td class="num">—<small>已包含于父行</small></td>
            <td class="num negative">${this.money(detail.amount,'USD')}</td>
            <td>${this.esc(storage?detail.period:(detail.report_date||'—'))}<small>${storage?'费用所属月份':'AWD费用报告'}</small></td>
            <td><span class="af-no-item">${this.esc(detail.batch)}</span><small>源文件第 ${this.num(detail.source_row)} 行</small></td>
            <td>USD</td><td class="num">—</td><td>—</td></tr>`;
    }
    feeIcon(description,type){const v=String(description||type||'');if(/Storage|Warehouse/i.test(v))return '仓';if(/Transportation|Shipment|Inbound/i.test(v))return '运';if(/Ads|Coupon|Promotion/i.test(v))return '推';if(/Subscription/i.test(v))return '订';if(/Transfer|Disbursement/i.test(v))return '款';if(/Adjustment|Reserve/i.test(v))return '调';return '费'}
    feeGroup(description,type){const v=String(description||type||'');if(/Storage|Warehouse/i.test(v))return '仓储及库存费用';if(/Transportation|Shipment|Inbound/i.test(v))return '物流及入库费用';if(/Ads|Coupon|Promotion/i.test(v))return '广告及推广费用';if(/Subscription/i.test(v))return '店铺订阅费用';if(/Transfer|Disbursement/i.test(v))return '平台资金账户';if(/Adjustment|Reserve/i.test(v))return '平台调整款';return '店铺公共费用'}
    bindData(p){return `data-store="${this.esc(p.store||'')}" data-marketplace_id="${this.esc(p.marketplace_id||'')}" data-asin="${this.esc(p.asin||'')}" data-sku="${this.esc(p.sku||'')}" data-transaction_type="${this.esc(p.transaction_type||'')}"`}
    bindItem(d){const x=new frappe.ui.Dialog({title:'绑定到 ERPNext 物料',fields:[{fieldtype:'HTML',fieldname:'x',options:`<div class="af-bind-summary"><b>ASIN：</b>${this.esc(d.asin||'—')}<br><b>${d.transaction_type==='RemovalShipment'?'FNSKU':'SKU'}：</b>${this.esc(d.sku||'—')}<br><b>店铺：</b>${this.esc(d.store||'—')}</div>`},{fieldtype:'Link',fieldname:'item',label:'对应物料',options:'Item',reqd:1}],primary_action_label:'保存并回填历史交易',primary_action:async v=>{await frappe.call({method:'fengjing_app.fengjing_business.page.amazon_finance.amazon_finance.bind_finance_item',freeze:true,freeze_message:'正在建立物料映射…',args:{...d,item:v.item}});x.hide();frappe.show_alert({message:'物料绑定成功',indicator:'green'});this.load()}});x.show()}
    async refreshBindings(){const r=await frappe.call({method:'fengjing_app.fengjing_business.page.amazon_finance.amazon_finance.refresh_finance_item_bindings',freeze:true,freeze_message:'正在重新匹配历史财务交易…'});frappe.msgprint(`已检查 ${r.message.total} 条，更新 ${r.message.updated} 条。`);this.load()}
    translate(v){return ({ATVPDKIKX0DER:'美国站 Amazon.com',A2ZV50J4W1RKNI:'美国站非商城交易',A2EUQ1WTGCTBG2:'加拿大站 Amazon.ca',A1AM78C64UM0Y8:'墨西哥站 Amazon.com.mx',A2Q3Y263D00KWC:'巴西站 Amazon.com.br','United States':'美国',Canada:'加拿大',Mexico:'墨西哥',Brazil:'巴西',Shipment:'发货交易',Refund:'退款交易',ServiceFee:'服务费',Adjustment:'调整交易',DebtRecovery:'欠款回收',FBAInventoryReimbursement:'FBA库存赔偿',ProductAdsPayment:'广告付款',RemovalShipment:'移除货件',Retrocharge:'追溯扣款',Transfer:'资金划转',ShipmentEvent:'发货交易',RefundEvent:'退款交易',ServiceFeeEvent:'服务费',AdjustmentEvent:'调整交易',ChargebackEvent:'拒付交易',RELEASED:'已释放',DEFERRED:'延期释放',DEFERRED_RELEASED:'延期后已释放',Released:'已释放',Pending:'待处理',Deferred:'延期',Amazon:'亚马逊配送',Merchant:'卖家配送','Order Payment':'订单销售回款',Refund:'退款','FBAInboundConvenience':'FBA入库便利服务费','AGSGlobalInboundTransportation':'亚马逊全球入库运输费','AWDProcessingFee':'AWD处理费','AWDTransportationFee':'AWD运输费','FBAStorageBilling':'FBA月度仓储费','STARStorageBilling':'AWD仓储费','FBALongTermStorageBilling':'FBA长期仓储费','FBAPostInboundTransportation':'FBA入库后运输费','FBAShipmentInjectionFulfillmentFee':'FBA货件注入履约费','FBADisposal':'FBA库存销毁费','FulfillmentAdjustment':'履约费用调整','Subscription':'店铺订阅费','ProductAdsPayment':'广告付款','CouponParticipationEvent':'优惠券参与费','CouponPerformanceEvent':'优惠券使用费','LabmanLabelPurchase':'标签购买费','LabmanLabelReturn':'标签退回费','LabmanLabelChargeBack':'标签费用追回','LabmanLabelRefund':'标签费退款','Disbursement':'平台提现划转','DebtPayment':'欠款偿还','Reserve':'平台预留款','Retrocharge':'追溯扣款','RecommerceAfterMarketplaceShipmentItem':'移除货件再销售收入','REVERSAL_REIMBURSEMENT':'赔偿冲销','COMPENSATED_CLAWBACK':'赔偿追回','WAREHOUSE_LOST':'仓库丢失赔偿','AWD_WAREHOUSE_LOST_MANUAL':'AWD仓库丢失人工赔偿','MISSING_FROM_INBOUND':'入库缺失赔偿','FREE_REPLACEMENT_REFUND_ITEMS':'免费换货退款赔偿',SELLABLE:'可售',DEFECTIVE:'有缺陷',CUSTOMER_DAMAGED:'买家损坏',CARRIER_DAMAGED:'承运商损坏',WAREHOUSE_DAMAGED:'仓库损坏'})[v]||v}
    money(v,c){return `${c||''} ${Number(v||0).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}`}
    num(v){return Number(v||0).toLocaleString('zh-CN')}
    date(v){return v?String(v).replace('T',' ').replace(/\.\d+$/,'').slice(0,19):'—'}
    flag(c){return ({USD:'🇺🇸',CAD:'🇨🇦',MXN:'🇲🇽',BRL:'🇧🇷',EUR:'🇪🇺',GBP:'🇬🇧',JPY:'🇯🇵',CNY:'🇨🇳'})[c]||'💱'}
    image(v){return v||'/assets/frappe/images/default-avatar.png'}
    esc(v){return frappe.utils.escape_html(String(v??''))}
}
