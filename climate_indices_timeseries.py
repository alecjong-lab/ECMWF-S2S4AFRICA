import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import xarray as xr

import get_ECMWF_functions as gef

# forecast date to run for, defaults to 2 days ago
if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

prefix = os.environ.get("MAIN_PATH", os.getcwd())
data_path = f'{prefix}/data/{date_str}'

out_dir = f'data/{date_str}/diagnostics/'
os.makedirs(out_dir, exist_ok=True)
out_csv = f'{out_dir}/climate_indices_{date_str}.csv'

G = 9.80665  # m/s^2, for the IVT vertical integration


def box_mean(da, lat1, lat2, lon1, lon2, lat_name='latitude', lon_name='longitude'):
    """Area-weighted (cos-latitude) mean of `da` over a lat/lon box.

    lat1/lat2 are the box's two latitude bounds in either order. lon1/lon2 are
    normally West/East in either order, except lon1 > lon2 is treated as a box
    that crosses the antimeridian (e.g. Nino3.4's 170E-120W)."""
    lat_vals = da[lat_name]
    lat_ascending = bool(lat_vals[0] < lat_vals[-1])
    lo, hi = sorted((lat1, lat2))
    lat_slice = slice(lo, hi) if lat_ascending else slice(hi, lo)
    sub_lat = da.sel({lat_name: lat_slice})

    if lon1 <= lon2:
        sub = sub_lat.sel({lon_name: slice(lon1, lon2)})
    else:
        east = sub_lat.sel({lon_name: slice(lon1, 180)})
        west = sub_lat.sel({lon_name: slice(-180, lon2)})
        sub = xr.concat([east, west], dim=lon_name)

    weights = np.cos(np.deg2rad(sub[lat_name]))
    return sub.weighted(weights).mean(dim=[lat_name, lon_name], skipna=True)


def series_from_da(da, name):
    """(step,)-shaped DataArray -> date-indexed Series, keyed by init date + step.
    Computed from 'step' rather than a 'valid_time' coordinate since operations like
    week_mean/resample or arithmetic between two DataArrays don't reliably carry
    valid_time (a non-dim coordinate) through."""
    da = da.compute()
    dates = (pd.Timestamp(date_str) + pd.to_timedelta(da['step'].values)).normalize()
    return pd.Series(da.values, index=dates, name=name)


def _align_weekly_step(forecast_weekly, clim):
    """Positionally align a (step,)-having climatology onto forecast_weekly's own step
    labels (matching how gef.anomaly_from_mclimate compares by lead-week position, not
    by the climatology's own step/time coordinate values)."""
    n = min(forecast_weekly.sizes['step'], clim.sizes['step'])
    forecast_weekly = forecast_weekly.isel(step=slice(0, n))
    clim_aligned = clim.isel(step=slice(0, n)).assign_coords(step=forecast_weekly.step.values)
    return forecast_weekly, clim_aligned


series = {}
# anomaly vs. model climatology (median only for now); Nino3.4 is left out until its
# own m-climate exists, and Central Equatorial Indian Ocean rainfall is left out since
# the T_pr climatology domain doesn't reach 80-90E
anomaly_series = {}

# ---------------------------------------------------------------
# SST-based indices: IOD West, IOD East. Nino3.4 needs the separate
# Pacific-domain SST fetch added to download_s2s.py (ECMWF_s2s_sst_nino34_*)
# since the S2S alt-domain SST fetch used for IOD doesn't reach the Pacific.
# ---------------------------------------------------------------
sst_path = f'{data_path}/ECMWF_s2s_sst_{date_str}.zarr'
if os.path.isdir(sst_path):
    sst = xr.open_zarr(sst_path, consolidated=True).sst
    sst_c = (sst - 273.15).mean('number')

    series['IOD_West_SST_50-70E_10S-10N'] = series_from_da(
        box_mean(sst_c, 10, -10, 50, 70), 'IOD_West_SST')
    series['IOD_East_SST_90-110E_10S-0N'] = series_from_da(
        box_mean(sst_c, 0, -10, 90, 110), 'IOD_East_SST')

    # anomaly vs. climatology only reaches weekly resolution (climatology is stored
    # per-week); subtract in Kelvin so the offset from any degC conversion cancels out
    sst_mclimate = gef.open_mclimate(sst, folder_path=f'{prefix}/m-climate/', var='IO_sst').sst.sel(quantile=0.5)
    sst_weekly = gef.week_mean(sst.mean('number')).isel(step=slice(None, 4))
    sst_weekly, sst_mclimate_aligned = _align_weekly_step(sst_weekly, sst_mclimate)
    sst_anom = sst_weekly - sst_mclimate_aligned

    anomaly_series['IOD_West_SST_50-70E_10S-10N'] = series_from_da(
        box_mean(sst_anom, 10, -10, 50, 70), 'IOD_West_SST_anom')
    anomaly_series['IOD_East_SST_90-110E_10S-0N'] = series_from_da(
        box_mean(sst_anom, 0, -10, 90, 110), 'IOD_East_SST_anom')
