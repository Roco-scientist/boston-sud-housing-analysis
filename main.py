import os
import time
import requests
import pandas as pd
import numpy as np
import folium
import plotly.express as px
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from shapely.geometry import shape, Point
from shapely.ops import unary_union
from folium.features import DivIcon
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from collections import defaultdict

# --- CONFIGURATION ---
SOURCE_CSV = "Properties Owned or Managed by Recovery Housing Providers.csv"
CACHE_CSV = "geocoded_cache.csv"
CENSUS_CSV = "census_data_for_2022_redistricting.csv"
WARD_GEOJSON_URL = "https://data.boston.gov/dataset/e491f77b-0094-40ea-ae20-f91cfa9f7ccc/resource/86e050bf-ca6a-45ff-aca1-c68aa611f7d6/download/boston_ward_boundaries.geojson"
PRECINCT_GEOJSON_URL = "https://data.boston.gov/dataset/c37ac1db-da06-4347-a130-96134be12fe6/resource/59ad0d8e-bafc-4206-9556-291a83dd137d/download/boston_precinct_boundaries.geojson"
CITATION = "Source: U.S. Census Bureau, 2020 Decennial Census P.L. 94-171 Redistricting Data; BPDA Research Division Analysis"
WARD_TO_NEIGHBORHOOD = {
    1: "East Boston",
    2: "Charlestown",
    3: "North End/West End",
    4: "Fenway/South End",
    5: "Beacon Hill/Back Bay",
    6: "South Boston/Seaport",
    7: "South Boston/Dorchester",
    8: "South End/Roxbury",
    9: "Roxbury/South End",
    10: "Mission Hill/JP",
    11: "Roxbury/JP",
    12: "Roxbury",
    13: "Dorchester",
    14: "Dorchester/Mattapan",
    15: "Dorchester",
    16: "Dorchester (Neponset)",
    17: "Dorchester (Codman Sq)",
    18: "Hyde Park/Mattapan",
    19: "JP/Roslindale",
    20: "West Roxbury/Roslindale",
    21: "Allston/Brighton",
    22: "Brighton",
}


# --- 1. DATA LOADING & GEOCODING ---
def get_geocoded_data():
    df = pd.read_csv(SOURCE_CSV).dropna(subset=["Street Address"])

    if os.path.exists(CACHE_CSV):
        print("Loading geocode cache...")
        cache_df = pd.read_csv(CACHE_CSV)
        df = pd.merge(
            df,
            cache_df[["Street Address", "lat", "lon"]],
            on="Street Address",
            how="left",
        )
    else:
        df["lat"], df["lon"] = np.nan, np.nan

    missing = df["lat"].isna()
    if missing.any():
        print(f"Geocoding {missing.sum()} new addresses...")
        geolocator = Nominatim(user_agent="boston_housing_mapper")
        geocode_service = RateLimiter(geolocator.geocode, min_delay_seconds=1.2)

        def do_geocode(row):
            try:
                nb = (
                    f", {row['Neighborhood']}"
                    if pd.notna(row.get("Neighborhood"))
                    else ""
                )
                addr = f"{row['Street Address']}{nb}, Boston, MA"
                loc = geolocator.geocode(addr)
                return (loc.latitude, loc.longitude) if loc else (np.nan, np.nan)
            except:
                return np.nan, np.nan

        new_coords = df[missing].apply(do_geocode, axis=1, result_type="expand")
        df.loc[missing, ["lat", "lon"]] = new_coords.values
        df[["Street Address", "lat", "lon"]].dropna(
            subset=["lat"]
        ).drop_duplicates().to_csv(CACHE_CSV, index=False)

    return df


