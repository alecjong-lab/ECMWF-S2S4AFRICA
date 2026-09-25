import os
from datetime import datetime, timedelta

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import matplotlib.patches
from matplotlib.colors import ListedColormap, BoundaryNorm, LinearSegmentedColormap
import geopandas as gpd
import numpy as np
import pandas as pd
import rioxarray
import xarray as xr

import get_ECMWF_functions as gef

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

prefix = os.environ.get("MAIN_PATH", os.getcwd())
data_path = f'{prefix}/data/{date_str}'

country = os.environ.get("COUNTRY", "Kenya")

# same bbox convention (and duplication) as plot_s2s.py / plot_gefs.py / fetch_dynamical.py
bboxes = {
    "Namibia":    {"lat1": -16.5, "lon1": 11.5, "lat2": -30,   "lon2": 25.5},
    "Botswana":   {"lat1": -17.5, "lon1": 19.5, "lat2": -27,   "lon2": 30},
    "Kenya":      {"lat1": 6,     "lon1": 33,   "lat2": -5,    "lon2": 42},
    "Zambia":     {"lat1": -8,    "lon1": 21,   "lat2": -18.5, "lon2": 34},
    "Madagascar": {"lat1": -10.5, "lon1": 42,   "lat2": -27,   "lon2": 51},
    "Angola":     {"lat1": -5.5,  "lon1": 11.5, "lat2": -18,   "lon2": 24.5},
    "Ghana":      {"lat1": 12,    "lon1": -3.5, "lat2": 4,     "lon2": 1.5},
    "Senegal":    {"lat1": 17,    "lon1": -18,  "lat2": 12,    "lon2": -11.25},
    "Ethiopia":   {"lat1": 16.5,  "lon1": 31.5, "lat2": 1.5,   "lon2": 49.5},
    "Great_Horn": {"lat1": 25.5,  "lon1": 19.5, "lat2": -9,    "lon2": 57},
    "Zimbabwe":   {"lat1": -15,   "lon1": 25,   "lat2": -22.5, "lon2": 33.5},
    "Malawi":     {"lat1": -9,    "lon1": 31.5, "lat2": -18,   "lon2": 37.5},
}
bbox = bboxes[country]

# same Kenya county shapefile dowscale_dekade.py clips the downscaled forecast to
kenya_shapefile = "downscale_data/Kenya_Counties_KNSDI.shp"

plot_dir = f'plots/{country}/{date_str}/monthly'

# minimum % of ensemble members that must find an onset for a cell to get an
# onset-date color; cells below it are hatched out instead (see plot_onset_map)
ONSET_AGREEMENT_THRESH = float(os.environ.get("ONSET_AGREEMENT_THRESH", 66))

# gaussian sigma (in 0.05deg fine-grid cells) for smoothing the 1.5deg daily
# timing profile the downscaled forecast is disaggregated with -- see
# gef.disaggregate_weekly_to_daily's profile_sigma (0 = no smoothing)
ONSET_PROFILE_SIGMA = float(os.environ.get("ONSET_PROFILE_SIGMA", 10))
os.makedirs(plot_dir, exist_ok=True)


def clip_to_kenya(ds):
    """
    Clip to Kenya's actual land shape (union of counties), not just its bbox.
    all_touched=True keeps every cell the shape touches rather than only cells
    whose center falls inside it -- on the coarse S2S 1.5deg grid, center-only
    clipping drops most border/coastal cells to NaN.
    """
    return gef.clip_to_shapefile(ds.rio.write_crs("EPSG:4326"), kenya_shapefile, all_touched=True)


def summarize(name, onset):
    found = onset.notnull()
    n_found, n_total = int(found.sum()), int(found.size)
    print(f"{name}: onset found for {n_found}/{n_total} points/members")
    if n_found:
        print(f"{name}: earliest onset {onset.min(skipna=True).values}, latest onset {onset.max(skipna=True).values}")


