frappe.pages['amazon-finance'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({ parent: wrapper, title: 'Amazon 财务交易中心', single_column: true });
    wrapper.amazon_finance = new AmazonFinancePage(page);
};
frappe.pages['amazon-finance'].on_page_show = wrapper => wrapper.amazon_finance?.load();

const AMAZON_JOURNAL_COLUMNS = [
    ['cost_center','成本中心'], ['item','对应物料'], ['account','科目'],
    ['foreign_debit','借方外币'], ['foreign_credit','贷方外币'],
    ['debit','借方（科目货币）'], ['credit','贷方（科目货币）'], ['description','摘要'],
];
const AMAZON_JOURNAL_AMOUNT_COLUMNS = new Set(['foreign_debit','foreign_credit','debit','credit']);
const AMAZON_JOURNAL_SUMMARY_TITLES = {removal:'移除货件',service:'订阅与服务',adjustment:'调整与赔偿'};
const AMAZON_SKU_GROUP_LABELS = {order:'订单',refund:'退款'};
const AMAZON_FUNDS_COLUMNS = [
    ['store','店铺 / 站点'], ['description','交易内容'], ['amount','原币金额'], ['currency','原币币种'],
    ['disbursement_amount','汇出金额'], ['disbursement_currency','汇出币种'],
    ['disbursement_date','汇出日期（北京时间）'], ['settlement_id','交易结算ID'],
];
const AMAZON_FUNDS_AMOUNT_COLUMNS = new Set(['amount','disbursement_amount']);
class AmazonFinancePage {
    constructor(page) {
        this.page=page; this.pageNo=1; this.pageSize=300; this.returnPage=1; this.detailGroup='order'; this.charts=[]; this.loading=false; this.summaryOpen=false; this.collapsedGroups=new Set();
        this.selectedMonths=new Set([this.currentMonthKey()]); this.monthSelectionStarted=false; this.selectedCountry=''; this.summaryCostCenters=new Map(); this.summaryAccounts=new Map();
        this.transactionPeriod='posted';
        this.pendingMonth='';
        this.renderShell(); this.renderMonthButtons(); this.renderCountryButtons(); this.bindEvents();
    }
    renderShell() {
        const {from,to}=this.monthBounds(this.currentMonthKey());
        $(this.page.main).html(`<div class="af-root">
          <section class="af-hero"><div><span>AMAZON FINANCE INTELLIGENCE</span><h1>财务交易中心</h1><p>销售、退款、税费、平台费用与结算批次，穿透至店铺、SKU和ERPNext物料。</p></div><div><button class="btn af-refresh-bind">重新匹配物料</button><button class="btn btn-primary af-refresh">刷新数据</button></div></section>
          <section class="af-filter-panel">
            <div class="af-ranges"><span>记账月份</span><div class="af-month-range af-release-month-range"></div></div>
            <div class="af-ranges af-pending-ranges" hidden><span>原交易月份</span><div class="af-month-range af-pending-month-range" role="group" aria-label="未释放交易的原交易月份"></div></div>
            <div class="af-country-ranges"><span>国家</span><div class="af-country-range"></div></div><div class="af-filters">
            ${this.input('date_from','开始日期（美西）','date',from)}${this.input('date_to','结束日期（美西）','date',to)}${this.select('store','店铺')}${this.select('marketplace_id','站点')}${this.select('currency_code','币种')}${this.select('transaction_type','交易类型')}${this.select('transaction_status','状态')}
            <label>交易归属<select data-filter="scope"><option value="">全部</option><option value="product">商品级交易</option><option value="store">店铺级费用</option></select></label><label>物料绑定<select data-filter="binding"><option value="">全部</option><option value="bound">已绑定</option><option value="unbound">未绑定</option></select></label><label class="af-search-label">搜索<input data-filter="search" placeholder="订单号 / SKU / ASIN / 费用内容 / 物料 / 结算ID"></label></div></section>
          <div class="af-loading"><i></i>正在分析财务交易…</div><main class="af-content"><section class="af-kpis"></section>
          <section class="af-card af-table-card"><header><div><h2>完整交易明细</h2><span class="af-table-count"></span></div><div class="af-table-actions"><a class="af-profit-link" href="https://sellercentral.amazon.com/manage/profit-analytics?" target="_blank" rel="noopener noreferrer">费用分配</a><a class="af-profit-link" href="https://sellercentral.amazon.com/manage/shipments" target="_blank" rel="noopener noreferrer">管理货件</a><a class="af-profit-link" href="https://sellercentral.amazon.com/awd/reports/fee?reportType=AwdOutboundTransportationFee" target="_blank" rel="noopener noreferrer">AWD费用</a><a class="af-profit-link af-return-links" href="https://sellercentral.amazon.com/reportcentral/CUSTOMER_RETURNS/0" target="_blank" rel="noopener noreferrer">退货管理</a><button class="af-summary-toggle" type="button">汇总明细</button><div class="af-pages"></div><button class="af-table-expand" type="button" title="放大交易明细" aria-label="放大交易明细"><svg class="af-expand-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/></svg><svg class="af-compress-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5"/></svg></button></div></header><div class="af-detail-tabs"></div><div class="af-table-wrap"><table><thead></thead><tbody></tbody></table></div><div class="af-summary-overlay" hidden><div class="af-summary-backdrop" data-summary-close></div><aside class="af-summary-drawer" role="dialog" aria-label="汇总明细"><header><div><h3>汇总明细</h3><span class="af-summary-count"></span></div><button type="button" data-summary-close aria-label="关闭汇总明细">×</button></header><p>按交易类型、交易内容和币种汇总当前筛选范围，包含所有分页。</p><div class="af-summary-body"></div></aside></div></section>
          <section class="af-card af-returns-card">
            <header><div><h2>后-FBA客户退货</h2><span class="af-return-count"></span></div></header>
            <p class="af-returns-note">按美西退货时间筛选。入库和移除日期见库存流水。</p><div class="af-return-metrics"></div>
            <div class="af-returns-table-wrap"><table><thead><tr><th>退货时间（美西）</th><th>订单</th><th>商品 / SKU</th><th>商品状态</th><th>处理状态</th><th>数量</th><th>原记录</th></tr></thead><tbody><tr><td colspan="7" class="af-empty">正在加载退货记录…</td></tr></tbody></table></div><footer class="af-return-pages"></footer>
          </section>
          <section class="af-currencies"></section>
          <section class="af-grid af-grid-2"><article class="af-card af-wide"><header><h2>净额与费用趋势</h2><span class="af-trend-basis">按美西释放或入账日期与币种</span></header><div id="af-trend" class="af-chart"></div></article><article class="af-card"><header><h2>费用构成</h2><span>负值代表扣费</span></header><div id="af-fees" class="af-chart"></div></article></section>
          <section class="af-grid af-grid-2"><article class="af-card"><header><h2>交易类型</h2><span>金额与笔数</span></header><div id="af-types" class="af-chart"></div></article><article class="af-card"><header><h2>店铺贡献</h2><span>店铺净额</span></header><div id="af-stores" class="af-chart"></div></article></section>
          <section class="af-grid af-grid-2"><article class="af-card"><header><h2>SKU 与物料表现</h2><span>未绑定商品可直接绑定</span></header><div class="af-products"></div></article><article class="af-card"><header><h2>结算批次</h2><span>最近200个批次</span></header><div class="af-settlements"></div></article></section></main></div>`);
        this.$=$(this.page.main).find('.af-root');
    }
    input(n,l,t,v){return `<label>${l}<input type="${t}" data-filter="${n}" value="${v}"></label>`}
    select(n,l){return `<label>${l}<select data-filter="${n}"><option value="">全部</option></select></label>`}
    currentMonthKey(){const parts=new Intl.DateTimeFormat('en-US',{timeZone:'America/Los_Angeles',year:'numeric',month:'2-digit'}).formatToParts(new Date());return `${parts.find(x=>x.type==='year').value}-${parts.find(x=>x.type==='month').value}`}
    availableMonths(){const months=this.data?.available_months||[];return months.length?months:[this.currentMonthKey()]}
    monthBounds(month){const [year,value]=String(month).split('-').map(Number),lastDay=new Date(year,value,0).getDate();return {from:`${year}-${String(value).padStart(2,'0')}-01`,to:`${year}-${String(value).padStart(2,'0')}-${String(lastDay).padStart(2,'0')}`}}
    renderMonthButtons(){
        const pending=this.transactionPeriod==='pending';
        const months=this.availableMonths().map(month=>{
            const active=!pending&&this.selectedMonths.has(month);
            return `<button type="button" data-month="${this.esc(month)}" class="af-month ${active?'active':''}" aria-pressed="${active}">${Number(month.slice(5))}月</button>`;
        }).join('');
        this.$.find('.af-release-month-range').html(`${months}<button type="button" data-pending-period class="af-month ${pending?'active':''}" aria-pressed="${pending}">未释放</button>`);
        this.$.find('.af-pending-ranges').prop('hidden',!pending);
        if(pending){
            const currentYear=this.currentMonthKey().slice(0,4);
            const monthButton=(month,label)=>{
                const active=this.pendingMonth===month;
                return `<button type="button" data-pending-month="${this.esc(month)}" class="af-month ${active?'active':''}" aria-pressed="${active}">${label}</button>`;
            };
            const months=(this.data?.pending_months||[]).map(month=>{
                const [year,number]=month.split('-');
                const label=`${year===currentYear?'':`${year}年`}${Number(number)}月`;
                return monthButton(month,label);
            });
            this.$.find('.af-pending-month-range').html(
                monthButton('','全部月份')+months.join('')
            );
        }
        this.$.find('[data-filter=date_from], [data-filter=date_to]').prop('disabled',pending);
        this.$.find('[data-filter=transaction_status]').prop('disabled',pending);
    }
    renderCountryButtons(){
        const order=['United States','Canada','Mexico','Brazil'];
        const countries=[...(this.data?.options?.country||[])];
        if(this.selectedCountry&&!countries.includes(this.selectedCountry))countries.push(this.selectedCountry);
        countries.sort((a,b)=>{const ai=order.indexOf(a),bi=order.indexOf(b);return (ai<0?99:ai)-(bi<0?99:bi)||a.localeCompare(b)});
        const buttons=['',...countries].map(country=>`<button type="button" data-country="${this.esc(country)}" class="af-country ${country===this.selectedCountry?'active':''}">${country?`${this.esc(this.translate(country))} ${this.countryFlag(country)}`:'全部 🌐'}</button>`).join('');
        this.$.find('.af-country-range').html(buttons);
    }
    countryFlag(country){return ({'United States':'🇺🇸',Canada:'🇨🇦',Mexico:'🇲🇽',Brazil:'🇧🇷'})[country]||'🌐'}
    selectMonth(month){const months=this.availableMonths();if(!months.includes(month))return;if(this.transactionPeriod==='pending')this.monthSelectionStarted=false;this.transactionPeriod='posted';if(!this.monthSelectionStarted||!this.selectedMonths.size){this.selectedMonths=new Set([month]);this.monthSelectionStarted=true}else if(this.selectedMonths.has(month)){if(this.selectedMonths.size>1)this.selectedMonths.delete(month)}else this.selectedMonths.add(month);const indexes=months.map((value,index)=>this.selectedMonths.has(value)?index:-1).filter(index=>index>=0);if(indexes.length)this.selectedMonths=new Set(months.slice(Math.min(...indexes),Math.max(...indexes)+1));this.applyMonthSelection()}
    applyMonthSelection(){const months=[...this.selectedMonths].sort();if(!months.length)return;const first=this.monthBounds(months[0]),last=this.monthBounds(months[months.length-1]);this.$.find('[data-filter=date_from]').val(first.from);this.$.find('[data-filter=date_to]').val(last.to);this.renderMonthButtons();this.pageNo=1;this.returnPage=1;this.load()}
    selectPendingPeriod(){
        if(this.transactionPeriod==='pending')return;
        this.transactionPeriod='pending';
        this.pendingMonth='';
        this.$.find('[data-filter=transaction_status]').val('');
        this.pageNo=1;
        this.renderMonthButtons();
        this.load();
    }
    selectPendingMonth(month){
        if(this.transactionPeriod!=='pending'||this.pendingMonth===month)return;
        this.pendingMonth=month;
        this.pageNo=1;
        this.renderMonthButtons();
        this.load();
    }
    bindEvents(){
        this.$.on('click','.af-refresh',()=>this.load()).on('click','.af-refresh-bind',()=>this.refreshBindings());
        this.$.on('click','.af-return-links',()=>window.open('https://sellercentral.amazon.com/fba-return','_blank','noopener,noreferrer'));
        this.$.on('click','[data-month]',e=>this.selectMonth(e.currentTarget.dataset.month));
        this.$.on('click','[data-pending-period]',()=>this.selectPendingPeriod());
        this.$.on('click','[data-pending-month]',e=>this.selectPendingMonth(e.currentTarget.dataset.pendingMonth));
        this.$.on('click','[data-country]',e=>{const country=e.currentTarget.dataset.country||'';if(country===this.selectedCountry)return;this.selectedCountry=country;this.pageNo=1;this.returnPage=1;this.renderCountryButtons();this.load()});
        let timer;this.$.on('change','[data-filter]:not([data-filter=search])',e=>{if(['date_from','date_to'].includes(e.currentTarget.dataset.filter)){this.selectedMonths.clear();this.monthSelectionStarted=false;this.renderMonthButtons()}this.pageNo=1;this.returnPage=1;this.load()}).on('input','[data-filter=search]',()=>{clearTimeout(timer);timer=setTimeout(()=>{this.pageNo=1;this.returnPage=1;this.load()},400)});
        this.$.on('click','[data-page]',e=>{this.pageNo=+e.currentTarget.dataset.page;this.load()}).on('click','.af-bind',e=>this.bindItem($(e.currentTarget).data())).on('click','[data-copy]',e=>{frappe.utils.copy_to_clipboard(String($(e.currentTarget).data('copy')||''));frappe.show_alert({message:'已复制',indicator:'green'})});
        this.$.on('click','[data-return-page]',e=>{this.returnPage=+e.currentTarget.dataset.returnPage;this.loadReturnCard()});
        this.$.on('click','[data-detail-group]',e=>{
            const group=e.currentTarget.dataset.detailGroup;
            if(!group||group===this.detailGroup)return;
            this.detailGroup=group;
            this.pageNo=1;
            this.renderMonthButtons();
            this.load();
        });
        this.$.on('click','.af-manual-select',e=>this.openManualItems(e.currentTarget.dataset.manualRow,e.currentTarget.dataset.childKey||''));
        this.$.on('click','.af-release-toggle',e=>{const key=e.currentTarget.dataset.releaseToggle;if(this.collapsedGroups.has(key))this.collapsedGroups.delete(key);else this.collapsedGroups.add(key);this.renderTable()});
        this.$.on('click','.af-summary-toggle',()=>this.toggleSummary()).on('click','[data-summary-close]',()=>this.toggleSummary(false));
        this.$.on('click','[data-order-summary-retry]',()=>{
            this.orderSummary=null;
            this.renderSummary();
        });
        this.$.on('click','[data-summary-copy]',e=>this.copyLogisticsSummaryColumn(e.currentTarget.dataset.summaryCopy));
        this.$.on('click','[data-journal-summary-copy]',e=>{
            const {journalSummaryCopy,journalSummaryGroup}=e.currentTarget.dataset;
            this.copyPlatformJournalColumn(journalSummaryCopy,Number(journalSummaryGroup));
        });
        this.$.on('click','[data-funds-summary-copy]',e=>this.copyFundsSummaryColumn(e.currentTarget.dataset.fundsSummaryCopy));
        this.$.on('click','[data-order-sku-copy]',e=>this.copyOrderSkuColumn(e.currentTarget.dataset.orderSkuCopy));
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
    filters(){
        const r={period:this.transactionPeriod};
        this.$.find('[data-filter]').each((_,e)=>{if(e.value&&!e.disabled)r[e.dataset.filter]=e.value});
        if(this.selectedCountry)r.country=this.selectedCountry;
        if(this.detailGroup)r.detail_group=this.detailGroup;
        if(this.transactionPeriod==='pending'&&this.pendingMonth)r.pending_month=this.pendingMonth;
        return r;
    }
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
    copyPlatformJournalColumn(column,groupIndex){
        const label=AMAZON_JOURNAL_COLUMNS.find(([key])=>key===column)?.[1];
        const rows=this.data?.journal_summary?.[groupIndex]?.rows||[];
        if(!label||!rows.length)return;
        const values=rows.map(row=>{
            if(AMAZON_JOURNAL_AMOUNT_COLUMNS.has(column))return Number(row[column]||0).toFixed(2);
            const value=String(row[column]||'');
            return column==='description'?value.replace(/[;；]/g,'，'):value;
        });
        frappe.utils.copy_to_clipboard(values.join(column==='description'?';':'\n'));
        frappe.show_alert({message:`已复制${label}整列，共 ${rows.length} 行`,indicator:'green'});
    }
    renderPlatformJournalSummary(){
        const groups=this.data.journal_summary||[];
        const removal=this.detailGroup==='removal';
        const postedRemoval=removal&&this.transactionPeriod==='posted';
        const rowCount=groups.reduce((total,group)=>total+group.rows.length,0);
        const removalCount=groups.reduce((total,group)=>total+(group.transaction_count||0),0);
        this.$.find('.af-summary-drawer h3').text(`${AMAZON_JOURNAL_SUMMARY_TITLES[this.detailGroup]}汇总明细`);
        this.$.find('.af-summary-count').text(`${removal?`${this.num(removalCount)} 笔移除 · `:''}${this.num(rowCount)} 行日记账分录`);
        this.$.find('.af-summary-drawer>p').text(postedRemoval
            ?'按原财务时间汇总。清算净额计入含冻结款的平台余额，点击列头复制。'
            :removal?'点击列头复制。期末待释放款保留原币净额，对方科目按实际记账情况选择。'
            :'点击列头复制。外币交易的人民币科目用外币辅助列换算，余额科目直接填科目货币金额。');
        const html=groups.map((group,index)=>{
            const headers=AMAZON_JOURNAL_COLUMNS.map(([key,label])=>{
                const heading=removal&&key==='item'?'SKU / 物料':label;
                return `<th scope="col"><button type="button" class="af-journal-copy" data-journal-summary-copy="${key}" data-journal-summary-group="${index}" title="点击复制${label}整列">${heading}${removal?'':' ⧉'}</button></th>`;
            }).join('');
            const rows=group.rows.map(row=>`<tr>${AMAZON_JOURNAL_COLUMNS.map(([key])=>{
                if(AMAZON_JOURNAL_AMOUNT_COLUMNS.has(key))return `<td class="num">${row[key]?this.esc(this.money(row[key],group.currency)):'—'}</td>`;
                if(removal&&key==='item'&&(row.item||row.sku))return `<td>${this.summarySkuItem({...row,item_id:row.item})}</td>`;
                const value=this.esc(row[key]||'');
                const cellClass=removal?{cost_center:'af-order-sku-cost-center',account:'af-order-sku-fee-account'}[key]:null;
                return `<td${cellClass?` class="${cellClass}"`:''} title="${value}">${value||'—'}</td>`;
            }).join('')}</tr>`).join('');
            const company=this.esc(group.currency)+(groups.length>1&&group.company?` · ${this.esc(group.company)}`:'');
            const heading=removal?this.renderRemovalSummaryTotals(group,groups.length>1?company:'')
                :`<header><strong>${company}</strong><span>平台余额变动 ${this.esc(this.money(group.platform_net,group.currency))}</span></header>`;
            const tableClass=removal?'af-order-table af-order-sku-grid af-removal-journal-grid':'af-summary-table-wrap';
            return `<section class="${removal?'af-removal-journal-summary':'af-journal-summary'}">
                ${heading}
                ${group.rows.some(row=>!row.account)?'<p class="af-summary-balance is-mismatched">部分交易的科目需在粘贴后补齐。</p>':''}
                <div class="${tableClass}"><table aria-label="${AMAZON_JOURNAL_SUMMARY_TITLES[this.detailGroup]}汇总明细"><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table></div>
            </section>`;
        }).join('');
        this.$.find('.af-summary-body').html(html||'<div class="af-empty">当前范围内没有可汇总的交易</div>');
    }
    renderRemovalSummaryTotals(group,company){
        const totals=group.liquidation_totals||{};
        const cards=[['清算回收额',totals.gross_recovery],['清算处理费',totals.processing_fee],
            ['清算佣金',totals.referral_fee],['总净额',group.platform_net]];
        return `${company?`<h4>${company}</h4>`:''}<div class="af-summary-totals af-order-sku-totals" aria-label="移除货件汇总合计">
            ${cards.map(([label,amount])=>`<article><span>${label}</span><strong>${amount==null?'—':this.esc(this.money(amount,group.currency))}</strong></article>`).join('')}
            </div>${group.transactions_without_breakdown?`<p>其中 ${this.num(group.transactions_without_breakdown)} 笔只计入总净额。</p>`:''}`;
    }
    copyFundsSummaryColumn(column){
        const label=AMAZON_FUNDS_COLUMNS.find(([key])=>key===column)?.[1];
        const rows=this.data?.funds_summary?.rows||[];
        if(!label||!rows.length)return;
        const values=rows.map(row=>{
            const value=row[column];
            if(value==null)return '';
            if(column==='description')return this.translate(value);
            if(AMAZON_FUNDS_AMOUNT_COLUMNS.has(column))return Number(value).toFixed(2);
            return String(value);
        });
        frappe.utils.copy_to_clipboard(values.join('\n'));
        frappe.show_alert({message:`已复制${label}整列，共 ${rows.length} 行`,indicator:'green'});
    }
    renderFundsSummary(){
        const summary=this.data.funds_summary||{},rows=summary.rows||[];
        this.$.find('.af-summary-drawer h3').text('资金划转汇总明细');
        this.$.find('.af-summary-count').text(`共 ${this.num(rows.length)} 笔资金划转`);
        this.$.find('.af-summary-drawer>p').text('汇出金额和日期取自亚马逊结算批次，点击任一列头复制整列。');
        const formatTotals=totals=>(totals||[]).map(total=>this.esc(this.money(total.amount,total.currency))).join(' · ');
        const originalTotals=formatTotals(summary.original_totals),totals=formatTotals(summary.totals);
        const heads=AMAZON_FUNDS_COLUMNS.map(([key,label])=>`<th scope="col"><button type="button" class="af-journal-copy" data-funds-summary-copy="${key}" title="点击复制${label}整列">${label} ⧉</button></th>`).join('');
        const body=rows.map(row=>`<tr>${AMAZON_FUNDS_COLUMNS.map(([key])=>{
            const value=row[key];
            if(AMAZON_FUNDS_AMOUNT_COLUMNS.has(key))return `<td class="num">${value==null?'—':this.esc(this.money(value,'').trim())}</td>`;
            return `<td>${this.esc(key==='description'?this.translate(value):value??'—')||'—'}</td>`;
        }).join('')}</tr>`).join('');
        this.$.find('.af-summary-body').html(body?`<section class="af-journal-summary">
            ${originalTotals?`<header><span>原币金额合计 ${originalTotals}</span>${totals?`<span>汇出金额合计 ${totals}</span>`:''}</header>`:''}
            ${summary.unmatched?`<p class="af-summary-balance is-mismatched">${this.num(summary.unmatched)} 笔提现的汇出信息待匹配。</p>`:''}
            <div class="af-summary-table-wrap"><table><thead><tr>${heads}</tr></thead><tbody>${body}</tbody></table></div>
        </section>`:'<div class="af-empty">当前范围内没有资金划转</div>');
    }
    async load(){
        if(this.loading){
            this.reloadPending=true;
            return;
        }
        this.loading=true;
        this.$.addClass('is-loading');
        try{
            const r=await frappe.call({
                method:'fengjing_app.fengjing_business.page.amazon_finance.amazon_finance.get_finance_dashboard_data',
                args:{filters:this.filters(),page:this.pageNo,page_size:this.pageSize},
            });
            if(this.reloadPending)return;
            this.data=r.message||{};
            this.orderSummary=null;
            if(this.transactionPeriod==='pending'&&this.pendingMonth
                &&!(this.data.pending_months||[]).includes(this.pendingMonth)){
                this.pendingMonth='';
                this.pageNo=1;
                this.reloadPending=true;
                return;
            }
            const groups=(this.data.detail_groups||[]).filter(group=>group.key);
            if(groups.length&&!groups.some(group=>group.key===this.detailGroup)){
                this.detailGroup=groups[0].key;
                this.pageNo=1;
                this.reloadPending=true;
                return;
            }
            this.render();
        }catch(e){
            console.error(e);
            frappe.msgprint({title:'财务页面加载失败',message:e.message||'请检查后台日志',indicator:'red'});
        }finally{
            this.loading=false;
            this.$.removeClass('is-loading');
            if(this.reloadPending){
                this.reloadPending=false;
                this.load();
            }
        }
    }
    render(){
        const pending=this.transactionPeriod==='pending';
        const basis=pending?'按美西原交易日期与币种':'按美西记账日期与币种';
        this.$.find('.af-trend-basis').text(basis);
        this.renderMonthButtons();this.renderCountryButtons();this.fillOptions();this.renderKpis();this.renderCurrencies();this.renderCharts();this.renderProducts();this.renderSettlements();this.renderTable();
        this.$.find('.af-returns-card').prop('hidden',pending);
        if(pending)this.returnRequestId=(this.returnRequestId||0)+1;
        else this.loadReturnCard();
        if(this.summaryOpen)this.renderSummary();
    }
    fillOptions(){['store','marketplace_id','currency_code','transaction_type','transaction_status'].forEach(k=>{const e=this.$.find(`[data-filter=${k}]`),v=e.val(),values=[...(this.data.options?.[k]||[])];if(v&&!values.includes(v))values.unshift(v);const o=values.map(x=>`<option value="${this.esc(x)}">${this.esc(this.translate(x))}</option>`).join('');e.html(`<option value="">全部</option>${o}`).val(v||'')})}
    renderKpis(){const s=this.data.summary||{},a=[[this.transactionPeriod==='pending'?'未释放交易明细':'本期交易明细',s.report_row_count??s.row_count,`${this.num(s.row_count)} 组原交易，按报告商品行口径`],['关联释放',s.release_count,'随原交易展示，不重复计入金额'],['商品级交易',s.product_count,'带SKU、ASIN或订单商品ID'],['店铺级费用',s.store_fee_count,'仓储、订阅、运输等'],['已绑定物料',s.bound_count,`${s.binding_rate||0}% 商品交易已识别`],['未绑定商品',s.unbound_count,'仅商品交易需要绑定']];this.$.find('.af-kpis').html(a.map((x,i)=>`<article class="af-kpi c${i}"><span>${x[0]}</span><strong>${this.num(x[1])}</strong><small>${x[2]}</small></article>`).join('')+(s.truncated?'<div class="af-warning">数据达到10万行，请缩小日期范围。</div>':''))}
    renderCurrencies(){const a=Object.entries(this.data.summary?.currencies||{});this.$.find('.af-currencies').html(`<div class="af-section-title"><h2>${this.transactionPeriod==='pending'?'多币种待释放金额':'多币种交易总览'}</h2><span>${this.transactionPeriod==='pending'?'按原交易归集待释放金额。':'资金释放明细随主行展示，金额只计一次。'}</span></div><div class="af-currency-grid">${a.map(([c,v])=>`<article class="af-currency"><div class="af-currency-head"><span>${this.flag(c)}</span><strong>${c}</strong></div><div class="af-net ${v.net>=0?'positive':'negative'}">${this.money(v.net,c)}</div><div class="af-money-grid"><span>流入<b>${this.money(v.inflow,c)}</b></span><span>流出<b>${this.money(v.outflow,c)}</b></span><span>销售<b>${this.money(v.sales,c)}</b></span><span>平台费用<b>${this.money(v.fees,c)}</b></span></div></article>`).join('')||'<div class="af-empty">暂无金额数据</div>'}</div>`)}
    async renderCharts(){if(typeof echarts==='undefined'){await this.loadEcharts();return this.renderCharts()}this.charts.forEach(c=>c?.dispose());this.charts=[];const d=this.data.daily||{},dates=Object.keys(d),cs=[...new Set(dates.flatMap(x=>Object.keys(d[x])))];this.chart('af-trend',{tooltip:{trigger:'axis',appendToBody:true},legend:{top:4},grid:{left:65,right:20,top:50,bottom:50},xAxis:{type:'category',data:dates},yAxis:{type:'value'},dataZoom:[{type:'inside'},{type:'slider',height:17,bottom:8}],series:cs.map(c=>({name:`${c} 净额`,type:'line',smooth:true,showSymbol:false,data:dates.map(x=>d[x][c]?.net||0),lineStyle:{width:3},areaStyle:{opacity:.06}}))});const c=this.$.find('[data-filter=currency_code]').val()||Object.keys(this.data.fees||{})[0],fees=this.data.fees?.[c]||{};this.chart('af-fees',{tooltip:{trigger:'item',appendToBody:true},legend:{bottom:0,type:'scroll'},series:[{type:'pie',radius:['40%','68%'],center:['50%','43%'],data:Object.entries(fees).map(([name,value])=>({name,value:Math.abs(value)}))}]});this.bar('af-types',this.data.types?.[c]||{},x=>x.amount,'#377cf5');this.bar('af-stores',this.data.stores?.[c]||{},x=>x.amount,'#16bba4')}
    bar(id,obj,value,color){const a=Object.entries(obj).sort((x,y)=>Math.abs(value(y[1]))-Math.abs(value(x[1]))).slice(0,15);this.chart(id,{tooltip:{trigger:'axis',appendToBody:true},grid:{left:165,right:25,top:15,bottom:25},xAxis:{type:'value'},yAxis:{type:'category',data:a.map(x=>this.translate(x[0])),axisLabel:{width:150,overflow:'truncate'}},series:[{type:'bar',data:a.map(x=>value(x[1])),itemStyle:{borderRadius:[0,7,7,0],color}}]})}
    chart(id,o){const e=this.$.find('#'+id)[0];if(e){const c=echarts.init(e);c.setOption(o);this.charts.push(c)}}
    loadEcharts(){if(!this.echartsPromise)this.echartsPromise=new Promise((ok,bad)=>frappe.require('/assets/fengjing_app/js/图表-echarts.js',ok,bad));return this.echartsPromise}
    renderProducts(){this.$.find('.af-products').html(`<div class="af-scroll-list">${(this.data.products||[]).slice(0,100).map((p,i)=>`<div class="af-product"><span class="af-rank">${i+1}</span><img src="${this.image(p.image)}"><div class="af-product-main"><b>${this.esc(p.item_name||p.product_name||p.sku||'未命名商品')}</b><small><button data-copy="${this.esc(p.asin||'')}">${this.esc(p.asin||'无ASIN')}</button> · <button data-copy="${this.esc(p.sku||'')}">${this.esc(p.sku||'无SKU')}</button></small><small>${this.esc(p.store||'未归属')} · 数量 ${this.num(p.quantity)} · ${p.transactions}条</small></div><div class="af-product-money">${Object.entries(p.amounts||{}).map(([c,v])=>`<b class="${v>=0?'positive':'negative'}">${this.money(v,c)}</b>`).join('')}</div>${p.item?`<a class="af-bound" href="/app/item/${encodeURIComponent(p.item)}">${this.esc(p.item)}</a>`:`<button class="btn btn-xs btn-primary af-bind" ${this.bindData(p)}>绑定物料</button>`}</div>`).join('')||'<div class="af-empty">暂无商品交易</div>'}</div>`)}
    renderSettlements(){this.$.find('.af-settlements').html(`<div class="af-scroll-list">${(this.data.settlements||[]).map(s=>`<div class="af-settlement"><i>结</i><div><b>${this.esc(s.settlement_id)}</b><small>${this.esc(s.store||'未归属')} · ${s.count}条 · ${this.date(s.last_date)}</small></div><strong class="${s.amount>=0?'positive':'negative'}">${this.money(s.amount,s.currency)}</strong></div>`).join('')||'<div class="af-empty">暂无结算批次</div>'}</div>`)}
    renderSummary(){
        const pending=this.transactionPeriod==='pending';
        const skuGroup=AMAZON_SKU_GROUP_LABELS[this.detailGroup];
        this.$.find('.af-summary-drawer').toggleClass('af-order-summary-drawer',Boolean(skuGroup)||this.detailGroup==='removal');
        if(this.detailGroup==='funds')return this.renderFundsSummary();
        if(!pending&&Object.hasOwn(AMAZON_JOURNAL_SUMMARY_TITLES,this.detailGroup))return this.renderPlatformJournalSummary();
        this.$.find('.af-summary-drawer h3').text(skuGroup?`${skuGroup}汇总明细`:pending?'未释放汇总明细':'汇总明细');
        if(skuGroup)return this.renderOrderSummary();
        const summary=this.data?.detail_summary||{},totals=summary.currencies||[],rows=summary.rows||[];
        const logistics=this.detailGroup==='logistics';
        const balance=logistics?this.summaryBalance():null;
        const balanceHtml=logistics?`<div class="af-summary-balance ${balance.ok?'is-matched':'is-mismatched'}">${balance.ok?'✓ 原交易净额与汇总行净额一致（按币种及正负号核对）':`⚠ 金额核对未通过，已暂停整列复制。${balance.issues.map(issue=>`<span>${this.esc(issue)}</span>`).join('')}`}</div>${balance.hasCredits?'<div class="af-summary-balance is-mismatched">含正向交易；净额(借)显示绝对值，不能将整列直接记入借方。</div>':''}`:'';
        this.$.find('.af-summary-count').text(`共 ${this.num(summary.count)} 组原交易 · ${this.num(rows.length)} 个汇总分类`);
        this.$.find('.af-summary-drawer>p').text(pending?'按原交易汇总待释放金额，包含所有分页。':logistics?'按店铺、站点、SKU、交易内容和货件汇总当前筛选范围，包含所有分页。':'按 SKU、交易类型、交易内容和币种汇总当前筛选范围，包含所有分页。');
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
        const group=AMAZON_SKU_GROUP_LABELS[this.detailGroup];
        const body=this.$.find('.af-summary-body');
        this.$.find('.af-summary-drawer>p').text('点击列头复制。');
        if(this.orderSummary?.key===key){
            const current=this.orderSummary;
            if(current.data)return this.renderOrderSkuSummary(current.data);
            if(current.error){
                this.$.find('.af-summary-count').text('加载失败');
                body.html(`<div class="af-empty">${group}汇总读取失败。<button class="btn btn-default btn-xs" type="button" data-order-summary-retry>重试</button></div>`);
                return;
            }
        }
        this.$.find('.af-summary-count').text(`正在汇总${group}…`);
        body.html('<div class="af-empty" role="status">正在读取财务明细…</div>');
        if(this.orderSummary?.key===key)return;
        const request=this.orderSummary={key};
        try{
            const response=await frappe.call({
                method:'fengjing_app.fengjing_business.page.amazon_finance.amazon_finance.get_finance_dashboard_data',
                args:{filters,order_sku_summary:1},
            });
            if(!Array.isArray(response.message?.rows))throw new Error(`${group}汇总数据格式错误`);
            request.data=response.message;
        }catch(error){
            console.error(`${group}汇总加载失败`,error);
            request.error=true;
        }
        if(this.orderSummary===request&&this.summaryOpen
            &&JSON.stringify(this.filters())===key)this.renderOrderSummary();
    }
    orderSkuCopyButton(key,label,text=label){
        const title=this.esc(`复制整列${label}`);
        return `<button type="button" data-order-sku-copy="${this.esc(key)}" title="${title}">${this.esc(text)}</button>`;
    }
    orderSkuColumnAmount(entry,key){
        const separator=key.indexOf(':');
        if(separator<0)return entry[key]??0;
        return entry[key.slice(0,separator)]?.[key.slice(separator+1)]??0;
    }
    orderSkuSummaryRows(data){
        return data.rows.flatMap(group=>{
            const items=new Map(group.items.map(item=>[item.name,item]));
            return group.price_lines.map(line=>({...line,sku:group.sku,item:items.get(line.item_id)}));
        });
    }
    orderSkuDetails(row,data){
        const accounts=data.fee_accounts?.[row.cost_center]||{};
        return [
            ...Object.entries(row.fees).map(([key,amount])=>[`fees:${key}`,data.fee_labels[key]||key,amount]),
            ['shipping',data.shipping_label||'配送收入',row.shipping],
            ...Object.entries(row.adjustments).map(([key,amount])=>[`adjustments:${key}`,data.adjustment_labels[key]||key,amount]),
        ].filter(([, ,amount])=>amount).map(([key,label,amount])=>[key,label,amount,accounts[key]||'']);
    }
    copyOrderSkuColumn(key){
        const data=this.orderSummary?.data;
        if(!AMAZON_SKU_GROUP_LABELS[this.detailGroup]||!data?.rows?.length||this.orderSummary.key!==JSON.stringify(this.filters()))return;
        const labels={cost_center:'成本中心',detail_cost_center:'明细成本中心',item_id:'物料 ID',
            detail_item_id:'明细物料 ID',quantity:'数量',unit_price:'单价',sales:'销售额',shipping:data.shipping_label||'配送收入',
            charge_type:'税费类型',fee_account:'费用科目',detail_amount:'费用',details:'明细',net:'净额'};
        const separator=key.indexOf(':');
        const group=key.slice(0,separator),name=key.slice(separator+1);
        const label=separator<0?labels[key]:(group==='fees'?data.fee_labels:data.adjustment_labels)?.[name]||name;
        const rows=this.orderSkuSummaryRows(data);
        const multipleCurrencies=new Set(rows.map(row=>row.currency)).size>1;
        const formatAmount=(row,amount)=>{
            const value=Number(amount).toFixed(2);
            return multipleCurrencies?`${row.currency} ${value}`:value;
        };
        const values=rows.flatMap(row=>{
            if(['charge_type','fee_account','detail_amount','detail_item_id','detail_cost_center','details'].includes(key)){
                return this.orderSkuDetails(row,data).map(([,name,amount,account])=>{
                    if(key==='charge_type')return 'Actual';
                    if(key==='fee_account')return account;
                    if(key==='detail_item_id')return row.item_id;
                    if(key==='detail_cost_center')return row.cost_center;
                    if(key==='details')return name.replace(/[;；]/g,'，');
                    return formatAmount(row,amount);
                });
            }
            if(['cost_center','item_id','quantity'].includes(key))return String(row[key]??'');
            if(key==='unit_price'&&row.unit_price==null)return '';
            return formatAmount(row,this.orderSkuColumnAmount(row,key));
        });
        if(!values.length)return;
        frappe.utils.copy_to_clipboard(values.join(key==='details'?';':'\n'));
        frappe.show_alert({message:`已复制${label} · ${values.length} 行`,indicator:'green'});
    }
    renderOrderSkuTotals(lines,data){
        let quantity=0;
        const amounts=new Map();
        const cents=value=>Math.round(Number(value)*100);
        for(const line of lines){
            quantity+=line.quantity;
            const total=amounts.get(line.currency)||{sales:0,fees:0,net:0};
            total.sales+=cents(line.sales);
            for(const [,,amount] of this.orderSkuDetails(line,data))total.fees+=cents(amount);
            total.net+=cents(line.net);
            amounts.set(line.currency,total);
        }
        const currencies=[...amounts].sort(([left],[right])=>left.localeCompare(right));
        const totals=key=>currencies.map(([currency,total])=>`<strong>${this.esc(this.money(total[key]/100,currency))}</strong>`).join('')||'<strong>—</strong>';
        return `<div class="af-summary-totals af-order-sku-totals" aria-label="订单汇总合计">
            <article><span>总数量</span><strong>${this.num(quantity)} 件</strong></article>
            <article><span>总销售额</span>${totals('sales')}</article>
            <article><span title="下方费用列的整列合计">总费用</span>${totals('fees')}</article>
            <article><span>总净额</span>${totals('net')}</article>
        </div>`;
    }
    renderOrderSkuSummary(data){
        const lines=this.orderSkuSummaryRows(data);
        const group=AMAZON_SKU_GROUP_LABELS[this.detailGroup];
        this.$.find('.af-summary-count').text(`${this.num(data.rows.length)} 个 SKU · ${this.num(lines.length)} 行`);
        const columns=[['cost_center','成本中心'],['item_id','SKU / 物料','物料 ID'],['quantity','数量'],
            ['unit_price','单价'],['sales','销售额'],['detail_cost_center','成本中心','明细成本中心'],
            ['detail_item_id','物料','明细物料 ID'],['charge_type','税费类型'],['fee_account','费用科目'],
            ['detail_amount','费用'],['details','明细'],['net','净额']];
        const headers=columns.map(([key,label,copyLabel])=>
            `<th scope="col">${this.orderSkuCopyButton(key,copyLabel||label,label)}</th>`).join('');
        const rows=lines.map(row=>{
            const money=key=>this.esc(this.money(this.orderSkuColumnAmount(row,key),row.currency));
            const details=this.orderSkuDetails(row,data);
            const span=Math.max(1,details.length);
            const costCenter=this.esc(row.cost_center||'—');
            const itemId=this.esc(row.item_id||'—');
            const [first,...rest]=details.map(([key,label,amount,account])=>
                `<td class="af-order-sku-cost-center">${costCenter}</td><td>${itemId}</td><td>实际</td>
                <td class="af-order-sku-fee-account">${this.esc(account||'—')}</td>
                <td>${this.esc(this.money(amount,row.currency))}</td><td>${this.orderSkuCopyButton(key,label)}</td>`);
            return `<tbody><tr><td rowspan="${span}" class="af-order-sku-cost-center">${costCenter}</td>
                <th scope="rowgroup" rowspan="${span}">${this.summarySkuItem(row,'拆分行')}</th>
                <td rowspan="${span}">${this.num(row.quantity)}</td>
                <td rowspan="${span}">${row.unit_price==null?'—':money('unit_price')}</td><td rowspan="${span}">${money('sales')}</td>
                ${first||'<td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>'}<td rowspan="${span}">${money('net')}</td></tr>
                ${rest.map(cells=>`<tr>${cells}</tr>`).join('')}</tbody>`;
        }).join('');
        this.$.find('.af-summary-body').html(`
            ${data.truncated?'<p class="af-summary-balance is-mismatched">财务记录达到查询上限，请缩小日期范围。</p>':''}
            ${this.renderOrderSkuTotals(lines,data)}
            ${lines.length?`<div class="af-order-table af-order-sku-grid"><table aria-label="${group}汇总明细">
                <thead><tr>${headers}</tr></thead>${rows}</table></div>`:
                `<div class="af-empty">当前范围内没有${group}</div>`}`);
    }
    summarySkuItem(row,adjustmentLabel='调整行'){
        const image=row.item?.image||row.item_image;
        const name=row.item_id?`<a href="/app/item/${encodeURIComponent(row.item_id)}" target="_blank"
            rel="noopener noreferrer">${this.esc(row.item?.label||row.item_name||row.item_id)}</a>`
            :`<small>${this.esc(row.productName||row.product_name||'未关联物料')}</small>`;
        return `<div class="af-order-sku-item">
            ${image?`<img src="${this.esc(this.image(image))}" alt="" loading="lazy">`:''}
            <div><b>${this.esc(row.sku||'无 SKU')}</b>
            ${row.adjustment?`<span class="af-order-sku-adjustment">${this.esc(adjustmentLabel)}</span>`:''}
            ${name}</div>
        </div>`;
    }
    async loadReturnCard(){
        const requestId=this.returnRequestId=(this.returnRequestId||0)+1;
        const filters=this.filters();
        this.$.find('.af-return-count, .af-return-metrics, .af-return-pages').empty();
        const tableBody=this.$.find('.af-returns-table-wrap > table > tbody');
        tableBody.html('<tr><td colspan="7" class="af-empty">正在加载退货记录…</td></tr>');
        try{
            const response=await frappe.call({method:'fengjing_app.fengjing_business.page.amazon_fba_return_analysis.amazon_fba_return_analysis.get_finance_return_data',args:{date_from:filters.date_from,date_to:filters.date_to,search:filters.search,page:this.returnPage,page_size:20,date_basis:'return'}});
            if(requestId!==this.returnRequestId)return;
            this.returnData=response.message||{};
            this.returnPage=this.returnData.pagination?.page||1;
            this.renderReturnCard();
        }catch(error){
            if(requestId!==this.returnRequestId)return;
            console.error(error);
            tableBody.html('<tr><td colspan="7" class="af-empty">退货记录加载失败，请稍后刷新。</td></tr>');
        }
    }
    renderReturnInventory(row){
        const events=row.inventory_events||[];
        const labels={reference:'按参考编号匹配',conditions:'按条件匹配',review:'候选 · 待核对'};
        const details=events.length?`<table><thead><tr><th>分类账日期</th><th>事件</th><th>配送中心</th><th>库存属性</th>
            <th>数量</th><th>匹配结果</th><th>参考编号</th><th>原流水</th></tr></thead><tbody>${events.map(event=>`<tr>
                <td>${this.esc(event.date)}${event.cross_month?'<span class="af-return-cross-month">跨月</span>':''}</td>
                <td>${event.event_type==='CustomerReturns'?'退货入库':'移除出库'}</td>
                <td>${this.esc(event.fulfillment_center||'—')}</td>
                <td>${this.esc(this.translate(event.disposition||'—'))}</td>
                <td class="num">${Number(event.quantity)>0?'+':''}${this.num(event.quantity)}</td>
                <td${event.match==='conditions'?' title="按 SKU、配送中心、库存属性、数量和日期匹配"':''}>${this.esc(labels[event.match]||'待核对')}</td>
                <td>${this.esc(event.reference_id||'—')}</td>
                <td><a href="/app/amazon-fba-inventory-ledger-detail/${encodeURIComponent(event.name)}">查看 ↗</a></td></tr>`).join('')}</tbody></table>`
            :'<p>当前没有匹配到库存分类账流水。</p>';
        return `<tr class="af-return-ledger-row"><td colspan="7"><div class="af-return-ledger">${details}</div></td></tr>`;
    }
    renderReturnCard(){
        const data=this.returnData||{},summary=data.summary||{},pagination=data.pagination||{};
        this.$.find('.af-return-count').text(`${this.num(summary.records)} 条记录 · ${this.num(summary.orders)} 个订单 · ${this.num(summary.units)} 件退货`);
        const metrics=[['退货件数',summary.units],['可售',summary.sellable],['不可售',summary.unsellable],
            ['已退回库存',summary.returned_to_inventory],['立即清算',summary.liquidation]];
        this.$.find('.af-return-metrics').html(metrics.map(([label,value])=>`<div><strong>${this.num(value)}</strong><span>${label}</span></div>`).join('')+(data.truncated?'<p>退货原始记录超过处理上限，请缩小日期范围。</p>':''));
        this.$.find('.af-returns-table-wrap > table > tbody').html((data.rows||[]).map(row=>{
            const status=({IMMEDIATE_LIQUIDATION:'立即清算',IMMEDIATE_DISPOSAL:'立即处置','Unit returned to inventory':'已退回库存'})[row.status]||row.status||'未提供';
            const condition=row.detailed_disposition?this.translate(row.detailed_disposition):'未判定';
            const orderMonth=row.order_month
                ?`原订单：${this.esc(row.order_month.slice(0,4))}年${this.esc(String(Number(row.order_month.slice(5))))}月`
                :'原订单月份待核实';
            const itemName=this.esc(row.item_name||row.product_name||row.seller_sku||'未命名商品');
            return `<tr><td>${this.date(row.report_date)}<small>${this.esc(row.report_timezone||'')}</small></td>
                <td>${this.esc(row.order_id||'—')}<small class="af-order-month">${orderMonth}</small></td>
                <td><div class="af-return-product"><img src="${this.esc(this.image(row.item_image))}" alt=""><span>
                    <b title="${itemName}">${itemName}</b><small>${this.esc(row.seller_sku||'无 SKU')} · ${this.esc(row.asin||'无 ASIN')}
                    ${row.item_id?` · ${this.esc(row.item_id)}`:''}</small></span></div></td>
                <td><span class="af-return-condition ${row.detailed_disposition==='SELLABLE'?'sellable':row.detailed_disposition?'unsellable':'unknown'}">${this.esc(condition)}</span>
                    <small>${this.esc(this.translate(row.reason||'—'))}</small></td>
                <td>${this.esc(status)}<small>${this.esc(row.fulfillment_center_id||'—')}</small></td>
                <td class="num">${this.num(row.quantity)}</td>
                <td><a href="/app/amazon-fba-customer-return/${encodeURIComponent(row.name)}">查看 ↗</a></td>
            </tr>${this.renderReturnInventory(row)}`;
        }).join('')||'<tr><td colspan="7" class="af-empty">当前日期范围没有 FBA 客户退货。</td></tr>');
        this.$.find('.af-return-pages').html(`<span>第 ${pagination.page||1} / ${pagination.pages||1} 页</span><button type="button" data-return-page="${(pagination.page||1)-1}" ${(pagination.page||1)<=1?'disabled':''}>上一页</button><button type="button" data-return-page="${(pagination.page||1)+1}" ${(pagination.page||1)>=(pagination.pages||1)?'disabled':''}>下一页</button>`);
    }
    renderTable(){
        const p=this.data.pagination||{};
        const pending=this.transactionPeriod==='pending';
        const refundOnly=this.detailGroup==='refund';
        const orderOnly=this.detailGroup==='order';
        const removal=this.detailGroup==='removal';
        const flatDetail=orderOnly||refundOnly||removal;
        this.$.find('.af-table-wrap').toggleClass('af-orders-table',flatDetail);
        this.$.find('.af-summary-toggle').prop('disabled',!this.detailGroup).attr('title',this.detailGroup?'汇总当前分类':'请先选择一个交易分类');
        const flatAmounts=removal?['清算回收额','清算处理费','清算佣金']:['商品销售额','配送收入','费用','调整明细'];
        let columns=['订单','交易内容','物料',...flatAmounts,'净额','释放金额','释放时间','财务时间'];
        if(!flatDetail){
            columns=['店铺 / 站点','交易 / 订单','交易内容'];
            if(this.detailGroup==='logistics')columns.push('关联物料');
            const amounts=['净额','费用'];
            if(this.detailGroup==='funds')amounts.push('汇出金额','汇出日期（北京时间）');
            columns.push('类型 / 状态',...amounts,'财务时间（美西）','物料 / 费用归属','币种','销售额','结算ID');
        }
        this.$.find('.af-table-wrap > table > thead').html(`<tr>${columns.map(label=>`<th scope="col"${label==='关联物料'?' class="af-manual-items-head"':''}>${label}</th>`).join('')}</tr>`);
        this.$.find('.af-detail-tabs').html((this.data.detail_groups||[]).filter(group=>group.key).map(group=>`<button type="button" class="af-detail-tab ${group.key===this.detailGroup?'active':''}" data-detail-group="${this.esc(group.key)}"><span>${this.esc(group.label)}</span><b>${this.num(group.count)}</b></button>`).join(''));
        const releaseCount=(this.data.rows||[]).reduce((count,row)=>count+(row.related_releases||[]).length,0);
        const itemCount=(this.data.rows||[]).reduce((count,row)=>count+(row.item_lines||[]).length,0);
        const awdCount=(this.data.rows||[]).reduce((count,row)=>count+(row.awd_fee_details||[]).length,0);
        const pageLabel=`第 ${p.page}/${p.pages} 页`;
        const orderTotals=this.data.order_list_totals||{};
        const pendingCount=orderOnly
            ?`${this.num(orderTotals.orders)} 笔待释放订单 · ${this.num(orderTotals.units)} 件商品`
            :refundOnly?`${this.num(p.total)} 笔待释放退款 · ${this.num(this.data.refund_list_units)} 件商品`
                :`${this.num(p.total)} 组待释放交易`;
        const count=pending
            ?`共 ${pendingCount} · ${pageLabel}`
            :orderOnly
            ?`共 ${this.num(orderTotals.orders)} 笔订单 · ${this.num(orderTotals.units)} 件商品 · ${pageLabel}`
            :refundOnly?`共 ${this.num(p.total)} 笔退款 · ${this.num(this.data.refund_list_units)} 件商品 · ${pageLabel}`
            :removal?`共 ${this.num(p.total)} 笔移除 · ${pageLabel}`
                :`共 ${this.num(p.display_total??p.total)} 条交易明细（${this.num(p.total)} 组）· 当前页展开 ${this.num(itemCount)} 条商品明细、${this.num(releaseCount)} 条释放记录、${this.num(awdCount)} 条 AWD 费用明细 · ${pageLabel}`;
        this.$.find('.af-table-count').text(count);
        this.$.find('.af-pages').html(`<button class="btn btn-xs" data-page="${p.page-1}" ${p.page<=1?'disabled':''}>上一页</button><button class="btn btn-xs" data-page="${p.page+1}" ${p.page>=p.pages?'disabled':''}>下一页</button>`);
        this.$.find('.af-table-wrap > table > tbody').html((this.data.rows||[]).flatMap(r=>flatDetail?this.transactionRow(r):[
            this.transactionRow(r),
            ...(r.item_lines||[]).map(line=>this.transactionRow(r,false,r.name,line)),
            ...(r.awd_fee_details||[]).map(detail=>this.awdFeeRow(r,detail)),
            ...(r.related_releases||[]).map(child=>this.transactionRow(child,true,r.name)),
        ]).join('')||`<tr><td colspan="${columns.length}" class="af-empty">${pending?'当前筛选下没有未释放交易':orderOnly?'当前范围内没有订单':refundOnly?'当前范围内没有退款':'暂无交易'}</td></tr>`)
    }
    transactionTime(row,isRelease){
        if(!isRelease&&row.cutoff_activity_date){
            return `${this.esc(this.date(row.original_transaction_date||row.cutoff_activity_date))}<small>${this.esc(row.original_report_timezone||'')}</small>`;
        }
        const label=isRelease?'释放时间 · ':'';
        const original=!isRelease&&row.original_transaction_date
            ?`<small>${this.detailGroup==='refund'?'退款发生':'原交易'} · ${this.esc(this.date(row.original_transaction_date))}</small>`:'';
        const maturity=this.detailGroup!=='order'&&this.transactionPeriod==='pending'&&row.report_maturity_date
            ?`<small>预计释放 · ${this.esc(this.date(row.report_maturity_date))}</small>`:'';
        return `${this.esc(this.date(row.report_posted_date||row.posted_date))}<small>${label}${this.esc(row.report_timezone||'')}</small>${original}${maturity}`;
    }
    transactionRow(r,isRelease=false,parentName='',itemLine=null){
            const isItem=Boolean(itemLine);
            const skuDetail=Boolean(AMAZON_SKU_GROUP_LABELS[this.detailGroup]);
            const removal=this.detailGroup==='removal';
            const flatDetail=skuDetail||removal;
            const detail=itemLine||r;
            const orderAmounts=r.order_amounts;
            const fees=skuDetail&&orderAmounts?orderAmounts.fees.reduce((total,fee)=>total+fee.amount,0)
                :detail.display_fees??((detail.selling_fees||0)+(detail.fba_fees||0)+(detail.other_transaction_fees||0));
            const net=isItem?detail.net_amount:(r.display_net_amount??r.net_amount);
            const product=Boolean(r.is_product);
            const releases=flatDetail?[]:(r.related_releases||[]);
            const itemLines=flatDetail?[]:(r.item_lines||[]);
            const awdDetails=flatDetail?[]:(r.awd_fee_details||[]);
            const collapsed=this.collapsedGroups.has(r.name);
            const toggle=!isRelease&&!isItem&&(itemLines.length||releases.length||awdDetails.length)?`<button type="button" class="af-release-toggle" data-release-toggle="${this.esc(r.name)}" aria-expanded="${!collapsed}" title="${collapsed?'展开':'收起'}关联明细">${collapsed?'▸':'▾'}</button>`:'';
            const relation=isItem?`<span class="af-item-label">↳ 商品明细 ${this.num(detail.line_no)}/${this.num(detail.line_count)}</span>`:isRelease?`<span class="af-release-label">↳ 资金释放${r.context_only?' · 范围外':''}</span>`:itemLines.length||releases.length||awdDetails.length?`<span class="af-parent-label">原交易${itemLines.length?` · ${this.num(itemLines.length)} 条商品`:''}${awdDetails.length?` · ${this.num(awdDetails.length)} 条 AWD 明细`:''}${releases.length?` · ${this.num(releases.length)} 条释放`:''}</span>`:'';
            const content=product
                ? `<div class="af-table-product"><img src="${this.image(r.item_image)}"><span><b>${this.esc((isItem&&detail.description)||r.product_name||r.sku||'未命名商品')}</b><small>${this.esc(detail.asin||r.asin||'无ASIN')} · ${r.transaction_type==='RemovalShipment'?'FNSKU ':''}${this.esc(detail.sku||r.sku||'无SKU')}${isItem||flatDetail?` · 数量 ${this.num(detail.quantity)}`:''}</small></span></div>`
                : `<div class="af-fee-content"><span class="af-fee-icon">${this.feeIcon(r.description,r.transaction_type)}</span><span><b>${this.esc(this.translate(r.description||r.transaction_type||'店铺级费用'))}</b><small>${this.esc(this.translate(r.transaction_type||'未分类'))} · 不对应具体SKU</small></span></div>`;
            const assignment=product
                ? (r.corresponding_item?`<a href="/app/item/${encodeURIComponent(r.corresponding_item)}">${this.esc(r.item_name||r.corresponding_item)}</a><small class="af-scope-tag product">商品级交易</small>`:`<button class="btn btn-xs btn-primary af-bind" ${this.bindData(r)}>绑定物料</button><small class="af-scope-tag product">商品级交易</small>`)
                : `<span class="af-no-item">${this.esc(this.feeGroup(r.description,r.transaction_type))}</span><small class="af-scope-tag store">店铺级费用 · 无需绑定物料</small>`;
            const hasChildren=itemLines.length||awdDetails.length;
            const manualCell=this.detailGroup==='logistics'
                ?this.manualItemsCell(r,Boolean(isRelease||(!isItem&&hasChildren)),isItem?detail.manual_key:'',isItem?detail.manual_items:undefined):'';
            const rowClass=isItem?'af-item-child':isRelease?'af-release-child':product?'af-product-row':'af-fee-row';
            const groupAttr=(isItem||isRelease)?` data-release-parent="${this.esc(parentName)}"${this.collapsedGroups.has(parentName)?' hidden':''}`:'';
            const taxNote=isItem&&((detail.product_sales_tax||0)||(detail.marketplace_withheld_tax||0))?`<small>销售税 ${this.money(detail.product_sales_tax,r.currency_code)} · 平台代扣税 ${this.money(detail.marketplace_withheld_tax,r.currency_code)}</small>`:'';
            const feeDetails=(orderAmounts?.fees||[]).filter(({amount})=>Math.abs(Number(amount||0))>=0.005)
                .map(({label,amount})=>`<small>${this.esc(label)} ${this.money(amount,r.currency_code)}</small>`).join('');
            const parentRow=!isItem&&!isRelease;
            const statusLabel=this.translate(r.transaction_status||'');
            const transactionDate=this.date(r.original_transaction_date);
            const crossPeriodTag=parentRow&&r.detail_group==='order'&&r.finance_outside_period
                ?`<span class="af-shipment-month finance" title="原交易日期 ${this.esc(transactionDate)}">${this.esc(transactionDate.slice(0,7))} 原交易</span>`
                :'';
            const netCell=`<td class="num ${net>=0?'positive':'negative'}">${this.money(net,r.currency_code)}${isRelease?'<small>资金流转，不重复计入汇总</small>':isItem?'<small>已计入原交易，不重复汇总</small>':''}${taxNote}</td>`;
            const feeCell=`<td class="num ${fees<0?'negative':''}">${isRelease?'—':this.money(fees,r.currency_code)}${feeDetails}</td>`;
            if(flatDetail){
                const order=this.esc(r.amazon_order_id||(removal?r.transaction_id:'')||'');
                const orderId=order?`<button type="button" data-copy="${order}">${order}</button>`:'—';
                const releaseAmounts=new Map(),releaseTimes=new Set();
                for(const release of (removal?r.release_details:r.order_releases)||[]){
                    releaseAmounts.set(release.currency,(releaseAmounts.get(release.currency)||0)+release.amount);
                    releaseTimes.add(`${this.date(release.time)} ${release.timezone||''}`.trim());
                }
                const released=[...releaseAmounts].map(([currency,amount])=>this.esc(this.money(amount,currency))).join('<br>')||'—';
                const releasedAt=[...releaseTimes].map(time=>this.esc(time)).join('<br>')||'—';
                const adjustments=(orderAmounts?.adjustments||[]).map(({label,amount})=>`<small>${this.esc(label)} ${this.money(amount,r.currency_code)}</small>`).join('')||'—';
                const shipping=orderAmounts?.shipping??r.shipping_credits;
                const liquidation=r.liquidation||{};
                const amounts=removal?[liquidation.gross_recovery,liquidation.processing_fee,liquidation.referral_fee]
                    .map(amount=>`<td class="num af-liquidation-amount${amount<0?' negative':''}">${amount==null?'—':this.money(amount,r.currency_code)}</td>`).join('')
                    :`<td class="num">${this.money(orderAmounts?.sales??detail.product_sales,r.currency_code)}</td><td class="num">${shipping?this.money(shipping,r.currency_code):'—'}</td>${feeCell}<td class="num">${adjustments}</td>`;
                return `<tr class="${rowClass}"><td><div class="af-transaction-id">${orderId}</div></td><td>${content}</td><td>${assignment}</td>${amounts}${netCell}<td class="num">${released}</td><td>${releasedAt}</td><td>${this.transactionTime(r,false)}</td></tr>`;
            }
            const disbursement=!isRelease&&!isItem?r.disbursement:null;
            const fundsCells=this.detailGroup==='funds'
                ?`<td class="num af-disbursement-value">${disbursement?.amount==null?'—':this.esc(this.money(disbursement.amount,disbursement.currency))}</td><td class="af-disbursement-value">${this.esc(disbursement?.date||'—')}</td>`:'';
            return `<tr class="${rowClass}"${groupAttr}><td><b>${this.esc(r.store||'—')}</b><small>${this.esc(this.translate(r.country||''))} · ${this.esc(this.translate(r.marketplace_id||''))}</small></td><td><div class="af-transaction-id">${toggle}${relation}<button data-copy="${this.esc(r.transaction_id||'')}">${this.esc(r.transaction_id||'—')}</button></div><small>${this.esc(r.amazon_order_id||'无订单号')}</small>${crossPeriodTag?`<div class="af-shipment-months">${crossPeriodTag}</div>`:''}</td><td>${content}</td>${manualCell}<td><b>${this.esc(this.translate(r.transaction_type||'未分类'))}</b><small>${this.esc(statusLabel)}</small></td>${netCell}${feeCell}${fundsCells}<td>${this.transactionTime(r,isRelease)}</td><td>${assignment}</td><td>${this.esc(r.currency_code||'—')}</td><td class="num">${isRelease?'—':this.money(detail.product_sales,r.currency_code)}</td><td>${this.esc(r.settlement_id||'—')}</td></tr>`;
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
