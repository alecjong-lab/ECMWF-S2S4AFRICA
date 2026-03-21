"""Wrappers around TAHMO API functions.

Columns of the TAHMO raw data:
    quality   (float64)         TAHMO's QA/QC flag. 1 = good, 2 = questionable, 3 = probably bad,
                                4 = bad and removed from data.
    sensor    (string)          Sensor ID, e.g. "S000570". If a sensor is part of an ATMOS or
                                similar module, the sensor ID is shared across the whole module.
    station   (string)          The station ID. Same as the one passed into tahmo_raw().
    time      (datetime64[ns])  The timestamp of the reading.
    value     (float64)         The reading. To learn the units, see tahmo_variables().
    variable  (string)          The variable, e.g. "ap" for atmospheric pressure.

Variables (accessible thru tahmo_variables()):
    ap: Atmospheric pressure (kPa)
    dw: Depth of water (mm)
    ec: Electrical conductivity of precipitation (mS/cm)
    ew: Electrical conductivity of water (mS/cm)
    ld: Lightning distance (km)
    le: Lightning events (-)
    ra: Shortwave radiation (W/m2)
    sm: Soil moisture content (m3/m3)
    st: Soil temperature (degrees Celsius)
    te: Surface air temperature (degrees Celsius)
    vp: Vapor pressure (kPa)
    wg: Wind gusts (m/s)
    ws: Wind speed (m/s)
    ht: Temperature of humidity sensor (degrees Celsius)
    tx: X-axis level (degrees)
    ty: Y-axis level (degrees)
    lb: Logger battery percentage (-)
    lp: Logger reference pressure (kPa)
    lt: Logger temperature (degrees Celsius)
    cp: Cumulative precipitation (mm)
    wl: Water level (m)
    wv: Water velocity (m/s)
    pr: Precipitation (mm)
    rh: Relative humidity (-)
    wd: Wind direction (degrees)
    se: Soil electrical conductivity (mS/cm)
    tw: Water temperature (degrees Celsius)
    wq: Water discharge (m3/s)
    mp: Matric potential (kPa)
    lv: Logger battery voltage (mV)
"""
import re
import warnings
import os
import dask.dataframe as dd
import pandas as pd

from TAHMO import apiWrapper

VAR_MAP_TAHMO = {
    "ap": "pressure",
    "cp": "cumulative_precipitation",
    "dw": "water_depth",
    "ec": "electrical_conductivity",
    "ew": "water_electrical_conductivity",
    "ht": "humidity_sensor_temperature",
    "lb": "battery_percent",
    "lv": "battery_voltage",
    "ld": "lightning_distance",
    "le": "lightning_events",
    "lp": "logger_pressure",
    "lt": "logger_temperature",
    "mp": "matric_potential",
    "pr": "precipitation",
    "ra": "solar_radiation",
    "rh": "humidity",
    "se": "soil_conductivity",
    "sm": "soil_moisture",
    "st": "soil_temperature",
    "te": "temperature",
    "tw": "water_temperature",
    "tx": "tilt_x",
    "ty": "tilt_y",
    "vp": "vapor_pressure",
    "wd": "wind_direction",
    "wg": "wind_gusts",
    "wl": "water_level",
    "wq": "water_discharge",
    "ws": "wind_speed",
    "wv": "water_velocity",
}


def tahmo_deployment():
    """Gets the TAHMO stations and some metadata. 

    This returns the data almost exactly as the TAHMO API returns it. We unpack the nested dicts,
    with keys separated by underscores.
    """
    api = apiWrapper()
    username = os.environ["TAHMO_API_USERNAME"]
    password = os.environ["TAHMO_API_PASSWORD"]
    api.setCredentials(username, password)
    stations = api.getStations()

    # Convert to array for records
    records = []
    for key in stations:
        records.append(stations[key])

    # Flatten the dict using underscores
    df = pd.json_normalize(records, sep="_")
    df = df.drop("sensorinstallations", axis=1)
    df = df.drop("dataloggerinstallations", axis=1)
    return df