def clean_for_netcdf(da):
    """Strip attrs netCDF can't serialize (e.g. dict-valued 'statistics_approximate'
    on dynamical.org catalog coords) so .to_netcdf() doesn't blow up."""
    def safe(attrs):
        return {k: v for k, v in attrs.items()
                 if isinstance(v, (str, bytes, int, float, np.integer, np.floating, np.ndarray, list, tuple))}

    da = da.copy()
    da.attrs = safe(da.attrs)
    for name in da.coords:
        da.coords[name].attrs = safe(da.coords[name].attrs)
    return da


def stack_reforecast_years(onset_per_year):
    """Combine a list of per-year onset DataArrays (each dims: number, latitude,
    longitude) into one DataArray with a single 'number' dim covering every
    (year, ensemble member) combination -- so plot_onset_map's existing
    mean/'% of members found an onset' logic (which reduces over 'number')
    doubles as an average over the reforecast climatology without changes."""
    onset = xr.concat(onset_per_year, dim='year').rename({'number': 'member'})
    if 'init_time' in onset.coords:
        onset = onset.drop_vars('init_time')
    return onset.stack(number=('year', 'member')).reset_index('number', drop=True)


def build_discrete_cmap(vmin, vmax, n_shades=4):
    """Discrete colormap: 5 main color bands (sand, green, cyan, pink-purple, gray),
    each split into n_shades discrete light->dark steps.
    Returns (cmap, norm, boundaries, segment_edges)."""
    segments = [
        ("#EFDFC0", "#8B5A2B"),  # sand
        ("#B9E3A8", "#1B5E20"),  # green
        ("#A9F0EC", "#00838F"),  # cyan
        ("#F3BEDE", "#7B2D8E"),  # pink-purple
        ("#E3E3E3", "#4D4D4D"),  # gray
    ]

    # 6 edges marking where one main color band switches to the next
    segment_edges = np.linspace(vmin, vmax, len(segments) + 1)

    colors = []
    boundaries = [segment_edges[0]]
    for i, (c_light, c_dark) in enumerate(segments):
        seg_cmap = LinearSegmentedColormap.from_list("", [c_light, c_dark])
        # discrete shades within this band, sampled at bin centers for even spacing
        shade_positions = (np.arange(n_shades) + 0.5) / n_shades
        colors.extend(seg_cmap(shade_positions))

        # sub-boundaries within this band
        sub_edges = np.linspace(segment_edges[i], segment_edges[i + 1], n_shades + 1)[1:]
        boundaries.extend(sub_edges)

    cmap = ListedColormap(colors, name="onset_bands_discrete")
    norm = BoundaryNorm(boundaries, ncolors=cmap.N)
    return cmap, norm, np.array(boundaries), segment_edges


