"""Load a meadow dataset and create a garden dataset."""

from etl.data_helpers import geo
from etl.helpers import PathFinder, create_dataset

# Get paths and naming conventions for current step.
paths = PathFinder(__file__)


def run(dest_dir: str) -> None:
    #
    # Load inputs.
    #
    # Load meadow dataset.
    ds_meadow = paths.load_dataset("eurostat_cancer")

    # Read table from meadow dataset.
    tb = ds_meadow["eurostat_cancer"].reset_index()

    #
    # Process data.
    #
    tb = geo.harmonize_countries(df=tb, countries_file=paths.country_mapping_path)

    # Pivot the DataFrame
    tb = tb.pivot(index=["country", "year"], columns=["icd10", "sex"], values="pct_of_population")
    # Flatten the MultiIndex columns
    tb.columns = [f"{icd10}_{sex}" for icd10, sex in tb.columns]
    tb = tb.reset_index()

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
