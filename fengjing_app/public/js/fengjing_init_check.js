


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

frappe.ui.form.on('Item', {
    onload(frm) {
        if (
            frm?.doc &&
            (frm.doc.__islocal === 1 || String(frm.doc.name || '').startsWith('new-item-'))
        ) {
            frm.trigger('丰境_同步物料命名模版');
        }
    },

    refresh(frm) {
        setTimeout(() => {
            //成本价计算方法
            frm.set_value('valuation_method', 'FIFO');

            if (frm.fields_dict['custom_ai生成物料名称']) {
                frm.fields_dict['custom_ai生成物料名称'].$wrapper
                    .find('textarea, input')
                    .css({
                        'height': '380px',
                        'min-height': '380px'
                    });
            }
        }, 300);
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








// 默认不勾选优化选项
(() => {
    const OPTIMIZE_SELECTOR =
        '#uploader-optimize-checkbox input[type="checkbox"]';

    function disable_default_optimization(root = document) {
        root.querySelectorAll(OPTIMIZE_SELECTOR).forEach((checkbox) => {
            // 每个上传项只自动处理一次，之后仍允许用户手动重新勾选
            if (checkbox.dataset.fengjingDefaultApplied === "1") {
                return;
            }

            checkbox.dataset.fengjingDefaultApplied = "1";

            // 必须触发点击事件，才能同时修改 Vue 内部的 file.optimize
            if (checkbox.checked) {
                checkbox.click();
            }
        });
    }

    function start_observer() {
        disable_default_optimization();

        const observer = new MutationObserver(() => {
            disable_default_optimization();
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }

    if (document.body) {
        start_observer();
    } else {
        document.addEventListener("DOMContentLoaded", start_observer, {
            once: true
        });
    }
})();






// ============================================================
// 物料图片增强
// 1. items子表的物料编号左侧显示缩略图
// 2. 鼠标经过缩略图显示完整大图
// 3. Item物料下拉菜单显示图片并美化
// ============================================================
(() => {
    if (window.__fengjing_item_image_module_loaded) {
        return;
    }

    window.__fengjing_item_image_module_loaded = true;

    const 支持的单据 = [
        "Stock Entry",
        "Material Request",
        "Request for Quotation",
        "Supplier Quotation",
        "Purchase Order",
        "Purchase Receipt",
        "Purchase Invoice",
        "Opportunity",
        "Quotation",
        "Sales Order",
        "Delivery Note",
        "Sales Invoice",
        "POS Invoice",
        "BOM",
        "Subcontracting Order",
        "Subcontracting Receipt"
    ];

    // 缓存物料图片，避免重复查询
    const 物料图片缓存 = new Map();

    // --------------------------------------------------------
    // 样式
    // --------------------------------------------------------
    function 添加样式() {
        if (document.getElementById("fengjing-item-image-style")) {
            return;
        }

        const style = document.createElement("style");
        style.id = "fengjing-item-image-style";

        style.textContent = `
            /* items表格中的物料图片 */
            .fengjing-item-cell {
                display: flex;
                align-items: center;
                gap: 7px;
                min-height: 28px;
                max-width: 100%;
                overflow: hidden;
            }

            .fengjing-item-thumbnail {
                width: 26px;
                height: 26px;
                flex: 0 0 26px;
                display: flex;
                align-items: center;
                justify-content: center;
                box-sizing: border-box;
                padding: 2px;
                overflow: hidden;
                border: 1px solid #e2e8f0;
                border-radius: 5px;
                background: #ffffff;
                cursor: zoom-in;
                transition:
                    border-color 0.15s ease,
                    box-shadow 0.15s ease,
                    transform 0.15s ease;
            }

            .fengjing-item-thumbnail:hover {
                z-index: 2;
                border-color: #7c9cff;
                box-shadow: 0 2px 8px rgba(37, 99, 235, 0.20);
                transform: translateY(-1px);
            }

            .fengjing-item-thumbnail img {
                display: block;
                width: auto;
                height: auto;
                max-width: 100%;
                max-height: 100%;
                object-fit: contain;
                object-position: center center;
            }

            .fengjing-item-code {
                min-width: 0;
                overflow: hidden;
                white-space: nowrap;
                text-overflow: ellipsis;
            }

            /* 鼠标悬停大图 */
            #fengjing-item-image-preview {
                position: fixed;
                z-index: 1000000;
                display: none;
                align-items: center;
                justify-content: center;
                width: min(480px, calc(100vw - 32px));
                height: min(480px, calc(100vh - 32px));
                box-sizing: border-box;
                padding: 14px;
                overflow: hidden;
                pointer-events: none;
                border: 1px solid rgba(15, 23, 42, 0.12);
                border-radius: 12px;
                background:
                    linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
                box-shadow:
                    0 22px 55px rgba(15, 23, 42, 0.24),
                    0 6px 18px rgba(15, 23, 42, 0.12);
            }

            #fengjing-item-image-preview img {
                display: block;
                width: auto;
                height: auto;
                max-width: 100%;
                max-height: 100%;
                object-fit: contain;
                object-position: center center;
            }

            /* 点击表格缩略图后显示的沉浸式大图 */
            #fengjing-item-image-modal {
                position: fixed;
                inset: 0;
                z-index: 1100000;
                display: none;
                align-items: center;
                justify-content: center;
                box-sizing: border-box;
                padding: 32px;
                background: rgba(0, 0, 0, 0.78);
                backdrop-filter: blur(2px);
                cursor: zoom-out;
            }

            #fengjing-item-image-modal.is-open {
                display: flex;
            }

            #fengjing-item-image-modal-content {
                display: flex;
                align-items: center;
                justify-content: center;
                max-width: 94vw;
                max-height: 92vh;
                cursor: default;
            }

            #fengjing-item-image-modal-content img {
                display: block;
                width: auto;
                height: auto;
                max-width: 94vw;
                max-height: 92vh;
                object-fit: contain;
                object-position: center center;
                border-radius: 10px;
                background: #ffffff;
                box-shadow: 0 28px 80px rgba(0, 0, 0, 0.48);
            }

            /* 物料下拉菜单 */
            .fengjing-item-dropdown {
                width: min(500px, calc(100vw - 30px)) !important;
                max-width: min(500px, calc(100vw - 30px)) !important;
                padding: 6px !important;
                overflow-x: hidden !important;
                border: 1px solid #dbe3ef !important;
                border-radius: 10px !important;
                background: #ffffff !important;
                box-shadow:
                    0 16px 38px rgba(15, 23, 42, 0.16),
                    0 4px 12px rgba(15, 23, 42, 0.08) !important;
            }

            .fengjing-item-dropdown [role="option"] {
                box-sizing: border-box;
                margin: 2px 0 !important;
                padding: 0 !important;
                overflow: hidden;
                border: 1px solid transparent;
                border-radius: 8px;
                transition:
                    background-color 0.12s ease,
                    border-color 0.12s ease;
            }

            .fengjing-item-dropdown [role="option"]:hover,
            .fengjing-item-dropdown [role="option"][aria-selected="true"] {
                border-color: #c7d7fe;
                background: #eff6ff !important;
            }

            .fengjing-item-dropdown [role="option"] > p {
                margin: 0 !important;
                padding: 0 !important;
            }

            .fengjing-dropdown-item {
                display: flex;
                align-items: center;
                gap: 10px;
                min-height: 54px;
                box-sizing: border-box;
                padding: 6px 8px;
            }

            .fengjing-dropdown-thumbnail {
                width: 46px;
                height: 46px;
                flex: 0 0 46px;
                display: flex;
                align-items: center;
                justify-content: center;
                box-sizing: border-box;
                padding: 3px;
                overflow: hidden;
                border: 1px solid #e2e8f0;
                border-radius: 7px;
                background: #ffffff;
                cursor: zoom-in;
            }

            .fengjing-dropdown-thumbnail img {
                display: block;
                width: auto;
                height: auto;
                max-width: 100%;
                max-height: 100%;
                object-fit: contain;
                object-position: center center;
            }

            .fengjing-dropdown-thumbnail.is-empty {
                color: #94a3b8;
                background: #f8fafc;
                cursor: default;
            }

            .fengjing-dropdown-placeholder {
                font-size: 11px;
                line-height: 1;
                color: #94a3b8;
            }

            .fengjing-dropdown-content {
                min-width: 0;
                flex: 1;
                overflow: hidden;
                line-height: 1.4;
            }

            .fengjing-dropdown-content strong {
                display: block;
                overflow: hidden;
                color: #172033;
                font-size: 13px;
                font-weight: 600;
                white-space: nowrap;
                text-overflow: ellipsis;
            }

            .fengjing-dropdown-content .small {
                display: block;
                margin-top: 2px;
                overflow: hidden;
                color: #64748b;
                font-size: 12px;
                white-space: nowrap;
                text-overflow: ellipsis;
            }
        `;

        document.head.appendChild(style);
    }

    // --------------------------------------------------------
    // 悬停大图
    // --------------------------------------------------------
    function 初始化大图预览() {
        if (document.getElementById("fengjing-item-image-preview")) {
            return;
        }

        const preview = document.createElement("div");
        preview.id = "fengjing-item-image-preview";

        const image = document.createElement("img");
        preview.appendChild(image);
        document.body.appendChild(preview);

        function 移动预览(event) {
            const 间距 = 18;
            const previewWidth = preview.offsetWidth || 480;
            const previewHeight = preview.offsetHeight || 480;

            let left = event.clientX + 间距;
            let top = event.clientY + 间距;

            if (left + previewWidth > window.innerWidth - 12) {
                left = event.clientX - previewWidth - 间距;
            }

            if (top + previewHeight > window.innerHeight - 12) {
                top = window.innerHeight - previewHeight - 12;
            }

            left = Math.max(12, left);
            top = Math.max(12, top);

            preview.style.left = `${left}px`;
            preview.style.top = `${top}px`;
        }

        document.addEventListener("mouseover", event => {
            if (!(event.target instanceof Element)) {
                return;
            }

            const source = event.target.closest(
                ".fengjing-image-hover-source"
            );

            if (!source) {
                return;
            }

            const imageUrl = source.dataset.imageUrl;

            if (!imageUrl) {
                return;
            }

            image.src = imageUrl;
            preview.style.display = "flex";
            移动预览(event);
        });

        document.addEventListener("mousemove", event => {
            if (preview.style.display === "flex") {
                移动预览(event);
            }
        });

        document.addEventListener("mouseout", event => {
            if (!(event.target instanceof Element)) {
                return;
            }

            const source = event.target.closest(
                ".fengjing-image-hover-source"
            );

            if (
                source &&
                !source.contains(event.relatedTarget)
            ) {
                preview.style.display = "none";
                image.removeAttribute("src");
            }
        });

        // 表格缩略图点击后显示遮罩大图；下拉菜单缩略图不会触发。
        const modal = document.createElement("div");
        modal.id = "fengjing-item-image-modal";

        const modalContent = document.createElement("div");
        modalContent.id = "fengjing-item-image-modal-content";

        const modalImage = document.createElement("img");
        modalContent.appendChild(modalImage);
        modal.appendChild(modalContent);
        document.body.appendChild(modal);

        function 关闭点击大图() {
            modal.classList.remove("is-open");
            modalImage.removeAttribute("src");
        }

        document.addEventListener("click", event => {
            if (!(event.target instanceof Element)) {
                return;
            }

            const tableThumbnail = event.target.closest(
                ".fengjing-table-thumbnail"
            );

            if (!tableThumbnail) {
                return;
            }

            const imageUrl = tableThumbnail.dataset.imageUrl;

            if (!imageUrl) {
                return;
            }

            event.preventDefault();
            event.stopPropagation();

            preview.style.display = "none";
            image.removeAttribute("src");
            modalImage.src = imageUrl;
            modal.classList.add("is-open");
        }, true);

        // 只有点击图片外面的黑色透明区域才关闭。
        modal.addEventListener("click", event => {
            if (event.target === modal) {
                关闭点击大图();
            }
        });

        // 点击已经放大的图片本身不会关闭。
        modalContent.addEventListener("click", event => {
            event.stopPropagation();
        });

        document.addEventListener("keydown", event => {
            if (event.key === "Escape" && modal.classList.contains("is-open")) {
                关闭点击大图();
            }
        });
    }

    // --------------------------------------------------------
    // items子表缩略图
    // --------------------------------------------------------
    function 设置表格物料缩略图(frm) {
        window.setTimeout(() => {
            const grid = frm.fields_dict.items?.grid;

            if (!grid || grid.__fengjing_thumbnail_enabled) {
                return;
            }

            const itemField = grid.docfields?.find(
                field => field.fieldname === "item_code"
            );

            if (!itemField) {
                return;
            }

            const itemFormatter = function (value, df, options, row) {
                const itemLink = frappe.form.formatters.Link(
                    value,
                    df,
                    options,
                    row
                );

                const imageUrl = row?.image;

                if (!imageUrl) {
                    return itemLink;
                }

                const safeImageUrl = frappe.utils.escape_html(
                    String(imageUrl)
                );

                return `
                    <div class="fengjing-item-cell">
                        <span
                            class="
                                fengjing-item-thumbnail
                                fengjing-image-hover-source
                                fengjing-table-thumbnail
                            "
                            data-image-url="${safeImageUrl}"
                        >
                            <img
                                src="${safeImageUrl}"
                                alt=""
                                loading="lazy"
                            >
                        </span>

                        <span class="fengjing-item-code">
                            ${itemLink}
                        </span>
                    </div>
                `;
            };

            itemField.formatter = itemFormatter;

            if (grid.fields_map?.item_code) {
                grid.fields_map.item_code.formatter = itemFormatter;
            }

            (grid.grid_rows || []).forEach(gridRow => {
                const rowField = gridRow.docfields?.find(
                    field => field.fieldname === "item_code"
                );

                if (rowField) {
                    rowField.formatter = itemFormatter;
                }
            });

            /*
             * 先设置标记，再刷新。
             * 不调用reset_grid，不修改子表结构。
             */
            grid.__fengjing_thumbnail_enabled = true;
            grid.refresh();
        }, 0);
    }

    支持的单据.forEach(doctype => {
        frappe.ui.form.on(doctype, {
            onload_post_render(frm) {
                设置表格物料缩略图(frm);
            },

            refresh(frm) {
                设置表格物料缩略图(frm);
            }
        });
    });

    // --------------------------------------------------------
    // Item下拉菜单图片
    // --------------------------------------------------------
    function 获取下拉选项数据(optionElement) {
        const domLibrary = window.jQuery || window.$;

        if (!domLibrary) {
            return null;
        }

        let optionData = domLibrary(optionElement).data(
            "item.autocomplete"
        );

        if (optionData) {
            return optionData;
        }

        const innerOption = optionElement.querySelector(
            '[role="option"]'
        );

        if (innerOption) {
            optionData = domLibrary(innerOption).data(
                "item.autocomplete"
            );
        }

        return optionData || null;
    }

    async function 查询缺失物料图片(itemCodes) {
        const missingCodes = itemCodes.filter(
            itemCode => !物料图片缓存.has(itemCode)
        );

        if (!missingCodes.length) {
            return;
        }

        try {
            const rows = await frappe.db.get_list("Item", {
                fields: ["name", "item_name", "image"],
                filters: {
                    name: ["in", missingCodes]
                },
                limit: missingCodes.length
            });

            const foundNames = new Set();

            (rows || []).forEach(row => {
                foundNames.add(row.name);

                物料图片缓存.set(row.name, {
                    image: row.image || "",
                    item_name: row.item_name || ""
                });
            });

            // 查询不到或没有权限的也做缓存，避免反复请求
            missingCodes.forEach(itemCode => {
                if (!foundNames.has(itemCode)) {
                    物料图片缓存.set(itemCode, {
                        image: "",
                        item_name: ""
                    });
                }
            });
        } catch (error) {
            console.warn("读取物料下拉图片失败：", error);
        }
    }

    function 美化单个下拉选项(optionElement, itemCode) {
        const paragraph = optionElement.querySelector("p");

        if (!paragraph) {
            return;
        }

        const itemData = 物料图片缓存.get(itemCode) || {};
        const imageUrl = itemData.image || "";

        let wrapper = paragraph.querySelector(
            ".fengjing-dropdown-item"
        );

        if (!wrapper) {
            wrapper = document.createElement("span");
            wrapper.className = "fengjing-dropdown-item";

            const thumbnail = document.createElement("span");
            thumbnail.className = "fengjing-dropdown-thumbnail";

            const content = document.createElement("span");
            content.className = "fengjing-dropdown-content";

            while (paragraph.firstChild) {
                content.appendChild(paragraph.firstChild);
            }

            wrapper.appendChild(thumbnail);
            wrapper.appendChild(content);
            paragraph.appendChild(wrapper);
        }

        const thumbnail = wrapper.querySelector(
            ".fengjing-dropdown-thumbnail"
        );

        if (!thumbnail) {
            return;
        }

        thumbnail.replaceChildren();
        thumbnail.classList.remove(
            "is-empty",
            "fengjing-image-hover-source"
        );
        thumbnail.removeAttribute("data-image-url");

        if (imageUrl) {
            const image = document.createElement("img");
            image.src = imageUrl;
            image.alt = "";
            image.loading = "lazy";

            image.addEventListener("error", () => {
                thumbnail.classList.add("is-empty");
                thumbnail.classList.remove(
                    "fengjing-image-hover-source"
                );
                thumbnail.removeAttribute("data-image-url");
                thumbnail.innerHTML =
                    '<span class="fengjing-dropdown-placeholder">无图</span>';
            });

            thumbnail.appendChild(image);
            thumbnail.classList.add(
                "fengjing-image-hover-source"
            );
            thumbnail.dataset.imageUrl = imageUrl;
        } else {
            thumbnail.classList.add("is-empty");
            thumbnail.innerHTML =
                '<span class="fengjing-dropdown-placeholder">无图</span>';
        }
    }

    async function 美化物料下拉菜单(input) {
        // 直接取得Frappe正在使用的Awesomplete实例。下拉选项的
        // 物料编号优先从instance.suggestions读取，不再依赖DOM数据。
        const awesompleteInstance = Array.from(
            window.Awesomplete?.all || []
        ).find(instance => instance.input === input);

        const awesomplete =
            awesompleteInstance?.container ||
            input.closest(".awesomplete");

        if (!awesomplete) {
            return;
        }

        const dropdown =
            awesompleteInstance?.ul ||
            awesomplete.querySelector("ul");

        if (!dropdown) {
            return;
        }

        // 使用直接子项，兼容不同版本的Awesomplete结构并避免重复。
        const options = Array.from(dropdown.children);

        const validOptions = [];
        const itemCodes = [];

        options.forEach((optionElement, optionIndex) => {
            const optionData = 获取下拉选项数据(optionElement);
            const suggestion =
                awesompleteInstance?.suggestions?.[optionIndex];

            const itemCode =
                optionData?.value ||
                suggestion?.value ||
                "";

            // 排除“高级搜索”等功能选项
            if (
                !itemCode ||
                itemCode === "advanced_search__link_option" ||
                itemCode === "filter_description__link_option"
            ) {
                return;
            }

            const displayElement = optionElement.matches('[role="option"]')
                ? optionElement
                : optionElement.querySelector('[role="option"]') || optionElement;

            validOptions.push({
                element: displayElement,
                itemCode
            });

            itemCodes.push(itemCode);
        });

        if (!itemCodes.length) {
            return;
        }

        const uniqueCodes = [...new Set(itemCodes)];
        const currentKey = uniqueCodes.join("|");

        if (
            dropdown.dataset.fengjingProcessedKey === currentKey &&
            validOptions.every(item =>
                item.element.querySelector(
                    ".fengjing-dropdown-item"
                )
            )
        ) {
            return;
        }

        dropdown.dataset.fengjingProcessedKey = currentKey;
        dropdown.classList.add("fengjing-item-dropdown");

        await 查询缺失物料图片(uniqueCodes);

        // 用户快速输入时，旧的异步请求可能已经失效
        if (!dropdown.isConnected) {
            return;
        }

        validOptions.forEach(item => {
            美化单个下拉选项(
                item.element,
                item.itemCode
            );
        });
    }

    let 扫描计时器 = null;

    function 安排扫描物料下拉菜单() {
        window.clearTimeout(扫描计时器);

        扫描计时器 = window.setTimeout(() => {
            document
                .querySelectorAll(
                    'input[data-target="Item"]'
                )
                .forEach(input => {
                    const awesompleteInstance = Array.from(
                        window.Awesomplete?.all || []
                    ).find(instance => instance.input === input);

                    const awesomplete =
                        awesompleteInstance?.container ||
                        input.closest(".awesomplete");

                    const dropdown =
                        awesompleteInstance?.ul ||
                        awesomplete?.querySelector("ul");

                    const isOpen = awesompleteInstance
                        ? Boolean(awesompleteInstance.isOpened)
                        : Boolean(
                            dropdown &&
                            !dropdown.hasAttribute("hidden")
                        );

                    if (
                        dropdown &&
                        isOpen
                    ) {
                        美化物料下拉菜单(input);
                    }
                });
        }, 60);
    }

    const dropdownObserver = new MutationObserver(() => {
        安排扫描物料下拉菜单();
    });

    // Frappe每次打开或重新计算下拉结果时都会触发该事件。
    document.addEventListener(
        "awesomplete-open",
        event => {
            const input = event.target;

            if (
                input instanceof HTMLInputElement &&
                input.dataset.target === "Item"
            ) {
                window.setTimeout(() => {
                    美化物料下拉菜单(input);
                }, 0);
            }
        },
        true
    );

    function 初始化模块() {
        添加样式();
        初始化大图预览();

        dropdownObserver.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: [
                "hidden",
                "class",
                "aria-selected"
            ]
        });
    }

    if (document.body) {
        初始化模块();
    } else {
        document.addEventListener(
            "DOMContentLoaded",
            初始化模块,
            { once: true }
        );
    }
})();