def tahmo_raw(start_time, end_time, station_id, dataset="controlled"):
    """Get a time series of data from a TAHMO station.

    See the module docstring for info on the shape of the returned data.

    To do:
        * Call each year separately and then join them. This is already done internally in
            apiWrapper, but doing it here would let us pull some years from cache while
            redownloading others.

    Args:
        start_time (str or None): Used by timeseries caching.
        end_time (str or None): Used by timeseries caching.
        station_id (str): The station ID to get data from, e.g. "TA00639".
        dataset (str, optional): Defaults to "controlled", which is after some initial TAHMO
            filtering. "raw" gets data from before this filtering.

    Returns:
        Dask DataFrame
    """
    if isinstance(station_id, int):
        station_id = f"TA{station_id:05d}"

    if not ((isinstance(station_id, str) and re.match("^T[ADH]\\d{5}$", station_id))):
        warnings.warn(
            "station_id should be of the form T[A, D, or H][5-digit number]")

    api = apiWrapper()
    username = os.environ["TAHMO_API_USERNAME"]
    password = os.environ["TAHMO_API_PASSWORD"]
    api.setCredentials(username, password)

    # These dates should be kinda close to the real limits of the data, because we send a request to
    # the TAHMO API for each year. So, if we do 1900 to 2100, we'll do ~200 unnecessary requests.
    if start_time is None:
        start_time = "2005-01-01"
    if end_time is None:
        end_time = "2030-01-01"

    try:
        measurements = api.getRawData(
            station=station_id,
            startDate=str(start_time),
            endDate=str(end_time),
            dataset=dataset,
        )
    except Exception as e:
        # If a station doesn't exist, the API raises Exception with a message including the phase
        # "duplicate entries".  Return None, so we cache that nothing was found.
        if "duplicate entries" in str(e):
            warnings.warn(f"Station {station_id} does not exist.")
            return None
        else:
            raise e

    if (measurements is None) or (len(measurements) == 0):
        warnings.warn(f"No data from {station_id}.")
        return None

    measurements["time"] = pd.to_datetime(measurements["time"], format="mixed")
    return dd.from_pandas(measurements)


def combine_precip_sensors_by_individual_quality(wide):
    """Grab the two best precipitation readings for each timestamp.

    For each timestamp, keep the two best precipitation readings. "Best" means low (and non-NaN)
    quality flag. Also, record the sensor ID's of the precipitation sensors, so that e.g. we can see
    whether it was a drop counter or a tipping bucket.

    This returns a DataFrame with six columns:
        precipitation_1_tahmo:            Precipitation according to the best precip sensor.
        precipitation_1_quality_tahmo:    Quality flag of best precip sensor.
        precipitation_1_sensor_id_tahmo:  Sensor ID of best precip sensor.
        precipitation_2_tahmo:            Precipitation according to the second-best precip sensor.
        precipitation_2_quality_tahmo:    Quality flag of second-best precip sensor.
        precipitation_2_sensor_id_tahmo:  Sensor ID of second-best precip sensor.
    """
    # Pre-allocate result DataFrame
    result_cols = [
        "precipitation_1_tahmo",
        "precipitation_1_quality_tahmo",
        "precipitation_1_sensor_id_tahmo",
        "precipitation_2_tahmo",
        "precipitation_2_quality_tahmo",
        "precipitation_2_sensor_id_tahmo",
    ]

    # Get precipitation columns
    precip_mask = wide.columns.get_level_values("variable") == "precipitation"
    quality_mask = wide.columns.get_level_values(
        "value_or_quality") == "quality"
    quality_cols = wide.columns[precip_mask & quality_mask]

    if len(quality_cols) == 0:
        return pd.DataFrame(index=wide.index, columns=result_cols)

    # Get quality data and use pandas rank (handles pd.NA correctly)
    quality_df = wide[quality_cols]

    # Rank qualities (method='first' ensures unique ranks)
    ranks = quality_df.rank(axis=1, method="first")

    # Initialize result dictionary
    result_data = {col: pd.Series(
        index=wide.index, dtype="object") for col in result_cols}

    # Process each rank
    for rank in [1, 2]:
        # Find which column has this rank for each row
        for i, col in enumerate(quality_cols):
            mask = ranks.iloc[:, i] == rank

            if not mask.any():
                continue

            # Get corresponding value and sensor columns
            value_col = (col[0], "value", col[2])
            sensor_col = (col[0], "sensor", col[2])

            # Assign values where this column has the current rank
            result_data[f"precipitation_{rank}_tahmo"][mask] = wide.loc[mask, value_col]
            result_data[f"precipitation_{rank}_quality_tahmo"][mask] = wide.loc[mask, col]
            result_data[f"precipitation_{rank}_sensor_id_tahmo"][mask] = wide.loc[mask, sensor_col]

    return pd.DataFrame(result_data, index=wide.index)


