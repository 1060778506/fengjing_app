


// 自动跳转到树状图科目表
frappe.router.on('change', () => {
    let route = frappe.get_route();

    if (!route) return;

    // 判断是不是 Account 的默认 List 视图
    if (route[0] === "List" && route[1] === "Account") {

        // 如果不是 Tree 视图才跳转（防止死循环）
        if (!(route[2] && route[2] === "Tree")) {

            console.log("--- ⚡ 自动跳转到 Account Tree 视图 ---");

            frappe.set_route("List", "Account", "Tree");
        }
    }
});

//点击了新建物料
function 丰境_新建物料(frm) {
    return Boolean(
        frm &&
        frm.doc &&
        (frm.doc.__islocal === 1 || String(frm.doc.name || '').startsWith('new-item-'))
    );
}

// 新 Item 的默认值是在表单创建前由 get_new_doc -> set_default_values 写入的。
// 在这里拦截，避免折叠的“序列号/批次”区域稍后按旧元数据生成勾选状态。
if (!window.__fengjingItemBatchDefaultGuardBound && frappe.model) {
    window.__fengjingItemBatchDefaultGuardBound = true;
    const originalGetDefaultValue = frappe.model.get_default_value;
    if (typeof originalGetDefaultValue === 'function') {
        frappe.model.get_default_value = function (df, doc, parent_doc) {
            if (
                df &&
                df.fieldname === 'has_batch_no' &&
                doc &&
                doc.doctype === 'Item' &&
                (doc.__islocal === 1 || String(doc.name || '').startsWith('new-item-'))
            ) {
                return 0;
            }
            return originalGetDefaultValue.apply(this, arguments);
        };
    }

    const originalSetDefaultValues = frappe.model.set_default_values;
    if (typeof originalSetDefaultValues === 'function') {
        frappe.model.set_default_values = function (doc, parent_doc) {
            const updated = originalSetDefaultValues.apply(this, arguments);
            if (
                doc &&
                doc.doctype === 'Item' &&
                (doc.__islocal === 1 || String(doc.name || '').startsWith('new-item-'))
            ) {
                doc.has_batch_no = 0;
                const field = frappe.meta?.get_docfield?.('Item', 'has_batch_no');
                if (field) {
                    field.default = 0;
                    field.__default_value = 0;
                }
            }
            return updated;
        };
    }
}

function 丰境_清除新建物料批号(frm) {
    if (!丰境_新建物料(frm)) return;

    const metaField = frm.meta?.fields?.find(field => field.fieldname === 'has_batch_no');
    if (metaField) metaField.default = '0';
    if (typeof frm.set_df_property === 'function') frm.set_df_property('has_batch_no', 'default', '0');
    frm.doc.has_batch_no = 0;
    if (frm.fields_dict && frm.fields_dict.has_batch_no) {
        const control = frm.fields_dict.has_batch_no;
        if (typeof control.set_value === 'function') control.set_value(0);
    }
    frm.refresh_field('has_batch_no');
}

function 丰境_监控新建物料批号(frm) {
    if (!丰境_新建物料(frm) || frm.__fengjing_batch_monitor_bound) return;
    frm.__fengjing_batch_monitor_bound = true;

    const apply = () => {
        if (!丰境_新建物料(frm)) return;
        丰境_清除新建物料批号(frm);

        const input = frm.fields_dict?.has_batch_no?.$wrapper?.find('input[type="checkbox"]')?.get(0);
        if (input && input.checked) {
            input.checked = false;
            input.removeAttribute('checked');
        }
    };

    // Item 的折叠区域展开时可能会重新渲染控件，短时监控可覆盖这次延迟写入。
    const observer = new MutationObserver(apply);
    if (frm.wrapper) observer.observe(frm.wrapper, { childList: true, subtree: true });
    const timer = setInterval(apply, 300);
    setTimeout(() => {
        clearInterval(timer);
        observer.disconnect();
    }, 12000);
    apply();
}

