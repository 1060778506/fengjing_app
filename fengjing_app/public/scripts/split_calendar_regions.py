"""Create per-country calendar map files from the original province GeoJSON."""

import json
from pathlib import Path


MAP_DIR = Path(__file__).resolve().parent.parent / "data" / "Vector_map"
COUNTRIES_FILE = (
    MAP_DIR
    / "国家边界ne_10m_admin_0_countries_chn"
    / "ne_10m_admin_0_countries_chn.geojson"
)
REGIONS_DIR = MAP_DIR / "省州边界ne_10m_admin_1_states_provinces"
REGIONS_FILE = REGIONS_DIR / "ne_10m_admin_1_states_provinces.geojson"
OUTPUT_DIR = REGIONS_DIR / "countries"


def main():
    countries = json.loads(COUNTRIES_FILE.read_text(encoding="utf-8"))["features"]
    regions = json.loads(REGIONS_FILE.read_text(encoding="utf-8"))["features"]
    OUTPUT_DIR.mkdir(exist_ok=True)

    for country in countries:
        properties = country["properties"]
        country_code = properties["ADM0_A3"]
        iso2 = properties["ISO_A2"]
        selected = [
            region
            for region in regions
            if region["properties"].get("iso_a2") == iso2
            or region["properties"].get("adm0_a3") == country_code
        ]
        output = OUTPUT_DIR / f"{country_code}.geojson"
        output.write_text(
            json.dumps(
                {"type": "FeatureCollection", "features": selected},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    print(f"已按现有地图匹配规则生成 {len(countries)} 个国家文件：{OUTPUT_DIR}")


if __name__ == "__main__":
    main()
