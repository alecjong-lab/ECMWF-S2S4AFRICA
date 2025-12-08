import sys
import xarray as xr
import get_ECMWF_functions as gef
import xarray as xr
import cfgrib

with open("latest_file.txt") as f:
    grib_file = f.read().strip()

print("Plotting from:", grib_file)
data=xr.open_dataset(grib_file,engine='cfgrib')

diff_data = xr.DataArray(
    data.isel(step=slice(1,None)).tp.values - data.isel(step=slice(0,6)).tp.values,
    coords={'latitude':data.latitude,'longitude':data.longitude,'step': data.step[1:],'time':data.time},  # new step coordinate
    dims=data.dims
).clip(min=0)
diff_data=diff_data.to_dataset(name='tp')
diff_data.tp.attrs=data.tp.attrs

ensmean=diff_data.mean(dim='number')

bboxes = {
    "Namibia": {"lat1": -15, "lon1": 10, "lat2": -31, "lon2": 27},
    "Botswana": {"lat1": -15, "lon1": 18, "lat2": -28, "lon2": 31},
    "Kenya": {"lat1": 7, "lon1": 32, "lat2": -6, "lon2": 43},
    "Zambia": {"lat1": -6, "lon1": 20, "lat2": -20, "lon2": 35},
    "Madagascar": {"lat1": -10, "lon1": 42, "lat2": -27, "lon2": 52}
}


country='Madagascar'

gef.lat1=bboxes[country]['lat1']
gef.lat2=bboxes[country]['lat2']
gef.lon1=bboxes[country]['lon1']
gef.lon2=bboxes[country]['lon2']

ds_to_plot=diff_data.sel(longitude=slice(bboxes[country]['lon1'],bboxes[country]['lon2']),latitude=slice(bboxes[country]['lat1'],bboxes[country]['lat2']))
fig=gef.panel_plot_variable(ds_to_plot,variable='tp',forecast_timestep=ds_to_plot.step.values,cmap='Blues',fontsize=12)
plt.savefig('test.png')