"""Plot satellite vs. station rainfall over a country."""
import os
import argparse
from datetime import datetime, timedelta
from dateutil import parser as dateparser
import matplotlib.cm as cm
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from matplotlib.colors import LinearSegmentedColormap, BoundaryNorm, ListedColormap

import xarray as xr
import pandas as pd
import numpy as np
from scipy.spatial import Voronoi
from shapely.geometry import Polygon, Point

from sheerwater.utils import roll_and_agg
from sheerwater.spatial_subdivisions import polygon_subdivision_geodataframe

# Use a grey color for zero value on the colorbar instead of white.
colors = ["#bdbdbd", "wheat", "lightgreen", "green",
          "lightblue", "blue", "yellow", "orange", "red", "purple"]
cmap = ListedColormap(colors)
bounds = [0, 10, 20, 40, 60, 80, 110, 150, 200, 250, 350]
norm = BoundaryNorm(bounds, ncolors=len(colors), clip=True)

parser = argparse.ArgumentParser(
    description="Plot satellite vs. station rainfall over a country")
parser.add_argument("--country", type=str, default="Kenya,Ghana,Senegal,Ethiopia",
                    help="Country to process (default: Kenya)")
parser.add_argument("--satellite", type=str, default="imerg,chirps",
                    help="Satellite to process (default: imerg,chirps)")
parser.add_argument("--agg", type=str,
                    default="weekly,dekadal",
                    help="Aggregation period for plotting: 'dekadal' or 'weekly' (default: dekadal)")
parser.add_argument("--time", type=str,
                    default=datetime.now().date().strftime("%Y-%m-%d"), help="Time to run script for")
args = parser.parse_args()


def voronoi_bubbles(ax, lon, lat, values, cmap, norm, max_radius):
    points = np.column_stack([lon, lat])
    vor = Voronoi(points)


    for i, point in enumerate(points):
        region_idx = vor.point_region[i]
        region = vor.regions[region_idx]

        if -1 in region or len(region) == 0:
            continue

        # voroni polygon
        poly_coords = [vor.vertices[j] for j in region]
        vor_poly = Polygon(poly_coords)
        # bounding circle
        circle = Point(point).buffer(max_radius)
        # intersection of voroni and circle bound
        clipped = vor_poly.intersection(circle)

        if clipped.is_empty:
            continue

        x, y = clipped.exterior.xy
        print(i,values,np.size(values))
        ax.fill(
            x, y,
            color=cmap(norm(values[i])),
            edgecolor="black",
            linewidth=0.3
        )


