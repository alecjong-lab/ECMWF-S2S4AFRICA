"""
Build a 0.4 degree precipitation model climatology (m-climate) from ECMWF
extended-range hindcast GRIBs (stream eefh), in the same layout as the 1.5 degree
files under m-climate/T_pr/ that gef.open_mclimate reads:

    dims  time (one per lead week), quantile (101), latitude, longitude
    tp    weekly rainfall total in mm, at quantiles 0..100 of the pooled sample

The sample pools every hindcast init given (ECMWF's own m-climate pools the
inits in a window around the forecast date), every hindcast year and every
member, so 5 inits x 20 years x 11 members = 1100 values per grid cell and week.
Weeks are counted from each hindcast's own init, the same way plot_s2s.py takes
its weekly totals from the forecast.

Expects, per init date, a control and a perturbed file of accumulated tp (m):
    ECMWF-IFS-ext_range_sfc_tp_cf_init_<YYYY-MM-DD>.grib
    ECMWF-IFS-ext_range_sfc_tp_pf_init_<YYYY-MM-DD>.grib

With --yearly-output it also writes, per init, the ensemble-mean weekly total of
every hindcast year (hindcast_weekly_<init>.nc: year, week, latitude, longitude),
which downscale_04deg_.py ranks the forecast against.

Usage:
    python build_mclimate_0p4.py --hindcast-dir <folder> --output m-climate/T_pr_0p4
    python build_mclimate_0p4.py --hindcast-dir <folder> --output m-climate/T_pr_0p4 \
        --yearly-output downscale_data/hindcast_04deg
    python build_mclimate_0p4.py --hindcast-dir <folder> --output <folder> \
        --inits 2025-10-03,2025-10-05,2025-10-07 --label 2025-10-05
"""

import argparse
import re
from pathlib import Path

import eccodes
import numpy as np
import pandas as pd
import xarray as xr

FILE_PATTERN = re.compile(r"ECMWF-IFS-ext_range_sfc_tp_(cf|pf)_init_(\d{4}-\d{2}-\d{2})\.grib$")
WEEK_HOURS = 168


def read_weekly_marks(path, n_weeks):
    """
    Accumulated tp (mm) at the weekly marks 0, 168, ... h of one hindcast GRIB,
    as a DataArray (year, number, step, latitude, longitude). Only the messages
    at those lead times are decoded, the other 6-hourly ones are skipped.
    """
    marks = [WEEK_HOURS * i for i in range(n_weeks + 1)]
    fields = {}
    lats = lons = None
    with open(path, "rb") as f:
        while True:
            gid = eccodes.codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                step = int(eccodes.codes_get(gid, "endStep"))
                if step not in marks:
                    continue
                if lats is None:
                    nj, ni = eccodes.codes_get(gid, "Nj"), eccodes.codes_get(gid, "Ni")
                    lats = np.round(eccodes.codes_get_array(gid, "distinctLatitudes"), 4)
                    lons = np.round(eccodes.codes_get_array(gid, "distinctLongitudes"), 4)
                    # distinctLatitudes comes back sorted; the field itself runs
                    # from latitudeOfFirstGridPoint, so order the coord to match
                    if eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees") > \
                            eccodes.codes_get(gid, "latitudeOfLastGridPointInDegrees"):
                        lats = np.sort(lats)[::-1]
                    else:
                        lats = np.sort(lats)
                key = (int(str(eccodes.codes_get(gid, "hdate"))[:4]), int(eccodes.codes_get(gid, "number")), step)
                fields[key] = eccodes.codes_get_values(gid).reshape(nj, ni).astype("float32")
            finally:
                eccodes.codes_release(gid)

    years = sorted({k[0] for k in fields})
    numbers = sorted({k[1] for k in fields})
    missing = [(y, n, s) for y in years for n in numbers for s in marks if (y, n, s) not in fields]
    if missing:
        raise RuntimeError(f"{path}: {len(missing)} fields missing, e.g. {missing[:3]} (year, member, step)")

    cube = np.stack([[[fields[(y, n, s)] for s in marks] for n in numbers] for y in years])
    return xr.DataArray(
        cube * 1000.0,  # m -> mm
        dims=("year", "number", "step", "latitude", "longitude"),
        coords={"year": years, "number": numbers, "step": marks, "latitude": lats, "longitude": lons},
    )