else:
    print(f"WARNING: {sst_path} not found, skipping IOD indices")

# Nino3.4 climatology, built from reforecasts the same way as IndianOceanState.py's
# Indian Ocean precip climatology, and cached under m-climate/Nino34_sst/ - independent
# of whether the operational Nino3.4 SST download (below) has run yet
nino_mclimate_path = gef.find_cached_mclimate(date_str, 'Nino34_sst', folder_path=f'{prefix}/m-climate/', max_gap_days=3)
if nino_mclimate_path:
    print(f"Using cached Nino3.4 SST climatology: {nino_mclimate_path}")
    nino_mclimate_ds = xr.open_dataset(nino_mclimate_path, engine="netcdf4", decode_timedelta=True)
else:
    # reforecast longitude only runs -180..178.5 (no wraparound support in a plain
    # slice), so pull the whole band and let box_mean do the antimeridian split later
    reforecast_nino_sst, reforecast_center_day = gef.load_reforecasts(
        date_str, 'single', var='sst',
        bbox={'lat1': 5, 'lon1': -180, 'lat2': -5, 'lon2': 178.5}, time_range=slice(0, 28))
    reforecast_nino_sst_weekly = gef.week_mean(reforecast_nino_sst).isel(step=slice(0, 4))
    nino_mclimate_ds = xr.Dataset({
        'sst': reforecast_nino_sst_weekly.quantile(0.5, {'number', 'init_time'})
    })
    # label with the reforecast archive's actual center date, not date_str, since
    # the 5-init-time window is built around the nearest day reforecasts exist for
    reforecast_center_date = f"{date_str[:4]}-{reforecast_center_day}"
    print(f"Reforecast window for Nino34_sst is centered on {reforecast_center_day} (nearest to {date_str[5:]})")
    gef.save_mclimate(nino_mclimate_ds, reforecast_center_date, 'Nino34_sst', folder_path=f'{prefix}/m-climate/')

nino_path = f'{data_path}/ECMWF_s2s_sst_nino34_{date_str}.zarr'
if os.path.isdir(nino_path):
    nino_sst = xr.open_zarr(nino_path, consolidated=True).sst
    nino_sst_c = (nino_sst - 273.15).mean('number')
    series['Nino3.4_SST_170E-120W_5S-5N'] = series_from_da(
        box_mean(nino_sst_c, 5, -5, 170, -120), 'Nino3.4_SST')

    nino_sst_weekly = gef.week_mean(nino_sst.mean('number')).isel(step=slice(None, 4))
    nino_sst_weekly, nino_mclimate_aligned = _align_weekly_step(nino_sst_weekly, nino_mclimate_ds.sst)
    nino_anom = nino_sst_weekly - nino_mclimate_aligned

    anomaly_series['Nino3.4_SST_170E-120W_5S-5N'] = series_from_da(
        box_mean(nino_anom, 5, -5, 170, -120), 'Nino3.4_SST_anom')
else:
    print(f"WARNING: {nino_path} not found - Nino3.4 needs a dedicated Pacific SST "
          f"download (run download_s2s.py after pulling in its sst_nino34 group); "
          f"leaving that column empty for this run")

# ---------------------------------------------------------------
# Rainfall indices from the daily, Africa-domain precip zarr
# (Kenya, Tanzania/Western Indian Ocean)
# ---------------------------------------------------------------
precip_path = f'{data_path}/ECMWF_s2s_precip_{date_str}.zarr'
if os.path.isdir(precip_path):
    precip = xr.open_zarr(precip_path, consolidated=True)
    precip_daily = gef.acum_to_instant(precip, dim='step', var='tp').tp.mean('number')

    series['Central_Eastern_Kenya_Rainfall_36-42E_5S-5N'] = series_from_da(
        box_mean(precip_daily, 5, -5, 36, 42), 'Kenya_Rainfall')
    series['Tanzania_W_Indian_Ocean_Rainfall_36-50E_10S-5S'] = series_from_da(
        box_mean(precip_daily, -5, -10, 36, 50), 'Tanzania_Rainfall')

    # T_pr climatology is built against weekly-accumulation-mark steps (7, 14, ... days),
    # the same as plot_s2s.py's own data_weekly - reconstruct that subset from the daily
    # cumulative series before deaccumulating, instead of the plain daily series above
    precip_step_hours = (precip.step.values * 1e-9 / 3600).astype('int')
    precip_weekly_marks = precip.step.values[precip_step_hours % 168 == 0]
    precip_weekly = gef.acum_to_instant(precip.sel(step=precip_weekly_marks), dim='step', var='tp').tp.mean('number')

    tpr_mclimate = gef.open_mclimate(precip, folder_path=f'{prefix}/m-climate/', var='T_pr').tp
    # quantile here is a bare position index into 101 climatological samples (see
    # gef.anomaly_from_mclimate) rather than a real quantile label - 50 of 101 is the
    # median. tp's 'units': 'm' attribute is mislabeled (checked against real values):
    # the rest of the codebase (e.g. plot_s2s.py's own mclimate comparisons) also
    # compares it directly against mm forecast tp with no conversion.
    tpr_mclimate_mm = tpr_mclimate.isel(quantile=50).rename({'time': 'step'})

    precip_weekly, tpr_mclimate_aligned = _align_weekly_step(precip_weekly, tpr_mclimate_mm)
    precip_anom = precip_weekly - tpr_mclimate_aligned

    anomaly_series['Central_Eastern_Kenya_Rainfall_36-42E_5S-5N'] = series_from_da(
        box_mean(precip_anom, 5, -5, 36, 42), 'Kenya_Rainfall_anom')
    anomaly_series['Tanzania_W_Indian_Ocean_Rainfall_36-50E_10S-5S'] = series_from_da(
        box_mean(precip_anom, -5, -10, 36, 50), 'Tanzania_Rainfall_anom')