# --- 2. CENSUS & SPATIAL PROCESSING ---
def process_spatial_data(df):
    # Load GeoJSON
    ward_geojson = requests.get(WARD_GEOJSON_URL).json()
    precinct_geojson = requests.get(PRECINCT_GEOJSON_URL).json()

    def find_geo_id(row, geojson, key_name):
        if pd.isna(row["lat"]):
            return np.nan
        pt = Point(row["lon"], row["lat"])
        for feat in geojson["features"]:
            if shape(feat["geometry"]).contains(pt):
                props = feat["properties"]
                val = props.get(key_name)
                if val is not None:
                    return int(val)
        return np.nan

    # Save manual fix file
    unfound = df[df["lat"].isna()]
    if not unfound.empty:
        unfound[["Street Address", "Neighborhood", "Owner/Manager"]].to_csv(
            "manual_fix_needed.csv", index=True
        )
        print(f"⚠️ {len(unfound)} addresses unfound. See manual_fix_needed.csv")

    df.dropna(subset=["lat"], inplace=True)
    df.drop_duplicates(subset=["Street Address"], inplace=True)

    print("Assigning Wards and Precincts...")
    df["Ward_ID"] = df.apply(lambda r: find_geo_id(r, ward_geojson, "Ward1"), axis=1)
    precinct_id_start = (
        df.apply(lambda r: find_geo_id(r, precinct_geojson, "Precinct1"), axis=1)
        .astype(int)
        .astype(str)
        .str.zfill(2)
    )
    df["Precinct_ID"] = [
        int(f"{int(ward_id)}{precinct_id}")
        for ward_id, precinct_id in zip(df["Ward_ID"], precinct_id_start)
    ]

    df[["Street Address", "lat", "lon", "Ward_ID", "Precinct_ID"]].to_csv(
        CACHE_CSV, index=False
    )

    # Process Census
    census = pd.read_csv(CENSUS_CSV)
    census["Precinct_ID"] = (
        census["Ward and Precinct (Updated 2022)"].astype(str).str.zfill(4).astype(int)
    )
    census["Total Population"] = pd.to_numeric(
        census["Total Population"].astype(str).str.replace(",", ""), errors="coerce"
    )
    census["White alone"] = pd.to_numeric(
        census["White alone"].astype(str).str.replace(",", ""), errors="coerce"
    )

    census["Ward_ID"] = (
        census["Ward and Precinct (Updated 2022)"]
        .astype(str)
        .str.zfill(4)
        .str[:2]
        .astype(int)
    )

    precinct_stats = (
        census.groupby("Precinct_ID")
        .agg({"Total Population": "sum", "White alone": "sum"})
        .reset_index()
    )
    site_counts_precinct = (
        df.groupby("Precinct_ID").size().reset_index(name="Site_Count")
    )

    p_stats = pd.merge(
        precinct_stats, site_counts_precinct, on="Precinct_ID", how="left"
    ).fillna(0)
    p_stats["Normalized_Sites"] = (
        p_stats["Site_Count"] / p_stats["Total Population"]
    ) * 10000
    p_stats["White_Pct"] = (p_stats["White alone"] / p_stats["Total Population"]) * 100

    # Export precinct stats
    p_stats.to_csv("precinct_summary_statistics.csv", index=False)

    ward_stats = (
        census.groupby("Ward_ID")
        .agg({"Total Population": "sum", "White alone": "sum"})
        .reset_index()
    )
    site_counts = df.groupby("Ward_ID").size().reset_index(name="Site_Count")
    w_stats = pd.merge(ward_stats, site_counts, on="Ward_ID", how="left").fillna(0)

    # Calculations
    w_stats["Normalized_Sites"] = (
        w_stats["Site_Count"] / w_stats["Total Population"]
    ) * 10000
    w_stats["White_Pct"] = (w_stats["White alone"] / w_stats["Total Population"]) * 100

    # ADD DEMOGRAPHIC GROUPING LOGIC (Specific Wards vs Rest)
    target_list = [8, 9, 11, 12, 13, 14, 15, 16, 17, 19]
    w_stats["Group"] = w_stats["Ward_ID"].apply(
        lambda x: "Target Wards" if x in target_list else "Remaining Wards"
    )

    comparison = w_stats.groupby("Group").agg(
        {"White_Pct": "mean", "Normalized_Sites": "mean", "Site_Count": "sum"}
    )

    print("\n" + "=" * 50)
    print("DEMOGRAPHIC COMPARISON SUMMARY")
    print("-" * 50)
    print(comparison.to_string())
    print("=" * 50 + "\n")

    # CREATE COMPREHENSIVE WARD OUTPUT FILE
    w_stats["Neighborhood"] = w_stats["Ward_ID"].map(WARD_TO_NEIGHBORHOOD)
    output_columns = [
        "Ward_ID",
        "Neighborhood",
        "Total Population",
        "White alone",
        "White_Pct",
        "Site_Count",
        "Normalized_Sites",
        "Group",
    ]
    w_stats[output_columns].to_csv("ward_summary_statistics.csv", index=False)
    print("Comprehensive ward data saved to 'ward_summary_statistics.csv'")

    # Statistics

    w_stats["Ward_Label"] = w_stats["Ward_ID"].apply(lambda x: f"Ward {x}")
    w_stats["Display_Label"] = (
        w_stats["Ward_Label"] + " (" + w_stats["Neighborhood"] + ")"
    )

    return df, w_stats, p_stats, ward_geojson, precinct_geojson