def plot_onset_map(onset, bbox, year, title, save_path, forecast_start, n_time, search_days=21,
                   agreement_thresh=None):
    """
    Map of ensemble-mean onset day-of-year (deterministic sources plot their
    single onset field directly). Where an ensemble dimension is present,
    cells where fewer than agreement_thresh % of members found an onset are
    not colored but hatched instead, so only onsets a large enough share of
    the ensemble agrees on get a date color. Cells where no member at all
    found an onset stay blank. On a coarse grid (e.g. S2S) the % of members
    is also written in each cell.

    The color scale runs from the forecast's first day (forecast_start) to
    the last day that still leaves a full search_days window for the
    dry-spell check (see _rainfall_onset_nd's t_max) -- i.e. the actual
    achievable onset range for this forecast, not just whichever onset dates
    happened to occur. Fixing it to the forecast window (rather than the
    data's own min/max) puts every source on a comparable "days since
    forecast start" scale regardless of which onsets it actually found.
    """
    if agreement_thresh is None:
        agreement_thresh = ONSET_AGREEMENT_THRESH
    onset_doy = onset.dt.dayofyear  # NaT -> NaN

    has_ensemble = 'number' in onset_doy.dims
    if has_ensemble:
        mean_doy = onset_doy.mean(dim='number', skipna=True)
        pct_valid = onset_doy.notnull().mean(dim='number') * 100
    else:
        mean_doy = onset_doy
        pct_valid = None

    mean_doy = mean_doy.sel(longitude=slice(bbox['lon1'], bbox['lon2']), latitude=slice(bbox['lat1'], bbox['lat2']))
    # per-cell "% of members" text only stays legible on a coarse grid (e.g. S2S)
    show_text = False
    if pct_valid is not None:
        pct_valid = pct_valid.sel(longitude=slice(bbox['lon1'], bbox['lon2']), latitude=slice(bbox['lat1'], bbox['lat2']))
        show_text = pct_valid.sizes['latitude'] * pct_valid.sizes['longitude'] <= 200

    if bool(mean_doy.isnull().all()):
        print(f"{title}: no onset found anywhere, skipping plot")
        return

    vmin = float(forecast_start.dayofyear)
    vmax = float((forecast_start + pd.Timedelta(days=n_time - search_days)).dayofyear)
    if vmin == vmax:
        vmax = vmin + 1  # BoundaryNorm needs a non-degenerate range
    onset_cmap, onset_norm, boundaries, segment_edges = build_discrete_cmap(vmin, vmax, n_shades=4)

    fig, ax = plt.subplots(figsize=(9, 7), subplot_kw={'projection': ccrs.PlateCarree()})

    low_agreement = None
    if pct_valid is not None:
        low_agreement = (pct_valid < agreement_thresh) & mean_doy.notnull()
        colored_doy = mean_doy.where(~low_agreement)
    else:
        colored_doy = mean_doy

    mesh = colored_doy.plot.pcolormesh(
        x='longitude', y='latitude', ax=ax, cmap=onset_cmap, norm=onset_norm,
        transform=ccrs.PlateCarree(), add_colorbar=False,
    )

    if low_agreement is not None and bool(low_agreement.any()):
        # pcolor (not pcolormesh) so masked cells are left out of the collection
        # entirely -- only the low-agreement cells get the hatch. Hatching is a
        # texture rather than a fill color, so it can't be mistaken for any band
        # of the onset colorscale (which already uses gray).
        hatch_field = low_agreement.where(low_agreement).transpose('latitude', 'longitude')
        lon_c, lat_c = hatch_field.longitude.values, hatch_field.latitude.values
        hatch = ax.pcolor(
            lon_c, lat_c, np.ma.masked_invalid(hatch_field.values.astype(float)),
            cmap=ListedColormap(['white']), transform=ccrs.PlateCarree(),
            shading='nearest', edgecolor='#555555', linewidth=0, hatch='////',
        )
        hatch.set_zorder(mesh.get_zorder() + 0.1)
        ax.legend(
            handles=[matplotlib.patches.Patch(facecolor='white', edgecolor='#555555', hatch='////',
                                              label=f'< {agreement_thresh:g}% of members find an onset')],
            loc='lower left', fontsize=10, framealpha=0.9,
        )

    if country == 'Kenya':
        # outline the KMD county shapefile the data is clipped to, instead of
        # cartopy's Natural Earth coastline/borders -- the two don't quite line
        # up, so drawing both makes the clipped data look offset from the border
        kenya_outline = gpd.read_file(kenya_shapefile).set_crs("EPSG:4326", allow_override=True).dissolve()
        ax.add_geometries(kenya_outline.geometry, crs=ccrs.PlateCarree(),
                          facecolor='none', edgecolor='black', linewidth=1.0, zorder=3)
    else:
        ax.coastlines(resolution='10m', linewidth=0.8)
        ax.add_feature(cfeature.BORDERS, linewidth=0.6)
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, color='gray', alpha=0.5, linestyle='--')
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {'size': 11}
    gl.ylabel_style = {'size': 11}
    ax.set_title(title, fontsize=15, fontweight='bold', pad=10)

    if show_text:
        lon2d, lat2d = np.meshgrid(pct_valid.longitude.values, pct_valid.latitude.values)
        pct_vals = pct_valid.transpose('latitude', 'longitude').values
        for i in range(lat2d.shape[0]):
            for j in range(lat2d.shape[1]):
                val = pct_vals[i, j]
                if not np.isnan(val):
                    ax.text(
                        lon2d[i, j], lat2d[i, j], f'{val:.0f}%',
                        transform=ccrs.PlateCarree(), ha='center', va='center',
                        fontsize=18, color='black',
                        path_effects=[pe.withStroke(linewidth=2, foreground='white')],
                    )

    cbar = fig.colorbar(
        mesh, ax=ax, orientation='vertical', pad=0.03, shrink=0.85, aspect=25,
        boundaries=boundaries, ticks=segment_edges,
    )

    # day-of-year has no year attached, so pick the year the forecast was issued in
    labels = [
        (pd.Timestamp(year=year, month=1, day=1) + pd.Timedelta(days=t - 1)).strftime('%b %d')
        for t in segment_edges
    ]
    cbar.set_ticklabels(labels)
    cbar.set_label('Onset date', fontsize=13)
    cbar.ax.tick_params(labelsize=11)

    fig.tight_layout()
    plt.savefig(save_path, bbox_inches='tight')
    plt.close(fig)


