# Boston SUD Supportive Housing Analysis

This project provides a spatial and demographic analysis of **Substance Use Disorder (SUD) Supportive Housing Sites** across Boston’s 22 wards. It correlates site density (normalized per 10,000 residents) with racial demographics using 2020 U.S. Census data.

## Features
- **Spatial Analysis:** Automatically assigns housing addresses to Boston Wards using GeoJSON boundaries.
- **Normalization:** Calculates site density per 10,000 residents to allow for fair comparison across wards of different sizes.
- **Interactive Mapping:** Generates a Leaflet-based choropleth map with a "YlOrRd" (Yellow-Orange-Red) gradient and high-contrast site markers.
- **Demographic Correlation:** Analyzes the relationship between the percentage of White (non-Hispanic) residents and SUD housing density.
- **High-Resolution Output:** Uses **R (ggrepel)** to generate poster-ready scatter plots with non-overlapping neighborhood labels and **Selenium** for high-DPI map screenshots.

## Prerequisites

### 1. Python
This project uses [uv](https://github.com/astral-sh/uv) for extremely fast Python package management. Ensure you have `uv` installed.

### 2. R
To handle the advanced label repulsion in the scatter plots, you must have R installed. Install the required libraries within R:
```R
install.packages(c("ggplot2", "ggrepel"))
```

### 3. Chrome / ChromeDriver
For high-resolution map screenshots, the script uses Selenium. Ensure you have Google Chrome installed.

## Installation

Clone the repository and sync the Python environment using `uv`:

```bash
git clone https://github.com/Roco-scientist/boston-sud-housing-analysis.git
cd boston-sud-housing-analysis
uv sync
```

## Usage

Place your provider data in a file named `Properties Owned or Managed by Recovery Housing Providers.csv` and the census data in `census_data_for_2022_redistricting.csv`.

Run the full pipeline:
```bash
uv run main.py
```

### What happens when you run it?
1. **Geocoding:** Checks `geocoded_cache.csv` for existing coordinates. New addresses are geocoded via Nominatim (with rate-limit respect).
2. **Spatial Join:** Assigns each site to a Ward ID using the Boston Ward Boundaries GeoJSON.
3. **Census Merge:** Aggregates 2020 Census data to calculate White % and Total Population per ward.
4. **Map Generation:** Creates `boston_housing_map.html` and takes a high-res screenshot `map_poster_final.png`.
5. **R Visualization:** Python exports the data and triggers `generate_repel_plot.R` to create the final scatter plot with smart label placement.

## Data Sources
- **Housing Data:** Provided provider list.
- **Demographics:** [2020 Decennial Census P.L. 94-171 Redistricting Data](https://data.boston.gov/dataset/census-data-for-2022-redistricting).
- **Ward Boundaries:** [City of Boston Open Data Portal](https://data.boston.gov/dataset/e491f77b-0094-40ea-ae20-f91cfa9f7ccc/resource/86e050bf-ca6a-45ff-aca1-c68aa611f7d6/download/boston_ward_boundaries.geojson).

## Output Files
- `boston_housing_map.html`: Interactive web map.
- `map_poster_final.png`: High-resolution map for print.
- `demographic_scatter_final.png`: The R-generated scatter plot with non-overlapping labels.
- `manual_fix_needed.csv`: A list of any addresses that failed to geocode for manual review.
- `geocoded_cache.csv`: Local cache of coordinates to prevent redundant API calls.

## License
[MIT](LICENSE)