// 兼容字段控件在折叠区域首次展开时才创建的情况。click() 会同时更新
// Frappe 模型和 checkbox，而不是只改视觉状态；守卫只在新物料初始化的短窗口内运行。
if (!window.__fengjingBatchDomInitGuardBound) {
    window.__fengjingBatchDomInitGuardBound = true;
    window.__fengjingBatchUserTouched = false;
    window.__fengjingBatchGuardRoute = location.pathname;

    // 记录用户自己的点击，守卫只负责初始化，不覆盖用户后续选择。
    document.addEventListener('change', event => {
        const input = event.target;
        if (
            input?.matches?.('[data-fieldname="has_batch_no"] input[type="checkbox"]') &&
            location.pathname.includes('/item/new-item-') &&
            event.isTrusted
        ) {
            window.__fengjingBatchUserTouched = true;
        }
    }, true);

    const applyCurrentItem = () => {
        if (location.pathname !== window.__fengjingBatchGuardRoute) {
            window.__fengjingBatchGuardRoute = location.pathname;
            window.__fengjingBatchUserTouched = false;
        }
        if (!location.pathname.includes('/item/new-item-') || window.__fengjingBatchUserTouched) return;
        const frm = window.cur_frm;
        if (frm?.doc && (frm.doc.__islocal === 1 || String(frm.doc.name || '').startsWith('new-item-'))) {
            frm.doc.has_batch_no = 0;
            const control = frm.fields_dict?.has_batch_no;
            if (control && typeof control.set_value === 'function' && control.value) control.set_value(0);
        }
        const input = document.querySelector('[data-fieldname="has_batch_no"] input[type="checkbox"]');
        if (input && input.checked) input.click();
    };

    // 该控件位于可折叠区域，可能在页面打开数分钟后才首次创建。
    window.__fengjingBatchDomInitGuardTimer = setInterval(() => {
        if (!location.pathname.includes('/item/new-item-')) {
            clearInterval(window.__fengjingBatchDomInitGuardTimer);
            return;
        }
        applyCurrentItem();
    }, 1000);
    applyCurrentItem();
}

// 某些表单初始化路径会在字段渲染后再次写入 has_batch_no=1。
// 只在新物料的初始化阶段拦截这一次写入，保存后的物料和用户手动操作不受影响。
if (!window.__fengjingBatchModelGuardBound) {
    window.__fengjingBatchModelGuardBound = true;
    frappe.model.on('Item', 'has_batch_no', (fieldname, value, doc) => {
        if (!doc || !value || !(doc.__islocal === 1 || String(doc.name || '').startsWith('new-item-'))) return;
        // 只处理初始化阶段第一次出现的 1；之后用户手动勾选不被拦截。
        if (doc.__fengjing_batch_initial_value_seen) return;
        doc.__fengjing_batch_initial_value_seen = true;
        const frm = window.cur_frm;
        if (!frm || frm.doc !== doc) return;
        doc.has_batch_no = 0;
        const control = frm.fields_dict?.has_batch_no;
        if (control && typeof control.set_value === 'function') control.set_value(0);
        frm.refresh_field('has_batch_no');
    });
}

if (!window.__fengjingBatchSectionResetBound) {
    window.__fengjingBatchSectionResetBound = true;
    $(document).on('click.fengjingBatchReset', '.section-head', function () {
        const title = String($(this).text() || '');
        if (!title.includes('序列号') && !title.includes('批次')) return;

        const frm = window.cur_frm;
        [0, 100, 400].forEach(delay => {
            setTimeout(() => 丰境_清除新建物料批号(frm), delay);
        });
    });

    // Frappe 展开折叠区域后会重新创建 checkbox，直接在 DOM 层再校正一次。
    document.addEventListener('click', event => {
        const head = event.target.closest?.('.section-head');
        if (!head || !String(head.textContent || '').includes('序列号/批次')) return;
        if (!location.pathname.includes('/item/new-item-')) return;
        [0, 100, 400, 1000, 2000].forEach(delay => {
            setTimeout(() => {
                document.querySelectorAll('[data-fieldname="has_batch_no"] input[type="checkbox"]').forEach(input => {
                    input.checked = false;
                    input.removeAttribute('checked');
                });
            }, delay);
        });
    }, true);
}

