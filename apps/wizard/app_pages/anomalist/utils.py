"""Utils for chart revision tool."""
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st
from structlog import get_logger

from apps.wizard.utils.db import WizardDB
from apps.wizard.utils.io import get_steps_df

# Logger
log = get_logger()


# TODO: Consider refactoring the following function, which does too many things.
@st.cache_data(show_spinner=False)
@st.spinner("Retrieving datasets...")
def get_datasets_and_mapping_inputs() -> Tuple[pd.DataFrame, List[Dict[str, Dict[str, Any]]], Dict[int, int]]:
    # NOTE: The following ignores DB datasets that are archived (which is a bit unexpected).
    # I had to manually un-archive the testing datasets from the database manually to make things work.
    # This could be fixed, but maybe it's not necessary (since we won't archive an old version of a dataset until the
    # new has been analyzed).
    steps_df_grapher, grapher_changes = get_steps_df(archived=True)

    # List new dataset ids based on changes in files.
    datasets_new_ids = [ds["new"]["id"] for ds in grapher_changes]

    # Replace NaN with empty string in etl paths (otherwise dataset won't be shown if 'show step names' is chosen)
    steps_df_grapher["step"] = steps_df_grapher["step"].fillna("")

    # Add a convenient column for "[dataset id] Dataset Name"
    steps_df_grapher["id_name"] = [f"[{ds['id']}] {ds['name']}" for ds in steps_df_grapher.to_dict(orient="records")]

    # Load mapping created by indicator upgrader (if any).
    mapping = WizardDB.get_variable_mapping_raw()
    if len(mapping) > 0:
        # Set of ids of new datasets that appear in the mapping generated by indicator upgrader.
        datasets_new_mapped = set(mapping["dataset_id_new"])
        # Set of ids of datasets that have appear as new datasets in the grapher_changes.
        datasets_new_expected = set(datasets_new_ids)
        # Sanity check.
        if not (datasets_new_mapped <= datasets_new_expected):
            log.error(
                f"Indicator upgrader mapped indicators to new datasets ({datasets_new_mapped}) that are not among the datasets detected as new in the code ({datasets_new_expected}). Look into this."
            )
        # Create a mapping dictionary.
        variable_mapping = mapping.set_index("id_old")["id_new"].to_dict()
        # Sanity check.
        # TODO: Remove this check once we're sure that this works properly (to save time).
        assert variable_mapping == WizardDB.get_variable_mapping(), "Unexpected mapping issues."
    else:
        # NOTE: Here we could also infer the mapping of the new datasets (assuming no names have changed).
        #  This could be useful if a user wants to compare two arbitrary versions of existing grapher datasets.
        variable_mapping = dict()

    # List all grapher datasets.
    datasets_all = steps_df_grapher["id_name"].to_list()

    # List new datasets.
    datasets_new = [
        ds_id_name for ds_id_name in datasets_all if int(ds_id_name[1 : ds_id_name.index("]")]) in datasets_new_ids
    ]

    return datasets_all, datasets_new, variable_mapping  # type: ignore