if __name__ == "__main__":

    args = parser.parse_args()
    countries = args.country.split(',')
    aggs = args.agg.split(',')
    satellites = args.satellite.split(',')
    live_time = args.time
    now_dt = dateparser.parse(live_time).date()

    for country in countries:
        # Convert to xarray dataset
        gdf = polygon_subdivision_geodataframe('admin_1')
        # Select down to kenya
        country_gdf = gdf[gdf['region_name'].str.contains(f"{country.lower()}-")]
        try:
            df = pd.read_csv(f"private_data/station_data/{country}/{live_time}/tahmo_data_{country}.csv")
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
        except FileNotFoundError:
            print(f"No TAHMO data found for country {country}")
            ds = None

        for satellite in satellites:
            # Get TAHMO and IMERG data
            try:
                ds_sat = xr.open_dataset(f"satellite_data/{country}/{live_time}/{satellite}_data_{country}.nc")
            except FileNotFoundError:
                print(f"No data found for country {country}")
                continue

            sat_lag = pd.to_datetime(ds_sat.time.values[-1]).date() - now_dt

            missing_data = False
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

                if ds is not None:
                    ds_agg = roll_and_agg(ds, agg=agg_days, agg_col='time', agg_fn='sum')
                else:
                    ds_agg = None
                if ds_sat is not None:
                    ds_sat_agg = roll_and_agg(ds_sat, agg=agg_days, agg_col='time', agg_fn='sum')
                else:
                    ds_sat_agg = None

                fig = plt.figure(figsize=(22, 10))
                gs = GridSpec(2, n_indices, figure=fig, wspace=0.08, hspace=0.15)
                top_axes = [fig.add_subplot(gs[0, i]) for i in range(n_indices)]
                bottom_axes = [fig.add_subplot(gs[1, i]) for i in range(n_indices)]

                times = [now_dt - timedelta(days=1+agg_days*(ind+1)) for ind in range(n_indices)[::-1]]
                end_times = [t + timedelta(days=agg_days - 1) for t in times]
                times = [x.strftime("%Y-%m-%d") for x in times]
                end_times = [x.strftime("%Y-%m-%d") for x in end_times]

                for i, t_idx in enumerate(times):
                    # Top row: TAHMO station accumulations.
                    ax_top = top_axes[i]
                    try:
                        vals_tahmo = ds_agg.sel(time=t_idx).precip
                        vals_tahmo = vals_tahmo.dropna(dim='station_id')
                        voronoi_bubbles(
                            ax_top,
                            vals_tahmo.lon.values,
                            vals_tahmo.lat.values,
                            vals_tahmo.values,
                            cmap,
                            norm,
                            max_radius=0.15
                        )
                        ax_top.set_title(f"TAHMO: {times[i]}\nto {end_times[i]}", fontsize=9)
                    except (KeyError, AttributeError):
                        print(f"No TAHMO data found for time {t_idx}")
                        ax_top.set_title(f"No TAHMO data: {times[i]}\nto {end_times[i]}", fontsize=9)
                    country_gdf.boundary.plot(edgecolor='grey', linewidth=1.0, ax=ax_top, zorder=0)
                    if i > 0:
                        ax_top.set_ylabel("")
                        ax_top.tick_params(left=False, labelleft=False)
                    else:
                        ax_top.set_ylabel("lat")

                    # Bottom row: IMERG gridded accumulations.
                    ax_bottom = bottom_axes[i]
                    try:
                        im = ds_sat_agg.sel(time=t_idx).precip.plot(
                            x='lon',
                            y='lat',
                            ax=ax_bottom,
                            cmap=cmap,
                            norm=norm,
                            add_colorbar=False,
                        )
                        ax_bottom.set_title(f"{satellite.upper()}: {times[i]}\nto {end_times[i]}", fontsize=9)
                    except (KeyError, AttributeError):
                        missing_data = True
                        break
                        #ax_bottom.set_title(f"No {satellite.upper()} data: {times[i]}\nto {end_times[i]}", fontsize=9)
                    country_gdf.boundary.plot(edgecolor='grey', linewidth=1.0, ax=ax_bottom)
                    ax_bottom.set_xlabel("lon" if i == n_indices // 2 else "")
                    if i > 0:
                        ax_bottom.set_ylabel("")
                        ax_bottom.tick_params(left=False, labelleft=False)
                    else:
                        ax_bottom.set_ylabel("lat")

                if missing_data:
                    break

                fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), ax=top_axes, label='TAHMO Precipitation (mm)', shrink=0.6, fraction=0.02, pad=0.02)
                fig.colorbar(im, ax=bottom_axes, label=f'{satellite.upper()} Precipitation (mm)', shrink=0.6, fraction=0.02, pad=0.02)
                dir = f"private_plots/{country}/{live_time}"
                os.makedirs(dir, exist_ok=True)
                plt.savefig(f"{dir}/{agg}_{satellite}_vs_stations_{country}.png", bbox_inches='tight', dpi=150)
                plt.close()

                fig = plt.figure(figsize=(14, 4))  # wider figure
                gs = GridSpec(1, n_indices, figure=fig, wspace=0.08)
                top_axes = [fig.add_subplot(gs[0, i]) for i in range(n_indices)]

                if 'spatial_ref' in ds_sat_agg:
                    ds_sat_agg = ds_sat_agg.drop_vars('spatial_ref')

                for i, t_idx in enumerate(times):
                    ax = top_axes[i]
                    try:
                        im = ds_sat_agg.sel(time=t_idx).precip.plot(
                            x='lon',
                            y='lat',
                            ax=ax,
                            cmap=cmap,
                            norm=norm,
                            add_colorbar=False,
                        )
                        ax_bottom.set_title(f"{satellite.upper()}: {times[i]}\nto {end_times[i]}", fontsize=9)
                    except (KeyError, AttributeError):
                        print(f"No {satellite} data found for time {t_idx}")
                        ax_bottom.set_title(f"No {satellite.upper()} data: {times[i]}\nto {end_times[i]}", fontsize=9)
                    country_gdf.boundary.plot(edgecolor='grey', linewidth=1.0, ax=ax)
                    ax.set_xlabel("lon" if i == n_indices // 2 else "")  # only center subplot gets label
                    if i > 0:
                        ax.set_ylabel("")
                        ax.tick_params(left=False, labelleft=False)
                    else:
                        ax.set_ylabel("lat")

                fig.colorbar(im, ax=top_axes, label=f'{satellite.upper()} Precipitation (mm)', shrink=0.8, pad=0.02)

                ecmwf_date = now_dt - timedelta(days=2)
                dir = f"plots/{country}/{ecmwf_date.strftime('%Y-%m-%d')}/{agg}"
                os.makedirs(dir, exist_ok=True)
                plt.savefig(f"{dir}/{agg}_{satellite}_{country}.png", bbox_inches='tight', dpi=150)
                plt.close()