def weekly_totals(hindcast_dir, init, n_weeks):
    """Weekly totals (mm) of control + perturbed members for one hindcast init:
    (year, number, week, latitude, longitude)."""
    parts = []
    for kind in ("cf", "pf"):
        path = hindcast_dir / f"ECMWF-IFS-ext_range_sfc_tp_{kind}_init_{init}.grib"
        if not path.is_file():
            raise FileNotFoundError(path)
        print(f"reading {path.name}")
        parts.append(read_weekly_marks(path, n_weeks))
    accumulated = xr.concat(parts, dim="number")
    weekly = accumulated.diff("step").clip(min=0)
    return weekly.rename(step="week").assign_coords(week=np.arange(1, n_weeks + 1))


def quantile_mclimate(pooled, label):
    """
    The m-climate Dataset (time, quantile, latitude, longitude) of weekly totals
    pooled over (init, year, number, week, latitude, longitude); 'init' holds the
    init date strings. plot_s2s_04deg_.py calls this too, on the hindcast store.
    """
    sample = pooled.stack(sample=("init", "year", "number"))
    n_samples = sample.sizes["sample"]
    print(f"pooled {pooled.sizes['init']} inits x {pooled.sizes['year']} years x {pooled.sizes['number']} members "
          f"= {n_samples} samples per cell and week")

    quantiles = sample.quantile(np.linspace(0, 1, 101), dim="sample").astype("float32")
    quantiles = quantiles.assign_coords(quantile=np.arange(101)).transpose("week", "quantile", "latitude", "longitude")

    # 'time' = first day of each lead week counted from the label date, like m-climate/T_pr
    week_starts = pd.Timestamp(label) + pd.to_timedelta(7 * (quantiles.week.values - 1), unit="D")
    out = quantiles.rename(week="time").assign_coords(time=week_starts.values).to_dataset(name="tp")
    out.tp.attrs = {"long_name": "Total precipitation", "units": "mm"}
    out.attrs = {
        "description": "Weekly precipitation model climatology from ECMWF extended-range hindcasts",
        "hindcast_inits": ",".join(str(init) for init in pooled.init.values),
        "hindcast_years": f"{int(pooled.year.min())}-{int(pooled.year.max())}",
        "members": int(pooled.sizes["number"]),
        "samples": int(n_samples),
    }
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hindcast-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="folder the m-climate_<label>.nc file is written to")
    parser.add_argument("--inits", help="comma-separated hindcast init dates to pool; default: every date found in --hindcast-dir")
    parser.add_argument("--label", help="date in the output filename (what gef.open_mclimate matches the forecast date against); default: the middle init")
    parser.add_argument("--weeks", type=int, default=6)
    parser.add_argument("--yearly-output", type=Path, help="folder to also write per-init hindcast_weekly_<init>.nc files to (ensemble mean per hindcast year)")
    args = parser.parse_args()

    if args.inits:
        inits = sorted(args.inits.split(","))
    else:
        inits = sorted({m.group(2) for p in args.hindcast_dir.iterdir() if (m := FILE_PATTERN.search(p.name))})
    if not inits:
        raise SystemExit(f"No hindcast GRIBs found in {args.hindcast_dir}")
    label = args.label or inits[len(inits) // 2]

    per_init = [weekly_totals(args.hindcast_dir, init, args.weeks) for init in inits]

    if args.yearly_output:
        args.yearly_output.mkdir(parents=True, exist_ok=True)
        for init, weekly in zip(inits, per_init):
            yearly = weekly.mean("number").astype("float32").transpose("year", "week", "latitude", "longitude").to_dataset(name="tp")
            yearly.tp.attrs = {"long_name": "Ensemble-mean weekly total precipitation", "units": "mm"}
            yearly.attrs = {"hindcast_init": init, "members": int(weekly.sizes["number"])}
            yearly_path = args.yearly_output / f"hindcast_weekly_{init}.nc"
            yearly.to_netcdf(yearly_path, encoding={"tp": {"zlib": True, "complevel": 4}})
            print(f"wrote {yearly_path}: {dict(yearly.sizes)}")

    pooled = xr.concat(per_init, dim=pd.Index(inits, name="init"))
    out = quantile_mclimate(pooled, label)

    args.output.mkdir(parents=True, exist_ok=True)
    out_path = args.output / f"m-climate_{label}.nc"
    out.to_netcdf(out_path, encoding={"tp": {"zlib": True, "complevel": 4}})
    print(f"wrote {out_path}: {dict(out.sizes)}")


if __name__ == "__main__":
    main()
