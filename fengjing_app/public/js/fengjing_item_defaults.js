// Item 表单创建阶段的默认值修正。
// doctype_js 会在表单初始化前加载，早于全局 app_include_js。
frappe.ui.form.on('Item', {
    setup(frm) {
        if (!frm?.doc || !(frm.doc.__islocal === 1 || String(frm.doc.name || '').startsWith('new-item-'))) return;

        frm.doc.has_batch_no = 0;
        const field = frappe.meta?.get_docfield?.('Item', 'has_batch_no');
        if (field) {
            field.default = 0;
            field.__default_value = 0;
        }
    },
});
