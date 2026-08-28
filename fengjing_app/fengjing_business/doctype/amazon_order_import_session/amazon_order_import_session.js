// Copyright (c) 2026, Fengjing E-Commerce and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Amazon Order Import Session", {
// 	refresh(frm) {

// 	},
// });


frappe.ui.form.on('Amazon Order Import Session', {

    // 点击右上角去配置标题
    refresh: function (frm) {
        frm.add_custom_button(__('配置文件标题'), function () {
            let 列表路径 = "/app/amazon-internal-form-binding-table-title?单据类型=Amazon+Order+Summary";
            window.open(列表路径, '_blank');
        });
    },

    // 下载翻译后的文件
    下载翻译后的订单序号: function (frm) {

        // 1. 【必须放在最前面】先定义函数，再使用它
        const 触发执行下载 = () => {
            if (!frm.doc.订单原始文件) {
                frappe.msgprint(__('请先在【订单原始文件】字段上传附件！'));
                return;
            }

            // --- 第一步：先校验 ---
            frappe.call({
                // 这里的路径必须完整：app名.文件名.函数名
                method: "fengjing_app.fengmaode.检查是否绑定了标题",
                args: {
                    "站点区": frm.doc.站点区,
                    "所在的表单": "Amazon Order Summary",
                },
                callback: (r) => {
                    // --- 第二步：校验通过后，再执行下载 ---
                    // 如果后端 throw 了，这里不会被执行，用户会看到红框提示
                    if (r.message === true) {
                        let url = `/api/method/run_doc_method?method=下载修改过的基础订单文件&dt=${frm.doc.doctype}&dn=${frm.doc.name}`;
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

    // 当“订单原始文件”字段上传完成后触发
    目标店铺: function (frm) {
        // 逻辑：如果文件已上传且“目标店铺”字段不为空
        if (frm.doc.订单原始文件 && frm.doc.目标店铺) {
            // 如果单据当前有未保存的改动（Dirty 状态），则执行保存
            if (frm.is_dirty()) {
                frm.save().then(() => {
                    frappe.show_alert({
                        message: __('文件已关联并自动保存'),
                        indicator: 'green'
                    });
                });
            }
        }
    },

    // 2. 响应“跳转到亚马逊下载订单页面”按钮字段
    跳转到亚马逊下载订单页面: function (frm) {
        const amz_url = "https://sellercentral.amazon.com/order-reports-and-feeds/reports/allOrders";
        window.open(amz_url, '_blank');
    },

    开始导入: function (frm) {
        // 基础检查：必须有文件和店铺才能开始
        if (!frm.doc.订单原始文件 || !frm.doc.目标店铺) {
            frappe.msgprint(__('请确保【订单原始文件】已上传且【目标店铺】已选择'));
            return;
        }

        // --- 新增：JS 查重逻辑 ---
        let 导入内容 = frm.doc.导入内容 || [];
        let 已存在的组合 = new Set();
        let 重复行记录 = [];

        导入内容.forEach((row) => {
            // 提取四个核心字段，转为字符串并去空格
            let 订单号 = (row.亚马逊订单号 || "").toString().trim();
            let 时间 = (row.下单时间 || "").toString().trim();
            let 数量 = (row.数量 || "").toString().trim();
            let sku = (row.卖家skuid || "").toString().trim();

            // 只有四个字段都不为空时才校验
            if (订单号 && 时间 && 数量 && sku) {
                let 指纹 = `${订单号}|${时间}|${数量}|${sku}`;
                if (已存在的组合.has(指纹)) {
                    重复行记录.push(`第 ${row.idx} 行：订单 ${订单号}`);
                } else {
                    已存在的组合.add(指纹);
                }
            }
        });

        if (重复行记录.length > 0) {
            frappe.msgprint({
                title: __('重复数据校验失败'),
                indicator: 'red',
                message: `<b>发现以下行存在完全重复（订单号+时间+数量+SKU）：</b><br><br>` +
                    重复行记录.join('<br>') +
                    `<br><br><small>请处理后再点击开始导入。</small>`
            });
            return; // 发现重复直接拦截，不执行后续逻辑
        }
        // --- 查重结束 ---

        // 定义真正干活的函数
        let 执行后端导入 = function () {

            frm.call('开始导入订单').then(r => {
                if (!r.message) return; // 收到 True，继续往下跑
                frm.reload_doc().then(() => {
                    frm.call('开始统计各种金额').then(() => {
                        frappe.msgprint(__('导入并统计完成')); // 这里又有一个弹窗
                    });
                });
            });

        };

        // --- 核心分流处理 ---
        if (frm.is_dirty()) {
            // 场景 A：有修改，必须先保存，确保后端能读到最新数据
            frm.save().then(() => {
                执行后端导入();
            });
        } else {
            // 场景 B：没有任何修改，直接跳过保存步骤，运行后端函数
            执行后端导入();
        }
    }

});

