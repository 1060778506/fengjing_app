// Copyright (c) 2026, Fengjing E-Commerce and contributors
// For license information, please see license.txt

// frappe.ui.form.on("TEMU - Full Custody - Completion Document", {
// 	refresh(frm) {

// 	},
// });


//批量上传文件
frappe.ui.form.on('TEMU - Full Custody - Completion Document', {
    批量上传: function (frm) {
        // 添加一个批量上传按钮
        new frappe.ui.FileUploader({
            make_attachments: true, // 关联到当前文档
            on_success: (file_doc) => {
                // 每上传成功一个文件，自动在子表增加一行
                let row = frm.add_child("附件"); // 这里的 '附件' 替换为你子表的实际字段名

                // 将文件路径写入子表中的文件字段
                // 注意：请确认你子表里存放文件路径的字段名（比如叫 '文件' 还是 'file_url'）
                frappe.model.set_value(row.doctype, row.name, "文件", file_doc.file_url);

                frm.refresh_field("附件");
            }
        });
    }
});

// 自动选择
frappe.ui.form.on('TEMU - Five orders and financial attachments-Sub-table', {
    // 当子表中的文件上传字段发生变化时触发
    文件: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.文件) {
            // 1. 获取文件名称 (从 URL 中提取最后一段)
            let filename = row.文件.split('/').pop();

            // 3. 自动匹配文件类型
            // 这里你可以写逻辑，比如文件名包含“美区”就选“财务明细(美区)”
            if (filename.includes("美")) {
                frappe.model.set_value(cdt, cdn, '文件类型', '财务明细(美区)');
            } else if (filename.includes("欧")) {
                frappe.model.set_value(cdt, cdn, '文件类型', '财务明细(欧区)');
            } else if (filename.includes("全球")) {
                frappe.model.set_value(cdt, cdn, '文件类型', '财务明细(全球)');
            } else if (filename.includes("卖家中心")) {
                frappe.model.set_value(cdt, cdn, '文件类型', '财务明细(卖家中心)');
            } else {
                frappe.model.set_value(cdt, cdn, '文件类型', '');
            }
            // 刷新子表显示
            frm.refresh_field('attachments');
        }
    }
});

//禁止选择同一个选项
frappe.ui.form.on('TEMU - Five orders and financial attachments-Sub-table', {
    文件类型: function (frm, cdt, cdn) {
        var row = locals[cdt][cdn];

        if (row.文件类型) {
            let all_rows = frm.doc.附件 || [];
            let duplicate_found = false;

            $.each(all_rows, function (i, d) {
                if (d.文件类型 === row.文件类型 && d.name !== row.name) {
                    duplicate_found = true;
                    return false;
                }
            });

            if (duplicate_found) {
                let alert_msg = `文件类型 [${row.文件类型}] 已经存在，请勿重复选择。`;
                frappe.msgprint({
                    title: __('重复选择'),
                    indicator: 'red',
                    message: __(alert_msg)
                });
                frappe.model.set_value(cdt, cdn, "文件类型", "");
            }
        }
    }
});





// 按下按钮开始获取附件
frappe.ui.form.on('TEMU - Full Custody - Completion Document', {
    处理文件: function (frm) {
        let items = frm.doc.附件 || [];

        if (items.length === 0) {
            frappe.msgprint(__("请先在子表中上传文件并选择类型"));
            return;
        }

        // --- 将后端调用逻辑封装成一个函数，避免写两遍 ---
        let run_process = () => {
            frappe.call({
                method: "fengjing_app.fengjing_business.doctype.temu___full_custody___completion_document.temu___full_custody___completion_document.批量处理文件",
                args: {
                    关联文档名称: frm.doc.name,
                    文件列表: items
                },
                freeze: true,
                freeze_message: __("正在解析文件，请稍候..."),
                callback: function (r) {
                    if (r.message) {
                        // 1. 刷新文档
                        frm.reload_doc().then(() => {
                            // 2. 构造弹窗
                            let d = new frappe.ui.Dialog({
                                title: __("📊 财务处理最终对账报告"),
                                fields: [{
                                    fieldtype: 'HTML',
                                    fieldname: 'report_html',
                                    options: r.message.html
                                }]
                            });

                            // 3. 修改宽度
                            d.$wrapper.find('.modal-dialog').css({
                                "max-width": "1200px",
                                "width": "1200px"
                            });

                            d.show();
                        });
                    }
                }
            });
        };

        // --- 核心改进：判断文档状态 ---
        if (frm.is_dirty()) {
            // 如果文档被修改过（右上角有保存按钮），先保存
            frm.save().then(() => {
                run_process();
            });
        } else {
            // 如果文档已经保存过了，直接跑逻辑
            run_process();
        }
    }
});