frappe.ui.form.on('Item', {
    setup(frm) {
        if (丰境_新建物料(frm)) {
            frm.__fengjing_batch_initializing = true;
            const field = frm.meta?.fields?.find(df => df.fieldname === 'has_batch_no');
            if (field) field.default = '0';
            setTimeout(() => { frm.__fengjing_batch_initializing = false; }, 5000);
            frm.doc.has_batch_no = 0;
        }
    },

    before_load(frm) {
        if (丰境_新建物料(frm)) frm.doc.has_batch_no = 0;
    },

    onload(frm) {
        if (丰境_新建物料(frm)) {
            丰境_监控新建物料批号(frm);
            // 新建物料不继承库存设置或旧表单状态中的批号值。
            丰境_清除新建物料批号(frm);
            frm.trigger('丰境_同步物料命名模版');
        }
    },

    onload_post_render(frm) {
        if (丰境_新建物料(frm)) {
            丰境_监控新建物料批号(frm);
            丰境_清除新建物料批号(frm);
        }
    },

    refresh(frm) {
        if (丰境_新建物料(frm)) {
            丰境_监控新建物料批号(frm);
            丰境_清除新建物料批号(frm);

            const section = frm.fields_dict && frm.fields_dict.serial_nos_and_batches;
            if (section && !section.__fengjing_batch_reset_bound) {
                section.__fengjing_batch_reset_bound = true;
                $(section.wrapper).on('click.fengjingBatchReset', '.section-head', () => {
                    [0, 100, 400].forEach(delay => {
                        setTimeout(() => 丰境_清除新建物料批号(frm), delay);
                    });
                });
            }
        }

        setTimeout(() => {
            //成本价计算方法
            frm.set_value('valuation_method', 'FIFO');
            // 新建物料默认不启用批号，避免旧版缓存或旧表单状态带入 1。
            if (丰境_新建物料(frm)) {
                丰境_清除新建物料批号(frm);
            }

            if (frm.fields_dict['custom_ai生成物料名称']) {
                frm.fields_dict['custom_ai生成物料名称'].$wrapper
                    .find('textarea, input')
                    .css({
                        'height': '380px',
                        'min-height': '380px'
                    });
            }
        }, 300);

        if (丰境_新建物料(frm)) {
            [1000, 2500].forEach(delay => {
                setTimeout(() => 丰境_清除新建物料批号(frm), delay);
            });
        }
    },

    async 丰境_同步物料命名模版(frm) {
        let config_doc = await frappe.db.get_doc(
            'Fengjing - Product Corresponding Platform - Configuration'
        );
        
        if (config_doc && config_doc.物料命名模版) {
            frm.set_value(
                'custom_丰境ai物料描述',
                config_doc.物料命名模版
            );
            frm.set_value(
                'custom_ai生成物料名称',
                config_doc.物料命名模版
            );
            frm.set_value(
                'item_name',
                config_doc.物料命名模版
            );
        }
    }
});





/**
 * 丰境 AI 物料自动命名逻辑
 * 放置位置：fengjing_init_check.js
 * 触发场景：物料单据 (Item) 页面点击 AI 命名按钮
 */
