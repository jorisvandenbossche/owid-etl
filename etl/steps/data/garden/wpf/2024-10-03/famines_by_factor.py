"""Load a meadow dataset and create a garden dataset."""

import pandas as pd
from owid.catalog import Table

from etl.helpers import PathFinder, create_dataset

# Get paths and naming conventions for current step.
paths = PathFinder(__file__)


def run(dest_dir: str) -> None:
    #
    # Load inputs.
    #
    # Load meadow dataset.
    ds_meadow = paths.load_dataset("famines")

    # Read table from meadow dataset.
    tb = ds_meadow["famines"].reset_index()
    origins = tb["famine_name"].metadata.origins

    #
    # Process data.
    #
    # Select relevant columns.
    tb = tb[
        [
            "date",
            "wpf_authoritative_mortality_estimate",
            "conflict",
            "government_policy_overall",
            "external_factors",
            "region",
        ]
    ]
    # Divide each row's 'wpf_authoritative_mortality_estimate' by the length of the corresponding 'Date' value to assume a uniform distribution of deaths over the period.
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

    # Create new columns for the sum of mortality estimates for where each cause was not (0) a factor.
    for factor in ["conflict", "government_policy_overall", "external_factors"]:
        new_column_name = f"sum_{factor}_mortality"
        tb[new_column_name] = tb.apply(
            lambda row: row["wpf_authoritative_mortality_estimate"] if row[factor] == 0 else 0, axis=1
        )

    # Group by year and region, and calculate the sum for each new column
    grouped_tb = tb.groupby(["year", "region"]).sum().reset_index()

    # Keep only the relevant columns
    relevant_columns = ["year", "region"] + [
        f"sum_{factor}_mortality" for factor in ["conflict", "government_policy_overall", "external_factors"]
    ]
    grouped_tb = grouped_tb[relevant_columns]

    grouped_tb = Table(grouped_tb, short_name=paths.short_name)
    grouped_tb = grouped_tb.rename({"region": "country"}, axis=1)
    grouped_tb = grouped_tb.format(["year", "country"])

    for col in ["sum_conflict_mortality", "sum_government_policy_overall_mortality", "sum_external_factors_mortality"]:
        grouped_tb[col].metadata.origins = origins

    #
    # Save outputs.
    #
    # Create a new garden dataset with the same metadata as the meadow dataset.
    ds_garden = create_dataset(
        dest_dir, tables=[grouped_tb], check_variables_metadata=True, default_metadata=ds_meadow.metadata
    )

    # Save changes in the new garden dataset.
    ds_garden.save()
