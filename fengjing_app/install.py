import frappe
import os
import json
import csv
from frappe.utils import get_bench_path
from frappe.translate import set_default_language
import shutil
import re
import time
from erpnext.accounts.doctype.account.chart_of_accounts.chart_of_accounts import create_charts
from frappe.utils.nestedset import rebuild_tree




'''
下面的内官方已经修复了,可以无脑删除了，
下面的内官方已经修复了,可以无脑删除了，
下面的内官方已经修复了,可以无脑删除了，
# --- 丰境终极拦截：暴力替换 Insights 入口 ---
# 只要这个 api.py 被 Frappe 扫过一眼，手术就完成了
try:
    from insights.setup.demo import DemoDataFactory

    # 定义统一的拦截动作
    def mocked_skip(*args, **kwargs):
        print("🌸 [丰境拦截] 已阻断 Insights 演示数据动作。")
        return True

    # 1. 封死总入口
    DemoDataFactory.run = mocked_skip
    
    # 2. 封死分步骤入口（这就是您担心的那两个）
    DemoDataFactory.sync_tables = mocked_skip
    DemoDataFactory.create_sample_workbook = mocked_skip
    
    # 3. 封死最底层的联网下载（预防万一）
    DemoDataFactory.download_demo_data = mocked_skip

    print("[补丁全家桶] Insights 工厂已彻底断网并禁用下载演示数据。")
except Exception:
    print("[补丁全家桶] Insights 工厂拦截下载演示数据失败,可能是app已经升级没有演示数据了。")
上面的内官方已经修复了,可以无脑删除了，
上面的内官方已经修复了,可以无脑删除了，
上面的内官方已经修复了,可以无脑删除了，
'''





