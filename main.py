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
CITATION = "Source: U.S. Census Bureau, 2020 Decennial Census P.L. 94-171 Redistricting Data; BPDA Research Division Analysis"
WARD_TO_NEIGHBORHOOD = {
    1: "East Boston", 2: "Charlestown", 3: "North End/West End", 
    4: "Fenway/South End", 5: "Beacon Hill/Back Bay", 6: "South Boston/Seaport", 
    7: "South Boston/Dorchester", 8: "South End/Roxbury", 9: "Roxbury/South End", 
    10: "Mission Hill/JP", 11: "Roxbury/JP", 12: "Roxbury", 
    13: "Dorchester", 14: "Dorchester/Mattapan", 15: "Dorchester", 
    16: "Dorchester (Neponset)", 17: "Dorchester (Codman Sq)", 18: "Hyde Park/Mattapan", 
    19: "JP/Roslindale", 20: "West Roxbury/Roslindale", 21: "Allston/Brighton", 
    22: "Brighton"
}

# --- 1. DATA LOADING & GEOCODING ---
def get_geocoded_data():
    df = pd.read_csv(SOURCE_CSV).dropna(subset=['Street Address'])
    
    if os.path.exists(CACHE_CSV):
        print("Loading geocode cache...")
        cache_df = pd.read_csv(CACHE_CSV)
        df = pd.merge(df, cache_df[['Street Address', 'lat', 'lon']], on='Street Address', how='left')
    else:
        df['lat'], df['lon'] = np.nan, np.nan

    missing = df['lat'].isna()
    if missing.any():
        print(f"Geocoding {missing.sum()} new addresses...")
        geolocator = Nominatim(user_agent="boston_housing_mapper")
        geocode_service = RateLimiter(geolocator.geocode, min_delay_seconds=1.2)

        def do_geocode(row):
            try:
                nb = f", {row['Neighborhood']}" if pd.notna(row.get('Neighborhood')) else ""
                addr = f"{row['Street Address']}{nb}, Boston, MA"
                loc = geolocator.geocode(addr)
                return (loc.latitude, loc.longitude) if loc else (np.nan, np.nan)
            except:
                return np.nan, np.nan

        new_coords = df[missing].apply(do_geocode, axis=1, result_type='expand')
        df.loc[missing, ['lat', 'lon']] = new_coords.values
        df[['Street Address', 'lat', 'lon']].dropna().drop_duplicates().to_csv(CACHE_CSV, index=False)
    
    return df

# --- 2. CENSUS & SPATIAL PROCESSING ---
def process_spatial_data(df):
    # Load GeoJSON
    ward_geojson = requests.get(WARD_GEOJSON_URL).json()
    
    # Map points to Ward IDs (as Integers)
    def find_ward(row):
        if pd.isna(row['lat']): return np.nan
        pt = Point(row['lon'], row['lat'])
        for feat in ward_geojson['features']:
            if shape(feat['geometry']).contains(pt):
                props = feat['properties']
                
                # 1. Try "WARD" (The most common key in this specific file)
                # 2. Try "Ward_Num" (Alternative)
                # 3. Fallback: Extract the number from "WardLabel" (e.g., "Ward 1" -> 1)
                val = props.get('WARD') or props.get('Ward_Num')
                
                if val is not None:
                    return int(val)
                
                label = props.get('WardLabel', '')
                import re
                match = re.search(r'\d+', str(label))
                if match:
                    return int(match.group())
                    
        return np.nan

    print("Assigning wards to sites...")
    df['Ward_ID'] = df.apply(find_ward, axis=1)
    
    # Save manual fix file
    unfound = df[df['lat'].isna()]
    if not unfound.empty:
        unfound[['Street Address', 'Neighborhood', 'Owner/Manager']].to_csv("manual_fix_needed.csv", index=True)
        print(f"⚠️ {len(unfound)} addresses unfound. See manual_fix_needed.csv")

    # Process Census
    census = pd.read_csv(CENSUS_CSV)
    census['Total Pop'] = pd.to_numeric(census['Total Population'].astype(str).str.replace(',', ''), errors='coerce')
    census['White alone'] = pd.to_numeric(census['White alone'].astype(str).str.replace(',', ''), errors='coerce')
    
    census['Ward_ID'] = census['Ward and Precinct (Updated 2022)'].astype(str).str.zfill(4).str[:2].astype(int)
    
    ward_stats = census.groupby('Ward_ID').agg({'Total Pop': 'sum', 'White alone': 'sum'}).reset_index()
    
    # Aggregate Site Counts
    site_counts = df.groupby('Ward_ID').size().reset_index(name='Site_Count')
    
    # Merge Statistics
    merged = pd.merge(ward_stats, site_counts, on='Ward_ID', how='left').fillna(0)
    merged['Normalized_Sites'] = (merged['Site_Count'] / merged['Total Pop']) * 10000
    merged['White_Pct'] = (merged['White alone'] / merged['Total Pop']) * 100
    merged['Neighborhood'] = merged['Ward_ID'].map(WARD_TO_NEIGHBORHOOD)
    merged['Ward_Label'] = merged['Ward_ID'].apply(lambda x: f"Ward {x}")
    merged['Display_Label'] = merged['Ward_Label'] + " (" + merged['Neighborhood'] + ")"
    
    return df, merged, ward_geojson