frappe.ui.form.on('Item', {
    /**
     * 当点击“custom_丰境使用ai命名”字段（按钮）时触发
     */
    custom_丰境使用ai命名: async function (frm) {

        // 1. 第一步：尝试从数据库获取“提示词模板”
        // 使用 get_single_value 专门读取 Single DocType 类型的配置
        frappe.db.get_single_value('Fengjing - Product Corresponding Platform - Configuration', '丰境_ai生成物料提示词')
            .then(async (提示词模板) => {

                // --- 核心判断点：如果没有拿到模板数据 ---
                if (!提示词模板) {
                    // 弹出明确提示，告知用户配置缺失
                    frappe.msgprint({
                        title: __('配置缺失'),
                        indicator: 'orange',
                        message: __('未能获取到 AI 命名规范。请前往“产品对应平台-配置”页面填写【丰境_ai生成物料提示词】并保存。')
                    });
                    return; // 终止执行
                }

                // 2. 第二步：准备物料原始描述文本
                // 优先读取“AI物料描述”字段，若为空则读取标准“描述”字段
                let 物料描述 = frm.doc.custom_丰境ai物料描述 || frm.doc.description;

                // 校验：如果描述内容为空，则无法进行 AI 属性提取
                if (!物料描述) {
                    frappe.msgprint(__('当前物料没有描述内容，AI 无法提取属性。'));
                    return;
                }

                // 3. 第三步：获取当前数据库物料总数用于生成逻辑序号
                frappe.db.count('Item').then(async (count) => {
                    // 生成逻辑序号：当前总数 + 1
                    let 总数 = count + 1;

                    // 4. 第四步：封装最终发送给 AI 的指令
                    // 构造格式：[模板] + [描述] + [序号]


                    // 由程序直接生成六位序列号
                    let 六位序列号 = String(总数).padStart(6, '0');
                    // 读取最多 500 个已有物料名称，帮助 AI 参考现有命名风格。
                    // 参考数据不是当前物料的事实，不能替代当前输入或被强制照抄。
                    // 读取最多 500 个已有物料名称和物料号
                    let 参考物料名称 = '（暂无可用的历史物料参考）';

                    try {
                        const 历史物料 = await frappe.db.get_list('Item', {
                            fields: ['item_code', 'item_name'],
                            filters: { disabled: 0 },
                            order_by: 'modified desc',
                            limit_page_length: 100
                        });

                        const 物料参考列表 = (历史物料 || [])
                            .map(item => {
                                const 物料号 = String(item.item_code || '').trim();
                                const 物料名称 = String(item.item_name || '').trim();

                                return `物料号：${物料号}，物料名称：${物料名称}`;
                            })
                            .filter(item => item !== '物料号：，物料名称：');

                        if (物料参考列表.length) {
                            参考物料名称 = 物料参考列表.join('\n');
                        }
                    } catch (参考错误) {
                        console.warn(
                            '读取历史物料名称和物料号失败，将继续执行 AI 命名：',
                            参考错误
                        );
                    }

                    // 组合最终提示词
                    let 最终提示词 =
                        `${提示词模板}\n\n` +
                        `已有物料参考（最多100个，仅用于参考命名风格和物料号格式，不得替代当前输入）：\n` +
                        `${参考物料名称}\n\n` +
                        `物料自然语言输入：${物料描述}\n` +
                        `本次固定序列号：${六位序列号}\n` +
                        `所有10组结果必须原样使用序列号“${六位序列号}”，禁止自行计算或修改。`;

                    console.log('最终提示词：', 最终提示词);
                    // 开启 UI 冻结：显示加载动画，提升交互体验，防止重复点击
                    frappe.dom.freeze(__('AI 正在按照规范计算物料名称...'));
                    try {
                        // 核心调用：执行全局挂载的网关函数“自动找可用的ai”
                        // 使用 await 等待跨节点故障转移逻辑返回最终结果
                        let ai的回答 = await window.自动找可用的ai(最终提示词);


                        const config_doc = await frappe.db.get_doc(
                            'Fengjing - Product Corresponding Platform - Configuration'
                        );

                        let 命名模版 = config_doc.物料命名模版 || '';



                        // 序列号由程序负责，不再依赖 AI 是否正确补零。
                        const ai原始回答 = String(ai的回答 || '').trim();
                        const 规范化回答 = ai原始回答
                            .split(/\r?\n/)
                            .map(line => {
                                const trimmed = line.trim();
                                if (/^fj-/i.test(trimmed)) {
                                    return trimmed.replace(/-\d+$/, `-${六位序列号}`);
                                }
                                return line;
                            })
                            .join('\n')
                            .trim();

                        // 优先取第一条以 fj- 开头的结果作为物料号。
                        const AI物料号 = 规范化回答
                            .split(/\r?\n/)
                            .map(line => line.trim())
                            .find(line => /^fj-/i.test(line));

                        if (!AI物料号) {
                            frappe.msgprint({
                                title: __('未获取到物料号'),
                                indicator: 'orange',
                                message: __('AI返回内容的第一行为空。')
                            });
                            return;
                        }

                        // 界面显示“产品”，数据库中的实际物料组名称是 Products。
                        await frm.set_value('item_code', AI物料号);
                        await frm.set_value('item_group', 'Products');

                        // 完整 AI 回答继续写入“AI生成物料名称”。
                        await frm.set_value(
                            'custom_ai生成物料名称',
                            命名模版 + '\n' + 规范化回答
                        );

                        frm.refresh_field('item_code');
                        frm.refresh_field('item_group');
                        frm.refresh_field('custom_ai生成物料名称');

                        if (frm.doc.item_code !== AI物料号 || frm.doc.item_group !== 'Products') {
                            throw new Error(
                                `字段写入校验失败：物料号=${frm.doc.item_code || '空'}，物料组=${frm.doc.item_group || '空'}`
                            );
                        }

                        frappe.show_alert({
                            message: __('AI命名成功，物料号：') + AI物料号,
                            indicator: 'green'
                        });
                    } catch (err) {
                        // 异常处理：捕获网络错误或 API 节点全部失效的情况
                        frappe.msgprint({
                            title: __('AI 命名请求失败'),
                            indicator: 'red',
                            message: typeof err === 'string' ? err : JSON.stringify(err)
                        });
                        console.error("AI 命名逻辑执行错误:", err);
                    } finally {
                        // 无论结果如何，必须解除 UI 锁定状态
                        frappe.dom.unfreeze();
                    }
                });
            });
    }
});