# ==========================================
# 场景 1：新公司创建 - 全净空初始化
# 对应 hooks.py 中的 doc_events: {"Company": {"after_insert": ...}}
# ==========================================
@frappe.whitelist()
def 页面触发的强制净化逻辑(company_name=None):
    frappe.db.set_default("看门狗是否运行", "1")
    frappe.db.commit() # 钉死在数据库里
    """
    专门给初始化页面调用的函数，不看勾选框，直接开干
    """
    # 如果前端没传名字，我们自动抓取系统中唯一的（或最新的）那家公司
    if not company_name:
        company_name = frappe.db.get_single_value('Global Defaults', 'default_company') or \
                       frappe.get_all("Company", limit=1, order_by="creation desc")[0].name
    # 在后台终端打印开始初始化的提示信息
    print(f"--- 🚀 正在为新公司 {company_name} 执行【全净空】初始化 ---")
    # 1. 安全检查：如果数据库中已经存在该公司的账务凭证(GL Entry)，则绝对不能执行删除操作，防止误删已记账数据
    if frappe.db.exists("GL Entry", {"company": company_name}):
        frappe.throw(
            msg=f"检测到公司 <b>{company_name}</b> 已经存在账务凭证（GL Entry），为了数据安全，系统已自动拦截初始化丰境科目初始化操作！",
            title="⚠️ 严禁操作"
        )
        return
    新建仓库()
    try:

        # 1. 预检查：先读 JSON，如果 JSON 有错，在删数据前就报错中断
        path = frappe.get_app_path('fengjing_app', 'data', 'chart_of_accounts', 'zh_fengjing_coa.json')
        if not os.path.exists(path):
            frappe.throw(f"找不到科目表配置文件: {path}")

        with open(path, "r", encoding="utf-8") as f:
            chart_data = json.load(f)
        tree = chart_data.get("tree")
        # 等待10秒等其他app运行完成的，
        frappe.publish_realtime('msgprint', {
                'message': f'正在等待基础丰境科目表写入，请稍候...',
                'indicator': 'blue',
                'alert': True
            })
        time.sleep(10)

        # 3. 彻底删除系统默认生成的旧科目：根据公司名称删除 tabAccount 表中的所有记录
        frappe.db.sql("DELETE FROM `tabAccount` WHERE company=%s", company_name)

        # 提交数据库事务，使更改生效
        frappe.db.commit()

        # 4. 获取 JSON 科目表配置文件的完整物理路径
        path = frappe.get_app_path('fengjing_app', 'data', 'chart_of_accounts', 'zh_fengjing_coa.json')
        # 如果文件不存在，则抛出异常提醒用户
        if not os.path.exists(path):
            frappe.throw(f"找不到科目表配置文件: {path}")

        # 以 UTF-8 编码读取 JSON 文件内容
        with open(path, "r", encoding="utf-8") as f:
            chart_data = json.load(f)
        
        # 从 JSON 数据中获取 tree（科目树）结构
        tree = chart_data.get("tree")


        # 核心操作：大规模创建科目
        create_charts(company=company_name, custom_chart=tree)

        # 清除科目缓存，防止前端读取到旧数据
        frappe.clear_cache(doctype="Account")




        #查询科目表
        def 查询科目表名称(account_number, company):
            account = frappe.db.get_value(
                "Account",
                {
                    "account_number": account_number,
                    "company": company,
                    "is_group": 0
                },
                "name"
            )

            if not account:
                frappe.throw(f"❌ 找不到科目编号：{account_number}")

            return account

        #查询仓库
        def 查询仓库(warehouse_name, company):
            warehouse = frappe.db.get_value(
                "Warehouse",
                {
                    "warehouse_name": warehouse_name,
                    "company": company,
                    "is_group": 0
                },
                "name"
            )

            if not warehouse:
                frappe.throw(f"❌ 找不到仓库：{warehouse_name}")

            return warehouse

        # 2. 断开公司与系统自带旧科目的关联：将公司表中的所有默认科目字段设为 NULL，防止后续删除科目时因外键约束报错
        # 2. 断开公司与系统自带旧科目的关联（v15 兼容版）
        frappe.db.sql("""
            UPDATE `tabCompany`
            SET
                default_bank_account=%s,/* 默认银行科目 */
                default_expense_account=%s,/* 默认销货成本科目 */
                default_cash_account=%s,/* 默认现金科目 */
                default_income_account=%s,/* 默认收入科目 */
                default_receivable_account=%s,/* 默认应收科目 */
                default_discount_account=%s,/* 默认付款折扣科目 */
                default_payable_account=%s,/* 默认应付科目 */
                write_off_account=%s,/* 销账科目 */
                unrealized_profit_loss_account=%s,/* 未实现损益科目 */
                exchange_gain_loss_account=%s,/* 汇兑损益科目 */
                unrealized_exchange_gain_loss_account=%s,/* 未实现汇兑损益科目 */
                round_off_account=%s,/* 小数精度尾差科目 */
                round_off_for_opening=%s,/* 期初四舍五入 */
                default_deferred_revenue_account=%s,/* 默认递延收入科目 */
                default_deferred_expense_account=%s,/* 默认递延费用科目 */
                accumulated_depreciation_account=%s,/* 累计折旧科目 */
                disposal_account=%s,/* 资产处置收益/损失科目 */
                depreciation_expense_account=%s,/* 折旧费用科目 */
                capital_work_in_progress_account=%s,/* 在建工程科目 */
                asset_received_but_not_billed=%s,/* 暂估资产（已收货，未开票） */
                purchase_expense_account=%s,/* 采购费用科目 */
                purchase_expense_contra_account=%s,/* 采购费用备抵科目 */
                service_expense_account=%s,/* 服务费用科目 */
                default_expense_claim_payable_account=%s,/* 默认费用报销应付账款科目 */
                default_payroll_payable_account=%s,/* 默认应付薪资账户 */
                default_employee_advance_account=%s,/* 默认员工预支账户 */
                stock_adjustment_account=%s,/* 库存调整科目 */
                stock_received_but_not_billed=%s,/* 暂估库存(已收货，未开票) */
                default_provisional_account=%s,/* 默认暂估费用科目 */
                default_inventory_account=%s,/* 默认存货科目 */
                default_operating_cost_account=%s,/* 默认额外费用科目(物料移动) */
                default_warehouse_for_sales_return=%s,/* 默认销售退货仓 */
                default_scrap_warehouse=%s,/* 默认报废仓 */
                default_wip_warehouse=%s,/* 默认车间仓 */
                default_fg_warehouse=%s/* 默认成品仓(收料仓) */
            WHERE name=%s
        """, (
            查询科目表名称("100299_fj", company_name),# 默认银行科目
            查询科目表名称("540199_fj", company_name),#默认销货成本科目
            查询科目表名称("1001_fj", company_name),#默认现金科目
            查询科目表名称("5001_fj", company_name),#默认收入科目
            查询科目表名称("1122_fj", company_name),#默认应收科目
            查询科目表名称("560303_fj", company_name),#默认付款折扣科目
            查询科目表名称("2202_fj", company_name),#默认应付科目
            查询科目表名称("560198_fj", company_name),#销账科目
            查询科目表名称("560304_fj", company_name),#未实现损益科目
            查询科目表名称("560301_fj", company_name),#汇兑损益科目
            查询科目表名称("560301_fj", company_name),#未实现汇兑损益科目
            查询科目表名称("560302_fj", company_name),#小数精度尾差科目
            查询科目表名称("2242_fj", company_name),#期初四舍五入
            查询科目表名称("2401_fj", company_name),#默认递延收入科目
            查询科目表名称("1801_fj", company_name),#默认递延费用科目
            查询科目表名称("1602_fj", company_name),#累计折旧科目
            查询科目表名称("1606_fj", company_name),#资产处置收益/损失科目
            查询科目表名称("560208_fj", company_name),#折旧费用科目
            查询科目表名称("1604_fj", company_name),#在建工程科目
            查询科目表名称("2202_fj", company_name),#暂估资产（已收货，未开票）
            查询科目表名称("540102_fj", company_name),#采购费用科目
            查询科目表名称("2202_fj", company_name),#采购费用备抵科目
            查询科目表名称("560204_fj", company_name),#服务费用科目
            查询科目表名称("2202_fj", company_name),#默认费用报销应付账款科目
            查询科目表名称("221199_fj", company_name),#默认应付薪资账户
            查询科目表名称("1221_fj", company_name),#默认员工预支账户
            查询科目表名称("5604_fj", company_name),#库存调整科目
            查询科目表名称("2210_fj", company_name),#暂估库存(已收货，未开票)
            查询科目表名称("2241_fj", company_name),#默认暂估费用科目
            查询科目表名称("1405_fj", company_name),#默认存货科目
            查询科目表名称("540199_fj", company_name),#默认额外费用科目(物料移动)
            查询仓库("退货仓库", company_name),#默认销售退货仓
            查询仓库("退货仓库", company_name),#默认报废仓
            查询仓库("本地仓库", company_name),#默认车间仓
            查询仓库("本地仓库", company_name),#默认成品仓(收料仓)
            company_name
        ))

        # --- 新增逻辑：清理支付方式中的失效链接，防止前端保存公司时校验报错 ---
        frappe.db.sql("""
            DELETE FROM `tabMode of Payment Account` 
            WHERE company = %s
        """, company_name)
        # ----------------------------------------





        # 终端打印成功完成的标记
        print(f"成功：{company_name} 的科目表已重置为标准模板！")

        # 再次强制刷新科目缓存，确保 UI 界面能够立即同步显示
        frappe.clear_cache(doctype="Account")
        
        # 在前端界面弹出绿色成功提示框，告知用户初始化已完成
        frappe.msgprint(
            msg=f"已成功为 {company_name} 初始化科目表",
            realtime=True
        )
        # --- 核心操作：清除缓存 ---
        frappe.clear_cache(doctype="Account")
        frappe.clear_cache(doctype="Company") # 直接清掉 Company 类的所有缓存，简单粗暴有效
    except Exception as e:
        # 发生任何错误时执行数据库回滚，确保不会留下脏数据
        frappe.db.rollback()
        
        # 记录详细的错误堆栈到系统的“错误日志”列表中，方便后台排查
        frappe.log_error(message=frappe.get_traceback(), title="丰境科目表初始化失败")
        
        # 直接使用 throw，它自带中断流程和报错框功能
        frappe.throw(
            msg=(
                f"❌ <b>科目表初始化失败！</b><br>"
                f"错误原因：{str(e)}<br><br>"
                f"请检查 JSON 配置文件或科目类型是否正确。"
            ),
            title="初始化异常",
            exc=frappe.ValidationError  # 👈 throw 认识 exc，msgprint 不认识
        )
    return True