# S2S has the longest forecast window (~46 days) of the three sources, so its
# window is used as the universal colorbar scale for all three plots -- giving
# every plot the same length scale instead of each being cut to its own
# (shorter) forecast horizon. Falls back to a source's own window if the S2S
# computation itself fails.
universal_forecast_start = None
universal_n_time = None

# ---- S2S ECMWF forecast ---------------------------------------------------
s2s_path = f'{data_path}/ECMWF_s2s_precip_{date_str}.zarr'
try:
    s2s = xr.open_zarr(s2s_path, consolidated=True).compute()
    s2s = s2s.sel(latitude=slice(bbox['lat1'], bbox['lat2']), longitude=slice(bbox['lon1'], bbox['lon2']))
    # left un-clipped: at S2S's coarse 1.5deg resolution, shapefile clipping
    # is too blocky to be meaningful (a cell easily spans well past the border)

    s2s_daily = s2s.diff('step').tp
    s2s_daily.attrs = s2s.tp.attrs
    valid_time = s2s.time + s2s_daily.step

    onset_s2s = gef.rainfall_onset_date(s2s_daily, time_dim='step', valid_time=valid_time)
    clean_for_netcdf(onset_s2s).to_netcdf(f'{data_path}/rainfall_onset_s2s_{country}.nc')
    summarize('S2S', onset_s2s)

    title = (
        f'S2S rainy season onset — {country}\n'
        f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
        f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
    )
    universal_forecast_start = pd.Timestamp(valid_time.min().values)
    universal_n_time = s2s_daily.sizes['step']
    plot_onset_map(onset_s2s, bbox, pd.Timestamp(s2s.time.values).year, title, f'{plot_dir}/onset_s2s.png',
                   forecast_start=universal_forecast_start, n_time=universal_n_time)

    # ICPAC_10mm: same wet-spell definition, but a 10mm (not 20mm) 3-day wet-spell total
    onset_s2s_icpac10mm = gef.rainfall_onset_date(s2s_daily, wet_spell_thresh=10.0, time_dim='step', valid_time=valid_time)
    clean_for_netcdf(onset_s2s_icpac10mm).to_netcdf(f'{data_path}/rainfall_onset_icpac10mm_s2s_{country}.nc')
    summarize('S2S (ICPAC_10mm)', onset_s2s_icpac10mm)

    title_icpac10mm = (
        f'S2S rainy season onset (ICPAC_10mm) — {country}\n'
        f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
        f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
    )
    plot_onset_map(onset_s2s_icpac10mm, bbox, pd.Timestamp(s2s.time.values).year, title_icpac10mm,
                   f'{plot_dir}/onset_s2s_icpac10mm.png',
                   forecast_start=universal_forecast_start, n_time=universal_n_time)

    onset_s2s_accum = gef.rainfall_onset_date_accum(s2s_daily, time_dim='step', valid_time=valid_time)
    clean_for_netcdf(onset_s2s_accum).to_netcdf(f'{data_path}/rainfall_onset_accum_s2s_{country}.nc')
    summarize('S2S (accum)', onset_s2s_accum)

    title_accum = (
        f'S2S start of growing season — {country}\n'
        f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
        f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
    )
    plot_onset_map(onset_s2s_accum, bbox, pd.Timestamp(s2s.time.values).year, title_accum,
                   f'{plot_dir}/onset_s2s_accum.png',
                   forecast_start=universal_forecast_start, n_time=universal_n_time, search_days=30)
