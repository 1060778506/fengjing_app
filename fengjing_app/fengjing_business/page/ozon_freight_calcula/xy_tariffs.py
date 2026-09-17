"""September 3 XY sheet snapshots. Runtime does not depend on the workbook."""


def routes():
    result = []
    groups = [
        ("Extra Small", 3.37, .001, .5, 1, 1500, 60, 90, 0),
        ("Budget", 25.83, .501, 30, 1, 1500, 60, 150, 0),
        ("Small", 17.97, .001, 2, 1501, 7000, 60, 150, 0),
        ("Big", 40.44, 2.001, 30, 1501, 7000, 150, 310, 12000),
        ("Premium Small", 24.71, .001, 5, 7001, 250000, 150, 250, 0),
        ("Premium Big", 69.64, 5.001, 30, 7001, 250000, 150, 310, 12000),
    ]
    source = "【含计算器】20260903XY兴远最全价格表 / "
    for mode, destination, sheet in (
        ("FBP", "俄罗斯", "OZON-FBP运费"),
        ("RFBS", "吉尔吉斯斯坦", "OZON-吉尔吉斯斯坦专线运费"),
    ):
        for i, (group, fixed, lo, hi, vlo, vhi, side, total, divisor) in enumerate(groups):
            for speed in (["Economy", "Standard"] if mode == "FBP" else ["Standard"]):
                if mode == "FBP":
                    # E14 explicitly says .0258/g for Standard Premium Big too.
                    rate = 25.8 if i == 5 else (19.1 if i in (1, 3) else 28.1) if speed == "Economy" else (28.1 if i in (1, 3) else 39.3)
                    days = "12–15天" if speed == "Economy" else "4–8天"
                    note = "按绥芬河至莫斯科分拣中心时效。允许电池、液体仍需遵守禁运清单；京东/跨越优惠揽收。广州至绥芬河另列3元/kg或260元/m³，原表未明确二者取值规则，未计入本报价。Premium Big标准单价按E14的0.0258元/克录入，请向承运商复核。"
                    liquid = True
                else:
                    fixed = 23.83 if i == 1 else fixed
                    rate = 31.4 if i == 5 else 28.1 if i in (1, 3) else 39.3
                    vlo, vhi = [(1, 1600), (1, 1600), (1501, 7200), (1501, 7200), (7001, 18000), (7001, 18000)][i]
                    days = "30–35天"
                    liquid = i != 0
                    note = "原表按PUDO到取货点报价；电池允许，Extra Small禁止液体。原表货值档位有重叠（1501–1600、7001–7200），保留原限制，不擅自调整。遵守禁运清单，实际可寄品类需确认。"
                result.append(dict(
                    id=f"兴远-XY-{mode}-{destination}-{group}-{speed}",
                    provider="兴远", name=f"{group} · {speed}", enabled=True,
                    destination=destination, mode=mode, speed=speed,
                    fixed=fixed, rate=rate, min_weight=lo, max_weight=hi,
                    min_value=vlo, max_value=vhi, max_side=side, max_sum=total,
                    divisor=divisor, max_billable=0, sorted_sides=[], step=0,
                    surcharge=0, battery=True, liquid=liquid, days=days,
                    source=source + sheet, note=note))
    # Country labels are Drawing4 text boxes, not worksheet cell values.
    for suffix, name, country, fixed, rate, speed, days, minimum in (
        ("RU", "Ozon平台e邮宝特惠 · Standard", "俄罗斯", 15, 34.75, "Standard", "15–20天", [14, 11, 0]),
        ("KZ", "E邮宝特快陆运 · Standard", "哈萨克斯坦", 1.7, 34, "Standard", "15–20天", [14, 11, 0]),
        ("BY-Air", "E邮宝航空直飞 · Express", "白俄罗斯", 16.2, 77.4, "Express", "10–15天", []),
        ("BY-Land", "E邮宝特快陆运 · Standard", "白俄罗斯", 16.2, 31.5, "Standard", "15–20天", []),
    ):
        result.append(dict(
            id=f"兴远-XY-Post-{suffix}", provider="兴远", name=name, enabled=True,
            destination=country, mode="RFBS", speed=speed, fixed=fixed, rate=rate,
            min_weight=.001, max_weight=5, min_value=0, max_value=0,
            no_value_limit=True, max_side=60, max_sum=90, min_sorted_sides=minimum,
            divisor=0, max_billable=0, sorted_sides=[], step=0, surcharge=0,
            battery=False, liquid=False, days=days, source=source + "OZON-E邮宝运费",
            note="原表未注明货值限制，不沿用其他邮政表的1000元上限。按实重、不计抛；每日16:00截单，可提供预上网。普通长方体按表中尺寸检查；圆卷邮件有独立限制，本计算器未实现圆卷包装。带电、液体未注明，默认不放行。时效为预计签收时间；Standard/Express为本页面筛选分类。"))
    return result
