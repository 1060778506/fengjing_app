app_name = "fengjing_app"
app_title = "Fengjing Business"
app_publisher = "Fengjing E-Commerce"
app_description = "Internal business management app for cross-border e-commerce"
app_email = "1060778506@qq.com"
app_license = "mit"


def run_amazon_orders():
    """Short scheduler entry; Scheduled Job Type.method is limited to 140 chars."""
    from fengjing_app.fengjing_business.doctype.amazon_order_configuration.amazon_order_configuration import (
        run_scheduled_order_sync,
    )

    return run_scheduled_order_sync()


def run_amazon_finances():
    """Short scheduler entry; Scheduled Job Type.method is limited to 140 chars."""
    from fengjing_app.fengjing_business.doctype.amazon_financial_configuration.amazon_financial_configuration import (
        run_scheduled_financial_sync,
    )

    return run_scheduled_financial_sync()


def run_amazon_rankings():
    """Short scheduler entry; Scheduled Job Type.method is limited to 140 chars."""
    from fengjing_app.fengjing_business.doctype.amazon_ranking_configuration.amazon_ranking_configuration import (
        run_scheduled_ranking_sync,
    )

    return run_scheduled_ranking_sync()


def run_ozon_orders():
    """Short scheduler entry; Scheduled Job Type.method is limited to 140 chars."""
    from fengjing_app.fengjing_business.doctype.ozon_order_configuration.ozon_order_configuration import (
        run_scheduled_order_sync,
    )

    return run_scheduled_order_sync()


def run_ozon_rankings():
    """Short scheduler entry; Scheduled Job Type.method is limited to 140 chars."""
    from fengjing_app.fengjing_business.doctype.ozon_ranking_configuration.ozon_ranking_configuration import (
        run_scheduled_ranking_sync,
    )

    return run_scheduled_ranking_sync()


def run_ozon_prices():
    """Short scheduler entry; Scheduled Job Type.method is limited to 140 chars."""
    from fengjing_app.fengjing_business.doctype.ozon_price_configuration.ozon_price_configuration import (
        run_scheduled_price_recording,
    )

    return run_scheduled_price_recording()


def run_ozon_finances():
    """Short scheduler entry; Scheduled Job Type.method is limited to 140 chars."""
    from fengjing_app.fengjing_business.doctype.ozon_financial_configuration.ozon_financial_configuration import (
        run_scheduled_financial_sync,
    )

    return run_scheduled_financial_sync()


def run_ozon_settlement_statements():
    """Short scheduler entry for independent Ozon settlement statements."""
    from fengjing_app.fengjing_business.doctype.ozon_settlement_statement_configuration.ozon_settlement_statement_configuration import (
        run_scheduled_statement_sync,
    )

    return run_scheduled_statement_sync()