except Exception as e:
    print(f"S2S: could not compute onset from {s2s_path} ({e}), skipping")

# ---- S2S reforecast climatology (ECMWF only -- GEFS/downscaled have no public
# reforecast archive to build a climatology from) -----------------------------
try:
    # full 46-day horizon like the operational S2S branch above (not truncated to
    # 28 days), since the accum definition needs the full search window near the
    # end of the horizon
    reforecast_pr = gef.load_reforecast(date_str, 'single', 'pr', bbox=bbox, time_range=slice(0, 46), all_years=True)
    reforecast_daily = reforecast_pr * 86400
    reforecast_daily.attrs = dict(reforecast_pr.attrs)
    reforecast_daily.attrs['units'] = 'mm day-1'

    # onset has to be computed one reforecast year at a time: rainfall_onset_date's
    # absolute-date lookup assumes valid_time varies only along time_dim ("step"), so
    # passing one valid_time covering every year at once (each with its own calendar
    # dates) would misalign the lookup -- looping keeps each year's own 1-D valid_time
    # correct, and the results are combined into a climatology afterward
    hold_onset_clim, hold_onset_clim_icpac10mm, hold_onset_clim_accum = [], [], []
    for y in range(len(reforecast_daily.init_time.values)):
        year_da = reforecast_daily.isel(init_time=y)
        valid_time_year = year_da.init_time + year_da.step

        hold_onset_clim.append(gef.rainfall_onset_date(year_da, time_dim='step', valid_time=valid_time_year))
        hold_onset_clim_icpac10mm.append(gef.rainfall_onset_date(year_da, wet_spell_thresh=10.0, time_dim='step', valid_time=valid_time_year))
        hold_onset_clim_accum.append(gef.rainfall_onset_date_accum(year_da, time_dim='step', valid_time=valid_time_year))

    onset_s2s_clim = stack_reforecast_years(hold_onset_clim)
    clean_for_netcdf(onset_s2s_clim).to_netcdf(f'{data_path}/rainfall_onset_s2s_climatology_{country}.nc')
    summarize('S2S climatology', onset_s2s_clim)

    title = f'S2S climatological rainy season onset — {country} ({pd.Timestamp(date_str):%b %d})'
    plot_onset_map(onset_s2s_clim, bbox, pd.Timestamp(date_str).year, title, f'{plot_dir}/onset_s2s_climatology.png',
                   forecast_start=universal_forecast_start or pd.Timestamp(valid_time_year.min().values),
                   n_time=universal_n_time or reforecast_daily.sizes['step'])

    onset_s2s_clim_icpac10mm = stack_reforecast_years(hold_onset_clim_icpac10mm)
    clean_for_netcdf(onset_s2s_clim_icpac10mm).to_netcdf(f'{data_path}/rainfall_onset_icpac10mm_s2s_climatology_{country}.nc')
    summarize('S2S climatology (ICPAC_10mm)', onset_s2s_clim_icpac10mm)

    title_icpac10mm = f'S2S climatological rainy season onset (ICPAC_10mm) — {country} ({pd.Timestamp(date_str):%b %d})'
    plot_onset_map(onset_s2s_clim_icpac10mm, bbox, pd.Timestamp(date_str).year, title_icpac10mm,
                   f'{plot_dir}/onset_s2s_climatology_icpac10mm.png',
                   forecast_start=universal_forecast_start or pd.Timestamp(valid_time_year.min().values),
                   n_time=universal_n_time or reforecast_daily.sizes['step'])

    onset_s2s_clim_accum = stack_reforecast_years(hold_onset_clim_accum)
    clean_for_netcdf(onset_s2s_clim_accum).to_netcdf(f'{data_path}/rainfall_onset_accum_s2s_climatology_{country}.nc')
    summarize('S2S climatology (accum)', onset_s2s_clim_accum)

    title_accum = f'S2S climatological start of growing season — {country} ({pd.Timestamp(date_str):%b %d})'
    plot_onset_map(onset_s2s_clim_accum, bbox, pd.Timestamp(date_str).year, title_accum,
                   f'{plot_dir}/onset_s2s_climatology_accum.png',
                   forecast_start=universal_forecast_start or pd.Timestamp(valid_time_year.min().values),
                   n_time=universal_n_time or reforecast_daily.sizes['step'], search_days=30)