else:
    print(f"WARNING: {precip_path} not found, skipping Kenya/Tanzania rainfall indices")

# ---------------------------------------------------------------
# Central Equatorial Indian Ocean rainfall - only the alt (Indian Ocean
# domain) precip zarr reaches 80-90E; it's downloaded daily (cumulative-
# since-init, same cadence as the main precip zarr), so this deaccumulates
# the same way as the Kenya/Tanzania indices above.
# ---------------------------------------------------------------
precip_alt_path = f'{data_path}/ECMWF_s2s_precip_alt_{date_str}.zarr'
if os.path.isdir(precip_alt_path):
    precip_alt = xr.open_zarr(precip_alt_path, consolidated=True)
    precip_alt_daily = gef.acum_to_instant(precip_alt, dim='step', var='tp').tp.mean('number')
    series['Central_Equatorial_Indian_Ocean_Rainfall_80-90E_10S-5N'] = series_from_da(
        box_mean(precip_alt_daily, 5, -10, 80, 90), 'CEIO_Rainfall')
else:
    print(f"WARNING: {precip_alt_path} not found, skipping Central Equatorial Indian Ocean rainfall")

# ---------------------------------------------------------------
# Zonal moisture transport indices: vertically integrated q*u,
# 300-1000 hPa, from the Indian Ocean q_u zarr
# ---------------------------------------------------------------
qu_path = f'{data_path}/ECMWF_s2s_q_u_{date_str}.zarr'
if os.path.isdir(qu_path):
    qu = xr.open_zarr(qu_path, consolidated=True)
    flux_u = qu.q * qu.u
    ivt_u = flux_u.sortby('isobaricInhPa', ascending=True).sel(isobaricInhPa=slice(300, None)) \
        .integrate(coord='isobaricInhPa')
    ivt_u = (ivt_u * 100.0 / G).mean('number').isel(step=slice(1,None))  # hPa -> Pa, then divide by g

    series['Onshore_Zonal_Moisture_Transport_34-44E_10S-5S'] = series_from_da(
        box_mean(ivt_u, -5, -10, 34, 44), 'Onshore_IVT')
    series['Central_Indian_Ocean_Zonal_Moisture_Transport_60-80E_0S-5N'] = series_from_da(
        box_mean(ivt_u, 5, 0, 60, 80), 'Central_IO_IVT')

    ivt_mclimate = gef.open_mclimate(qu, folder_path=f'{prefix}/m-climate/', var='ivt_week').ivt_u.sel(quantile=0.5)
    ivt_weekly = gef.week_mean(ivt_u).isel(step=slice(None, 4))
    ivt_weekly, ivt_mclimate_aligned = _align_weekly_step(ivt_weekly, ivt_mclimate)
    ivt_anom = ivt_weekly - ivt_mclimate_aligned

    anomaly_series['Onshore_Zonal_Moisture_Transport_34-44E_10S-5S'] = series_from_da(
        box_mean(ivt_anom, -5, -10, 34, 44), 'Onshore_IVT_anom')
    anomaly_series['Central_Indian_Ocean_Zonal_Moisture_Transport_60-80E_0S-5N'] = series_from_da(
        box_mean(ivt_anom, 5, 0, 60, 80), 'Central_IO_IVT_anom')
else:
    print(f"WARNING: {qu_path} not found, skipping moisture transport indices")

if not series:
    raise SystemExit(f"No source zarr stores found under {data_path} - nothing to write")

df = pd.concat(series, axis=1).sort_index()
df.index.name = 'date'
df.to_csv(out_csv)
print(f"Wrote {out_csv}")

if anomaly_series:
    anomaly_out_csv = f'{out_dir}/climate_indices_anomaly_{date_str}.csv'
    anomaly_df = pd.concat(anomaly_series, axis=1).sort_index()
    anomaly_df.index.name = 'date'
    anomaly_df.to_csv(anomaly_out_csv)
    print(f"Wrote {anomaly_out_csv}")
else:
    print("No anomaly indices computed - nothing written for the anomaly CSV")