/**
 * 丰境 AI 物料命名全局逻辑包
 * 包含：网关自选节点逻辑 + 物料页面数据发送逻辑
 */

window.自动找可用的ai = async function (提示词) {

    // 动态获取配置表（Single DocType）的所有子表行
    let config_doc = await frappe.db.get_doc('Fengjing - Product Corresponding Platform - Configuration');
    let ai_rows = config_doc.丰境_ai配置页面 || [];

    if (ai_rows.length === 0) {
        throw __("AI 配置列表为空，请先在‘产品对应平台-配置’页面添加节点。");
    }

    // 遍历所有配置行进行“故障转移”式尝试
    for (let row of ai_rows) {
        if (row.丰境_ai秘钥) {
            let clean_key = row.丰境_ai秘钥.trim();
            //确定好了那个ai
            let 回答 = await window.ai直连(cur_frm, row.doctype, row.name, row, clean_key, 提示词);
            return 回答;
        }
    }
    throw __("当前所有 AI 节点（Gemini/OpenAI）均无法链接，请检查网络或 Key 额度。");

};


/**
 * AI 直连执行器：根据确定的模型来源，调用对应的底层驱动函数
 */
window.ai直连 = async function (frm, cdt, cdn, row, clean_key, 提示词) {
    let ai的回答;
    // --- 1. 模型分流调用 ---
    if (row.丰境_ai来源 === 'Gemini') {
        // 使用 await 等待谷歌 AI 驱动函数返回 Promise 结果
        // 注意：确保 window.去获取谷歌ai的回答 内部已经使用了 resolve(ai回答)
        ai的回答 = await window.去获取谷歌ai的回答(frm, cdt, cdn, row, clean_key, 提示词);
    } else if (row.丰境_ai来源 === 'Teamorouter') {
        ai的回答 = await window.去获取中转站ai的回答(
            frm, cdt, cdn, row, clean_key, 提示词
        );
    } else if (row.丰境_ai来源 === 'OpenAI') {
        // 预留位置：将来如果增加 OpenAI，在此处扩展
        // ai的回答 = await window.去获取OpenAI的回答(...);
        throw __("暂不支持 OpenAI 来源，请等待更新。");

    } else {
        // 如果来源不在已知列表中，抛出错误
        throw __("不支持的 AI 源: {0}", [row.丰境_ai来源]);
    }
    return ai的回答;
};