# --- 3. MAPPING ---
def create_ward_map(df, analysis_df, ward_geojson):
    m = folium.Map(
        location=[42.315, -71.08],
        zoom_start=13,
        tiles="cartodbpositron",
        control_scale=True,
        font_size="1.5rem",
    )

    # Gradient Fill
    folium.Choropleth(
        geo_data=ward_geojson,
        data=analysis_df,
        columns=["Ward_ID", "Normalized_Sites"],
        key_on="feature.properties.Ward1",
        fill_color="YlOrRd",
        fill_opacity=0.6,
        line_opacity=0.3,
        legend_name="Sites per 10k Residents",
    ).add_to(m)

    # 1. Define the target wards
    target_wards = [8, 9, 11, 12, 13, 14, 15, 16, 17, 19]
    
    # 2. Extract and union the geometries to create a single outer boundary
    belt_geoms = []
    for feat in ward_geojson['features']:
        if int(feat['properties'].get('Ward1', 0)) in target_wards:
            belt_geoms.append(shape(feat['geometry']))
    
    dissolved_belt = unary_union(belt_geoms)

    # 3. Add the single thick outer outline
    folium.GeoJson(
        dissolved_belt,
        name="Franklin Park Belt Outline",
        style_function=lambda x: {
            "fillColor": "none",
            "color": "black",
            "weight": 6,  # Thick outer line
            "opacity": 1
        }
    ).add_to(m)

    # 4. Add the Label (placed near the geographic center of the unioned shape)
    # folium.Marker(
    #     location=[42.308, -71.085], 
    #     icon=DivIcon(
    #         icon_size=(160,36),
    #         icon_anchor=(80,18),
    #         html='''<div style="font-size: 14pt; font-weight: bold; color: black; 
    #              background-color: rgba(255, 255, 255, 0.8); border: 2px solid black; 
    #              border-radius: 5px; text-align: center; padding: 3px;">
    #              Franklin Park Belt</div>''',
    #     )
    # ).add_to(m)

    # Ward Labels
    for feat in ward_geojson["features"]:
        ctr = shape(feat["geometry"]).centroid
        wnum = feat["properties"].get("Ward1")
        folium.Marker(
            [ctr.y, ctr.x],
            icon=DivIcon(
                html=f'<div style="font-size:10pt; font-weight:bold; text-shadow:1px 1px white;">{wnum}</div>'
            ),
        ).add_to(m)

    # Site Markers (with Jitter)
    tracker = defaultdict(int)
    for _, row in df.dropna(subset=["lat"]).iterrows():
        c = tracker[(row["lat"], row["lon"])]
        lat_j = (c * 0.00015) * (1 if c % 2 == 0 else -1)
        lon_j = (c * 0.00015) * (1 if c % 3 == 0 else -1)
        tracker[(row["lat"], row["lon"])] += 1

        folium.CircleMarker(
            location=[row["lat"] + lat_j, row["lon"] + lon_j],
            radius=5,
            color="white",
            weight=1,
            fill=True,
            fill_color="black",
            fill_opacity=0.8,
            popup=f"<b>{row['Street Address']}</b><br>{row['Owner/Manager']}",
        ).add_to(m)

    m.save("boston_housing_analysis.html")
    return "boston_housing_analysis.html"


def create_precinct_map(df, p_stats, p_geojson):
    for feat in p_geojson["features"]:
        w = str(feat["properties"]["Ward1"]).zfill(2)
        p = str(feat["properties"]["Precinct1"]).zfill(2)
        feat["properties"]["GEOID"] = int(w + p)

    m = folium.Map(
        location=[42.315, -71.08],
        zoom_start=13,
        tiles="cartodbpositron",
        control_scale=True,
    )

    folium.Choropleth(
        geo_data=p_geojson,
        data=p_stats,
        columns=["Precinct_ID", "Normalized_Sites"],
        key_on="feature.properties.GEOID",
        fill_color="YlOrRd",
        fill_opacity=0.7,
        legend_name="SUD Sites per 10k (Precinct Level)",
    ).add_to(m)

    # Precinct Labels
    # for feat in p_geojson['features']:
    #     ctr = shape(feat['geometry']).centroid
    #     pnum = feat['properties'].get('Precinct1')
    #     folium.Marker(
    #         [ctr.y, ctr.x],
    #         icon=DivIcon(html=f'<div style="font-size:10pt; font-weight:bold; text-shadow:1px 1px white;">{pnum}</div>')
    #     ).add_to(m)

    # Site Markers (with Jitter)
    tracker = defaultdict(int)
    for _, row in df.dropna(subset=["lat"]).iterrows():
        c = tracker[(row["lat"], row["lon"])]
        lat_j = (c * 0.00015) * (1 if c % 2 == 0 else -1)
        lon_j = (c * 0.00015) * (1 if c % 3 == 0 else -1)
        tracker[(row["lat"], row["lon"])] += 1

        folium.CircleMarker(
            location=[row["lat"] + lat_j, row["lon"] + lon_j],
            radius=5,
            color="white",
            weight=1,
            fill=True,
            fill_color="black",
            fill_opacity=0.8,
            popup=f"<b>{row['Street Address']}</b><br>{row['Owner/Manager']}",
        ).add_to(m)

    m.save("boston_precinct_analysis.html")
    return "boston_precinct_analysis.html"


