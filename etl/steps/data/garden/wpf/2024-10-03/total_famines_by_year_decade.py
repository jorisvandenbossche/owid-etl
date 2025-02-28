"""Load a meadow dataset and create a garden dataset."""


import numpy as np
import owid.catalog.processing as pr
import pandas as pd
from owid.catalog import Table

from etl.data_helpers import geo
from etl.helpers import PathFinder, create_dataset

# Get paths and naming conventions for current step.
paths = PathFinder(__file__)

REGIONS = ["North America", "South America", "Europe", "Africa", "Asia", "Oceania"]


def run(dest_dir: str) -> None:
    #
    # Load inputs.
    #
    # Load meadow dataset.
    ds_meadow = paths.load_dataset("famines")
    ds_population = paths.load_dataset("population")

    # Read table from meadow dataset.
    tb = ds_meadow["famines"].reset_index()
    origins = tb["famine_name"].metadata.origins

    #
    # Process data.
    #
    # Divide each row's 'wpf_authoritative_mortality_estimate' by the length of the corresponding 'Date' value to assume a uniform distribution of deaths over the period
    tb["wpf_authoritative_mortality_estimate"] = tb.apply(
        lambda row: row["wpf_authoritative_mortality_estimate"] / len(row["date"].split(","))
        if pd.notna(row["date"])
        else row["wpf_authoritative_mortality_estimate"],
        axis=1,
    )

    # Unravel the 'date' column so that there is only one value per row. Years separated by commas are split into separate rows.
    tb = tb.assign(date=tb["date"].str.split(",")).explode("date").drop_duplicates().reset_index(drop=True)

    tb = tb.rename(columns={"date": "year"})
    tb["year"] = tb["year"].astype(int)
    tb["region"] = tb["region"].astype("category")

    # Grouping by relevant columns and summing the 'wpf_authoritative_mortality_estimate' for regional data
    deaths_counts = (
        tb.groupby(["year", "region"], observed=False)["wpf_authoritative_mortality_estimate"]
        .sum()
        .reset_index(name="famine_deaths")
    )

    # Grouping by 'year', 'region', 'conflict', 'government_policy_overall', 'external_factors' and counting the occurrences
    famine_counts = tb.groupby(["year", "region"], observed=False).size().reset_index(name="famine_count")
    # Creating a 'World' row by summing counts across unique regions for each group
    famine_counts_world_only = tb.groupby(["year"]).size().reset_index(name="famine_count")
    famine_counts_world_only["region"] = "World"
    # Concatenating the world row data with the regional data
    famine_counts_combined = pr.concat([famine_counts, famine_counts_world_only], ignore_index=True)

    # Grouping by relevant columns and summing the 'wpf_authoritative_mortality_estimate' for regional data
    deaths_counts = (
        tb.groupby(["year", "region"], observed=False)["wpf_authoritative_mortality_estimate"]
        .sum()
        .reset_index(name="famine_deaths")
    )

    # Creating a 'World' row by summing mortality estimates across all regions for each group
    deaths_counts_world_only = (
        tb.groupby(["year"])["wpf_authoritative_mortality_estimate"].sum().reset_index(name="famine_deaths")
    )
    deaths_counts_world_only["region"] = "World"

    # Concatenating the world row data with the regional data
    deaths_counts_combined = pr.concat([deaths_counts, deaths_counts_world_only], ignore_index=True)

    tb = pr.merge(
        famine_counts_combined,
        deaths_counts_combined,
        on=["year", "region"],
    )

    # Create a DataFrame with all years from 1870 to 2023
    all_years = pd.DataFrame({"year": range(1870, 2024)})

    # Get all unique regions from the original data
    all_regions = tb["region"].unique()

    # Create a DataFrame with all combinations of years and regions - to ensure that where there are no data points for a year and region, the value is set to zero
    all_years_regions = pd.MultiIndex.from_product([all_years["year"], all_regions], names=["year", "region"]).to_frame(
        index=False
    )
    all_years_regions = Table(all_years_regions)

    # Merge this DataFrame with the existing data to ensure all years are present
    tb = pr.merge(tb, all_years_regions, on=["year", "region"], how="right")

    # Fill NaN values in the 'famine_count' and 'famine_deaths' columns with zeros
    tb["famine_count"] = tb["famine_count"].fillna(0)
    tb["famine_deaths"] = tb["famine_deaths"].fillna(0)

    # Calculate the decade
    tb["decade"] = (tb["year"] // 10) * 10

    # Group the data by region and decade, then calculate the decadal sum
    for column in ["famine_count", "famine_deaths"]:
        tb["decadal_" + column] = tb.groupby(["region", "decade"], observed=False)[column].transform("sum")
        # Set NaN everywhere except the start of a decade
        tb["decadal_" + column] = tb["decadal_" + column].where(tb["year"] % 10 == 0, np.nan)
    tb = tb.drop(columns=["decade"])
    tb = tb.rename(columns={"region": "country"})

    # Calculate the rate of famine deaths per 100,000 people

    tb = geo.add_population_to_table(tb, ds_population)

    # The World total population doesn't include a value for each year but every region does so calculate it for each year based on the regional sums instead
    filtered_tb = tb[tb["country"].isin(REGIONS)]
    population_by_year = filtered_tb.groupby("year")["population"].sum()

    # Replace the "World" values in the population column with these sums
    tb.loc[tb["country"] == "World", "population"] = tb["year"].map(population_by_year)

    tb["famine_deaths_per_rate"] = tb["famine_deaths"] / (tb["population"] / 100000)
    tb["decadal_famine_deaths_rate"] = tb["decadal_famine_deaths"] / (tb["population"] / 100000)

    tb = tb.drop(columns=["population"])

    tb = tb.format(["year", "country"], short_name=paths.short_name)
    tb = Table(tb, short_name=paths.short_name)
    for col in [
        "famine_count",
        "famine_deaths",
        "decadal_famine_count",
        "decadal_famine_deaths",
        "famine_deaths_per_rate",
        "decadal_famine_deaths_rate",
    ]:
        tb[col].metadata.origins = origins
    # for
    # Save outputs.
    #
    # Create a new garden dataset with the same metadata as the meadow dataset.
    ds_garden = create_dataset(
        dest_dir, tables=[tb], check_variables_metadata=True, default_metadata=ds_meadow.metadata
    )

    # Save changes in the new garden dataset.
    ds_garden.save()