/**
 * 使用 TeamoRouter 的 OpenAI Chat Completions 兼容接口。
 */
window.去获取中转站ai的回答 = async function (frm, cdt, cdn, row, clean_key, 提示词) {
    const base_url = (row.丰境_api链接 || '').trim().replace(/\/$/, '');
    const model = (row.中转站模型 || '').trim();

    if (!base_url) {
        frappe.msgprint(__('未检测到 API 链接，请重新选择 Teamorouter。'));
        throw new Error('Missing API URL');
    }
    if (!model) {
        frappe.msgprint(__('请先点击“获取模型”，并选择一个中转站模型。'));
        throw new Error('Missing TeamoRouter model');
    }

    const api_url = base_url.endsWith('/chat/completions')
        ? base_url
        : `${base_url}/chat/completions`;

    frappe.dom.freeze(__('正在连接 TeamoRouter：{0}', [model]));
    try {
        const response = await fetch(api_url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${clean_key}`
            },
            body: JSON.stringify({
                model: model,
                messages: [{ role: 'user', content: 提示词 }],
                max_tokens: 1024,
                stream: false
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.error?.message || data.message || `HTTP ${response.status}`);
        }

        const answer = data.choices?.[0]?.message?.content;
        if (!answer) {
            throw new Error(__('接口返回成功，但没有找到模型回答。'));
        }
        return String(answer).trim();
    } catch (err) {
        frappe.model.set_value(cdt, cdn, '丰境_ai通讯是否正常', '错误');
        console.error('TeamoRouter 通讯失败:', err);
        frappe.msgprint({
            title: __('TeamoRouter API 报错'),
            indicator: 'red',
            message: frappe.utils.escape_html(err.message || String(err))
        });
        throw err;
    } finally {
        // 解除 TeamoRouter 请求自己创建的那一层遮罩。
        frappe.dom.unfreeze();
    }
};










// 物料增加数量
frappe.ui.form.on('Item', {
    // 1. refresh 钩子现在只管初始化，不自动执行任何逻辑
    refresh: function (frm) {
        if (frm.is_new()) {
            // 方式 A：标准推荐写法 (模拟点击按钮)
            frm.trigger('custom_物料数量');

            // 方式 B：直接调用写法 (如果你非要直接运行逻辑)
            // this.custom_物料数量(frm); 
        }
    },
    // 2. 只有点击名为 custom_物料数量 的按钮字段时才执行
    custom_物料数量: function (frm) {

        frappe.call({
            method: "fengjing_app.install.检测这是第几个物料",
            callback: function (r) {
                if (r && r.message) {
                    const data_field = 'custom_目前第几个物料';
                    // 1. 强制解除只读（如果是只读字段）
                    frm.set_df_property(data_field, 'read_only', 0);
                    // 2. 填入后端返回的数字
                    frm.set_value(data_field, r.message);
                    // 3. 强制刷新前端界面显示
                    frm.refresh_field(data_field);
                    // 4. 恢复只读（保护数据不被手动乱改）
                    frm.set_df_property(data_field, 'read_only', 1);
                    frappe.show_alert({
                        message: __("物料序号查询成功: " + r.message),
                        indicator: 'green'
                    });
                }
            }
        });
    }
});


window.去获取谷歌ai的回答 = function (frm, cdt, cdn, row, clean_key, 提示词) {
    return new Promise((resolve, reject) => {
        let base_url = (row.丰境_api链接 || "").trim();

        if (!base_url) {
            frappe.msgprint(__('未检测到 API 链接，请先填写或选择 AI 来源'));
            return reject("Missing API URL"); // 必须 reject
        }

        let api_url = base_url.includes('?') ? `${base_url}&key=${clean_key}` : `${base_url}?key=${clean_key}`;

        // 1. 初始冻结
        frappe.dom.freeze(__('正在链接 Gemini'));
        console.log("开始发送时间:", new Date().toLocaleTimeString() + "." + new Date().getMilliseconds());
        fetch(api_url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ "contents": [{ "parts": [{ "text": 提示词 }] }] })
        })
            .then(response => {
                return response.json().then(data => {
                    if (response.ok) {
                        console.log("收到数据时间:", new Date().toLocaleTimeString() + "." + new Date().getMilliseconds());
                        let ai的回答 = data.candidates[0].content.parts[0].text.trim();
                        resolve(ai的回答);
                    } else {
                        // --- 错误处理修正 ---
                        frappe.dom.unfreeze(); // 1. 必须解冻
                        frappe.model.set_value(cdt, cdn, '丰境_ai通讯是否正常', "错误");

                        frappe.msgprint({
                            title: __('Gemini API 报错'),
                            indicator: 'red',
                            message: data.error ? data.error.message : __('未知错误')
                        });

                        reject(data.error ? data.error.message : "API Error"); // 2. 必须 reject
                    }
                });
            })
            .catch(err => {
                // --- 网络异常处理修正 ---
                frappe.dom.unfreeze(); // 1. 必须解冻
                frappe.model.set_value(cdt, cdn, '丰境_ai通讯是否正常', "网络异常");
                console.error("AI 通讯详细错误堆栈:", err);

                frappe.msgprint({
                    title: __('请求异常'),
                    indicator: 'red',
                    message: `<pre>${err.stack || err.message || JSON.stringify(err)}</pre>`
                });

                reject(err); // 2. 必须 reject
            });
    });
};


// 检测是否要弹窗写入新的科目表
$(document).on('app_ready', function () {
    if (frappe.boot.is_fresh_system == 1) {
        setTimeout(function () {
            let d = new frappe.ui.Dialog({
                title: '🎉 丰境环境部署成功',
                fields: [
                    {
                        fieldtype: 'HTML',
                        options: `
                            <div style="padding: 10px; line-height: 1.6;">
                                <h4 style="color: #1a1a1a;">发现全新系统环境！</h4>
                                <p>您尚未配置电商专业会计科目。</p>
                                <p style="color: #d9534f; font-weight: bold;">
                                    注意：点击“立即执行”后，系统将等待约 10 秒以确保环境初始化完成，期间请勿刷新页面。
                                </p>
                            </div>
                        `
                    }
                ],
                primary_action_label: '是 (立即执行)',
                secondary_action_label: '否 (暂不处理)',
                primary_action() {
                    // 1. 隐藏对话框
                    d.hide();

                    // 2. 【核心修改】立即锁定屏幕，阻止用户一切操作
                    frappe.dom.freeze("🚀 丰境净化中...<br>正在等待系统环境初始化（约 10 秒），请勿刷新或关闭页面。");

                    // 3. 显示一个进度条（视觉安抚）
                    frappe.show_progress('正在初始化', 20, 100, '正在等待其他组件就绪...');

                    frappe.call({
                        method: "fengjing_app.install.页面触发的强制净化逻辑",
                        callback: function (r) {
                            // 4. 【核心修改】解除锁定
                            frappe.dom.unfreeze();
                            
                            if(!r.exc) {
                                frappe.show_progress('完成', 100, 100);
                                frappe.msgprint({
                                    title: __('成功'),
                                    indicator: 'green',
                                    message: __("丰境专业会计科目已写入成功！系统即将自动刷新。")
                                });

                                // 刷新页面，清空所有缓存和残留弹窗
                                setTimeout(() => {
                                    location.reload();
                                }, 2000);
                            }
                        },
                        error: function() {
                            // 报错也要解锁，否则用户页面就卡死了
                            frappe.dom.unfreeze();
                            frappe.show_progress('失败', 100, 100);
                        }
                    });
                },
                secondary_action() {
                    d.hide();
                    frappe.call({
                        method: "fengjing_app.install.更改看门狗参数",
                        callback: function (r) {
                            frappe.show_alert({
                                message: "已跳过初始化，看门狗已记录。",
                                indicator: "orange"
                            });
                        }
                    });
                }
            });
            d.show();
        }, 5000);
    }
});
