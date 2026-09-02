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

save_path=f'plots/diagnostics/{date_str}/monthly/'
os.makedirs(save_path, exist_ok=True)

INDIAN_OCEAN_EXTENT = (30, 120, -20, 20)


def indian_ocean_basemap(ax, extent=INDIAN_OCEAN_EXTENT):
    """Coastlines, land/ocean shading, country borders, gridlines shared by every map here."""
    ax.set_extent(extent, crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.OCEAN, facecolor='#eaf3fa', zorder=0)
    ax.add_feature(cfeature.LAND, facecolor='#f2f2f0', zorder=0)
    ax.coastlines(resolution='50m', linewidth=0.6, color='#444444')
    ax.add_feature(cfeature.BORDERS, linestyle=':', linewidth=1, color='k')

    gl = ax.gridlines(draw_labels=True, linewidth=0.5, color='gray', alpha=0.7, linestyle='--')
    gl.top_labels = False
    gl.right_labels = False
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


# ========================================================
# WIND / SST ANOMALY (monthly mean over first 28 days)
# ========================================================

IO_winds=xr.open_zarr(f'{data_path}/ECMWF_s2s_10wind_alt_{date_str}.zarr',consolidated=True).isel(step=slice(None,4*28+1)).mean('step').compute()
IO_sst=xr.open_zarr(f'{data_path}/ECMWF_s2s_sst_{date_str}.zarr',consolidated=True).isel(step=slice(None,4*28+1)).mean('step').compute()

# model climatology (median) to compare against
IO_winds_mclimate=gef.open_mclimate(IO_winds,var='IO_10m_wind')
IO_sst_mclimate=gef.open_mclimate(IO_sst,var='IO_sst')

# ensemble-mean anomaly = forecast - climatological median
anom_winds=IO_winds-IO_winds_mclimate.sel(quantile=0.5)
ds_to_plot_winds=anom_winds.mean('number')

anom_sst=IO_sst-IO_sst_mclimate.sel(quantile=0.5)
ds_to_plot_sst=anom_sst.mean('number')

fig, axes = gef.plot_wind_and_sst_anomaly(ds_to_plot_winds, ds_to_plot_sst)
plt.savefig(save_path+f'ECMWF_s2s_10wind_sst_anomaly_{date_str}.png', bbox_inches='tight')
plt.close(fig)

# ========================================================
# PRECIPITATION ANOMALY (monthly total, first 4 weekly steps)
# ========================================================

# alt-region precip zarr has weekly-accumulated steps already, so
# acum_to_instant just un-accumulates them into per-week totals
IO_precip=xr.open_zarr(f'{data_path}/ECMWF_s2s_precip_alt_{date_str}.zarr',consolidated=True).compute()
IO_precip_mean=gef.acum_to_instant(IO_precip).isel(step=slice(None,4)).mean('number')

# reforecasts give the model climatology to compare the forecast against
reforecasts=gef.load_reforecasts(date_str,'single',var='pr',bbox={'lat1': 20, 'lon1': 30, 'lat2': -20, 'lon2': 120})
reforecasts_weekly=gef.week_sum(reforecasts.isel(step=slice(0,28))*60*60*24).sum('step')

# climatological median and spread, pooled over ensemble members and init times
mclimate_IO_monthly_precip=reforecasts_weekly.quantile(0.5,{'number','init_time'})
std_IO_monthly_precip=reforecasts_weekly.std({'number','init_time'})

precip_total=IO_precip_mean.sum('step')
anom_precip=precip_total-mclimate_IO_monthly_precip           # raw anomaly (mm)
std_anom_precip=anom_precip/std_IO_monthly_precip             # anomaly in std-dev units

