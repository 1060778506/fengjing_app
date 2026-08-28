app_name = "fengjing_app"
app_title = "Fengjing Business"
app_publisher = "Fengjing E-Commerce"
app_description = "Internal business management app for cross-border e-commerce"
app_email = "1060778506@qq.com"
app_license = "mit"

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

    #这是项目类似
    {
        "dt": "Project Type",
        "filters": [
            ["name", "in", [
                "亚马逊-北美(美国、加拿大、墨西哥、巴西)=站点",
                "亚马逊-欧洲(英国、德国、法国、意大利、西班牙、波兰、荷兰、瑞典、比利时)=站点",
                "亚马逊-亚太(日本、澳大利亚、新加坡、印度)=站点",
                "亚马逊-中东(土耳其、阿联酋、沙特、埃及)=站点"
            ]]
        ]
    },
    #这是 客户
    {
        "dt": "Customer",
        "filters": [
            ["customer_name", "in", [
                "shein-半托管=平台",
                "shein-全托管=平台",
                "temu-半托管=平台",
                "temu-全托管=平台",
                "tiktok-pop=平台",
                "亚马逊-自营=平台"
            ]]
        ]
    },
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
    # 2. 导出仪表盘配置 (新增：这一步决定了卡片显示在哪个页面)
    {
        "dt": "Dashboard",
        "filters": [["module", "=", "Fengjing Business"]]
    },
    #数字卡
    {
        "dt": "Number Card", 
        "filters": [["module", "=", "Fengjing Business"]]
    },
    # 3. 核心：导出你那 81 行对照表的所有数据
    "Amazon internal form binding table title",


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
    }
}

# 只有在“整页刷新”或者“重新进入系统”加载初始化数据时，才会调用一次。
extend_bootinfo = "fengjing_app.install.新系统公司执行的净化科目表"
# 加载科目表弹窗js
app_include_js = "/assets/fengjing_app/js/fengjing_init_check.js"

# 2. 专门针对 Account 列表页的 JS 加载（官方推荐做法）
# 这样系统只会在打开科目表时，才精准加载这个 JS


# 定时执行亚马逊抓取排名的函数
scheduler_events = {
    "all": [
        # 指向刚才创建的那个入口函数
        "fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log.定时执行亚马逊抓取排名的函数"
    ]
}

# APP图标
app_logo_url = "/assets/fengjing_app/images/fengjing-logo.svg"