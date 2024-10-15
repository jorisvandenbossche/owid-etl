from typing import Dict, List, Literal, Optional

import numpy as np
import pandas as pd
import plotly.express as px
import structlog
from owid.datautils.dataframes import map_series

from etl import grapher_model as gm
from etl.data_helpers.misc import bard

log = structlog.get_logger()

# Name of index columns for dataframe.
INDEX_COLUMNS = ["entity_name", "year"]

# Define anomaly types.
ANOMALY_TYPE = Literal["upgrade_change", "time_change", "upgrade_missing"]


def estimate_bard_epsilon(series: pd.Series) -> float:
    # Make all values positive, and ignore zeros.
    positive_values = abs(series.dropna())
    # Ignore zeros, since they can lead to epsilon being zero, hence allowing division by zero in BARD.
    positive_values = positive_values.loc[positive_values > 0]
    # Estimate epsilon as the absolute range of values divided by 10.
    # eps = (positive_values.max() - positive_values.min()) / 10
    # Instead of just taking maximum and minimum, take 95th percentile and 5th percentile.
    eps = (positive_values.quantile(0.95) - positive_values.quantile(0.05)) / 10

    return eps


def get_long_format_score_df(df_score: pd.DataFrame) -> pd.DataFrame:
    # Create a reduced score dataframe.
    df_score_long = df_score.melt(
        id_vars=["entity_name", "year"], var_name="variable_id", value_name="anomaly_score"
    ).fillna(0)
    # For now, keep only the latest year affected for each country-indicator.
    df_score_long = (
        df_score_long.sort_values("anomaly_score", ascending=False)
        .drop_duplicates(subset=["variable_id", "entity_name"], keep="first")
        .reset_index(drop=True)
    )

    return df_score_long


class AnomalyDetector:
    anomaly_type: str

    # Visually inspect the most significant anomalies on a certain scores dataframe.
    def inspect_anomalies(
        self,
        df: pd.DataFrame,
        variable_mapping: Dict[int, int],
        metadata: Dict[int, gm.Variable],
        anomalies: Optional[pd.DataFrame] = None,
        n_anomalies: int = 10,
    ) -> None:
        # Select the most significant anomalies.
        anomalies = anomalies.sort_values("anomaly_score", ascending=False).head(n_anomalies)  # type: ignore
        # Reverse variable mapping.
        variable_id_new_to_old = {v: k for k, v in variable_mapping.items()}
        anomalies["variable_id_old"] = map_series(  # type: ignore
            anomalies["variable_id"],  # type: ignore
            variable_id_new_to_old,
            warn_on_missing_mappings=False,  # type: ignore
        )
        for _, row in anomalies.iterrows():  # type: ignore
            variable_id = row["variable_id"]
            variable_name = metadata[variable_id].shortName  # type: ignore
            country = row["country"]
            score_name = row["anomaly_type"]
            title = f'{country} ({row["year"]} - {score_name} {row["anomaly_score"]:.0%}) {variable_name}'
            new = df[df["entity_name"] == row["entity_name"]][["entity_name", "year", variable_id]]
            new = new.rename(columns={row["variable_id"]: variable_name}, errors="raise")
            if score_name == "upgrade_change":
                variable_id_old = row["variable_id_old"]
                old = df[df["entity_name"] == row["entity_name"]][["entity_name", "year", variable_id_old]]
                old = old.rename(columns={row["variable_id_old"]: variable_name}, errors="raise")
                compare = pd.concat(
                    [old.assign(**{"source": "old"}), new.assign(**{"source": "new"})], ignore_index=True
                )
                px.line(
                    compare,
                    x="year",
                    y=variable_name,
                    color="source",
                    title=title,
                    markers=True,
                    color_discrete_map={"old": "rgba(256,0,0,0.5)", "new": "rgba(0,256,0,0.5)"},
                ).show()
            else:
                px.line(
                    new,
                    x="year",
                    y=variable_name,
                    title=title,
                    markers=True,
                    color_discrete_map={"new": "rgba(0,256,0,0.5)"},
                ).show()


class AnomalyUpgradeMissing(AnomalyDetector):
    """New data misses entity-years that used to exist in old version."""

    anomaly_type = "upgrade_missing"

    def get_score_df(self, df: pd.DataFrame, variable_ids: List[int], variable_mapping: Dict[int, int]) -> pd.DataFrame:
        # Create a dataframe of zeros.
        df_lost = pd.DataFrame(np.zeros_like(df), columns=df.columns)[INDEX_COLUMNS + variable_ids]
        df_lost[INDEX_COLUMNS] = df[INDEX_COLUMNS].copy()

        # Make 1 all cells that used to have data in the old version, but are missing in the new version.
        for variable_id_old, variable_id_new in variable_mapping.items():
            affected_rows = df[(df[variable_id_old].notnull()) & (df[variable_id_new].isnull())].index
            df_lost.loc[affected_rows, variable_id_new] = 1

        return df_lost


class AnomalyUpgradeChange(AnomalyDetector):
    """New dataframe has changed abruptly with respect to the old version."""

    anomaly_type = "upgrade_change"

    def get_score_df(self, df: pd.DataFrame, variable_ids: List[int], variable_mapping: Dict[int, int]) -> pd.DataFrame:
        # Create a dataframe of zeros.
        df_version_change = pd.DataFrame(np.zeros_like(df), columns=df.columns)[INDEX_COLUMNS + variable_ids]
        df_version_change[INDEX_COLUMNS] = df[INDEX_COLUMNS].copy()

        for variable_id_old, variable_id_new in variable_mapping.items():
            # Calculate the BARD epsilon for each variable.
            eps = estimate_bard_epsilon(series=df[variable_id_new])
            # Calculate the BARD for each variable.
            variable_bard = bard(a=df[variable_id_old], b=df[variable_id_new], eps=eps)
            # Add bard to the dataframe.
            df_version_change[variable_id_new] = variable_bard

        return df_version_change


class AnomalyTimeChange(AnomalyDetector):
    """New dataframe has abrupt changes in time series."""

    anomaly_type = "time_change"

    def get_score_df(self, df: pd.DataFrame, variable_ids: List[int], variable_mapping: Dict[int, int]) -> pd.DataFrame:
        # Create a dataframe of zeros.
        df_time_change = pd.DataFrame(np.zeros_like(df), columns=df.columns)[INDEX_COLUMNS + variable_ids]
        df_time_change[INDEX_COLUMNS] = df[INDEX_COLUMNS].copy()

        # Sanity check.
        error = "The function that detects abrupt time changes assumes the data is sorted by entity_name and year. But this is not the case. Either ensure the data is sorted, or fix the function."
        assert (df.sort_values(by=INDEX_COLUMNS).index == df.index).all(), error
        for variable_id in variable_ids:
            series = df[variable_id].copy()
            # Calculate the BARD epsilon for this variable.
            eps = estimate_bard_epsilon(series=series)
            # Calculate the BARD for this variable.
            _bard = bard(series, series.shift(), eps).fillna(0)

            # Add bard to the dataframe.
            df_time_change[variable_id] = _bard
        # The previous procedure includes the calculation of the deviation between the last point of an entity and the first point of the next, which is meaningless, and can lead to a high BARD.
        # Therefore, make zero the first point of each entity_name for all columns.
        # df_time_change.loc[df_time_change["entity_name"].diff().fillna(1) > 0, self.variable_ids] = 0
        df_time_change.loc[df_time_change["entity_name"] != df_time_change["entity_name"].shift(), variable_ids] = 0

        return df_time_change