# --- 3. MAPPING ---
def create_map(df, analysis_df, ward_geojson):
    m = folium.Map(location=[42.33, -71.08], zoom_start=12, tiles="cartodbpositron", control_scale=True)

    # Gradient Fill
    folium.Choropleth(
        geo_data=ward_geojson,
        data=analysis_df,
        columns=['Ward_ID', 'Normalized_Sites'],
        key_on="feature.properties.Ward1",
        fill_color="YlOrRd",
        fill_opacity=0.6,
        line_opacity=0.3,
        legend_name="Sites per 10k Residents"
    ).add_to(m)

    # Ward Labels
    for feat in ward_geojson['features']:
        ctr = shape(feat['geometry']).centroid
        wnum = feat['properties'].get('Ward1') or feat['properties'].get('Ward1')
        folium.Marker(
            [ctr.y, ctr.x],
            icon=DivIcon(html=f'<div style="font-size:10pt; font-weight:bold; text-shadow:1px 1px white;">{wnum}</div>')
        ).add_to(m)

    # Site Markers (with Jitter)
    tracker = defaultdict(int)
    for _, row in df.dropna(subset=['lat']).iterrows():
        c = tracker[(row['lat'], row['lon'])]
        lat_j = (c * 0.00015) * (1 if c % 2 == 0 else -1)
        lon_j = (c * 0.00015) * (1 if c % 3 == 0 else -1)
        tracker[(row['lat'], row['lon'])] += 1
        
        folium.CircleMarker(
            location=[row['lat'] + lat_j, row['lon'] + lon_j],
            radius=5, color='white', weight=1, fill=True, fill_color='black', fill_opacity=0.8,
            popup=f"<b>{row['Street Address']}</b><br>{row['Owner/Manager']}"
        ).add_to(m)

    m.save("boston_housing_analysis.html")
    return "boston_housing_analysis.html"

# --- 4. ANALYTICS GRAPHS ---
def create_graphs(stats):
    # 1. Bar Chart (Sites per Ward)
    fig1 = px.bar(stats.sort_values('Ward_ID'), x='Ward_Label', y='Site_Count', 
                  title="Substance Use Disorder Supportive Housing Sites by Boston Ward",
                  labels={'Site_Count': 'Number of SUD Supportive Housing Sites', 'Ward_Label': 'Ward'},
                  text='Site_Count', color_discrete_sequence=['skyblue'])
    fig1.update_layout(plot_bgcolor='white', font=dict(size=14))
    fig1.write_image("ward_bar_chart.png", scale=3)

    positions = ['top center', 'bottom center', 'top right', 'bottom left']
    stats['text_pos'] = [positions[i % len(positions)] for i in range(len(stats))]

    # 2. Demographic Scatter (Normalized Density vs Racial Makeup)
    fig2 = px.scatter(stats, x='White_Pct', y='Normalized_Sites', 
                      text='Display_Label', trendline="ols",
                      title="SUD Supportive Housing Density vs. Racial Demographics by Ward",
                      labels={'White_Pct': 'Percentage White (non-Hispanic) Residents', 
                              'Normalized_Sites': 'SUD Supportive Housing Sites per 10,000 Residents'},
                      hover_data={'Total Pop': ':,.0f', 'Site_Count': True})

    fig2.update_traces(
        marker=dict(size=14, color='firebrick', line=dict(width=1, color='white')),
        textposition=stats['text_pos'].tolist(),
        cliponaxis=False 
    )
    fig2.update_layout(
        plot_bgcolor='white', paper_bgcolor='white', font=dict(size=14),
        margin=dict(t=100, b=150, l=100, r=100),
        annotations=[dict(x=0.5, y=-0.18, showarrow=False, text=CITATION, xref="paper", yref="paper", font=dict(size=10, color="gray"))]
    )

    fig2.update_xaxes(showgrid=True, gridcolor='LightGray', ticksuffix="%")
    fig2.update_yaxes(showgrid=True, gridcolor='LightGray')
    
    fig2.write_image("demographic_scatter.png", width=1600, height=900, scale=3)
    stats.to_csv("ward_stats_for_r.csv", index=False)
    import subprocess
    try:
        subprocess.run(["Rscript", "generate_repel_plot.R"], check=True)
        print("R script executed successfully: 'demographic_scatter_final.png' generated.")
    except Exception as e:
        print(f"Error running R script: {e}. Make sure R and Rscript are in your PATH.")

# --- 5. SCREENSHOT ENGINE ---
def save_map_screenshot(html_file):
    print("Capturing high-res map image...")
    opts = webdriver.ChromeOptions()
    opts.add_argument('--headless')
    opts.add_argument('--force-device-scale-factor=2')
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    driver.set_window_size(2400, 3000)
    driver.get("file://" + os.path.abspath(html_file))
    time.sleep(5)
    driver.save_screenshot("map_poster_output.png")
    driver.quit()

# --- EXECUTION ---
if __name__ == "__main__":
    raw_df = get_geocoded_data()
    processed_df, stats_df, geojson = process_spatial_data(raw_df)
    map_file = create_map(processed_df, stats_df, geojson)
    create_graphs(stats_df)
    save_map_screenshot(map_file)
    print("All tasks complete. Files generated: Map (HTML/PNG), Charts (PNG).")