except Exception as e:
    print(f"S2S climatology: could not compute onset from reforecast archive ({e}), skipping")

# ---- GEFS forecast ----------------------------------------------------------
gefs_path = f'{data_path}/gefs/gefs_{country.lower()}.zarr'
try:
    gefs = xr.open_zarr(gefs_path).compute()
    if country == 'Kenya':
        gefs = clip_to_kenya(gefs)
    valid_time = gefs.time + gefs.step

    onset_gefs = gef.rainfall_onset_date(gefs.tp, time_dim='step', valid_time=valid_time)
    clean_for_netcdf(onset_gefs).to_netcdf(f'{data_path}/rainfall_onset_gefs_{country}.nc')
    summarize('GEFS', onset_gefs)

    title = (
        f'GEFS rainy season onset — {country}\n'
        f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
        f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
    )
    plot_onset_map(onset_gefs, bbox, pd.Timestamp(gefs.time.values).year, title, f'{plot_dir}/onset_gefs.png',
                   forecast_start=universal_forecast_start or pd.Timestamp(valid_time.min().values),
                   n_time=universal_n_time or gefs.tp.sizes['step'])

    # ICPAC_10mm: same wet-spell definition, but a 10mm (not 20mm) 3-day wet-spell total
    onset_gefs_icpac10mm = gef.rainfall_onset_date(gefs.tp, wet_spell_thresh=10.0, time_dim='step', valid_time=valid_time)
    clean_for_netcdf(onset_gefs_icpac10mm).to_netcdf(f'{data_path}/rainfall_onset_icpac10mm_gefs_{country}.nc')
    summarize('GEFS (ICPAC_10mm)', onset_gefs_icpac10mm)

    title_icpac10mm = (
        f'GEFS rainy season onset (ICPAC_10mm) — {country}\n'
        f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
        f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
    )
    plot_onset_map(onset_gefs_icpac10mm, bbox, pd.Timestamp(gefs.time.values).year, title_icpac10mm,
                   f'{plot_dir}/onset_gefs_icpac10mm.png',
                   forecast_start=universal_forecast_start or pd.Timestamp(valid_time.min().values),
                   n_time=universal_n_time or gefs.tp.sizes['step'])

    onset_gefs_accum = gef.rainfall_onset_date_accum(gefs.tp, time_dim='step', valid_time=valid_time)
    clean_for_netcdf(onset_gefs_accum).to_netcdf(f'{data_path}/rainfall_onset_accum_gefs_{country}.nc')
    summarize('GEFS (accum)', onset_gefs_accum)

    title_accum = (
        f'GEFS start of growing season — {country}\n'
        f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
        f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
    )
    plot_onset_map(onset_gefs_accum, bbox, pd.Timestamp(gefs.time.values).year, title_accum,
                   f'{plot_dir}/onset_gefs_accum.png',
                   forecast_start=universal_forecast_start or pd.Timestamp(valid_time.min().values),
                   n_time=universal_n_time or gefs.tp.sizes['step'], search_days=30)
except Exception as e:
    print(f"GEFS: could not compute onset from {gefs_path} ({e}), skipping")

