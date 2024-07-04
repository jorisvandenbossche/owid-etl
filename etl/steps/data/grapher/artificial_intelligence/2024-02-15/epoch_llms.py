"""Load a garden dataset and create a grapher dataset."""

from etl.helpers import PathFinder, create_dataset, grapher_checks

# Get paths and naming conventions for current step.
paths = PathFinder(__file__)


def run(dest_dir: str) -> None:
    #
    # Load inputs.
    #
    # Load garden dataset.
    ds_garden = paths.load_dataset("epoch_llms")
    # Read table from garden dataset.
    tb = ds_garden["epoch_llms"]

    #
    # Process data.
    #
    tb.reset_index(inplace=True)
    tb.rename(columns={"architecture": "country"}, inplace=True)
    tb.set_index(["country", "year"], inplace=True)
    #
    # Save outputs.
    #
    # Create a new grapher dataset with the same metadata as the garden dataset.
    ds_grapher = create_dataset(dest_dir, tables=[tb], default_metadata=ds_garden.metadata)

    #
    # Checks.
    #
    grapher_checks(ds_grapher)

    # Save changes in the new grapher dataset.
    ds_grapher.save()
