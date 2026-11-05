// 新系统首次运行时的科目表初始化提示。
// 日常业务代码已迁移到独立的运行时文件。

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
