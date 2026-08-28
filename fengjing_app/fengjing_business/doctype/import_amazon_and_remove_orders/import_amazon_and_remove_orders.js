// Copyright (c) 2026, Fengjing E-Commerce and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Import Amazon and remove orders", {
// 	refresh(frm) {

// 	},
// });




frappe.ui.form.on("Import Amazon and remove orders", {

    // 点击右上角去配置标题
    refresh: function (frm) {
        frm.add_custom_button(__('配置文件标题'), function () {
            let 列表路径 = "/app/amazon-internal-form-binding-table-title?单据类型=Amazon+removes+order+details";
            window.open(列表路径, '_blank');
        });
    },

    // 2. 响应“跳转到亚马逊下载订单页面”按钮字段
    点击打开下载数据页: function (frm) {
        const amz_url = "https://sellercentral.amazon.com/reportcentral/REMOVAL_ORDER_DETAIL/1";
        window.open(amz_url, '_blank');
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
                    "站点区": frm.doc.项目类型_选择区域,
                    "所在的表单": "Amazon removes order details",
                },
                callback: (r) => {
                    // --- 第二步：校验通过后，再执行下载 ---
                    // 如果后端 throw 了，这里不会被执行，用户会看到红框提示
                    if (r.message === true) {
                        let url = `/api/method/run_doc_method?method=下载翻译过的移除订单文件&dt=${frm.doc.doctype}&dn=${frm.doc.name}`;
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

    开始导入: function (frm) {
        // 1. 基础校验：没选文件直接拦截
        if (!frm.doc.添加csv文件) {
            frappe.msgprint(__('请先上传文件'));
            return;
        }
        // 2. 核心逻辑：定义一个真正的执行函数
        let 执行逻辑 = function () {
            frappe.call({
                doc: frm.doc,
                method: "开始处理并导入移除订单",
                freeze: true,
                freeze_message: __("正在同步最新数据并导入子表..."),
                callback: function (r) {
                    if (!r.exc) {
                        // 1. 第一步：提示导入成功
                        frappe.show_alert({
                            message: __('数据已成功导入，正在计算汇总...'),
                            indicator: 'blue'
                        });

                        // 2. 第二步：发起第二次调用去执行统计
                        frm.call({
                            doc: frm.doc,
                            method: "开始统计各种数据总和",
                            freeze: true,
                            freeze_message: __("正在重新计算总额..."),
                            callback: function (r2) {
                                if (!r2.exc) {
                                    // 3. 全部完成后刷新页面，看到最新的金额
                                    frm.refresh();
                                    frappe.msgprint(__('所有数据已导入并统计完成！'));
                                }
                            }
                        });
                    }
                }
            });
        };
        // 3. 判断是否需要保存
        if (frm.is_dirty()) {
            // 如果页面有改动（比如刚传了文件还没点保存按钮）
            frm.save().then(() => {
                执行逻辑();
            });
        } else {
            // 页面没改动，直接执行
            执行逻辑();
        }
    }
});