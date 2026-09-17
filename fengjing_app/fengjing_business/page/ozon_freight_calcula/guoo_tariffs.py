"""Independent July 20 GUOO Ozon tariff snapshot; no runtime XLSX dependency."""


def routes():
    result = []
    groups = [
        ("Extra Small", .001, .5, 1, 1500, 60, 90),
        ("Budget", .501, 30, 1, 1500, 60, 150),
        ("Small", .001, 2, 1501, 7000, 60, 150),
        ("Big", 2.001, 30, 1501, 7000, 150, 310),
        ("Premium Small", .001, 5, 7001, 250000, 150, 250),
        ("Premium Big", 5.001, 30, 7001, 250000, 150, 310),
    ]
    fixed_ru = [3.37, 25.83, 17.97, 40.44, 24.71, 69.64]
    fixed_cis = [3.12, 23.92, 16.64, 37.44, 22.88, 64.48]
    for country, mode, sheet in [
        ("俄罗斯", "rFBS", "GUOO realFBS资费试算表"),
        ("俄罗斯", "FBP", "GUOO FBP资费试算表"),
        ("哈萨克斯坦", "rFBS", "哈萨克斯坦专线"),
        ("白俄罗斯", "rFBS", "白俄专线"),
        ("吉尔吉斯斯坦", "rFBS", "吉尔吉斯斯坦"),
        ("乌兹别克斯坦", "rFBS", "乌兹别克斯坦"),
    ]:
        for i, (group, lo, hi, vlo, vhi, side, total) in enumerate(groups):
            ru = country == "俄罗斯"
            speeds = ["Standard", "Economy"]
            if ru and mode == "rFBS" and i in (0, 2, 4):
                speeds.insert(0, "Express")
            if country in ("吉尔吉斯斯坦", "乌兹别克斯坦"):
                speeds = ["Economy"]
            if not ru:
                if country in ("哈萨克斯坦", "白俄罗斯"):
                    hi = 35 if i in (1, 3, 5) else hi
                    if i >= 4: vhi = 500000
                elif i >= 4:
                    vlo, vhi = 7001, 18000
                # The Big formula says 250 while its description says 310.
                if i == 3 and country in ("哈萨克斯坦", "白俄罗斯"): total = 250
            for speed in speeds:
                if ru:
                    rate = {"Express": 50.55 if i == 0 else 50.5,
                            "Standard": 28.1 if i in (1, 3) else 31.4 if i == 5 else 39.3,
                            "Economy": 19.1 if i in (1, 3) else 25.8 if i == 5 else 28.1}[speed]
                else:
                    rate = {"Standard": 26 if i in (1, 3) else 29.12 if i == 5 else 36.4,
                            "Economy": 17.68 if i in (1, 3) else 23.92 if i == 5 else 26}[speed]
                divisor = 12000 if i in (3, 5) and country in ("俄罗斯", "哈萨克斯坦", "白俄罗斯") else 0
                note = "2026.7.20历史运价；到点/到门同价合并。带电仅代表内置电池，纯电池、配套电池、液体等需单独核实。"
                if divisor:
                    note += " 大件按文字说明采用12000抛比；原Excel部分条件或计费重量公式未计抛，存在不一致，请向承运商确认。"
                if i == 3 and country in ("哈萨克斯坦", "白俄罗斯"):
                    note += " 三边和文字310cm、公式250cm，保守采用250cm。"
                if country == "吉尔吉斯斯坦" and i >= 4:
                    note += " 原表两条Premium渠道名称互换，按对应重量区间及单价归类。"
                if ru and mode == "FBP" and i == 3:
                    total = 250
                    note += " Big尺寸文字310cm、计抛公式250cm，保守采用250cm。"
                result.append(dict(
                    id=f"GUOO-{country}-{mode}-{group}-{speed}", provider="GUOO", name=f"{group} · {speed}",
                    enabled=True, destination=country, mode=mode, fixed=(fixed_ru if ru else fixed_cis)[i], rate=rate,
                    min_weight=lo, max_weight=hi, min_exclusive=False, min_value=vlo, max_value=vhi,
                    value_currency="RUB", value_exclusive=False, max_side=side, max_sum=total,
                    divisor=divisor, max_billable=0, sorted_sides=([150, 80, 80] if i in (3, 5) else [150, 50, 50] if i == 4 else []) if ru else [],
                    step=.001, surcharge=0, battery=speed != "Express", liquid=False, days="原表未注明",
                    source=f"GUOO产品资费测算表【2026.7.20更新】 / {sheet}", note=note))
    return result
