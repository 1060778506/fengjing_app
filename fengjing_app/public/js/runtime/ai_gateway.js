// 丰境共享AI网关，供物料和产品对应平台配置页面调用。

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
