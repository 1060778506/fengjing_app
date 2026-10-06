// ERPNext 物料页面的丰境业务功能。

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
 * 放置位置：public/js/runtime/item.js
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
            method: "fengjing_app.events.item.检测这是第几个物料",
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