# install.py


@frappe.whitelist()  # 👈 这个装饰器必须有！
def 更改看门狗参数():
    """专门用来打标记，防止重复弹窗"""
    frappe.db.set_default("看门狗是否运行", 1)
    frappe.db.commit()
    return True


def 新系统公司执行的净化科目表(bootinfo):

    # 1. 【看门狗检查】：如果已经处理过，直接熄灯，不让前端弹窗
    if frappe.db.get_default("看门狗是否运行") == "1":
        bootinfo.is_fresh_system = 0
        return

    # 2. 获取基础统计数据
    company_count = frappe.db.count("Company")
    # 排除系统内置用户，只算真实用户
    #user_count = frappe.db.count("User", {"enabled": 1, "name": ["not in", ["Administrator", "Guest"]]})

    # 默认不弹窗
    bootinfo.is_fresh_system = 0
    # 3. 初步判定：只有 1 个公司且只有 1 个真实用户时才具备“新系统”相貌
    if company_count == 1 :
        # 拿到这唯一的公司名字
        company_name = frappe.db.get_value("Company", {}, "name")
        if not company_name:
            return

        # 4. 【核心安全检查】：如果满足以下任一条件，说明系统已经动过了，强行熄灯（is_fresh_system = 0）
        
        # 检查是否有账务凭证 (GL Entry)
        has_gl_entry = frappe.db.exists("GL Entry", {"company": company_name})
        
        # 检查是否有业务单据（比如销售订单或采购订单，防止没过账但有数据）
        has_sales_order = frappe.db.exists("Sales Order", {"company": company_name})
        
        # 检查是否已经创建了物料 (Item) - 排除系统可能自带的演示数据
        has_items = frappe.db.count("Item") > 0
        # 逻辑：只有当 (没账务) 且 (没物料) 且 (没订单) 时，才认为是“白纸一张”，可以弹窗
        if not has_gl_entry and not has_items and not has_sales_order:
            bootinfo.is_fresh_system = 1
        else:
            # 只要有数据，哪怕是新公司也不弹窗，安全第一
            bootinfo.is_fresh_system = 0







