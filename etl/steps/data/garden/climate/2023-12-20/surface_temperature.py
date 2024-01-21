"""Load a meadow dataset and create a garden dataset."""

import owid.catalog.processing as pr

from etl.data_helpers import geo
from etl.helpers import PathFinder, create_dataset

# Get paths and naming conventions for current step.
paths = PathFinder(__file__)

# Regions for which aggregates will be created.
REGIONS = ["North America", "South America", "Europe", "Africa", "Asia", "Oceania", "World"]


def run(dest_dir: str) -> None:
    #
    # Load inputs.
    #
    # Load meadow dataset and read its main table.
    ds_meadow = paths.load_dataset("surface_temperature")
    tb = ds_meadow["surface_temperature"].reset_index()

    # Load regions dataset.
    ds_regions = paths.load_dataset("regions")

    # Load income groups dataset.
    ds_income_groups = paths.load_dataset("income_groups")
    #
    # Process data.
    #
    # Harmonize country names.
    tb = geo.harmonize_countries(
        df=tb, countries_file=paths.country_mapping_path, excluded_countries_file=paths.excluded_countries_path
    )

    # Add region aggregates.
    tb = geo.add_regions_to_table(
        tb,
        aggregations={"temperature_2m": "mean"},
        regions=REGIONS,
        ds_regions=ds_regions,
        ds_income_groups=ds_income_groups,
        min_num_values_per_year=1,
        year_col="time",
    )

    tb["year"] = tb["time"].astype(str).str[0:4]
    tb["month"] = tb["time"].astype(str).str[5:7]

    # Calculate mean temperature for each month in the entire period (to be used for anomaly calculations)
    monthly_climatology = tb.groupby(["country", "month"], as_index=False)["temperature_2m"].mean()
    monthly_climatology = monthly_climatology.rename(columns={"temperature_2m": "mean_temp"})

    # Ensure that the reference mean DataFrame has a name for the mean column, e.g., 'mean_temp'
    merged_df = pr.merge(tb, monthly_climatology, on=["country", "month"])

    # Calculate the anomalies (below and above the mean)
    merged_df["temperature_anomaly"] = merged_df["temperature_2m"] - merged_df["mean_temp"]
    merged_df = merged_df.drop(columns=["mean_temp"])

    merged_df["anomaly_below_0"] = merged_df["temperature_anomaly"].copy()
    merged_df.loc[merged_df["anomaly_below_0"] >= 0, "anomaly_below_0"] = None

    merged_df["anomaly_above_0"] = merged_df["temperature_anomaly"].copy()
    merged_df.loc[merged_df["anomaly_above_0"] <= 0, "anomaly_above_0"] = None
    merged_df = merged_df.drop(columns=["month", "year"])
    merged_df = merged_df.set_index(["country", "time"], verify_integrity=True)

    #
    # Save outputs.
    #
    # Create a new garden dataset with the same metadata as the meadow dataset.
    ds_garden = create_dataset(
        dest_dir, tables=[merged_df], check_variables_metadata=True, default_metadata=ds_meadow.metadata
    )
    ds_garden.save()
