import os
import numpy as np
import matplotlib
import get_ECMWF_functions as gef
from datetime import datetime, timedelta
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import pandas as pd
import matplotlib.colors as mcolors
from matplotlib.ticker import MaxNLocator

# forecast date to run for, defaults to 2 days ago
if "DATE_STR" in os.environ:
    date_str=os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

matplotlib.rcParams.update({'font.size': 20})

prefix=os.environ["MAIN_PATH"]
data_path=f'{prefix}/data/{date_str}'

monthly_save_path=f'plots/diagnostics/{date_str}/monthly/'
weekly_save_path=f'plots/diagnostics/{date_str}/weekly/'
os.makedirs(monthly_save_path, exist_ok=True)
os.makedirs(weekly_save_path, exist_ok=True)

INDIAN_OCEAN_EXTENT = (30, 120, -20, 20)


def indian_ocean_basemap(ax, extent=INDIAN_OCEAN_EXTENT, left_labels=True, bottom_labels=True):
    """Coastlines, land/ocean shading, country borders, gridlines shared by every map here."""
    ax.set_extent(extent, crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.OCEAN, facecolor='#eaf3fa', zorder=0)
    ax.add_feature(cfeature.LAND, facecolor='#f2f2f0', zorder=0)
    ax.coastlines(resolution='50m', linewidth=0.6, color='#444444')
    ax.add_feature(cfeature.BORDERS, linestyle=':', linewidth=1, color='k')

    gl = ax.gridlines(draw_labels=True, linewidth=0.5, color='gray', alpha=0.7, linestyle='--')
    gl.top_labels = False
    gl.right_labels = False
    gl.left_labels = left_labels
    gl.bottom_labels = bottom_labels
    return gl


def zero_centered_diverging_cmap(vmin, vmax, neg_colors, pos_colors, white='#ffffff', nbins=11):
    """Boundary-normed diverging colormap with a dedicated white bin centered on zero."""
    raw_levels = MaxNLocator(nbins=nbins).tick_values(vmin, vmax)
    step = raw_levels[1] - raw_levels[0]

    # shift boundaries by half a step so zero sits at the CENTER of one bin
    levels = raw_levels - step / 2
    levels = np.append(levels, levels[-1] + step)   # keep the same coverage

    n_bins = len(levels) - 1
    center_idx = np.searchsorted(levels, 0) - 1     # bin index that contains zero
    n_neg = center_idx                              # bins fully left of the center (white) bin
    n_pos = n_bins - center_idx - 1                 # bins fully right of it

    neg_part = mcolors.LinearSegmentedColormap.from_list('neg', neg_colors, N=256)(np.linspace(0, 1, n_neg))
    pos_part = mcolors.LinearSegmentedColormap.from_list('pos', pos_colors, N=256)(np.linspace(0, 1, n_pos))
    white_part = np.array([mcolors.to_rgba(white)])

    cmap = mcolors.ListedColormap(np.vstack([neg_part, white_part, pos_part]))
    norm = mcolors.BoundaryNorm(levels, cmap.N)
    return cmap, norm, levels


def plot_moisture_anomaly_map(field, var, title, cbar_label, out_path, neg_colors, pos_colors,
                               extent=INDIAN_OCEAN_EXTENT, figsize=(20, 20)):
    """Zero-centered diverging map (TCW / IVT anomalies) over the Indian Ocean, saved to out_path."""
    vmin, vmax = gef.symmetric_vmin_vmax(field, var=var)
    cmap, norm, levels = zero_centered_diverging_cmap(vmin, vmax, neg_colors, pos_colors)

    fig, ax = plt.subplots(1, figsize=figsize, subplot_kw={'projection': ccrs.PlateCarree()})
    indian_ocean_basemap(ax, extent=extent)

    cf = ax.pcolormesh(field['longitude'], field['latitude'], field[var],
                        transform=ccrs.PlateCarree(), cmap=cmap, norm=norm, shading='auto')
    ax.set_title(title)

    cbar = fig.colorbar(cf, ax=ax, orientation='horizontal', pad=0.03, shrink=0.6, ticks=levels)
    cbar.set_label(cbar_label)

    fig.tight_layout()
    plt.savefig(out_path, bbox_inches='tight')
    plt.close(fig)


