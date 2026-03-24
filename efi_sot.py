# imports
import numpy as np
import pandas as pd
import xarray as xr
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import glob
import datetime


def EFI_SOT(data_path, filelist_path, weekly_path):

    def open_files(data_path,filelist_path):
        # read in data
        data = xr.open_dataset(data_path)
        #data_mclim = xr.open_mfdataset("D:/Hiwi/EFI_SOT_Kenya/m-climate/*.nc",combine="nested",concat_dim="init_time")

        filelist = glob.glob(filelist_path)
        filelist.sort()
        data2 = None
        for filepath in filelist:
            data_mclim = xr.open_dataset(filepath)

            # open a new step dimension in order to truly obtain the m-climate for all steps
            first_time = data_mclim["time"].min()
            data_mclim = data_mclim.assign_coords(step=("time",(data_mclim["time"].values - first_time.values)))
            data_mclim = data_mclim.expand_dims("time0")  # keep first date dimension
            data_mclim = data_mclim.assign_coords(time0=[first_time.values])  # only the first date
            data_mclim = data_mclim.swap_dims({"time": "step"})       # use step as the main dim
            data_mclim = data_mclim.drop_vars("time")
            data_mclim = data_mclim.rename({"time0": "time"})

            # concat all initial times in the m-climate
            if data2 is None: data2 = data_mclim
            else: data2 = xr.concat((data2, data_mclim),dim="time")

        data_mclim = data2
        
        return data, data_mclim


    def align_dims(data, data_mclim):
        mdata = data.copy(deep=True)
        mdata["time"] = mdata["time"].expand_dims("time")


        ### bring climatology and forecast to the same dimensions

        # rename dims TODO: automatize this for lat and lon
        data_changed = mdata.rename({"latitude":"lat","longitude":"lon"})
        data_mclim_changed = data_mclim.rename({"avg_2t":"t2m","latitude":"lat","longitude":"lon"})

        # bring mclim for precip to mm
        #data_mclim_changed["tp"] = data_mclim_changed["tp"]*1000
        data_mclim_changed["tp"].attrs["units"] = "mm"

        # time
        data_changed = data_changed.sel(time=data_changed["time"].dt.month.isin(data_mclim_changed["time"].dt.month))
        data_mclim_changed = data_mclim_changed.sel(time=data_mclim_changed["time"].dt.month.isin(data_changed["time"].dt.month))

        data_f = data_changed.sel(time=data_changed["time"].dt.day.isin(data_mclim_changed["time"].dt.day))
        data_clim = data_mclim_changed.sel(time=data_mclim_changed["time"].dt.day.isin(data_changed["time"].dt.day))

        # step
        data_f = data_f.sel(step=data_f["step"].isin(data_clim["step"]))
        data_clim = data_clim.sel(step=data_clim["step"].isin(data_f["step"]))

        # lon and lat
        data_f = data_f.sel(lon=data_f["lon"].isin(data_clim["lon"])).sel(lat=data_f["lat"].isin(data_clim["lat"]))
        data_clim = data_clim.sel(lon=data_clim["lon"].isin(data_f["lon"])).sel(lat=data_clim["lat"].isin(data_f["lat"]))

        # remove accumulation for precipitation
        data_f = data_f.diff("step")


        ### some further preparations

        # aggregate the data to weekly forecast
        #data_f_calc = data_f.coarsen(step=7, boundary="trim").mean()
        data_f_calc = data_f

        # set the step which we want to compare
        #data_f_calc = data_f_calc.isel(step=0)

        # change the entries in the mclimate quantile column to the actual quantiles (due to a data issue)
        data_clim_calc = data_clim.assign_coords(quantile=list(np.linspace(0,1,101)))

        # align the times to the same year
        data_clim_calc = data_clim_calc.assign_coords(time=data_f_calc["time"])#.drop_vars("step"))

        #data_f_calc
        data_clim_calc

        return data_f_calc, data_clim_calc


    ### establish functions for EFI and SOT

    # EFI
    def EFI(data,data_mclim,ens_dim):
        '''
        Calculates the extreme forecast index (EFI) for a forecast with respect to the m-climate.
        
        Parameters:
            data (xr.ds): The forecast
            data_mclim (xr.ds): The m-climate
            ens_dim (str): The name of the dimension in data and data_mclim that contains the ensemble number

        Returns:
            EFI (xr.ds): The EFI for the given data and climatology
        '''
        p = np.linspace(0.01,0.99,99)

        # compute the p-th quantile of the ensemble dimension for the m-climate
        data_mclim_quantiled = data_mclim.where(~data_mclim["quantile"].isin([0,1]),drop=True)#data_mclim.quantile(p,ens_dim)
        quantile = data_mclim_quantiled["quantile"]

        # calculate Qf and compute the discretized integral w.r.t. p resp. quantile
        Qf = (data.expand_dims({"quantile": quantile}) < data_mclim_quantiled).mean(ens_dim) #(data<data_mclim_quantiled).mean(ens_dim)
        result = 2/np.pi * ((quantile - Qf) / (quantile * (1-quantile))**.5).integrate("quantile")

        return result

    # SOT
    def SOT(data,data_mclim,ens_dim,upper_lower=False):
        '''
        Calculates the shift of tails from data with respect to the climatology in m-clim, for the difference between .9 and .99 percentiles

        Parameters:
            data (xr.ds): The forecast
            data_mclim (xr.ds): The m-climate
            ens_dim (str): The name of the dimension in data and data_mclim that contains the ensemble number
            upper_lower (bool): True if lower SOT (for .01 vs. .1 quantiles) shall be calculated in addition to the upper SOT
            
        Returns:
            SOT (xr.ds): The SOT for the given data and climatology
        '''
        if not upper_lower:
            p = [.9,.99]

            # compute the p-th quantile of the ensemble dimension
            data_mclim_quantiled = data_mclim.where(data_mclim["quantile"].isin(p),drop=True)#data_mclim.quantile(p,ens_dim)
            data_quantiled = data.quantile(p,ens_dim)

            # for readability
            Qf_90 = data_quantiled.sel(quantile=.9)
            Qc_90 = data_mclim_quantiled.sel(quantile=.9)
            Qc_99 = data_mclim_quantiled.sel(quantile=.99)

            # compute SOT
            result = (Qf_90 - Qc_99) / (Qc_99 - Qc_90)

            return result

        else:
            raise NotImplementedError("The lower SOT is not implemented as of now.")


    # work through function pipeline
    data,mdata = open_files(data_path,filelist_path)
    data,mdata = align_dims(data,mdata)



    ### calculate
    # EFI
    efi = EFI(data["tp"],mdata["tp"],"number")

    # SOT
    sot = SOT(data["tp"],mdata["tp"],"number")

    return efi,sot



