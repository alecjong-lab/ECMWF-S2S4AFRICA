import os
import matplotlib
import get_ECMWF_functions as gef
from datetime import datetime, timedelta
import xarray as xr
import matplotlib.pyplot as plt

if "DATE_STR" in os.environ:
    date_str=os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

matplotlib.rcParams.update({'font.size': 20})

prefix=os.environ["MAIN_PATH"]
data_path=f'{prefix}/data/{date_str}'

IO_winds=xr.open_zarr(f'{data_path}/ECMWF_s2s_10wind_alt_{date_str}.zarr',consolidated=True).isel(step=slice(None,4*28+1)).mean('step').compute()
IO_sst=xr.open_zarr(f'{data_path}/ECMWF_s2s_sst_{date_str}.zarr',consolidated=True).isel(step=slice(None,4*28+1)).mean('step').compute()

IO_winds_mclimate=gef.open_mclimate(IO_winds,var='IO_10m_wind')
IO_sst_mclimate=gef.open_mclimate(IO_sst,var='IO_sst')

anom_winds=IO_winds-IO_winds_mclimate.sel(quantile=0.5)
ds_to_plot_winds=anom_winds.mean('number')

anom_sst=IO_sst-IO_sst_mclimate.sel(quantile=0.5)
ds_to_plot_sst=anom_sst.mean('number')

fig, axes = gef.plot_wind_and_sst_anomaly(ds_to_plot_winds, ds_to_plot_sst)

save_path=f'plots/diagnostics/{date_str}/monthly/'
os.makedirs(save_path, exist_ok=True)

plt.savefig(save_path+f'ECMWF_s2s_10wind_sst_anomaly_{date_str}.png', bbox_inches='tight')