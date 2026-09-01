// 版权所有 (c) 2026, 丰境电商及贡献者
// 有关许可信息，请参阅 license.txt

// --- 主表逻辑：产品对应平台配置 ---
frappe.ui.form.on('Fengjing - Product Corresponding Platform - Configuration', {
    // 页面刷新时的逻辑
    refresh: function (frm) {
        // 在控制台打印刷新日志，方便调试
        console.log("--- 页面已刷新 ---");
        setTimeout(() => 刷新全部中转站模型状态(frm), 0);
        setTimeout(() => 刷新全部ASIN配置行状态(frm), 0);
    },

    // 点击“恢复物料提示词”按钮时的逻辑
    // 点击“恢复物料提示词”按钮时的逻辑
    丰境_恢复物料提示词: function (frm) {
        frappe.confirm(__('确定要恢复默认的 AI 物料命名提示词吗？'), () => {
            frm.call({
                doc: frm.doc,
                method: "get_standard_prompt",
                callback: function (r) {
                    if (r.message) {
                        console.log(r.message.物料命名模版);
                        frm.set_value('丰境_ai生成物料提示词', r.message.STANDARD_PROMPT);
                        frm.set_value('物料命名模版', r.message.物料命名模版);

                        frappe.show_alert({
                            message: __('已同步后端标准规范'),
                            indicator: 'green'
                        });
                    }
                }
            });
        });
    },


    立即抓取: function (frm) {
        // 1. 弹出加载提示，防止用户重复点击
        frappe.show_alert({ message: __('开始抓取，请稍候...'), indicator: 'blue' });

        // 2. 调用后端的 Python 函数
        frappe.call({
            method: "fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log.获取sku排名",
            args: {
                "docname": frm.doc.name,
                "忽视定时抓取": 1  // <-- 新增参数，默认传 1
            },
            freeze: true, // 冻结屏幕，防止乱点
            freeze_message: __("正在连接亚马逊抓取数据..."),
            callback: function (r) {
                if (r.message && r.message.status === "success") {
                    frappe.msgprint({
                        title: __('成功'),
                        indicator: 'green',
                        message: r.message.message
                    });
                    // 3. 刷新页面以看到更新后的“上次抓取的时间”
                    frm.reload_doc();
                }
            }
        });
    },





    开启任务调度器: function (frm) {
        frappe.call({
            // 关键：这是从 apps/fengjing_app/ 后面开始算的 Python 路径
            method: "fengjing_app.fengjing_business.doctype.fengjing___product_corresponding_platform___configuration.fengjing___product_corresponding_platform___configuration.开启任务调度器",
            freeze: true,
            freeze_message: "正在开启调度器...",
            callback: function (r) {
                if (r.message) {
                    frappe.msgprint(r.message);
                }
            }
        });
    },

    查看走势: function (frm) {
        frappe.set_route("amazon-rank-trend");
    }



});

// --- 子表逻辑：Amazon ASIN 排名配置 ---
// 页面手工新增的行默认视为同行；程序在后端新增的自有商品行会明确写入“是否同行 = 0”。
frappe.ui.form.on('Amazon SKU Ranking Configuration Table', {
    抓取asin配置的子表_add: function (frm, cdt, cdn) {
        frappe.model.set_value(cdt, cdn, '是否同行', 1).then(() => {
            设置ASIN配置行状态(frm, cdt, cdn);
        });
    },

    是否同行: function (frm, cdt, cdn) {
        设置ASIN配置行状态(frm, cdt, cdn);
    },

    form_render: function (frm, cdt, cdn) {
        设置ASIN配置行状态(frm, cdt, cdn);
    }
});