def plot_moisture_anomaly_weekly_map(field, var, title, cbar_label, out_path, neg_colors, pos_colors,
                                      extent=INDIAN_OCEAN_EXTENT, panel_height=7):
    """4-panel (2x2) zero-centered diverging map of a weekly-resolved anomaly field (TCW / IVT),
    one panel per week, sharing a single color scale and colorbar."""
    vmin, vmax = gef.symmetric_vmin_vmax(field, var=var)
    cmap, norm, levels = zero_centered_diverging_cmap(vmin, vmax, neg_colors, pos_colors)

    # size the figure to the extent's aspect ratio so maps fill their panels
    # instead of leaving empty vertical space (which pushes the suptitle away visually)
    lon_min, lon_max, lat_min, lat_max = extent
    aspect = (lon_max - lon_min) / (lat_max - lat_min)
    figsize = (2 * panel_height * aspect, 2 * panel_height)

    steps = field.step.values
    # constrained_layout (unlike tight_layout) resolves spacing against the axes'
    # actual aspect-locked size, so it doesn't leave a double gap between columns
    fig, axes = plt.subplots(2, 2, figsize=figsize, subplot_kw={'projection': ccrs.PlateCarree()},
                              constrained_layout=True)

    cf = None
    for i, ax in enumerate(axes.flat):
        if i >= len(steps):
            ax.axis('off')
            continue
        week = field.isel(step=i)
        row, col = divmod(i, 2)
        # only the left column / bottom row draw axis labels, so the other
        # panels don't waste space repeating them in the middle of the grid
        indian_ocean_basemap(ax, extent=extent, left_labels=(col == 0), bottom_labels=(row == 1))
        cf = ax.pcolormesh(week['longitude'], week['latitude'], week[var],
                            transform=ccrs.PlateCarree(), cmap=cmap, norm=norm, shading='auto')

        # step marks the end of the day-mean valid day, i.e. it's 1 day past the
        # week's true last day, so shift back a day before taking the 7-day window
        week_end = pd.Timestamp(week.time.values) + pd.to_timedelta(week.step.values) - pd.Timedelta(days=1)
        week_start = week_end - pd.Timedelta(days=6)
        ax.set_title(f'Week {i + 1}: {week_start:%Y-%m-%d} to {week_end:%Y-%m-%d}')

    fig.suptitle(title)
    cbar = fig.colorbar(cf, ax=axes, orientation='horizontal', shrink=0.5, aspect=50, ticks=levels)
    cbar.set_label(cbar_label)

    plt.savefig(out_path)
    plt.close(fig)


# ========================================================
# WIND / SST ANOMALY (monthly mean over first 28 days)
# ========================================================

IO_winds_raw=xr.open_zarr(f'{data_path}/ECMWF_s2s_10wind_alt_{date_str}.zarr',consolidated=True).compute()
IO_sst_raw=xr.open_zarr(f'{data_path}/ECMWF_s2s_sst_{date_str}.zarr',consolidated=True).compute()

IO_winds=IO_winds_raw.isel(step=slice(None,4*28+1)).mean('step')
IO_sst=IO_sst_raw.isel(step=slice(None,4*28+1)).mean('step')

# model climatology (median) to compare against - older files are a single
# 28-day-mean reference (no per-week resolution); newer ones carry a per-week
# 'step' dim instead, so both cases are normalized below
IO_winds_mclimate=gef.open_mclimate(IO_winds,var='IO_10m_wind').sel(quantile=0.5)
IO_sst_mclimate=gef.open_mclimate(IO_sst,var='IO_sst').sel(quantile=0.5)