# ---- daily disaggregated downscaled forecast, per ensemble member (Kenya only)
if country == 'Kenya':
    downscaled_path = f'{data_path}/data_weekly_Kenya_downscaled.nc'
    try:
        rescaled_forecast = xr.open_dataset(downscaled_path).load()
        rescaled_forecast = clip_to_kenya(rescaled_forecast)
        # raw daily ECMWF ensemble (accumulated since init) -- per member, so each
        # downscaled member is split into days with its own member's daily profile
        data = xr.open_zarr(s2s_path, consolidated=True).compute()

        # disaggregate one member at a time: doing all 101 members at once
        # broadcasts the 1.5deg daily profile onto the fine grid for every member
        # (several GB); onset is per-member anyway, so nothing is lost
        daily_members = [
            gef.disaggregate_weekly_to_daily(rescaled_forecast.tp.sel(number=n), data.tp.sel(number=n),
                                             profile_sigma=ONSET_PROFILE_SIGMA or None).tp
            for n in rescaled_forecast.number.values
        ]
        daily_downscaled = xr.concat(daily_members, dim='number').transpose('number', 'step', 'latitude', 'longitude')
        daily_downscaled.attrs['units'] = 'mm day-1'
        valid_time = data.time + daily_downscaled.step

        title_period = (
            f'forecast {pd.Timestamp(valid_time.min().values) - pd.Timedelta(days=1):%Y-%m-%d} to '
            f'{pd.Timestamp(valid_time.max().values) - pd.Timedelta(days=1):%Y-%m-%d}'
        )
        year = pd.Timestamp(data.time.values).year
        forecast_start = universal_forecast_start or pd.Timestamp(valid_time.min().values)
        n_time = universal_n_time or daily_downscaled.sizes['step']

        onset_downscaled = gef.rainfall_onset_date(daily_downscaled, time_dim='step', valid_time=valid_time)
        clean_for_netcdf(onset_downscaled).to_netcdf(f'{data_path}/rainfall_onset_downscaled_{country}.nc')
        summarize('downscaled', onset_downscaled)
        plot_onset_map(onset_downscaled, bbox, year,
                       f'Downscaled rainy season onset — {country}\n{title_period}',
                       f'{plot_dir}/onset_downscaled.png',
                       forecast_start=forecast_start, n_time=n_time)

        # ICPAC_10mm: same wet-spell definition, but a 10mm (not 20mm) 3-day wet-spell total
        onset_downscaled_icpac10mm = gef.rainfall_onset_date(daily_downscaled, wet_spell_thresh=10.0, time_dim='step', valid_time=valid_time)
        clean_for_netcdf(onset_downscaled_icpac10mm).to_netcdf(f'{data_path}/rainfall_onset_icpac10mm_downscaled_{country}.nc')
        summarize('downscaled (ICPAC_10mm)', onset_downscaled_icpac10mm)
        plot_onset_map(onset_downscaled_icpac10mm, bbox, year,
                       f'Downscaled rainy season onset (ICPAC_10mm) — {country}\n{title_period}',
                       f'{plot_dir}/onset_downscaled_icpac10mm.png',
                       forecast_start=forecast_start, n_time=n_time)

        onset_downscaled_accum = gef.rainfall_onset_date_accum(daily_downscaled, time_dim='step', valid_time=valid_time)
        clean_for_netcdf(onset_downscaled_accum).to_netcdf(f'{data_path}/rainfall_onset_accum_downscaled_{country}.nc')
        summarize('downscaled (accum)', onset_downscaled_accum)
        plot_onset_map(onset_downscaled_accum, bbox, year,
                       f'Downscaled start of growing season — {country}\n{title_period}',
                       f'{plot_dir}/onset_downscaled_accum.png',
                       forecast_start=forecast_start, n_time=n_time, search_days=30)
    except Exception as e:
        print(f"downscaled: could not compute onset from {downscaled_path} ({e}), skipping")
else:
    print("downscaled: only available for Kenya, skipping")