function 设置ASIN配置行状态(frm, cdt, cdn) {
    const row = locals[cdt] && locals[cdt][cdn];
    const table = frm.fields_dict['抓取asin配置的子表'];
    const grid_row = table && table.grid && table.grid.get_row(cdn);
    if (!row || !grid_row) return;

    const 是同行 = Number(row.是否同行 || 0) === 1;
    // 自有 ASIN 只能由程序创建；页面手工新增的行一定是同行。
    // 因此无论哪种来源，“是否同行”都只用于显示，不允许手工切换。
    grid_row.toggle_editable('是否同行', false);
    grid_row.toggle_editable('属于哪个店铺', 是同行);

    ['是否同行', '属于哪个店铺'].forEach((fieldname) => {
        const controls = [
            grid_row.grid_form && grid_row.grid_form.fields_dict[fieldname],
            grid_row.on_grid_fields_dict && grid_row.on_grid_fields_dict[fieldname]
        ].filter(Boolean);
        controls.forEach((control) => {
            if (control.$input) {
                const 禁止编辑 = fieldname === '是否同行' || !是同行;
                control.$input.prop('disabled', 禁止编辑);
            }
        });
    });
}

function 刷新全部ASIN配置行状态(frm) {
    const table = frm.fields_dict['抓取asin配置的子表'];
    if (!table || !table.grid) return;
    table.grid.grid_rows.forEach((grid_row) => {
        设置ASIN配置行状态(frm, grid_row.doc.doctype, grid_row.doc.name);
    });
}

// --- 子表逻辑：Amazon订单同步配置 ---
frappe.ui.form.on('Amazon retrieves order configuration - sub-table', {
    同步历史订单: function (frm, cdt, cdn) {
        const row = locals[cdt] && locals[cdt][cdn];
        if (!row) return;
        if (frm.is_dirty()) {
            frappe.msgprint(__('请先保存配置，再启动历史订单同步。'));
            return;
        }
        if (!row.历史同步开始时间 || !row.历史同步结束时间) {
            frappe.msgprint(__('请先填写历史同步开始时间和历史同步结束时间。'));
            return;
        }
        frappe.confirm(
            __('确定开始同步店铺 {0} 的历史订单吗？任务将在后台运行。', [row.店铺]),
            () => frappe.call({
                method: 'fengjing_app.fengjing_business.doctype.fengjing___product_corresponding_platform___configuration.fengjing___product_corresponding_platform___configuration.启动亚马逊历史订单同步',
                args: { 配置行名称: row.name },
                freeze: true,
                freeze_message: __('正在提交历史订单同步任务...'),
                callback: function (r) {
                    if (r.message) {
                        frappe.show_alert({ message: r.message.message, indicator: 'green' });
                        frm.reload_doc();
                    }
                }
            })
        );
    }
});

// --- 子表逻辑：AI 配置项 (Fengjing - AI Configuration) ---
window.fengjingTeamorouterModels = window.fengjingTeamorouterModels || [];