# monthly climatology: mean over the per-week climatology when present,
# otherwise it's already a single monthly value
winds_mclimate_monthly = IO_winds_mclimate.mean('step') if 'step' in IO_winds_mclimate.dims else IO_winds_mclimate
sst_mclimate_monthly = IO_sst_mclimate.mean('step') if 'step' in IO_sst_mclimate.dims else IO_sst_mclimate

# ensemble-mean anomaly = forecast - climatological median
anom_winds=IO_winds-winds_mclimate_monthly
ds_to_plot_winds=anom_winds.mean('number')

anom_sst=IO_sst-sst_mclimate_monthly
ds_to_plot_sst=anom_sst.mean('number')

fig, axes = gef.plot_wind_and_sst_anomaly(ds_to_plot_winds, ds_to_plot_sst)
plt.savefig(monthly_save_path+f'ECMWF_s2s_10wind_sst_anomaly_{date_str}.png', bbox_inches='tight')
plt.close(fig)

# --- weekly (4-panel) version, per-week mean field vs. a per-week climatology
# when available (positionally aligned onto the forecast's own step labels,
# since the two step conventions aren't guaranteed to share exact coordinate
# values), else the same monthly climatology broadcast across every week ---
def _align_weekly_mclimate(forecast_weekly, mclimate_median):
    if 'step' not in mclimate_median.dims:
        return forecast_weekly, mclimate_median
    n = min(forecast_weekly.sizes['step'], mclimate_median.sizes['step'])
    forecast_weekly = forecast_weekly.isel(step=slice(0, n))
    mclimate_aligned = mclimate_median.isel(step=slice(0, n)).assign_coords(step=forecast_weekly.step.values)
    return forecast_weekly, mclimate_aligned

IO_winds_weekly=gef.week_mean(IO_winds_raw).isel(step=slice(None,4))
IO_sst_weekly=gef.week_mean(IO_sst_raw).isel(step=slice(None,4))

IO_winds_weekly, winds_mclimate_weekly = _align_weekly_mclimate(IO_winds_weekly, IO_winds_mclimate)
IO_sst_weekly, sst_mclimate_weekly = _align_weekly_mclimate(IO_sst_weekly, IO_sst_mclimate)

anom_winds_weekly=IO_winds_weekly-winds_mclimate_weekly
ds_to_plot_winds_weekly=anom_winds_weekly.mean('number')

anom_sst_weekly=IO_sst_weekly-sst_mclimate_weekly
ds_to_plot_sst_weekly=anom_sst_weekly.mean('number')

period_end = str(ds_to_plot_winds_weekly.time.values + pd.Timedelta("28d"))[:10]
wind_sst_title = f'Indian Ocean Weekly 10m Winds and SST Anomaly | {str(ds_to_plot_winds_weekly.time.values)[:10]} until {period_end}'

gef.plot_wind_and_sst_anomaly_weekly(
    ds_to_plot_winds_weekly, ds_to_plot_sst_weekly, wind_sst_title,
    weekly_save_path+f'ECMWF_s2s_10wind_sst_anomaly_{date_str}.png',
)

# ========================================================
# PRECIPITATION ANOMALY (monthly total and per-week, first 4 weekly steps)
# ========================================================

# alt-region precip zarr has weekly-accumulated steps already, so
# acum_to_instant just un-accumulates them into per-week totals
IO_precip=xr.open_zarr(f'{data_path}/ECMWF_s2s_precip_alt_{date_str}.zarr',consolidated=True).compute()
IO_precip_weekly=gef.acum_to_instant(IO_precip).isel(step=slice(None,4)).mean('number')

# reforecasts give the model climatology to compare the forecast against, kept
# per-week (not summed) so a weekly anomaly can be computed alongside the monthly one
reforecasts=gef.load_reforecasts(date_str,'single',var='pr',bbox={'lat1': 20, 'lon1': 30, 'lat2': -20, 'lon2': 120})
reforecasts_weekly_persist=gef.week_sum(reforecasts.isel(step=slice(0,28))*60*60*24)

