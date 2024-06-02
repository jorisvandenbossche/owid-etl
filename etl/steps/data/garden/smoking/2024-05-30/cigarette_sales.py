"""Load a meadow dataset and create a garden dataset."""

import numpy as np
import pandas as pd

from etl.data_helpers import geo
from etl.helpers import PathFinder, create_dataset

# Get paths and naming conventions for current step.
paths = PathFinder(__file__)


def standardise_years(df):
    new_df = []
    for __, row in df.iterrows():
        year = row["year"]
        year_int = -1
        dict_from_row = row.to_dict()
        try:
            year_int = int(year)
            dict_from_row["year"] = year_int
            new_df.append(dict_from_row)
            continue
        except ValueError:
            if "." in year:
                year_int = int(year.split(".")[0])
            elif "/" in year:
                year_int = int(year.split("/")[0])
            if year_int > 0:
                dict_from_row["year"] = year_int
                new_df.append(dict_from_row)
                continue
            elif "-" in year:  # timeframe given in excel
                timeframe = year.split("-")
                start_year = int(timeframe[0])
                end_year = int(timeframe[1])
                if end_year < 100:
                    if end_year > (start_year % 100):
                        end_year = int(round(start_year, -2) + end_year)
                    elif end_year < (start_year % 100):
                        end_year = int(round(start_year, -2) + end_year + 100)
                elif end_year > 10000:
                    end_year = int(np.floor(end_year / 10))
                for year_in_timeframe in range(start_year, end_year + 1):
                    dict_from_row = row.to_dict()
                    dict_from_row["year"] = year_in_timeframe
                    new_df.append(dict_from_row)
    return pd.DataFrame(new_df)


def run(dest_dir: str) -> None:
    # Load inputs.
    #
    # Load meadow dataset.
    ds_meadow = paths.load_dataset("cigarette_sales")

    # Read table from meadow dataset.
    tb = ds_meadow["cigarette_sales"].reset_index()

    #
    # Process data.

    # Fix years column (change dtype to integer and expand timeframes)
    df_years_fixed = standardise_years(tb)
    # replace table with dataframe with fixed years, concat with empty df to keep metadata
    tb = pd.concat([tb[0:0], df_years_fixed])

    # remove duplicate data (from hidden rows in excel sheet)
    tb = tb.drop_duplicates(subset=["country", "year"])

    # harmonize countries
    tb = geo.harmonize_countries(
        df=tb, countries_file=paths.country_mapping_path, excluded_countries_file=paths.excluded_countries_path
    )
    tb = tb.format(["country", "year"])

    #
    # Save outputs.
    #
    # Create a new garden dataset with the same metadata as the meadow dataset.
    ds_garden = create_dataset(
        dest_dir, tables=[tb], check_variables_metadata=True, default_metadata=ds_meadow.metadata
    )

    # Save changes in the new garden dataset.
    ds_garden.save()
