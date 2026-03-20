import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import argparse
from datetime import datetime, timedelta
from sheerwater.data.imerg import imerg_raw_live
from sheerwater.utils import roll_and_agg
from sheerwater.spatial_subdivisions import polygon_subdivision_geodataframe, clip_region
import xarray as xr
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, BoundaryNorm

# Use a grey color for zero value on the colorbar instead of white.
colors = ["#bdbdbd", "wheat", "lightgreen", "green",
          "lightblue", "blue", "yellow", "orange", "red", "purple"]
cmap = LinearSegmentedColormap.from_list("wgbrp", colors)
bounds = [0, 10, 20, 40, 60, 80, 110, 150, 200, 250, 350]
norm = BoundaryNorm(bounds, cmap.N)

parser = argparse.ArgumentParser(
    description="Plot satellite vs. station rainfall over a country")
parser.add_argument("--country", type=str, default="Kenya",
                    help="Country to process (default: Kenya)")
parser.add_argument("--agg", type=str, choices=["decadal", "weekly"],
                    default="decadal",
                    help="Aggregation period for plotting: 'decadal' or 'weekly' (default: decadal)")
args = parser.parse_args()

if __name__ == "__main__":
    live_lag = 3
    country = args.country
    now_dt = datetime.now().date()
    lagged_now_dt = now_dt - timedelta(days=live_lag)
    start_time = (lagged_now_dt - timedelta(days=30)).strftime("%Y-%m-%d")
    end_time = lagged_now_dt.strftime("%Y-%m-%d")
    live_time = now_dt.strftime("%Y-%m-%d")

    # Get TAHMO and IMERG data
    ds_imerg = xr.open_dataset(f"satellite_data/{country}/{live_time}/imerg_data_{country}.nc")
    df = pd.read_csv(f"station_data/{country}/{live_time}/tahmo_data_{country}.csv")

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

    # Roll over decads
    if args.agg == "decadal":
        agg_days = 10
        n_indices = 2
    elif args.agg == "weekly":
        agg_days = 7
        n_indices = 3
    else:
        raise ValueError(f"Invalid aggregation: {args.agg}")

    ds_agg = roll_and_agg(ds, agg=agg_days, agg_col='time', agg_fn='sum')
    ds_imerg_agg = roll_and_agg(ds_imerg, agg=agg_days,
                                agg_col='time', agg_fn='sum')

    # Convert to xarray dataset
    gdf = polygon_subdivision_geodataframe('admin_1')
    # Select down to kenya
    country_gdf = gdf[gdf['region_name'].str.contains(f"{country.lower()}-")]

    fig = plt.figure(figsize=(18, 10))
    gs = GridSpec(2, n_indices+1, figure=fig,
                  width_ratios=[1]*n_indices+[0.05], wspace=0.05, hspace=0.15)

    top_axes = [fig.add_subplot(gs[0, i]) for i in range(n_indices)]
    bottom_axes = [fig.add_subplot(gs[1, i]) for i in range(n_indices)]
    cbar_ax_top = fig.add_subplot(gs[0, n_indices])
    cbar_ax_bottom = fig.add_subplot(gs[1, n_indices])

    times = [now_dt - timedelta(days=live_lag+agg_days*(ind+1)) for ind in range(n_indices)[::-1]]
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
        ax_top.set_title(f"TAHMO: {times[i]} to {end_times[i]}", fontsize=11)
        if i > 0:
            # hide y ticks on middle/right panels
            ax_top.tick_params(left=False)

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
        country_gdf.boundary.plot(
            edgecolor='grey', linewidth=1.0, ax=ax_bottom)
        ax_bottom.set_title(
            f"IMERG: {times[i]} to {end_times[i]}", fontsize=11)
        if i > 0:
            ax_bottom.tick_params(left=False)

    fig.colorbar(sc, cax=cbar_ax_top, label='TAHMO Precipitation (mm)')
    fig.colorbar(im, cax=cbar_ax_bottom, label='IMERG Precipitation (mm)')
    plt.show()
