import pandas as pd
from TAHMO import apiWrapper
from sheerwater.utils import tahmo_secret


def tahmo_deployment():
    """Gets the TAHMO stations and some metadata. 

    This returns the data almost exactly as the TAHMO API returns it. We unpack the nested dicts,
    with keys separated by underscores.
    """
    api = apiWrapper()
    username, password = tahmo_secret()
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


if __name__ == "__main__":
    stations = tahmo_deployment()
    print(stations.head())