frappe.ui.form.on('Fengjing - AI Configuration', {
    // 按钮 1：测试当前行 AI 节点的通讯是否正常
    丰境_ai测试链接: async function (frm, cdt, cdn) {
        // 获取当前点击行的数据对象
        let row = locals[cdt][cdn];
        // 清理秘钥前后的空格
        let clean_key = (row.丰境_ai秘钥 || "").trim();
        // 生成当前时间戳
        let timestamp = new Date().toLocaleString();

        // 校验秘钥是否填写
        if (!clean_key) {
            // 提示用户必须填写秘钥
            frappe.msgprint(__('请先填写 API 秘钥'));
            // 终止后续执行
            return;
        }

        // 构造专门用于测试的简单提示词
        let 提示词 = `请按照以下格式回复：[您已经链接到【真实模型名称】/时间：${timestamp}]`;

        try {
            // 通过 await 调用全局 window 下的分拨中心函数进行通讯测试
            // 参数 0 表示非物料调用，此时 check.js 会执行复杂的弹窗逻辑
            let 返回的数据 = await window.ai直连(frm, cdt, cdn, row, clean_key, 提示词);

            // 3. 如果是配置页手动测试 (是否是物料调用 !== 1)，则执行弹窗逻辑
            // 注意：这里的 ai的回答 和 timestamp 已经可以正常使用了
            // 1. 先把数据写进字段
            frappe.model.set_value(cdt, cdn, '丰境_ai通讯是否正常', "是:" + new Date().toLocaleString());

            // 2. 解冻屏幕（因为保存本身会有加载条，不需要叠加冻结）
            frappe.dom.unfreeze();

            // 3. 执行保存逻辑
            frm.save().then(() => {
                // --- 这里是保存成功后的回调 ---
                // 4. 【核心点】用 setTimeout 错开 Frappe 的保存刷新周期
                // 这样弹窗就不会被 "Already saving" 或者页面重载给冲掉
                setTimeout(() => {
                    frappe.msgprint({
                        title: `<span style="color: #4361ee; font-weight: 800;">🚀 AI 系统就绪</span>`,
                        indicator: 'blue',
                        message: `
                <div style="font-family: 'Inter', sans-serif; padding: 16px; background: linear-gradient(145deg, #ffffff, #f0f4ff); border-radius: 12px; border: 1px solid #dbe4ff;">
                    <div style="display: flex; align-items: center; margin-bottom: 12px;">
                        <div style="width: 8px; height: 8px; background-color: #2ecc71; border-radius: 50%; margin-right: 10px;"></div>
                        <span style="font-size: 13px; font-weight: 600; color: #1e293b;">通讯链路已加密确认</span>
                    </div>
                    <div style="background: rgba(255, 255, 255, 0.8); padding: 12px; border-radius: 8px; border: 1px dashed #ced4da;">
                        <p style="font-size: 11px; color: #64748b; margin-bottom: 6px;">AI 响应原始凭据</p>
                        <code style="font-size: 12px; color: #334155; word-break: break-all;">${返回的数据}</code>
                    </div>
                </div>`,
                        primary_action: {
                            label: __('确认并开启 AI 功能'),
                            action(values) {
                                // 1. 尝试通过 Frappe 标准 API 关闭（最优雅）
                                if (frappe.msgprint_dialog) {
                                    frappe.msgprint_dialog.hide();
                                }

                                // 2. 强力清理：关闭所有正在显示的 modal 弹窗（最稳妥）
                                // 这里的 values 参数其实就是当前弹窗对象，我们直接调用它的 hide
                                if (cur_dialog) cur_dialog.hide();

                                // 3. 针对一些特殊情况，直接移除 DOM 遮罩层（兜底方案）
                                $('.modal:visible').modal('hide'); // 如果是 Bootstrap 模式
                                $('.modal-backdrop').remove();     // 移除黑色遮罩
                                $('body').removeClass('modal-open'); // 恢复身体滚动

                                // 4. 最后的提示
                                frappe.show_alert({
                                    message: __('AI 引擎已挂载'),
                                    indicator: 'blue'
                                });
                            }
                        }
                    });
                }, 500); // 延迟 500 毫秒，确保页面已经从 "Saving" 状态恢复过来

            }).catch(save_err => {
                // 如果保存失败（比如必填项没写），依然可以弹窗提示，但重点在排查报错
                console.error("保存过程中出错:", save_err);
                frappe.msgprint(__('AI 通讯正常，但保存单据时被拦截，请检查必填项。'));
            });
        } catch (err) {
            // 如果通讯过程中发生任何错误（Promise reject）
            console.error("测试连接失败:", err);
            // 错误处理已在全局函数中通过 msgprint 完成，此处仅记录日志
        }
    },

    // 按钮 2：根据不同的 AI 来源跳转到对应的 API 申请页面
    丰境_去获取ai秘钥: function (frm, cdt, cdn) {
        // 获取当前行数据
        let row = locals[cdt][cdn];
        // 默认设置为 Google Gemini 的申请地址
        let url = 'https://aistudio.google.com/projects';

        // 根据来源字段的值，动态修改跳转链接
        if (row.丰境_ai来源 === 'OpenAI') {
            // 如果是 OpenAI 来源
            url = 'https://platform.openai.com/api-keys';
        } else if (row.丰境_ai来源 === 'DeepSeek') {
            // 如果是 DeepSeek 来源
            url = 'https://platform.deepseek.com/api_keys';
        } else if (row.丰境_ai来源 === 'Teamorouter') {
            url = 'https://teamorouter.cn/?i=3c7710321b';
        }
        // 在新窗口打开对应的申请页面
        window.open(url, '_blank');
    },

    // 当用户修改“AI 来源”字段时触发的逻辑
    丰境_ai来源: function (frm, cdt, cdn) {
        // 检查填充函数是否存在
        if (typeof 填充ai链接 === "function") {
            // 调用下方定义的 API 链接自动填充逻辑
            填充ai链接(frm, cdt, cdn);
        }
        设置中转站模型状态(frm, cdt, cdn);
    },

    // 新增子表行时，默认的 AI 来源不会触发上面的字段变更事件，需主动填充。
    丰境_ai配置页面_add: function (frm, cdt, cdn) {
        setTimeout(() => {
            填充ai链接(frm, cdt, cdn);
            设置中转站模型状态(frm, cdt, cdn);
        }, 0);
    },

    // 打开某一行的完整编辑界面时，立即同步两个控件的启用状态。
    form_render: function (frm, cdt, cdn) {
        设置中转站模型状态(frm, cdt, cdn);
    },

    // 从 TeamoRouter 实时获取当前秘钥可用的模型。
    获取模型: async function (frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (row.丰境_ai来源 !== 'Teamorouter') {
            frappe.msgprint(__('只有 AI 来源为 Teamorouter 时才能获取模型。'));
            return;
        }

        const clean_key = (row.丰境_ai秘钥 || '').trim();
        if (!clean_key) {
            frappe.msgprint(__('请先填写 API 秘钥。'));
            return;
        }

        const base_url = (row.丰境_api链接 || 'https://api.teamorouter.cn/v1')
            .trim().replace(/\/$/, '');
        const models_url = base_url.endsWith('/v1')
            ? `${base_url}/models`
            : `${base_url}/v1/models`;

        frappe.dom.freeze(__('正在获取 TeamoRouter 模型...'));
        try {
            const response = await fetch(models_url, {
                method: 'GET',
                headers: { 'Authorization': `Bearer ${clean_key}` }
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error?.message || data.message || `HTTP ${response.status}`);
            }

            const models = (data.data || []).map(item => item.id).filter(Boolean);
            if (!models.length) {
                throw new Error(__('接口没有返回可用模型。'));
            }

            // 缓存本次获取结果，避免子表刷新或重新打开行时选项消失。
            window.fengjingTeamorouterModels = models;

            const control = 获取子表字段控件(frm, cdn, '中转站模型');
            if (control && typeof control.set_data === 'function') {
                control.set_data(['', ...models]);
                control.refresh();
            } else if (control) {
                control.df.options = ['', ...models].join('\n');
                control.refresh();
            }

            if (row.中转站模型 && !models.includes(row.中转站模型)) {
                await frappe.model.set_value(cdt, cdn, '中转站模型', '');
            }
            frappe.show_alert({
                message: __('已获取 {0} 个可用模型，请在“中转站模型”中选择。', [models.length]),
                indicator: 'green'
            });
        } catch (err) {
            console.error('获取 TeamoRouter 模型失败:', err);
            frappe.msgprint({
                title: __('获取模型失败'),
                indicator: 'red',
                message: frappe.utils.escape_html(err.message || String(err))
            });
        } finally {
            frappe.dom.unfreeze();
        }
    }
});