def combine_sensors_by_individual_quality(wide):
    """For each sensor other than precipitation, grab the best reading for each timestamp.

    For each sensor reading type other than precipitation, we keep only the best reading from each
    timestamp. "Best" means: if only one is not NaN, use it. Otherwise, use the one with the lowest
    quality flag. If there's a tie, use the one with the lower sensor ID.

    For each sensor type (e.g. humidity), the returned DataFrame has two columns:
        {sensor type}_tahmo:              Sensor reading, other than precipitation.
        {sensor type}_quality_tahmo:      Quality flag. 1 is good, 4 is bad.
    """
    variables = wide.columns.get_level_values("variable").unique()
    # We process precipitation data separately, in combine_precip_sensors_by_individual_quality(),
    # so we can keep the top two sensors instead of the top one.
    variables_no_precip = [v for v in variables if v != "precipitation"]

    # This will get converted to a dataframe later, but it's faster to write a dict first.
    result_data = {}

    for var in variables_no_precip:
        # Get columns for this variable
        var_mask = wide.columns.get_level_values("variable") == var
        quality_mask = wide.columns.get_level_values(
            "value_or_quality") == "quality"
        value_mask = wide.columns.get_level_values(
            "value_or_quality") == "value"

        quality_cols = wide.columns[var_mask & quality_mask]
        value_cols = wide.columns[var_mask & value_mask]

        if len(quality_cols) == 0:
            continue

        quality_df = wide[quality_cols]
        value_df = wide[value_cols]

        # Find best (minimum) quality per row
        min_quality = quality_df.min(axis=1)
        result_data[f"{var}_quality_tahmo"] = min_quality

        # Create a mask for [best quality and not null].  Initialize it empty, then fill it in a loop.
        best_value = pd.Series(index=wide.index, dtype="object")

        # Iterate backwards through columns, so that if there's a tie for best quality, the first
        # one wins.
        for i in range(len(quality_cols))[::-1]:
            mask = (quality_df.iloc[:, i] ==
                    min_quality) & value_df.iloc[:, i].notna()
            best_value[mask] = value_df.iloc[:, i][mask]

        result_data[f"{var}_tahmo"] = best_value

    result_data = pd.DataFrame(result_data, index=wide.index)
    result_data = result_data.replace({None: pd.NA})

    return result_data


def tahmo_wide(start_time, end_time, station_id, dataset="controlled"):
    """Convert TAHMO data to a wide table, where each variable is a column.

    Goal:
        * For precip, grab the two sensors with the best quality values.  (If they're tied, choose
            earlier ones.)  Make columns to store their sensor ID's.
        * For each other variable, grab the one with the best quality value, and forget which sensor
            it came from.
        * Return smallish data types, to save memory. We use Float32 for sensor readings, Int8 for
            quality, and PyArrow strings.

    Args:
        start_time (str or None): Used by timeseries caching.
        end_time (str or None): Used by timeseries caching.
        station_id (str): The station ID to get data from, e.g. "TA00639".
        dataset (str, optional): Defaults to "controlled", which is after some initial TAHMO
            filtering. "raw" gets data from before this filtering.

    Returns:
        Dask DataFrame
    """
    raw = tahmo_raw(
        start_time,
        end_time,
        station_id,
        dataset=dataset,
    )

    if raw is None:
        return None

    raw = raw.compute()

    if len(raw) == 0:
        return None

    grouped = raw.groupby(["time", "variable", "sensor"]).first().reset_index()

    duplicate_fraction = (len(raw) - len(grouped)) / len(raw)
    if duplicate_fraction > 0.001:
        warnings.warn(
            f"{duplicate_fraction * 100}% of rows have duplicate time + sensor + variable")

    result = grouped.pivot(index="time", columns=["variable", "sensor"], values=[
                           "value", "quality", "sensor"])

    result.columns = result.columns.set_levels(
        [VAR_MAP_TAHMO.get(var, var) for var in result.columns.levels[1]], level=1
    )

    # Reorder column MultiIndex to [variable, value or quality, sensor ID]
    result = result.swaplevel(0, 1, axis=1)
    result.columns.rename("value_or_quality", level=1, inplace=True)

    precip = combine_precip_sensors_by_individual_quality(result)
    other_sensors = combine_sensors_by_individual_quality(result)
    narrow = precip.join(other_sensors, how="outer")

    # Convert to the most appropriate datatypes.  The quality columns use Int64, because Pandas
    # doesn't actually check int range, so we manually convert those to Int8, shrinking the whole
    # dataframe by almost half.
    # narrow = narrow.convert_dtypes()
    for column_name in narrow.columns:
        new_type = None
        if "quality" in column_name:
            new_type = "Int8"
        elif (narrow.dtypes[column_name] == "string") or ("sensor_id" in column_name):
            new_type = "string[pyarrow]"
        elif narrow.dtypes[column_name] in ["Float64", "float32"]:
            new_type = "Float32"
        elif narrow.dtypes[column_name] in ["Int64", "int32"]:
            new_type = "Int32"
        elif narrow.dtypes[column_name] == "object":
            # This only happens when all of the datapoints are NaN / None. So, we should cast to
            # Float32 for consistency.
            new_type = "Float32"

        if new_type is not None:
            narrow[column_name] = narrow[column_name].astype(new_type)

    # Convert to UTC and drop time zone, if necessary. (In general, we want all of our timestamps to
    # be TZ-naive and in UTC.)
    if narrow.index.tz is not None:
        narrow.index = narrow.index.tz_convert(None)

    narrow = narrow.reset_index().rename(columns={"index": "time"})
    return narrow


if __name__ == "__main__":
    stations = tahmo_deployment()
    data = tahmo_wide(start_time="2026-01-01", end_time="2026-01-02", station_id="TA00025")
    print(data.head())

