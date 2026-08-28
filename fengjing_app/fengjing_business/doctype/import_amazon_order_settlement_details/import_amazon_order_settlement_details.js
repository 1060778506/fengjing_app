// Copyright (c) 2026, Fengjing E-Commerce and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Import Amazon order settlement details", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on("Import Amazon order settlement details", {
    refresh: function (frm) {
        // --- 1. 添加自定义按钮 ---
        frm.add_custom_button(__('配置文件标题'), function () {
            let 列表路径 = "/app/amazon-internal-form-binding-table-title?单据类型=Amazon+Settlement+Detail";
            window.open(列表路径, '_blank');
        });
        /*// --- 2. 强制弹窗选择站点逻辑 ---        强制弹窗先不用
        // 仅在【新建记录】且【站点字段为空】时执行
        if (frm.is_new() && (!frm.doc.站点 || frm.doc.站点.includes("请") || frm.doc.站点.includes("选"))) {
            // 延迟 0.5 秒弹出，确保页面渲染完成
            setTimeout(() => {
                let d = new frappe.ui.Dialog({
                    title: __('请选择所属站点,仅支持以下站点'),
                    fields: [
                        {
                            label: '当前操作国家*其他站点没有开发,因为没有数据,你可以提供数据我来开发15953992133',
                            fieldname: 'selected_site',
                            fieldtype: 'Select',
                            options: ['美国', '加拿大', '墨西哥'],
                            default: '美国',
                            reqd: 1
                        }
                    ],
                    primary_action_label: __('确认'),
                    primary_action(values) {
                        // 设置表单上的站点字段
                        frm.set_value('站点', values.selected_site);
                        d.hide();
                    }
                });
                // 禁止点击遮罩层关闭弹窗（强制选择）
                d.no_cancel_flag = true;
                d.show();
            }, 500);
        }*/
    },

    // 假设你的按钮字段名叫做 open_import_page
    点击打开导入页面: function (frm) {
        // 1. 获取“站点”字段的值 (假设字段名为 marketplace)
        let site = frm.doc.店铺国家;
        console.log(site)
        // 2. 定义不同站点的基础链接
        // 亚马逊通常使用相同的路径，但通过不同的卖家中心域名区分
        let base_url = "";
        if (site === "United States") {
            base_url = "https://sellercentral.amazon.com/payments/reports-repository?";
        } else if (site === "Canada") {
            base_url = "https://sellercentral.amazon.ca/payments/reports-repository?";
        } else if (site === "Mexico") {
            base_url = "https://sellercentral.amazon.com.mx/payments/reports-repository?";
        } else {
            // 如果没选或者选了其他的，默认给个提示或者打开通用后台
            frappe.msgprint(__("其他站点没有开发,因为没有数据,你可以提供数据我来开发15953992133"));
            return;
        }
        // 3. 在新标签页打开链接
        window.open(base_url, '_blank');
    },
 
    // 💡 监听 DocType 内部那个名为 "开始导入" 的按钮字段
    下载翻译后的pdf文件(frm) {
        if (!frm.doc.保存pdf附件) {
            frappe.msgprint(__('请先上传 PDF 文件并保存文档'));
            return;
        }
        // 1. 获取当前附件的完整 URL (如果不是私有文件)
        let file_url = frm.doc.保存pdf附件;
        // 2. 弹出提示并引导跳转
        frappe.confirm(
            __('即将前往 iLovePDF 进行专业原位翻译。请在打开的页面中上传该文件。是否继续？'),
            () => {
                // 🚀 核心：在新窗口打开 iLovePDF 翻译页面
                window.open("https://www.ilovepdf.com/zh-tw/translate-pdf", "_blank");
                frappe.show_alert({
                    message: __('已为您跳转至专业翻译平台'),
                    indicator: 'blue'
                });
            }
        );
    },

    // 下载翻译后的文件
    下载翻译后的csv文件: function (frm) {
        // 1. 【必须放在最前面】先定义函数，再使用它
        const 触发执行下载 = () => {
            if (!frm.doc.添加csv文件) {
                frappe.msgprint(__('请先在【添加csv文件】字段上传附件！'));
                return;
            }

            // --- 第一步：先校验 ---
            frappe.call({
                // 这里的路径必须完整：app名.文件名.函数名
                method: "fengjing_app.fengmaode.检查是否绑定了标题",
                args: {
                    "站点区": frm.doc.店铺国家,
                    "所在的表单": "Amazon Settlement Detail",
                },
                callback: (r) => {
                    // --- 第二步：校验通过后，再执行下载 ---
                    // 如果后端 throw 了，这里不会被执行，用户会看到红框提示
                    if (r.message === true) {
                        let url = `/api/method/run_doc_method?method=下载翻译过的结算订单文件&dt=${frm.doc.doctype}&dn=${frm.doc.name}`;
                        window.open(url, '_blank');
                    }
                }
            });
        };
        // 2. 核心逻辑：判断是否未保存
        if (frm.is_dirty()) {
            frappe.show_alert({
                message: __('未保存改动，正在自动保存...'),
                indicator: 'blue'
            });
            frm.save().then(() => {
                // 现在这里能找到“触发执行下载”了
                触发执行下载();
            }).catch((err) => {
                console.error(err);
                frappe.msgprint(__('自动保存失败'));
            });
        } else {
            // 这里也能找到
            触发执行下载();
        }
    },

    开始导入并绑定: function (frm) {
        // 1. 检查是否有未保存的更改
        if (frm.is_dirty()) {
            // 如果有更改，强制先保存
            frm.save().then(() => {
                // 保存成功后执行逻辑
                frm.trigger("执行后端绑定函数");
            });
        } else {
            // 如果没改动，直接运行
            frm.trigger("执行后端绑定函数");
        }
    },
    执行后端绑定函数: async function (frm) {
        try {
            // 第一步：导入
            await frm.call({
                doc: frm.doc,
                method: '处理csv文件进行导入',
                freeze: true,
                freeze_message: __("正在同步导入数据...")
            });

            // 全部完成
            frm.reload_doc();
            frappe.show_alert({
                message: __('所有步骤已顺序完成！'),
                indicator: 'green'
            });

        } catch (e) {
            // 任何一步出错都会跳到这里
            console.error(e);
        }
    }
});