function 获取子表字段控件(frm, cdn, fieldname) {
    const table = frm.fields_dict['丰境_ai配置页面'];
    const grid_row = table && table.grid && table.grid.get_row(cdn);
    if (!grid_row) return null;
    return (grid_row.grid_form && grid_row.grid_form.fields_dict[fieldname])
        || (grid_row.on_grid_fields_dict && grid_row.on_grid_fields_dict[fieldname])
        || null;
}

function 设置中转站模型状态(frm, cdt, cdn) {
    const row = locals[cdt] && locals[cdt][cdn];
    if (!row) return;
    const enabled = row.丰境_ai来源 === 'Teamorouter';
    const table = frm.fields_dict[row.parentfield || '丰境_ai配置页面'];
    const grid_row = table && table.grid && table.grid.get_row(cdn);
    if (!grid_row) return;

    grid_row.toggle_editable('中转站模型', enabled);
    grid_row.toggle_editable('获取模型', enabled);
    if (enabled) {
        const cached = window.fengjingTeamorouterModels || [];
        const models = [...new Set([row.中转站模型, ...cached].filter(Boolean))];
        const model_control = 获取子表字段控件(frm, cdn, '中转站模型');
        if (model_control && typeof model_control.set_data === 'function') {
            model_control.set_data(['', ...models]);
            model_control.refresh();
        } else if (model_control) {
            model_control.df.options = ['', ...models].join('\n');
            model_control.refresh();
        }
    }
    ['中转站模型', '获取模型'].forEach((fieldname) => {
        const control = 获取子表字段控件(frm, cdn, fieldname);
        if (control && control.$input) {
            control.$input.prop('disabled', !enabled);
        }
    });
}