### plot stuff
# created with ChatGPT support
def plot_map_EFI(
    da_EFI,
    projection=ccrs.PlateCarree(),
    cmap="RdBu_r",
    vmin=None,
    vmax=None,
    cbar_title_upper = "upper EFI",
    cbar_title_lower = "lower EFI",
    title=None,
    figsize=(10,6),
):
    
    # mask EFI values
    da_upper = da_EFI.where(da_EFI >= 0.5)
    da_lower = da_EFI.where(da_EFI <= -0.5)
    
    fig, ax = plt.subplots(
        figsize=figsize,
        subplot_kw={"projection": projection}
    )

    levels_upper = np.arange(0.5, 1.01, 0.1)
    levels_lower = np.arange(-1.00, -0.49, 0.1)

    # Create custom colormap: yellow → orange → red
    colors_upper = [
        "#ffff00",  # 0.5 yellow
        "#ffcc00",
        "#ff9900",
        "#ff6600",
        "#ff3300",
        "#ff0000"   # 1.0 red
    ]

    # Create custom colormap: light blue → blue → purple
    colors_lower = [
        "#5ca2fe",  # -0.5 light blue
        "#4457ff",
        "#0000ff",
        "#e100ff",
        "#9d00ff",
        "#42017b"   # -1.0 purple
    ]
    colors_lower = colors_lower[::-1]

    cmap_upper = mcolors.ListedColormap(colors_upper)
    norm_upper = mcolors.BoundaryNorm(levels_upper, cmap_upper.N)
    cmap_lower = mcolors.ListedColormap(colors_lower)
    norm_lower = mcolors.BoundaryNorm(levels_lower, cmap_lower.N)

    # plot data
    im_upper = da_upper.plot.contourf(
        ax=ax,
        transform=ccrs.PlateCarree(),
        cmap=cmap_upper,
        norm=norm_upper,
        #vmin=vmin,
        #vmax=vmax,
        cbar_kwargs={"shrink": 0.3,"label": cbar_title_upper, "orientation": "horizontal",
        "pad": 0.01,        # distance from plot
        "aspect": 50,       # length vs thickness
        "extend" : "neither",
        }
    )

    im_lower = da_lower.plot.contourf(
        ax=ax,
        transform=ccrs.PlateCarree(),
        cmap=cmap_lower,
        norm=norm_lower,
        #vmin=vmin,
        #vmax=vmax,
        cbar_kwargs={"shrink": 0.3,"label": cbar_title_lower, "orientation": "horizontal",
        "pad": 0.1,        # distance from plot
        "aspect": 50,       # length vs thickness
        "extend" : "neither",
        }
    )

    # map features
    ax.coastlines(resolution="110m", linewidth=1)
    ax.add_feature(cfeature.BORDERS, linewidth=0.7)

    # gridlines
    gl = ax.gridlines(draw_labels=True, linestyle="--", alpha=0.5)
    gl.top_labels = False
    gl.right_labels = False

    if title:
        ax.set_title(title)

    plt.tight_layout()
    return fig, ax

def plot_map_SOT(
    da_SOT,
    projection=ccrs.PlateCarree(),
    cmap="coolwarm",
    cbar_title="SOT",
    title=None,
    figsize=(10,6),
):
    
    fig, ax = plt.subplots(
        figsize=figsize,
        subplot_kw={"projection": projection}
    )

    # define norm
    da_SOT_non_inf = da_SOT.where(~np.isin(da_SOT,[-np.inf,np.inf]))
    v = max(abs(da_SOT_non_inf.min()), abs(da_SOT_non_inf.max()))
    print(v)
    norm = mcolors.TwoSlopeNorm(vmin=-v,vcenter=0,vmax=v)

    # plot data
    im = da_SOT.plot.contourf(
        ax=ax,
        transform=ccrs.PlateCarree(),
        cmap=cmap,
        norm=norm,
        cbar_kwargs={"label": cbar_title,}
        #norm=norm_upper
        #vmin=vmin,
        #vmax=vmax,
        #cbar_kwargs={"shrink": 0.7}
    )

    # map features
    ax.coastlines(resolution="110m", linewidth=1)
    ax.add_feature(cfeature.BORDERS, linewidth=0.7)

    # gridlines
    gl = ax.gridlines(draw_labels=True, linestyle="--", alpha=0.5)
    gl.top_labels = False
    gl.right_labels = False

    if title:
        ax.set_title(title)

    plt.tight_layout()
    return fig, ax




