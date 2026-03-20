"""Plot satellite vs. station rainfall over a country."""
import os
import argparse
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from matplotlib.colors import LinearSegmentedColormap, BoundaryNorm

import xarray as xr
import pandas as pd

from sheerwater.utils import roll_and_agg
from sheerwater.spatial_subdivisions import polygon_subdivision_geodataframe

# Use a grey color for zero value on the colorbar instead of white.
colors = ["#bdbdbd", "wheat", "lightgreen", "green",
          "lightblue", "blue", "yellow", "orange", "red", "purple"]
cmap = LinearSegmentedColormap.from_list("wgbrp", colors)
bounds = [0, 10, 20, 40, 60, 80, 110, 150, 200, 250, 350]
norm = BoundaryNorm(bounds, cmap.N)

parser = argparse.ArgumentParser(
    description="Plot satellite vs. station rainfall over a country")
parser.add_argument("--country", type=str, default="Kenya,Ghana,Senegal,Ethiopia",
                    help="Country to process (default: Kenya)")
parser.add_argument("--agg", type=str, 
                    default="weekly,dekadal",
                    help="Aggregation period for plotting: 'dekadal' or 'weekly' (default: dekadal)")
args = parser.parse_args()

if __name__ == "__main__":
    sat_lag = 4 # IMERG early release is 4 days behind
    now_dt = datetime.now().date()
    live_time = now_dt.strftime("%Y-%m-%d")

    args = parser.parse_args()
    countries = args.country.split(',')
    aggs = args.agg.split(',')

    for country in countries:
        # Get TAHMO and IMERG data
        try:
            ds_imerg = xr.open_dataset(f"satellite_data/{country}/{live_time}/imerg_data_{country}.nc")
            df = pd.read_csv(f"private_data/station_data/{country}/{live_time}/tahmo_data_{country}.csv")
        except FileNotFoundError:
            print(f"No data found for country {country}")
            continue

        # Clean up TAHMO station data and convert to xarray
        df = df[['time', 'station_id', 'location_latitude',
                'location_longitude', 'cumulative_precipitation_mm']]
        df = df.rename(columns={'location_latitude': 'lat',
                    'location_longitude': 'lon', 'cumulative_precipitation_mm': 'precip'})

        # Pull static station coords out before converting
        station_coords = df[['station_id', 'lat', 'lon']
                            ].groupby('station_id').first()
        df = df.drop(columns=['lat', 'lon'])
        df = df.set_index(['time', 'station_id'])

        # Convert to xarray
        ds = xr.Dataset.from_dataframe(df)
        # Assign lat/lon as 1D coordinates indexed only by station_id
        ds = ds.assign_coords(
            lat=('station_id', station_coords['lat'].values),
            lon=('station_id', station_coords['lon'].values),
        )
        # Convert time to a datetime index
        ds = ds.assign_coords(time=pd.to_datetime(ds.time))

        for agg in aggs:
            # Roll over decads
            if agg == "dekadal":
                agg_days = 10
                n_indices = 3
            elif agg == "weekly":
                agg_days = 7
                n_indices = 4
            else:
                raise ValueError(f"Invalid aggregation: {agg}")

            ds_agg = roll_and_agg(ds, agg=agg_days, agg_col='time', agg_fn='sum')
            ds_imerg_agg = roll_and_agg(ds_imerg, agg=agg_days, agg_col='time', agg_fn='sum')

            # Convert to xarray dataset
            gdf = polygon_subdivision_geodataframe('admin_1')
            # Select down to kenya
            country_gdf = gdf[gdf['region_name'].str.contains(f"{country.lower()}-")]

            fig = plt.figure(figsize=(22, 10))
            gs = GridSpec(2, n_indices, figure=fig, wspace=0.08, hspace=0.15)
            top_axes = [fig.add_subplot(gs[0, i]) for i in range(n_indices)]
            bottom_axes = [fig.add_subplot(gs[1, i]) for i in range(n_indices)]

            times = [now_dt - timedelta(days=sat_lag-1+agg_days*(ind+1)) for ind in range(n_indices)[::-1]]
            end_times = [t + timedelta(days=agg_days - 1) for t in times]
            times = [x.strftime("%Y-%m-%d") for x in times]
            end_times = [x.strftime("%Y-%m-%d") for x in end_times]

            for i, t_idx in enumerate(times):
                # Top row: TAHMO station accumulations.
                ax_top = top_axes[i]
                vals_tahmo = ds_agg.sel(time=t_idx).precip
                sc = ax_top.scatter(
                    ds_agg.lon, ds_agg.lat,
                    c=vals_tahmo.values,
                    cmap=cmap,
                    norm=norm,
                    s=30,
                )
                country_gdf.boundary.plot(edgecolor='grey', linewidth=1.0, ax=ax_top)
                ax_top.set_title(f"TAHMO: {times[i]}\nto {end_times[i]}", fontsize=9)
                if i > 0:
                    ax_top.set_ylabel("")
                    ax_top.tick_params(left=False, labelleft=False)
                else:
                    ax_top.set_ylabel("lat")

                # Bottom row: IMERG gridded accumulations.
                ax_bottom = bottom_axes[i]
                im = ds_imerg_agg.sel(time=t_idx).precip.plot(
                    x='lon',
                    y='lat',
                    ax=ax_bottom,
                    cmap=cmap,
                    norm=norm,
                    add_colorbar=False,
                )
                country_gdf.boundary.plot(edgecolor='grey', linewidth=1.0, ax=ax_bottom)
                ax_bottom.set_title(f"IMERG: {times[i]}\nto {end_times[i]}", fontsize=9)
                ax_bottom.set_xlabel("lon" if i == n_indices // 2 else "")
                if i > 0:
                    ax_bottom.set_ylabel("")
                    ax_bottom.tick_params(left=False, labelleft=False)
                else:
                    ax_bottom.set_ylabel("lat")

            fig.colorbar(sc, ax=top_axes, label='TAHMO Precipitation (mm)', shrink=0.6, fraction=0.02, pad=0.02)
            fig.colorbar(im, ax=bottom_axes, label='IMERG Precipitation (mm)', shrink=0.6, fraction=0.02, pad=0.02)

            dir = f"private_plots/{country}/{live_time}"
            os.makedirs(dir, exist_ok=True)
            plt.savefig(f"{dir}/sat_vs_stations_{country}_{agg}.png", bbox_inches='tight', dpi=150)
            plt.close()

            fig = plt.figure(figsize=(14, 4))  # wider figure
            gs = GridSpec(1, n_indices, figure=fig, wspace=0.08)
            top_axes = [fig.add_subplot(gs[0, i]) for i in range(n_indices)]

            for i, t_idx in enumerate(times):
                ax = top_axes[i]
                im = ds_imerg_agg.sel(time=t_idx).precip.plot(
                    x='lon',
                    y='lat',
                    ax=ax,
                    cmap=cmap,
                    norm=norm,
                    add_colorbar=False,
                )
                country_gdf.boundary.plot(edgecolor='grey', linewidth=1.0, ax=ax)
                ax.set_title(f"{times[i]}\nto {end_times[i]}", fontsize=9)  # split title onto two lines
                ax.set_xlabel("lon" if i == n_indices // 2 else "")  # only center subplot gets label
                if i > 0:
                    ax.set_ylabel("")
                    ax.tick_params(left=False, labelleft=False)
                else:
                    ax.set_ylabel("lat")

            fig.colorbar(im, ax=top_axes, label='IMERG Precipitation (mm)', shrink=0.8, pad=0.02)

            ecmwf_date = now_dt - timedelta(days=2)
            dir = f"plots/{country}/{ecmwf_date.strftime('%Y-%m-%d')}/{agg}"
            os.makedirs(dir, exist_ok=True)
            plt.savefig(f"{dir}/imerg_only_{country}_{agg}.png", bbox_inches='tight', dpi=150)
            plt.close()