function 刷新全部中转站模型状态(frm) {
    const table = frm.fields_dict['丰境_ai配置页面'];
    if (!table || !table.grid) return;
    table.grid.grid_rows.forEach((grid_row) => {
        设置中转站模型状态(frm, grid_row.doc.doctype, grid_row.doc.name);
    });
}

/**
 * 内部辅助函数：根据 AI 来源自动填充对应的默认 API 接口地址
 */
function 填充ai链接(frm, cdt, cdn) {
    // 获取子表当前行数据
    let row = locals[cdt][cdn];

    // 定义主流 AI 厂商的官方标准接口链接映射表
    const api_links = {
        'Gemini': 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent',
        'OpenAI': 'https://api.openai.com/v1/chat/completions',
        'DeepSeek': 'https://api.deepseek.com/chat/completions',
        'Teamorouter': 'https://api.teamorouter.cn/v1'
    };

    // 匹配当前行选中的 AI 来源
    if (row.丰境_ai来源 && api_links[row.丰境_ai来源]) {
        // 将匹配到的链接写入子表的“API 链接”字段中
        frappe.model.set_value(cdt, cdn, '丰境_api链接', api_links[row.丰境_ai来源]);
        // 弹出蓝色提示告知用户链接已自动更新
        frappe.show_alert({
            message: __('{0} 的 API 链接已自动同步', [row.丰境_ai来源]),
            indicator: 'blue'
        });
    }
}




// 测试亚马逊api
// 注意：'accounts' 替换为你主表中子表字段的名字
frappe.ui.form.on('Amazon API configuration', {
    // 监听子表中的按钮点击，假设按钮字段名为 'test_button'
    测试api: function (frm, cdt, cdn) {
        let row = locals[cdt][cdn]; // 获取当前行的数据

        frappe.call({
            method: "测试亚马逊api", // 调用的函数名
            doc: frm.doc, // 关键：指向主表文档
            args: {
                "account_name": row.name // 把当前子表行的 ID 传过去
            },
            freeze: true,
            callback: function (r) {
                if (r.message) {
                    const result = r.message;
                    frappe.msgprint({
                        title: result.status === 'success' ? __('Amazon API 测试成功') : __('Amazon API 测试失败'),
                        message: result.message || __('没有返回测试结果'),
                        indicator: result.status === 'success' ? 'green' : 'red'
                    });
                }
            }
        });
    }
});