# --- monthly: climatological median/spread of the summed (monthly-total) reforecasts ---
reforecasts_monthly=reforecasts_weekly_persist.sum('step')
mclimate_IO_monthly_precip=reforecasts_monthly.quantile(0.5,{'number','init_time'})
std_IO_monthly_precip=reforecasts_monthly.std({'number','init_time'})

precip_total=IO_precip_weekly.sum('step')
anom_precip=precip_total-mclimate_IO_monthly_precip           # raw anomaly (mm)
std_anom_precip=anom_precip/std_IO_monthly_precip             # anomaly in std-dev units

period_end = str(anom_precip.time.values + pd.Timedelta("28d"))[:10]
precip_title = f'Indian Ocean Monthly Precipitation Anomaly {str(anom_precip.time.values)[:10]} until {period_end}'
std_precip_title = f'Indian Ocean Monthly Standardized Precipitation Anomaly {str(anom_precip.time.values)[:10]} until {period_end}'

precip_colors = dict(
    neg_colors=["#f6e8c3", "#dfc27d", "#bf812d", "#8c510a", "#543005"][::-1],  # tan -> dark brown (dry)
    pos_colors=["#c7eae5", "#80cdc1", "#35978f", "#01665e", "#003c30"],        # light teal -> dark teal (wet)
)

plot_moisture_anomaly_map(
    anom_precip, 'tp', precip_title, 'Precipitation Anomaly (mm)',
    monthly_save_path+f'ECMWF_s2s_precip_anomaly_{date_str}.png', **precip_colors,
)
plot_moisture_anomaly_map(
    std_anom_precip, 'tp', std_precip_title, 'Standardized Precipitation Anomaly',
    monthly_save_path+f'ECMWF_s2s_precip_std_anomaly_{date_str}.png', **precip_colors,
)

# --- weekly: climatological median/spread kept per-week, positionally aligned onto
# IO_precip_weekly's own step labels since the two step conventions aren't guaranteed
# to share exact coordinate values ---
n_precip_weeks = min(len(IO_precip_weekly.step), len(reforecasts_weekly_persist.step))
IO_precip_weekly = IO_precip_weekly.isel(step=slice(0, n_precip_weeks))

mclimate_IO_weekly_precip = reforecasts_weekly_persist.quantile(0.5, {'number', 'init_time'}) \
    .isel(step=slice(0, n_precip_weeks)).assign_coords(step=IO_precip_weekly.step.values)
std_IO_weekly_precip = reforecasts_weekly_persist.std({'number', 'init_time'}) \
    .isel(step=slice(0, n_precip_weeks)).assign_coords(step=IO_precip_weekly.step.values)

anom_precip_weekly = IO_precip_weekly - mclimate_IO_weekly_precip
std_anom_precip_weekly = anom_precip_weekly / std_IO_weekly_precip

precip_weekly_title = f'Indian Ocean Weekly Precipitation Anomaly | {str(anom_precip_weekly.time.values)[:10]} until {period_end}'
std_precip_weekly_title = f'Indian Ocean Weekly Standardized Precipitation Anomaly | {str(anom_precip_weekly.time.values)[:10]} until {period_end}'

plot_moisture_anomaly_weekly_map(
    anom_precip_weekly, 'tp', precip_weekly_title, 'Precipitation Anomaly (mm)',
    weekly_save_path+f'ECMWF_s2s_precip_anomaly_{date_str}.png', **precip_colors,
)
plot_moisture_anomaly_weekly_map(
    std_anom_precip_weekly, 'tp', std_precip_weekly_title, 'Standardized Precipitation Anomaly',
    weekly_save_path+f'ECMWF_s2s_precip_std_anomaly_{date_str}.png', **precip_colors,
)

# ========================================================
# TCW ANOMALY (monthly total and per-week, first 4 weekly steps)
# ========================================================
IO_tcw=gef.week_mean(xr.open_zarr(f'{data_path}/ECMWF_s2s_tcw_{date_str}.zarr',consolidated=True).compute())
m_climate_IO_tcw=gef.open_mclimate(IO_tcw,folder_path=f'{prefix}/m-climate/',var='tcw_global')