fixtures = [
    # 第一个：导出计量单位 (UOM)
    {
        "dt": "UOM",
        "filters": [
            ["uom_name", "in", [
                "副", "根", "张", "个", "盒", "瓶", "把", "套", 
                "包", "件", "箱", "条", "打", "组", "卷", "扎", 
                "米", "平方米", "千克", "克", "千个", "万个", 
                "吨", "磅", "盎司", "毫克", "只", "双", "升", 
                "毫升", "立方米", "加仑", "厘米", "毫米", 
                "英寸", "英尺", "平方厘米"
            ]]
        ]
    },

    #这是项目类型
    #{
    #    "dt": "Project Type",
    #    "filters": [
    #        ["name", "in", [
    #            "亚马逊-自营北美(美国、加拿大、墨西哥、巴西)=站点",
    #            "亚马逊-自营欧洲(英国、德国、法国、意大利、西班牙、波兰、荷兰、瑞典、比利时)=站点",
    #            "亚马逊-自营亚太(日本、澳大利亚、新加坡、印度)=站点",
    #            "亚马逊-自营中东(土耳其、阿联酋、沙特、埃及)=站点",
    #            "temu-全托管(全球)=站点"
    #            "tiktok-pop美区(美国)=站点"
    #        ]]
    #    ]
    #},
    #这是 客户
    #{
    #    "dt": "Customer",
    #    "filters": [
    #        ["customer_name", "in", [
    #            "shein-半托管=平台",
    #            "shein-全托管=平台",
    #            "temu-半托管=平台",
    #            "temu-全托管=平台",
    #            "tiktok-pop=平台",
    #            "亚马逊-自营=平台"
    #        ]]
    #    ]
    #},
    #仓库类型
    {"dt": "Warehouse Type", "filters": [["name", "in", ["FBA", "AWD", "FBT", "其它第三方仓库"]]]},
    #字段，自定义字段
    {
        "dt": "Custom Field",
        "filters": [["module", "=", "Fengjing Business"]] 
    },
    # 1. 抓取你对系统默认字段的修改（比如：改了标签名、隐藏了字段、设置了默认值）
    {
        "dt": "Property Setter",
        "filters": [["module", "=", "Fengjing Business"]]
    },
    # Amazon CSV 离线翻译：报表类型和翻译词典跟随 App 导出。
    {
        "dt": "Amazon CSV Report Type"
    },
    {
        "dt": "Amazon CSV Translation Rule"
    },
    # 2. 导出仪表盘配置 (新增：这一步决定了卡片显示在哪个页面)
    #{
    #    "dt": "Dashboard",
    #    "filters": [["module", "=", "Fengjing Business"]]
    #},
    #数字卡
    #{
    #    "dt": "Number Card", 
    #    "filters": [["module", "=", "Fengjing Business"]]
    #},
    # 3. 核心：导出你那 81 行对照表的所有数据
    #"Amazon internal form binding table title",


    # 1. 导出 v3 版本的查询 (注意加了 v3)
    #{
    #    "dt": "Insights Query v3", 
    #    "filters": [["title", "like", "%tabAmazon Rank SKU Log%"]] 
    #},
    
    # 2. 导出 v3 版本的图表
    #{
    #    "dt": "Insights Chart v3", 
    #    "filters": [["title", "like", "亚马逊产品销量排名"]]
    #},
    
    # 3. 导出 v3 版本的仪表盘
    #{
    #    "dt": "Insights Dashboard v3", 
    #    "filters": [["title", "=", "亚马逊产品销量排名-仪表盘"]]
    #},

]
# --- 合并后的标准配置 ---

# 1. 安装 App 后执行（包含翻译和科目同步）
after_install = [
    "fengjing_app.install.追加科目表入口"                # 第三步：同步会计科目
]

# 2. 升级/迁移后执行（包含翻译和科目同步）
# 注意：这里只保留这一处，把后面那个重复的 after_migrate 删掉！
after_migrate = [
    "fengjing_app.install.追加科目表入口"     #加载新的科目表
]

# 3. 系统内新公司创建时的初始化
doc_events = {
    "Company": {
        "after_insert": "fengjing_app.install.在系统内新建公司"
    },
    "Stock Entry": {
        "before_validate": "fengjing_app.fengjing_business.doctype.temu_material_movement.temu_material_movement.validate_temu_product_bundle_movement"
    }
}

# 只有在“整页刷新”或者“重新进入系统”加载初始化数据时，才会调用一次。
extend_bootinfo = "fengjing_app.install.新系统公司执行的净化科目表"
# 加载科目表弹窗js
app_include_js = "/assets/fengjing_app/js/fengjing_init_check.js?v=20260923-3"

# 2. 专门针对 Account 列表页的 JS 加载（官方推荐做法）
# 这样系统只会在打开科目表时，才精准加载这个 JS


# 各平台后台调度入口
scheduler_events = {
    "all": [
        "fengjing_app.hooks.run_amazon_rankings",
        "fengjing_app.hooks.run_amazon_orders",
        "fengjing_app.hooks.run_amazon_finances",
        "fengjing_app.hooks.run_ozon_orders",
        "fengjing_app.hooks.run_ozon_rankings",
        "fengjing_app.hooks.run_ozon_prices",
        "fengjing_app.hooks.run_ozon_finances",
        "fengjing_app.hooks.run_ozon_settlement_statements"
    ],
    "daily": [
        "fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log.清理过期排名日志"
    ]
}

# APP图标
app_logo_url = "/assets/fengjing_app/images/fengjing-logo.svg"