# --- 4. ANALYTICS GRAPHS ---
def create_graphs(ward_stats, precinct_stats):
    # 1. Bar Chart (Sites per Ward)
    fig1 = px.bar(
        ward_stats.sort_values("Ward_ID"),
        x="Ward_Label",
        y="Site_Count",
        title="Substance Use Disorder Supportive Housing Sites by Boston Ward",
        labels={
            "Site_Count": "Number of SUD Supportive Housing Sites",
            "Ward_Label": "Ward",
        },
        text="Site_Count",
        color_discrete_sequence=["skyblue"],
    )
    fig1.update_layout(plot_bgcolor="white", font=dict(size=14))
    fig1.write_image("ward_bar_chart.png", scale=3)

    positions = ["top center", "bottom center", "top right", "bottom left"]
    ward_stats["text_pos"] = [
        positions[i % len(positions)] for i in range(len(ward_stats))
    ]

    # 2. Demographic Scatter (Normalized Density vs Racial Makeup)
    fig2 = px.scatter(
        ward_stats,
        x="White_Pct",
        y="Normalized_Sites",
        text="Display_Label",
        trendline="ols",
        title="SUD Supportive Housing Density vs. Racial Demographics by Ward",
        labels={
            "White_Pct": "Percentage White (non-Hispanic) Residents",
            "Normalized_Sites": "SUD Supportive Housing Sites per 10,000 Residents",
        },
        hover_data={"Total Population": ":,.0f", "Site_Count": True},
    )

    fig2.update_traces(
        marker=dict(size=14, color="firebrick", line=dict(width=1, color="white")),
        textposition=ward_stats["text_pos"].tolist(),
        cliponaxis=False,
    )
    fig2.update_layout(
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(size=14),
        margin=dict(t=100, b=150, l=100, r=100),
        annotations=[
            dict(
                x=0.5,
                y=-0.18,
                showarrow=False,
                text=CITATION,
                xref="paper",
                yref="paper",
                font=dict(size=10, color="gray"),
            )
        ],
    )

    fig2.update_xaxes(showgrid=True, gridcolor="LightGray", ticksuffix="%")
    fig2.update_yaxes(showgrid=True, gridcolor="LightGray")

    fig2.write_image("demographic_scatter.png", width=1600, height=900, scale=3)
    ward_stats.to_csv("ward_stats_for_r.csv", index=False)

    fig3 = px.scatter(
        precinct_stats,
        x="White_Pct",
        y="Normalized_Sites",
        hover_name="Precinct_ID",
        trendline="ols",
        title="SUD Housing Density vs. Race (Precinct Level)",
        labels={
            "White_Pct": "% White residents",
            "Normalized_Sites": "Sites/10k People",
        },
    )

    fig3.update_traces(marker=dict(size=8, color="firebrick", opacity=0.5))
    fig3.update_layout(plot_bgcolor="white")
    fig3.write_image("precinct_demographic_scatter.png", scale=3)
    import subprocess

    try:
        subprocess.run(["Rscript", "generate_repel_plot.R"], check=True)
        print(
            "R script executed successfully: 'demographic_scatter_final.png' generated."
        )
    except Exception as e:
        print(f"Error running R script: {e}. Make sure R and Rscript are in your PATH.")


# --- 5. SCREENSHOT ENGINE ---
def save_map_screenshot(html_file, map_type):
    print("Capturing high-res map image...")
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless")
    opts.add_argument("--force-device-scale-factor=2")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=opts
    )
    driver.set_window_size(2000, 1500)
    driver.get("file://" + os.path.abspath(html_file))
    time.sleep(5)
    driver.save_screenshot(f"map_poster_output_{map_type}.png")
    driver.quit()


# --- EXECUTION ---
if __name__ == "__main__":
    raw_df = get_geocoded_data()
    processed_df, w_stats, p_stats, ward_geojson, precinct_json = process_spatial_data(
        raw_df
    )
    ward_map_file = create_ward_map(processed_df, w_stats, ward_geojson)
    precinct_map_file = create_precinct_map(processed_df, p_stats, precinct_json)
    create_graphs(w_stats, p_stats)
    save_map_screenshot(ward_map_file, "ward")
    save_map_screenshot(precinct_map_file, "precinct")
    print("All tasks complete. Files generated: Map (HTML/PNG), Charts (PNG).")