# 在系统内新建公司
def 在系统内新建公司(doc, method=None):
    """
    当新建公司保存后（after_insert），系统会自动运行这个函数。
    doc: 就是当前你正在保存的那家公司的所有数据。
    """
    
    # 这里的 custom_use_fengjing_coa 就是你那个“【会删除所有科目】添加丰境标准科目表”的字段名
    # 如果用户勾选了，doc.get("字段名") 的值会是 1
    # 默认字段必须是0，如果默认是1，那就和新装的系统有冲突，
    if doc.get("custom_use_fengjing_coa") == 1:
        
        # 1. 确认逻辑：可以在日志里记一下
        frappe.logger().info(f"检测到公司 {doc.name} 勾选了丰境标准，准备开始净化...")

        # 2. 调用你之前写好的 111 项科目注入逻辑
        # 注意：这里传入的是 doc.name (公司名)
        页面触发的强制净化逻辑(doc.name)
        
        # 3. 提示用户（在保存后的界面弹出小黑条提示）
        frappe.msgprint(f"已为 {doc.name} 导入：丰境标准科目表。")
    else:
        # 如果没勾选，啥也不干，走系统默认流程
        pass








def 新建仓库():
    # 1. 获取系统内所有的公司列表
    # 我们需要拿公司的全名 (name) 和简称 (abbr)
    all_companies = frappe.get_all("Company", fields=["name", "abbr"])
    
    if not all_companies:
        return

    # 2. 定义 17 个仓库的逻辑结构模板
    # 格式: (显示名, 是否是组, 父级显示名)
    structure = [


        ("所有仓库", 1, None),
        
        ("本地仓库", 0, "所有仓库"),
        ("TikTok美国FBT仓库", 0, "所有仓库"),
        ("Temu全托管国内仓", 0, "所有仓库"),
        ("退货仓库", 0, "所有仓库"),

        ("亚马逊AWD中转FBA仓库", 1, "所有仓库"),
        ("亚马逊AWD仓库", 1, "所有仓库"),
        ("亚马逊FBA仓库", 1, "所有仓库"),
        ("亚马逊GWD中转FBA仓库", 1, "所有仓库"),
        ("亚马逊GWD仓库", 1, "所有仓库"),
        ("亚马逊丢失仓库", 1, "所有仓库"),

        ("亚马逊AWD美国中转FBA仓", 0, "亚马逊AWD中转FBA仓库"),
        ("亚马逊AWD加拿大中转FBA仓", 0, "亚马逊AWD中转FBA仓库"),
        ("亚马逊AWD墨西哥中转FBA仓", 0, "亚马逊AWD中转FBA仓库"),

        ("亚马逊AWD美国仓", 0, "亚马逊AWD仓库"),
        ("亚马逊AWD加拿大仓", 0, "亚马逊AWD仓库"),
        ("亚马逊AWD墨西哥仓", 0, "亚马逊AWD仓库"),

        ("亚马逊GWD美国中转FBA仓", 0, "亚马逊GWD中转FBA仓库"),
        ("亚马逊GWD加拿大中转FBA仓", 0, "亚马逊GWD中转FBA仓库"),
        ("亚马逊GWD墨西哥中转FBA仓", 0, "亚马逊GWD中转FBA仓库"),

        ("亚马逊GWD深圳仓", 0, "亚马逊GWD仓库"),
        ("亚马逊GWD上海仓", 0, "亚马逊GWD仓库"),
        ("亚马逊GWD宁波仓", 0, "亚马逊GWD仓库"),

        ("亚马逊FBA美国仓", 0, "亚马逊FBA仓库"),
        ("亚马逊FBA加拿大仓", 0, "亚马逊FBA仓库"),
        ("亚马逊FBA墨西哥仓", 0, "亚马逊FBA仓库"),

        ("亚马逊美国丢失仓", 0, "亚马逊丢失仓库"),
        ("亚马逊加拿大丢失仓", 0, "亚马逊丢失仓库"),
        ("亚马逊墨西哥丢失仓", 0, "亚马逊丢失仓库"),

    ]

    # 3. 第一层循环：遍历每一家公司
    for company_info in all_companies:
        company_name = company_info.name
        abbr = company_info.abbr

        # 4. 第二层循环：在该公司下创建 17 个仓库
        for wh_name, is_group, parent_wh_name in structure:
            # 动态拼接带当前公司后缀的 ID
            # 这样公司 A 会生成 "所有仓库 - A"，公司 B 会生成 "所有仓库 - B"
            full_name = f"{wh_name} - {abbr}"
            parent_full_name = f"{parent_wh_name} - {abbr}" if parent_wh_name else None

            # 检查该公司下的这个仓库是否已存在
            if not frappe.db.exists("Warehouse", full_name):
                try:
                    doc = frappe.new_doc("Warehouse")
                    doc.name = full_name 
                    doc.warehouse_name = wh_name
                    doc.is_group = is_group
                    doc.company = company_name
                    doc.parent_warehouse = parent_full_name
                    
                    # 插入数据，ignore_if_duplicate 确保即使已存在也不报错
                    doc.insert(ignore_permissions=True, ignore_if_duplicate=True)
                except Exception as e:
                    # 记录错误，但不中断其他仓库或公司的创建
                    frappe.log_error(f"公司 {company_name} 创建仓库 {wh_name} 失败: {str(e)}")

    # 5. 重建树结构并提交（这一步在所有公司处理完后统一做一次即可）
    rebuild_tree("Warehouse")
    frappe.db.commit()