anom_tcw=(IO_tcw-m_climate_IO_tcw).isel(step=slice(None,4)).mean('number')

period_end = str(anom_tcw.time.values + pd.Timedelta("28d"))[:10]
tcw_weekly_title = f'Indian Ocean Weekly Total Column Water Anomaly | {str(anom_tcw.time.values)[:10]} until {period_end}'
tcw_monthly_title = f'Indian Ocean Monthly Total Column Water Anomaly | {str(anom_tcw.time.values)[:10]} until {period_end}'

tcw_colors = dict(
    neg_colors=["#f6e8c3", "#dfc27d", "#bf812d", "#8c510a", "#543005"][::-1],  # tan -> dark brown
    pos_colors=["#c7eae5", "#80cdc1", "#35978f", "#01665e", "#003c30"],       # light teal -> dark teal
)

plot_moisture_anomaly_weekly_map(
    anom_tcw, 'tcw', tcw_weekly_title,
    'Total Column Water Anomaly [kg m$^{-2}$]',
    weekly_save_path+f'ECMWF_s2s_tcw_anomaly_{date_str}.png',
    **tcw_colors,
)

anom_tcw_monthly=anom_tcw.mean('step')
plot_moisture_anomaly_map(
    anom_tcw_monthly, 'tcw', tcw_monthly_title,
    'Total Column Water Anomaly [kg m$^{-2}$]',
    monthly_save_path+f'ECMWF_s2s_tcw_anomaly_{date_str}.png',
    **tcw_colors,
)

# ========================================================
# IVT / Q+U WIND (vertically integrated zonal moisture transport,
# monthly mean and per-week, first 4 weekly steps)
# ========================================================
IO_plev=gef.week_mean(xr.open_zarr(f'{data_path}/ECMWF_s2s_q_u_{date_str}.zarr',consolidated=True).compute())

g = 9.80665  # m/s^2
flux_u = IO_plev.q * IO_plev.u

ivt_u = flux_u.sortby('isobaricInhPa', ascending=True).sel(isobaricInhPa=slice(300,None)).integrate(coord="isobaricInhPa")
# pressure_level is in hPa; multiply by 100 to get Pa, then divide by g
ivt_u = (ivt_u * 100.0 / g)
ivt_u.name = "ivt_u"
ivt_u.attrs = {"units": "kg m-1 s-1", "long_name": "Vertically integrated zonal moisture transport"}

ivt_weekly = ivt_u.to_dataset().isel(step=slice(None,4)).mean('number')
ivt_plot = ivt_weekly.mean('step')

period_end = str(ivt_plot.time.values + pd.Timedelta("28d"))[:10]
ivt_title = f'Indian Ocean Monthly Eastward Moisture Transport {str(ivt_plot.time.values)[:10]} until {period_end}'
ivt_weekly_title = f'Indian Ocean Weekly Eastward Moisture Transport | {str(ivt_weekly.time.values)[:10]} until {period_end}'

ivt_colors = dict(
    neg_colors=["#8a29e1", "#0000fc", "#008b88", "#01fefd", "#afecee"],
    pos_colors=["#f6a25d", "#cf843c", "#ce5a5c", "#b12122", "#810001"],
)

plot_moisture_anomaly_map(
    ivt_plot, 'ivt_u', ivt_title,
    'Zonal Moisture Transport [kg m$^{-1}$ s$^{-1}$]',
    monthly_save_path+f'ECMWF_s2s_ivt_u_{date_str}.png',
    **ivt_colors,
)
plot_moisture_anomaly_weekly_map(
    ivt_weekly, 'ivt_u', ivt_weekly_title,
    'Zonal Moisture Transport [kg m$^{-1}$ s$^{-1}$]',
    weekly_save_path+f'ECMWF_s2s_ivt_u_{date_str}.png',
    **ivt_colors,
)