def plot_precip_anomaly(anom, cbar_label, out_name):
    """Map a precip anomaly field (raw or standardized) over the Indian Ocean and save it."""
    # symmetric diverging colour scale centered on zero
    vmin, vmax = gef.symmetric_vmin_vmax(anom)
    locator = MaxNLocator(nbins=20, symmetric=True)
    levels = locator.tick_values(vmin, vmax)

    cmap = plt.get_cmap('BrBG', len(levels) - 1)
    norm = mcolors.BoundaryNorm(levels, cmap.N)

    fig, ax = plt.subplots(
        1, figsize=(20, 20),
        subplot_kw={'projection': ccrs.PlateCarree()}
    )
    indian_ocean_basemap(ax)

    # anomaly field itself
    cf = ax.pcolormesh(anom['longitude'], anom['latitude'], anom.tp, transform=ccrs.PlateCarree(), cmap=cmap, norm=norm, shading='auto')
    period_end = str(anom.time.values + pd.Timedelta("28d"))[:10]
    ax.set_title(f'Indian Ocean Monthly Precipitation Anomaly {str(anom.time.values)[:10]} until {period_end}')
    cbar = fig.colorbar(cf, ax=ax, orientation='horizontal', pad=0.03, shrink=0.6)
    cbar.set_label(cbar_label)

    fig.tight_layout()
    plt.savefig(save_path+out_name, bbox_inches='tight')
    plt.close(fig)

plot_precip_anomaly(anom_precip, 'Precipitation Anomaly (mm)', f'ECMWF_s2s_precip_anomaly_{date_str}.png')
plot_precip_anomaly(std_anom_precip, 'Standardized Precipitation Anomaly', f'ECMWF_s2s_precip_std_anomaly_{date_str}.png')

# ========================================================
# TCW ANOMALY (monthly mean over first 4 weekly steps)
# ========================================================
IO_tcw=gef.week_mean(xr.open_zarr(f'{data_path}/ECMWF_s2s_tcw_{date_str}.zarr',consolidated=True).compute())
m_climate_IO_tcw=gef.open_mclimate(IO_tcw,folder_path=f'{prefix}/m-climate/',var='tcw_global')

anom_tcw=(IO_tcw-m_climate_IO_tcw).isel(step=slice(None,4)).mean('step')

period_end = str(anom_tcw.time.values + pd.Timedelta("28d"))[:10]
tcw_title = f'Indian Ocean Monthly Total Column Water Anomaly | {str(anom_tcw.time.values)[:10]} until {period_end}'

plot_moisture_anomaly_map(
    anom_tcw, 'tcw', tcw_title,
    'Total Column Water Anomaly [kg m$^{-2}$]',
    save_path+f'ECMWF_s2s_tcw_anomaly_{date_str}.png',
    neg_colors=["#f6e8c3", "#dfc27d", "#bf812d", "#8c510a", "#543005"][::-1],  # tan -> dark brown
    pos_colors=["#c7eae5", "#80cdc1", "#35978f", "#01665e", "#003c30"],       # light teal -> dark teal
)

# ========================================================
# IVT / Q+U WIND (vertically integrated zonal moisture transport,
# monthly mean over first 4 weekly steps)
# ========================================================
IO_plev=gef.week_mean(xr.open_zarr(f'{data_path}/ECMWF_s2s_q_u_{date_str}.zarr',consolidated=True).compute())

g = 9.80665  # m/s^2
flux_u = IO_plev.q * IO_plev.u

ivt_u = flux_u.sortby('isobaricInhPa', ascending=True).sel(isobaricInhPa=slice(300,None)).integrate(coord="isobaricInhPa")
# pressure_level is in hPa; multiply by 100 to get Pa, then divide by g
ivt_u = (ivt_u * 100.0 / g)
ivt_u.name = "ivt_u"
ivt_u.attrs = {"units": "kg m-1 s-1", "long_name": "Vertically integrated zonal moisture transport"}

ivt_plot = ivt_u.to_dataset().isel(step=slice(None,4)).mean({'number','step'})

period_end = str(ivt_plot.time.values + pd.Timedelta("28d"))[:10]
ivt_title = f'Indian Ocean Monthly Eastward Moisture Transport {str(ivt_plot.time.values)[:10]} until {period_end}'

plot_moisture_anomaly_map(
    ivt_plot, 'ivt_u', ivt_title,
    'Zonal Moisture Transport [kg m$^{-1}$ s$^{-1}$]',
    save_path+f'ECMWF_s2s_ivt_u_{date_str}.png',
    neg_colors=["#8a29e1", "#0000fc", "#008b88", "#01fefd", "#afecee"],
    pos_colors=["#f6a25d", "#cf843c", "#ce5a5c", "#b12122", "#810001"],
)