# ==========================================
# 场景 2：App 升级 - 增量追加新科目
# 对应 hooks.py 中的 after_migrate
# ==========================================

def 追加科目表入口():
    # 获取系统中所有的公司列表
    companies = frappe.get_all("Company")
    # 遍历每个公司，逐一执行科目同步
    for company in companies:
        sync_new_accounts(company_name=company.name)

def sync_new_accounts(company_name):


    #追加科目表前先追加仓库
    新建仓库()

    # 开启：物料可使用序列号 / 批号
    frappe.db.set_single_value(
        "Stock Settings",
        "enable_serial_and_batch_no_for_item",
        1
    )

    # 查询默认物料组
    def 查询物料组名称(item_group_name):
        item_group = frappe.db.get_value(
            "Item Group",
            {"item_group_name": item_group_name},
            "name"
        )

        if not item_group:
            print(f"❌ 找不到物料组：{item_group_name}")

        return item_group

    # 默认物料组
    frappe.db.set_single_value(
        "Stock Settings",
        "item_group",
        查询物料组名称("Products"),#这个就是产品的意思，Products == 产品
    )

    # 查询默认仓库
    def 查询仓库名称(warehouse_name, company):
        warehouse = frappe.db.get_value(
            "Warehouse",
            {
                "warehouse_name": warehouse_name,
                "company": company
            },
            "name"
        )

        if not warehouse:
            print(f"❌ 找不到仓库：{warehouse_name} - {company}")

        return warehouse


    # 默认仓库
    frappe.db.set_single_value(
        "Stock Settings",
        "default_warehouse",
        查询仓库名称("本地仓库",company_name),
    )


    #一周开始日
    frappe.db.set_single_value(
        "System Settings",
        "first_day_of_the_week",
        "Monday"
    )



    # 3. 核心：插完立刻钉死，更新数据库版本号
    frappe.db.commit()
    frappe.clear_cache()







    # 获取 JSON 科目表文件的路径
    path = frappe.get_app_path('fengjing_app', 'data', 'chart_of_accounts', 'zh_fengjing_coa.json')
    # 如果路径不存在则直接返回，不执行任何操作
    if not os.path.exists(path): return

    # 读取并解析 JSON 科目数据
    with open(path, "r", encoding="utf-8") as f:
        chart_data = json.load(f)
    
    # 获取科目树结构
    tree = chart_data.get("tree")
    # 如果树结构为空则退出
    if not tree: return


    # 定义内部递归函数用于遍历科目树
    def process_tree(children, parent_name=None):
        # 增加防御：如果 children 本身不是字典，直接返回（防止递归到属性值）
        if not isinstance(children, dict):
            return

        # 遍历当前层级的每个科目条目
        for acc_name, details in children.items():
            # 1. 过滤掉非科目的属性字段（增加了 account_currency 和 country 防止溢出）
            # 过滤掉所有已知的“配置属性”键
            excluded_keys = [
                "account_type", "root_type", "is_group", "account_number", 
                "account_currency", "country", "name", "parent_account"
            ]

            if acc_name in excluded_keys:
                continue

            # --- 核心修复点 1：检查 details 类型 ---
            # 你的报错原因就在这里：如果 details 是 "1002_fj" 这种字符串，它没有 .get 方法。
            # 只有当 details 是一个字典时，它才代表一个具体的科目数据。
            if not isinstance(details, dict):
                continue

            # --- 核心修复点 2：将 return 改为 continue ---
            # 原来的 return 会导致后续所有科目都不再检查，改为 continue 仅跳过当前异常项。
            account_number = details.get("account_number")
            if not account_number:
                print(f"未设置科目代码，跳过创建: {acc_name}")
                continue

            # 只按 company + account_number 判断是否存在
            exists = frappe.db.exists("Account", {
                "account_number": account_number,
                "company": company_name
            })

            if not exists:
                create_single_account(company_name, acc_name, parent_name, details)
            
            # --- 核心修复点 3：递归逻辑外置 ---
            # 即使 exists 为 True（比如“银行存款”组已存在），也要继续向下递归
            # 这样才能发现并添加该组下面新增加的子科目（如“亚马逊美国站USD”）
            process_tree(details, acc_name)

    # 从根节点开始启动递归遍历
    process_tree(tree)
    # 遍历完成后提交所有新增记录
    frappe.db.commit()

# 创建单个科目的核心函数
def create_single_account(company, acc_name, parent, details):
    try:
        # 实例化一个新的 Account 文档对象
        new_acc = frappe.new_doc("Account")
        # 设置科目所属公司
        new_acc.company = company
        # 设置科目的显示名称
        new_acc.account_name = acc_name
        
        # --- 核心修复：根据父级名称获取其在数据库中的真实唯一 ID ---
        if parent:
            # 这里的 parent 是递归传递下来的父级 account_name
            actual_parent_id = frappe.db.get_value("Account", 
                {"account_name": parent, "company": company}, "name")
            
            # 如果在库中找不到这个父级，说明父级可能创建失败，无法建立子级，直接返回
            if not actual_parent_id:
                return

            # 设置新科目的父级关联 ID
            new_acc.parent_account = actual_parent_id
        # ------------------------------------

        # 设置科目是否为组（1为组，0为末级科目）
        new_acc.is_group = details.get("is_group", 0)
        # 设置科目类型（如：银行、现金、应收等）
        new_acc.account_type = details.get("account_type")
        # 设置会计科目编号
        new_acc.account_number = details.get("account_number")
        # 设置货币
        json_currency = details.get("account_currency")
        if json_currency:
            new_acc.account_currency = json_currency
        
        # 如果没有父级科目（即该科目是根科目），则必须设置其 root_type
        if not new_acc.parent_account:
            new_acc.root_type = details.get("root_type")
        
        # 执行插入操作，忽略权限检查强制写入数据库
        new_acc.insert(ignore_permissions=True)
        # 终端打印创建成功的完整科目 ID
        print(f"✅ 成功创建科目: {new_acc.name}")
        
    except Exception as e:
        # 如果创建过程中发生任何报错（如类型错误、编号重复），打印具体的错误原因
        pass










# 物料增加数量
@frappe.whitelist()
def 检测这是第几个物料():
    # 获取当前系统中 Item 的总数
    # 如果你需要按公司过滤，可以增加 filters={'company': company}
    count = frappe.db.count('Item')
    return count + 1


