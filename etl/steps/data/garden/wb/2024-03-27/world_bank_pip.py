"""
Load a meadow dataset and create a garden dataset.

When running this step in an update, be sure to check all the outputs and logs to ensure the data is correct.

NOTE: To extract the log of the process (to review sanity checks, for example), run the following command in the terminal:
    nohup uv run etl run world_bank_pip > output.log 2>&1 &
"""

from typing import Tuple

import numpy as np
import owid.catalog.processing as pr
from owid.catalog import Table
from shared import add_metadata_vars, add_metadata_vars_percentiles
from structlog import get_logger
from tabulate import tabulate

from etl.data_helpers import geo
from etl.helpers import PathFinder, create_dataset

# Get paths and naming conventions for current step.
paths = PathFinder(__file__)

# Initialize logger.
log = get_logger()

# Define absolute poverty lines used depending on PPP version
# NOTE: Modify if poverty lines are updated from source
POVLINES_DICT = {
    2011: [100, 190, 320, 550, 1000, 2000, 3000, 4000],
    2017: [100, 215, 365, 685, 1000, 2000, 3000, 4000],
}

# Define regions in the dataset
REGIONS_LIST = [
    "East Asia and Pacific (PIP)",
    "Eastern and Southern Africa (PIP)",
    "Europe and Central Asia (PIP)",
    "Latin America and the Caribbean (PIP)",
    "Middle East and North Africa (PIP)",
    "Other high income countries (PIP)",
    "South Asia (PIP)",
    "Sub-Saharan Africa (PIP)",
    "Western and Central Africa (PIP)",
    "World",
    "World (excluding China)",
    "World (excluding India)",
]

# Define columns that are not poverty (mostly inequality)
NON_POVERTY_COLS = [
    "country",
    "year",
    "reporting_level",
    "welfare_type",
    "gini",
    "mld",
    "decile1_share",
    "decile2_share",
    "decile3_share",
    "decile4_share",
    "decile5_share",
    "decile6_share",
    "decile7_share",
    "decile8_share",
    "decile9_share",
    "decile10_share",
    "bottom50_share",
    "middle40_share",
    "headcount_40_median",
    "headcount_50_median",
    "headcount_60_median",
    "headcount_ratio_40_median",
    "headcount_ratio_50_median",
    "headcount_ratio_60_median",
    "income_gap_ratio_40_median",
    "income_gap_ratio_50_median",
    "income_gap_ratio_60_median",
    "poverty_gap_index_40_median",
    "poverty_gap_index_50_median",
    "poverty_gap_index_60_median",
    "avg_shortfall_40_median",
    "avg_shortfall_50_median",
    "avg_shortfall_60_median",
    "total_shortfall_40_median",
    "total_shortfall_50_median",
    "total_shortfall_60_median",
    "poverty_severity_40_median",
    "poverty_severity_50_median",
    "poverty_severity_60_median",
    "waits_40_median",
    "waits_50_median",
    "waits_60_median",
    "palma_ratio",
    "s80_s20_ratio",
    "p90_p10_ratio",
    "p90_p50_ratio",
    "p50_p10_ratio",
]

# Define countries expected to have both income and consumption data
COUNTRIES_WITH_INCOME_AND_CONSUMPTION = [
    "Albania",
    "Armenia",
    "Belarus",
    "Belize",
    "Bulgaria",
    "China",
    "China (rural)",
    "China (urban)",
    "Croatia",
    "Estonia",
    "Haiti",
    "Hungary",
    "Kazakhstan",
    "Kyrgyzstan",
    "Latvia",
    "Lithuania",
    "Montenegro",
    "Namibia",
    "Nepal",
    "Nicaragua",
    "North Macedonia",
    "Peru",
    "Philippines",
    "Poland",
    "Romania",
    "Russia",
    "Saint Lucia",
    "Serbia",
    "Seychelles",
    "Slovakia",
    "Slovenia",
    "Turkey",
    "Ukraine",
]

# Set table format when printing
TABLEFMT = "pretty"


def run(dest_dir: str) -> None:
    #
    # Load inputs.
    #
    # Load meadow dataset.
    ds_meadow = paths.load_dataset("world_bank_pip")

    # Read tables from meadow dataset.
    # Key indicators
    tb = ds_meadow["world_bank_pip"].reset_index()

    # Percentiles
    tb_percentiles = ds_meadow["world_bank_pip_percentiles"].reset_index()

    # Process data
    # Make table wide and change column names
    tb = process_data(tb)

    # Calculate inequality measures
    tb = calculate_inequality(tb)

    # Harmonize country names
    tb: Table = geo.harmonize_countries(df=tb, countries_file=paths.country_mapping_path)
    tb_percentiles: Table = geo.harmonize_countries(df=tb_percentiles, countries_file=paths.country_mapping_path)

    # Show regional data from 1990 onwards
    tb = regional_data_from_1990(tb, REGIONS_LIST)
    tb_percentiles = regional_data_from_1990(tb_percentiles, REGIONS_LIST)

    # Amend the entity to reflect if data refers to urban or rural only
    tb = identify_rural_urban(tb)

    # Separate out ppp and filled data from the main dataset
    tb_2011, tb_2017 = separate_ppp_data(tb)
    tb_percentiles_2011, tb_percentiles_2017 = separate_ppp_data(tb_percentiles)

    # Create stacked variables from headcount and headcount_ratio
    tb_2011, col_stacked_n_2011, col_stacked_pct_2011 = create_stacked_variables(
        tb_2011, POVLINES_DICT, ppp_version=2011
    )
    tb_2017, col_stacked_n_2017, col_stacked_pct_2017 = create_stacked_variables(
        tb_2017, POVLINES_DICT, ppp_version=2017
    )

    # Sanity checks. I don't run for percentile tables because that process was done in the extraction
    tb_2011 = sanity_checks(
        tb_2011, POVLINES_DICT, ppp_version=2011, col_stacked_n=col_stacked_n_2011, col_stacked_pct=col_stacked_pct_2011
    )
    tb_2017 = sanity_checks(
        tb_2017, POVLINES_DICT, ppp_version=2017, col_stacked_n=col_stacked_n_2017, col_stacked_pct=col_stacked_pct_2017
    )

    # Separate out consumption-only, income-only. Also, create a table with both income and consumption
    tb_inc_2011, tb_cons_2011, tb_inc_or_cons_2011_unsmoothed, tb_inc_or_cons_2011 = inc_or_cons_data(tb_2011)
    tb_inc_2017, tb_cons_2017, tb_inc_or_cons_2017_unsmoothed, tb_inc_or_cons_2017 = inc_or_cons_data(tb_2017)

    # Create regional headcount variable, by patching missing values with the difference between world and regional headcount
    tb_inc_or_cons_2017 = regional_headcount(tb_inc_or_cons_2017)

    # Create survey count dataset, by counting the number of surveys available for each country in the past decade
    tb_inc_or_cons_2017 = survey_count(tb_inc_or_cons_2017)

    # Add metadata by code
    tb_inc_2011 = add_metadata_vars(tb_garden=tb_inc_2011, ppp_version=2011, welfare_type="income")
    tb_cons_2011 = add_metadata_vars(tb_garden=tb_cons_2011, ppp_version=2011, welfare_type="consumption")
    tb_inc_or_cons_2011_unsmoothed = add_metadata_vars(
        tb_garden=tb_inc_or_cons_2011_unsmoothed,
        ppp_version=2011,
        welfare_type="income_consumption",
    )
    tb_inc_or_cons_2011_unsmoothed.m.short_name = "income_consumption_2011_unsmoothed"
    tb_inc_or_cons_2011 = add_metadata_vars(
        tb_garden=tb_inc_or_cons_2011,
        ppp_version=2011,
        welfare_type="income_consumption",
    )

    tb_inc_2017 = add_metadata_vars(tb_garden=tb_inc_2017, ppp_version=2017, welfare_type="income")
    tb_cons_2017 = add_metadata_vars(tb_garden=tb_cons_2017, ppp_version=2017, welfare_type="consumption")
    tb_inc_or_cons_2017_unsmoothed = add_metadata_vars(
        tb_garden=tb_inc_or_cons_2017_unsmoothed,
        ppp_version=2017,
        welfare_type="income_consumption",
    )
    tb_inc_or_cons_2017_unsmoothed.m.short_name = "income_consumption_2017_unsmoothed"
    tb_inc_or_cons_2017 = add_metadata_vars(
        tb_garden=tb_inc_or_cons_2017,
        ppp_version=2017,
        welfare_type="income_consumption",
    )

    tb_percentiles_2011 = add_metadata_vars_percentiles(
        tb_garden=tb_percentiles_2011,
        ppp_version=2011,
        welfare_type="income_consumption",
    )
    tb_percentiles_2017 = add_metadata_vars_percentiles(
        tb_garden=tb_percentiles_2017,
        ppp_version=2017,
        welfare_type="income_consumption",
    )

    # Set index and sort
    # Define index cols
    index_cols = ["country", "year"]
    index_cols_percentiles = ["country", "year", "reporting_level", "welfare_type", "percentile"]
    tb_inc_2011 = tb_inc_2011.format(keys=index_cols)
    tb_cons_2011 = tb_cons_2011.format(keys=index_cols)
    tb_inc_or_cons_2011_unsmoothed = tb_inc_or_cons_2011_unsmoothed.format(keys=index_cols)
    tb_inc_or_cons_2011 = tb_inc_or_cons_2011.format(keys=index_cols)

    tb_inc_2017 = tb_inc_2017.format(keys=index_cols)
    tb_cons_2017 = tb_cons_2017.format(keys=index_cols)
    tb_inc_or_cons_2017_unsmoothed = tb_inc_or_cons_2017_unsmoothed.format(keys=index_cols)
    tb_inc_or_cons_2017 = tb_inc_or_cons_2017.format(keys=index_cols)

    tb_percentiles_2011 = tb_percentiles_2011.format(keys=index_cols_percentiles)
    tb_percentiles_2017 = tb_percentiles_2017.format(keys=index_cols_percentiles)

    # Create spell tables to separate different survey spells in the explorers
    spell_tables_inc = create_survey_spells(tb=tb_inc_2017)
    spell_tables_cons = create_survey_spells(tb=tb_cons_2017)

    # For income and consumption we combine the tables to not lose information from tb_inc_or_cons_2017
    spell_tables_inc_or_cons = create_survey_spells_inc_cons(tb_inc=tb_inc_2017, tb_cons=tb_cons_2017)

    # Drop columns not needed
    tb_inc_2011 = drop_columns(tb_inc_2011)
    tb_cons_2011 = drop_columns(tb_cons_2011)
    tb_inc_or_cons_2011 = drop_columns(tb_inc_or_cons_2011)

    tb_inc_2017 = drop_columns(tb_inc_2017)
    tb_cons_2017 = drop_columns(tb_cons_2017)
    tb_inc_or_cons_2017 = drop_columns(tb_inc_or_cons_2017)

    # Merge tables for PPP comparison explorer
    tb_inc_2011_2017 = combine_tables_2011_2017(tb_2011=tb_inc_2011, tb_2017=tb_inc_2017, short_name="income_2011_2017")
    tb_cons_2011_2017 = combine_tables_2011_2017(
        tb_2011=tb_cons_2011, tb_2017=tb_cons_2017, short_name="consumption_2011_2017"
    )
    tb_inc_or_cons_2011_2017 = combine_tables_2011_2017(
        tb_2011=tb_inc_or_cons_2011, tb_2017=tb_inc_or_cons_2017, short_name="income_consumption_2011_2017"
    )

    # Define tables to upload
    # The ones we need in Grapher admin would be tb_inc_or_cons_2011, tb_inc_or_cons_2017
    tables = (
        [
            tb_inc_2011,
            tb_cons_2011,
            tb_inc_or_cons_2011_unsmoothed,
            tb_inc_or_cons_2011,
            tb_inc_2017,
            tb_cons_2017,
            tb_inc_or_cons_2017_unsmoothed,
            tb_inc_or_cons_2017,
            tb_inc_2011_2017,
            tb_cons_2011_2017,
            tb_inc_or_cons_2011_2017,
            tb_percentiles_2011,
            tb_percentiles_2017,
        ]
        + spell_tables_inc
        + spell_tables_cons
        + spell_tables_inc_or_cons
    )

    #
    # Save outputs.
    #
    # Create a new garden dataset with the same metadata as the meadow dataset.
    ds_garden = create_dataset(
        dest_dir,
        tables=tables,
        check_variables_metadata=True,
        default_metadata=ds_meadow.metadata,
    )

    # Save changes in the new garden dataset.
    ds_garden.save()


def process_data(tb: Table) -> Table:
    # rename columns
    tb = tb.rename(columns={"headcount": "headcount_ratio", "poverty_gap": "poverty_gap_index"})

    # Changing the decile(i) variables for decile(i)_share
    for i in range(1, 11):
        tb = tb.rename(columns={f"decile{i}": f"decile{i}_share"})

    # Calculate number in poverty
    tb["headcount"] = tb["headcount_ratio"] * tb["reporting_pop"]
    tb["headcount"] = tb["headcount"].round(0)

    # Calculate shortfall of incomes
    tb["total_shortfall"] = tb["poverty_gap_index"] * tb["poverty_line"] * tb["reporting_pop"]

    # Calculate average shortfall of incomes (averaged across population in poverty)
    tb["avg_shortfall"] = tb["total_shortfall"] / tb["headcount"]

    # Calculate income gap ratio (according to Ravallion's definition)
    tb["income_gap_ratio"] = (tb["total_shortfall"] / tb["headcount"]) / tb["poverty_line"]

    # Same for relative poverty
    for pct in [40, 50, 60]:
        tb[f"headcount_{pct}_median"] = tb[f"headcount_ratio_{pct}_median"] * tb["reporting_pop"]
        tb[f"headcount_{pct}_median"] = tb[f"headcount_{pct}_median"].round(0)
        tb[f"total_shortfall_{pct}_median"] = (
            tb[f"poverty_gap_index_{pct}_median"] * tb["median"] * pct / 100 * tb["reporting_pop"]
        )
        tb[f"avg_shortfall_{pct}_median"] = tb[f"total_shortfall_{pct}_median"] / tb[f"headcount_{pct}_median"]
        tb[f"income_gap_ratio_{pct}_median"] = (tb[f"total_shortfall_{pct}_median"] / tb[f"headcount_{pct}_median"]) / (
            tb["median"] * pct / 100
        )

    # Shares to percentages
    # executing the function over list of vars
    pct_indicators = [
        "headcount_ratio",
        "income_gap_ratio",
        "poverty_gap_index",
        "headcount_ratio_40_median",
        "headcount_ratio_50_median",
        "headcount_ratio_60_median",
        "income_gap_ratio_40_median",
        "income_gap_ratio_50_median",
        "income_gap_ratio_60_median",
        "poverty_gap_index_40_median",
        "poverty_gap_index_50_median",
        "poverty_gap_index_60_median",
    ]
    tb.loc[:, pct_indicators] = tb[pct_indicators] * 100

    # Create a new column for the poverty line in cents and string
    tb["poverty_line_cents"] = round(tb["poverty_line"] * 100).astype(int).astype(str)

    # Make the table wide, with poverty_line_cents as columns
    tb = tb.pivot(
        index=[
            "ppp_version",
            "country",
            "year",
            "reporting_level",
            "welfare_type",
            "survey_comparability",
            "comparable_spell",
            "reporting_pop",
            "mean",
            "median",
            "mld",
            "gini",
            "polarization",
            "decile1_share",
            "decile2_share",
            "decile3_share",
            "decile4_share",
            "decile5_share",
            "decile6_share",
            "decile7_share",
            "decile8_share",
            "decile9_share",
            "decile10_share",
            "decile1_thr",
            "decile2_thr",
            "decile3_thr",
            "decile4_thr",
            "decile5_thr",
            "decile6_thr",
            "decile7_thr",
            "decile8_thr",
            "decile9_thr",
            "is_interpolated",
            "distribution_type",
            "estimation_type",
            "headcount_40_median",
            "headcount_50_median",
            "headcount_60_median",
            "headcount_ratio_40_median",
            "headcount_ratio_50_median",
            "headcount_ratio_60_median",
            "income_gap_ratio_40_median",
            "income_gap_ratio_50_median",
            "income_gap_ratio_60_median",
            "poverty_gap_index_40_median",
            "poverty_gap_index_50_median",
            "poverty_gap_index_60_median",
            "avg_shortfall_40_median",
            "avg_shortfall_50_median",
            "avg_shortfall_60_median",
            "total_shortfall_40_median",
            "total_shortfall_50_median",
            "total_shortfall_60_median",
            "poverty_severity_40_median",
            "poverty_severity_50_median",
            "poverty_severity_60_median",
            "watts_40_median",
            "watts_50_median",
            "watts_60_median",
        ],
        columns="poverty_line_cents",
        values=[
            "headcount",
            "headcount_ratio",
            "income_gap_ratio",
            "poverty_gap_index",
            "avg_shortfall",
            "total_shortfall",
            "poverty_severity",
            "watts",
        ],
    )

    # Flatten column names
    tb.columns = ["_".join(col).strip() for col in tb.columns.values]

    # Reset index
    tb = tb.reset_index()

    return tb


def create_stacked_variables(tb: Table, povlines_dict: dict, ppp_version: int) -> Tuple[Table, list, list]:
    """
    Create stacked variables from the indicators to plot them as stacked area/bar charts
    """
    # Select poverty lines between 2011 and 2017 and sort in case they are not in order
    povlines = povlines_dict[ppp_version]
    povlines.sort()

    # Above variables

    col_above_n = []
    col_above_pct = []

    for p in povlines:
        varname_n = f"headcount_above_{p}"
        varname_pct = f"headcount_ratio_above_{p}"

        tb[varname_n] = tb["reporting_pop"] - tb[f"headcount_{p}"]
        tb[varname_pct] = tb[varname_n] / tb["reporting_pop"]

        col_above_n.append(varname_n)
        col_above_pct.append(varname_pct)

    tb.loc[:, col_above_pct] = tb[col_above_pct] * 100

    # Stacked variables

    col_stacked_n = []
    col_stacked_pct = []

    for i in range(len(povlines)):
        # if it's the first value only continue
        if i == 0:
            continue

        # If it's the last value calculate the people between this value and the previous
        # and also the people over this poverty line (and percentages)
        elif i == len(povlines) - 1:
            varname_n = f"headcount_between_{povlines[i-1]}_{povlines[i]}"
            varname_pct = f"headcount_ratio_between_{povlines[i-1]}_{povlines[i]}"
            tb[varname_n] = tb[f"headcount_{povlines[i]}"] - tb[f"headcount_{povlines[i-1]}"]
            tb[varname_pct] = tb[varname_n] / tb["reporting_pop"]
            col_stacked_n.append(varname_n)
            col_stacked_pct.append(varname_pct)
            varname_n = f"headcount_above_{povlines[i]}"
            varname_pct = f"headcount_ratio_above_{povlines[i]}"
            tb[varname_n] = tb["reporting_pop"] - tb[f"headcount_{povlines[i]}"]
            tb[varname_pct] = tb[varname_n] / tb["reporting_pop"]
            col_stacked_n.append(varname_n)
            col_stacked_pct.append(varname_pct)

        # If it's any value between the first and the last calculate the people between this value and the previous (and percentage)
        else:
            varname_n = f"headcount_between_{povlines[i-1]}_{povlines[i]}"
            varname_pct = f"headcount_ratio_between_{povlines[i-1]}_{povlines[i]}"
            tb[varname_n] = tb[f"headcount_{povlines[i]}"] - tb[f"headcount_{povlines[i-1]}"]
            tb[varname_pct] = tb[varname_n] / tb["reporting_pop"]
            col_stacked_n.append(varname_n)
            col_stacked_pct.append(varname_pct)

    tb.loc[:, col_stacked_pct] = tb[col_stacked_pct] * 100

    # Add variables below first poverty line to the stacked variables
    col_stacked_n.append(f"headcount_{povlines[0]}")
    col_stacked_pct.append(f"headcount_ratio_{povlines[0]}")

    # Calculate stacked variables which "jump" the original order

    tb[f"headcount_between_{povlines[1]}_{povlines[4]}"] = (
        tb[f"headcount_{povlines[4]}"] - tb[f"headcount_{povlines[1]}"]
    )
    tb[f"headcount_between_{povlines[4]}_{povlines[6]}"] = (
        tb[f"headcount_{povlines[6]}"] - tb[f"headcount_{povlines[4]}"]
    )

    tb[f"headcount_ratio_between_{povlines[1]}_{povlines[4]}"] = (
        tb[f"headcount_ratio_{povlines[4]}"] - tb[f"headcount_ratio_{povlines[1]}"]
    )
    tb[f"headcount_ratio_between_{povlines[4]}_{povlines[6]}"] = (
        tb[f"headcount_ratio_{povlines[6]}"] - tb[f"headcount_ratio_{povlines[4]}"]
    )

    return tb, col_stacked_n, col_stacked_pct


def calculate_inequality(tb: Table) -> Table:
    """
    Calculate inequality measures: decile averages and ratios
    """

    col_decile_share = []
    col_decile_avg = []
    col_decile_thr = []

    for i in range(1, 11):
        if i != 10:
            varname_thr = f"decile{i}_thr"
            col_decile_thr.append(varname_thr)

        varname_share = f"decile{i}_share"
        varname_avg = f"decile{i}_avg"
        tb[varname_avg] = tb[varname_share] * tb["mean"] / 0.1

        col_decile_share.append(varname_share)
        col_decile_avg.append(varname_avg)

    # Multiplies decile columns by 100
    tb.loc[:, col_decile_share] = tb[col_decile_share] * 100

    # Create bottom 50 and middle 40% shares
    tb["bottom50_share"] = (
        tb["decile1_share"] + tb["decile2_share"] + tb["decile3_share"] + tb["decile4_share"] + tb["decile5_share"]
    )
    tb["middle40_share"] = tb["decile6_share"] + tb["decile7_share"] + tb["decile8_share"] + tb["decile9_share"]

    # Palma ratio and other average/share ratios
    tb["palma_ratio"] = tb["decile10_share"] / (
        tb["decile1_share"] + tb["decile2_share"] + tb["decile3_share"] + tb["decile4_share"]
    )
    tb["s80_s20_ratio"] = (tb["decile9_share"] + tb["decile10_share"]) / (tb["decile1_share"] + tb["decile2_share"])
    tb["p90_p10_ratio"] = tb["decile9_thr"] / tb["decile1_thr"]
    tb["p90_p50_ratio"] = tb["decile9_thr"] / tb["decile5_thr"]
    tb["p50_p10_ratio"] = tb["decile5_thr"] / tb["decile1_thr"]

    # Replace infinite values with nulls
    tb = tb.replace([np.inf, -np.inf], np.nan)
    return tb


def identify_rural_urban(tb: Table) -> Table:
    """
    Amend the entity to reflect if data refers to urban or rural only
    """

    # Make country and reporting_level columns into strings
    tb["country"] = tb["country"].astype(str)
    tb["reporting_level"] = tb["reporting_level"].astype(str)
    ix = tb["reporting_level"].isin(["urban", "rural"])
    tb.loc[(ix), "country"] = tb.loc[(ix), "country"] + " (" + tb.loc[(ix), "reporting_level"] + ")"

    return tb


def sanity_checks(
    tb: Table, povlines_dict: dict, ppp_version: int, col_stacked_n: list, col_stacked_pct: list
) -> Table:
    """
    Sanity checks for the table
    """

    # Select poverty lines between 2011 and 2017 and sort in case they are not in order
    povlines = povlines_dict[ppp_version]
    povlines.sort()

    # Save the number of observations before the checks
    obs_before_checks = len(tb)

    # Create lists of variables to check
    col_headcount = []
    col_headcount_ratio = []
    col_povertygap = []
    col_tot_shortfall = []
    col_watts = []
    col_poverty_severity = []
    col_decile_share = []
    col_decile_thr = []

    for p in povlines:
        col_headcount.append(f"headcount_{p}")
        col_headcount_ratio.append(f"headcount_ratio_{p}")
        col_povertygap.append(f"poverty_gap_index_{p}")
        col_tot_shortfall.append(f"total_shortfall_{p}")
        col_watts.append(f"watts_{p}")
        col_poverty_severity.append(f"poverty_severity_{p}")

    for i in range(1, 11):
        col_decile_share.append(f"decile{i}_share")
        if i != 10:
            col_decile_thr.append(f"decile{i}_thr")

    ############################
    # Negative values
    mask = (
        tb[
            col_headcount
            + col_headcount_ratio
            + col_povertygap
            + col_tot_shortfall
            + col_watts
            + col_poverty_severity
            + col_decile_share
            + col_decile_thr
            + ["mean", "median", "mld", "gini", "polarization"]
        ]
        .lt(0)
        .any(axis=1)
    )
    tb_error = tb[mask].reset_index(drop=True).copy()

    if not tb_error.empty:
        log.fatal(
            f"""There are {len(tb_error)} observations with negative values! In
            {tabulate(tb_error[['country', 'year', 'reporting_level', 'welfare_type']], headers = 'keys', tablefmt = TABLEFMT)}"""
        )
        # NOTE: Check if we want to delete these observations
        # tb = tb[~mask].reset_index(drop=True)

    ############################
    # stacked values not adding up to 100%
    tb["sum_pct"] = tb[col_stacked_pct].sum(axis=1)
    mask = (tb["sum_pct"] >= 100.1) | (tb["sum_pct"] <= 99.9)
    tb_error = tb[mask].reset_index(drop=True).copy()

    if not tb_error.empty:
        log.warning(
            f"""{len(tb_error)} observations of stacked values are not adding up to 100% and will be deleted:
            {tabulate(tb_error[['country', 'year', 'reporting_level', 'welfare_type', 'sum_pct']], headers = 'keys', tablefmt = TABLEFMT, floatfmt=".1f")}"""
        )
        tb = tb[~mask].reset_index(drop=True).copy()

    ############################
    # missing poverty values (headcount, poverty gap, total shortfall)
    cols_to_check = (
        col_headcount + col_headcount_ratio + col_povertygap + col_tot_shortfall + col_stacked_n + col_stacked_pct
    )
    mask = (tb[cols_to_check].isna().any(axis=1)) & (
        ~tb["country"].isin(["World (excluding China)", "World (excluding India)"])
    )
    tb_error = tb[mask].reset_index(drop=True).copy()

    if not tb_error.empty:
        log.warning(
            f"""There are {len(tb_error)} observations with missing poverty values and will be deleted:
            {tabulate(tb_error[['country', 'year', 'reporting_level', 'welfare_type'] + col_headcount], headers = 'keys', tablefmt = TABLEFMT)}"""
        )
        tb = tb[~mask].reset_index(drop=True)

    ############################
    # Missing median
    mask = tb["median"].isna()
    tb_error = tb[mask].reset_index(drop=True).copy()

    if not tb_error.empty:
        log.info(f"""There are {len(tb_error)} observations with missing median. They will be not deleted.""")

    ############################
    # Missing mean
    mask = tb["mean"].isna()
    tb_error = tb[mask].reset_index(drop=True).copy()

    if not tb_error.empty:
        log.info(f"""There are {len(tb_error)} observations with missing mean. They will be not deleted.""")

    ############################
    # Missing gini
    mask = tb["gini"].isna()
    tb_error = tb[mask].reset_index(drop=True).copy()

    if not tb_error.empty:
        log.info(f"""There are {len(tb_error)} observations with missing gini. They will be not deleted.""")

    ############################
    # Missing decile shares
    mask = tb[col_decile_share].isna().any(axis=1)
    tb_error = tb[mask].reset_index(drop=True).copy()

    if not tb_error.empty:
        log.info(f"""There are {len(tb_error)} observations with missing decile shares. They will be not deleted.""")

    ############################
    # Missing decile thresholds
    mask = tb[col_decile_thr].isna().any(axis=1)
    tb_error = tb[mask].reset_index(drop=True).copy()

    if not tb_error.empty:
        log.info(
            f"""There are {len(tb_error)} observations with missing decile thresholds. They will be not deleted."""
        )

    ############################
    # headcount monotonicity check
    m_check_vars = []
    for i in range(len(col_headcount)):
        if i > 0:
            check_varname = f"m_check_{i}"
            tb[check_varname] = tb[f"{col_headcount[i]}"] >= tb[f"{col_headcount[i-1]}"]
            m_check_vars.append(check_varname)
    tb["check_total"] = tb[m_check_vars].all(axis=1)

    tb_error = tb[~tb["check_total"]].reset_index(drop=True)

    if not tb_error.empty:
        log.warning(
            f"""There are {len(tb_error)} observations with headcount not monotonically increasing and will be deleted:
            {tabulate(tb_error[['country', 'year', 'reporting_level', 'welfare_type'] + col_headcount], headers = 'keys', tablefmt = TABLEFMT, floatfmt="0.0f")}"""
        )
        tb = tb[tb["check_total"]].reset_index(drop=True)

    ############################
    # Threshold monotonicity check
    m_check_vars = []
    for i in range(1, 10):
        if i > 1:
            check_varname = f"m_check_{i}"
            tb[check_varname] = tb[f"decile{i}_thr"] >= tb[f"decile{i-1}_thr"]
            m_check_vars.append(check_varname)

    tb["check_total"] = tb[m_check_vars].all(axis=1)

    # Drop rows if columns in col_decile_thr are all null. Keep if some are null
    mask = (~tb["check_total"]) & (tb[col_decile_thr].notnull().any(axis=1))

    tb_error = tb[mask].reset_index(drop=True).copy()

    if not tb_error.empty:
        log.warning(
            f"""There are {len(tb_error)} observations with thresholds not monotonically increasing and will be deleted:
            {tabulate(tb_error[['country', 'year', 'reporting_level', 'welfare_type']], headers = 'keys', tablefmt = TABLEFMT)}"""
        )
        tb = tb[~mask].reset_index(drop=True)

    ############################
    # Shares monotonicity check
    m_check_vars = []
    for i in range(1, 11):
        if i > 1:
            check_varname = f"m_check_{i}"
            tb[check_varname] = tb[f"decile{i}_share"] >= tb[f"decile{i-1}_share"]
            m_check_vars.append(check_varname)

    tb["check_total"] = tb[m_check_vars].all(axis=1)

    # Drop rows if columns in col_decile_share are all null. Keep if some are null
    mask = (~tb["check_total"]) & (tb[col_decile_share].notnull().any(axis=1))
    tb_error = tb[mask].reset_index(drop=True).copy()

    if not tb_error.empty:
        log.warning(
            f"""There are {len(tb_error)} observations with shares not monotonically increasing and will be deleted:
            {tabulate(tb_error[['country', 'year', 'reporting_level', 'welfare_type'] + col_decile_share], headers = 'keys', tablefmt = TABLEFMT, floatfmt=".1f")}"""
        )
        tb = tb[~mask].reset_index(drop=True)

    ############################
    # Shares not adding up to 100%

    tb["sum_pct"] = tb[col_decile_share].sum(axis=1)

    # Drop rows if columns in col_decile_share are all null. Keep if some are null
    mask = (tb["sum_pct"] >= 100.1) | (tb["sum_pct"] <= 99.9) & (tb[col_decile_share].notnull().any(axis=1))
    tb_error = tb[mask].reset_index(drop=True).copy()

    if not tb_error.empty:
        log.warning(
            f"""{len(tb_error)} observations of shares are not adding up to 100% and will be deleted:
            {tabulate(tb_error[['country', 'year', 'reporting_level', 'welfare_type', 'sum_pct']], headers = 'keys', tablefmt = TABLEFMT, floatfmt=".1f")}"""
        )
        tb = tb[~mask].reset_index(drop=True)

    ############################
    # delete columns created for the checks
    tb = tb.drop(columns=m_check_vars + ["m_check_1", "check_total", "sum_pct"])

    obs_after_checks = len(tb)
    log.info(f"Sanity checks deleted {obs_before_checks - obs_after_checks} observations for {ppp_version} PPPs.")

    return tb


def separate_ppp_data(tb: Table) -> Tuple[Table, Table]:
    """
    Separate out ppp data from the main dataset
    """

    # Filter table to include only the right ppp_version
    # Also, drop columns with all NaNs (which are the ones that are not relevant for the ppp_version)
    tb_2011 = tb[tb["ppp_version"] == 2011].dropna(axis=1, how="all").reset_index(drop=True).copy()
    tb_2017 = tb[tb["ppp_version"] == 2017].dropna(axis=1, how="all").reset_index(drop=True).copy()

    return tb_2011, tb_2017


def inc_or_cons_data(tb: Table) -> Tuple[Table, Table, Table, Table]:
    """
    Separate income and consumption data
    """

    # Separate out consumption-only, income-only. Also, create a table with both income and consumption
    tb_inc = tb[tb["welfare_type"] == "income"].reset_index(drop=True).copy()
    tb_cons = tb[tb["welfare_type"] == "consumption"].reset_index(drop=True).copy()
    tb_inc_or_cons = tb.copy()
    tb_inc_or_cons_unsmoothed = tb.copy()

    tb_inc_or_cons = create_smooth_inc_cons_series(tb_inc_or_cons)

    tb_inc_or_cons = check_jumps_in_grapher_dataset(tb_inc_or_cons)

    # If both inc and cons are available in a given year, drop inc (legacy)
    tb_inc_or_cons_unsmoothed = remove_duplicates_inc_cons(tb_inc_or_cons_unsmoothed)

    return tb_inc, tb_cons, tb_inc_or_cons_unsmoothed, tb_inc_or_cons


def create_smooth_inc_cons_series(tb: Table) -> Table:
    """
    Construct an income and consumption series that is a combination of the two.
    """

    tb = tb.copy()

    # Flag duplicates per year – indicating multiple welfare_types
    # Sort values to ensure the welfare_type consumption is marked as False when there are multiple welfare types
    tb = tb.sort_values(by=["country", "year", "welfare_type"], ignore_index=True)
    tb["duplicate_flag"] = tb.duplicated(subset=["country", "year"], keep=False)

    # Create a boolean column that is true if each ppp_version, country, reporting_level has only income or consumption
    tb["only_inc_or_cons"] = tb.groupby(["country"])["welfare_type"].transform(lambda x: x.nunique() == 1)

    # Select only the rows with only income or consumption
    tb_only_inc_or_cons = tb[tb["only_inc_or_cons"]].reset_index(drop=True).copy()

    # Create a table with the rest
    tb_both_inc_and_cons = tb[~tb["only_inc_or_cons"]].reset_index(drop=True).copy()

    # Create a list of the countries with both income and consumption in the series
    countries_inc_cons = list(tb_both_inc_and_cons["country"].unique())

    # Assert that the countries with both income and consumption are expected
    assert countries_inc_cons == COUNTRIES_WITH_INCOME_AND_CONSUMPTION, log.fatal(
        f"Unexpected countries with both income and consumption: {countries_inc_cons}."
    )

    # Define empty table to store the smoothed series
    tb_both_inc_and_cons_smoothed = Table()
    for country in countries_inc_cons:
        # Filter country
        tb_country = tb_both_inc_and_cons[tb_both_inc_and_cons["country"] == country].reset_index(drop=True).copy()

        # Save the max_year for the country
        max_year = tb_country["year"].max()

        # Define welfare_type for income and consumption. If both, list is saved as ['income', 'consumption']
        last_welfare_type = list(tb_country[tb_country["year"] == max_year]["welfare_type"].unique())
        last_welfare_type.sort()

        # Count how many times welfare_type switches from income to consumption and vice versa
        number_of_welfare_series = (tb_country["welfare_type"] != tb_country["welfare_type"].shift(1)).cumsum().max()

        # If there are only two welfare series, use both, except for countries where we have to choose one
        if number_of_welfare_series == 2:
            # assert if last_welfare type values are expected
            if country in ["Armenia", "Belarus", "Kyrgyzstan", "North Macedonia", "Peru"]:
                if country in ["Armenia", "Belarus", "Kyrgyzstan"]:
                    welfare_expected = ["consumption"]
                    assert len(last_welfare_type) == 1 and last_welfare_type == welfare_expected, log.fatal(
                        f"{country} has unexpected values of welfare_type: {last_welfare_type} instead of {welfare_expected}."
                    )

                elif country in ["North Macedonia", "Peru"]:
                    assert len(last_welfare_type) == 1 and last_welfare_type == ["income"], log.fatal(
                        f"{country} has unexpected values of welfare_type: {last_welfare_type} instead of ['income']"
                    )

                tb_country = tb_country[tb_country["welfare_type"].isin(last_welfare_type)].reset_index(drop=True)

        # With Turkey I also want to keep both series, but there are duplicates for some years
        elif country in ["Turkey"]:
            welfare_expected = ["income"]
            assert len(last_welfare_type) == 1 and last_welfare_type == welfare_expected, log.fatal(
                f"{country} has unexpected values of welfare_type: {last_welfare_type} instead of {welfare_expected}"
            )

            tb_country = tb_country[
                (~tb_country["duplicate_flag"]) | (tb_country["welfare_type"].isin(last_welfare_type))
            ].reset_index(drop=True)

        elif country in ["Haiti", "Philippines", "Romania", "Saint Lucia"]:
            welfare_expected = ["consumption", "income"]
            assert len(last_welfare_type) == 2 and last_welfare_type == welfare_expected, log.fatal(
                f"{country} has unexpected values of welfare_type: {last_welfare_type} instead of {welfare_expected}"
            )
            if country in ["Haiti", "Romania", "Saint Lucia"]:
                tb_country = tb_country[tb_country["welfare_type"] == "income"].reset_index(drop=True)
            else:
                tb_country = tb_country[tb_country["welfare_type"] == "consumption"].reset_index(drop=True)

        else:
            if country in ["Albania", "Russia", "Ukraine"]:
                welfare_expected = ["consumption"]
                assert len(last_welfare_type) == 1 and last_welfare_type == welfare_expected, log.fatal(
                    f"{country} has unexpected values of welfare_type: {last_welfare_type} instead of {welfare_expected}."
                )
            else:
                welfare_expected = ["income"]
                assert len(last_welfare_type) == 1 and last_welfare_type == welfare_expected, log.fatal(
                    f"{country} has unexpected values of welfare_type: {last_welfare_type} instead of {welfare_expected}."
                )

            tb_country = tb_country[tb_country["welfare_type"].isin(last_welfare_type)].reset_index(drop=True)

        tb_both_inc_and_cons_smoothed = pr.concat([tb_both_inc_and_cons_smoothed, tb_country])

    tb_inc_or_cons = pr.concat([tb_only_inc_or_cons, tb_both_inc_and_cons_smoothed], ignore_index=True)

    # Drop the columns created in this function
    tb_inc_or_cons = tb_inc_or_cons.drop(columns=["only_inc_or_cons", "duplicate_flag"])

    return tb_inc_or_cons


def check_jumps_in_grapher_dataset(tb: Table) -> Table:
    """
    Check for jumps in the dataset, which can be caused by combining income and consumption estimates for one country series.
    """
    tb = tb.copy()

    # For each country, year, welfare_type and reporting_level, check if the difference between the columns is too high

    # Define columns to check: all the headcount ratio columns
    cols_to_check = [
        col for col in tb.columns if "headcount_ratio" in col and "above" not in col and "between" not in col
    ]

    for col in cols_to_check:
        # Create a new column, shift_col, that is the same as col but shifted one row down for each country, year, welfare_type and reporting_level
        tb["shift_col"] = tb.groupby(["country", "reporting_level"])[col].shift(1)

        # Create shift_year column
        tb["shift_year"] = tb.groupby(["country", "reporting_level"])["year"].shift(1)

        # Create shift_welfare_type column
        tb["shift_welfare_type"] = tb.groupby(["country", "reporting_level"])["welfare_type"].shift(1)

        # Calculate the difference between col and shift_col
        tb["check_diff_column"] = tb[col] - tb["shift_col"]

        # Calculate the difference between years
        tb["check_diff_year"] = tb["year"] - tb["shift_year"]

        # Calculate if the welfare type is the same
        tb["check_diff_welfare_type"] = tb["welfare_type"] == tb["shift_welfare_type"]

        # Check if the difference is too high
        mask = (abs(tb["check_diff_column"]) > 10) & (tb["check_diff_year"] <= 5) & ~tb["check_diff_welfare_type"]
        tb_error = tb[mask].reset_index(drop=True).copy()

        if not tb_error.empty:
            log.warning(
                f"""There are {len(tb_error)} observations with abnormal jumps for {col}:
                {tabulate(tb_error[['ppp_version', 'country', 'year', 'reporting_level', col, 'check_diff_column', 'check_diff_year']].sort_values('year').reset_index(drop=True), headers = 'keys', tablefmt = TABLEFMT, floatfmt=".1f")}"""
            )
            # tb = tb[~mask].reset_index(drop=True)

    # Drop the columns created for the check
    tb = tb.drop(
        columns=[
            "shift_col",
            "shift_year",
            "shift_welfare_type",
            "check_diff_column",
            "check_diff_year",
            "check_diff_welfare_type",
        ]
    )

    return tb


def remove_duplicates_inc_cons(tb: Table) -> Table:
    """
    Remove duplicates in the income and consumption data
    This is only for legacy purposes, because we don't use this for OWID, but we do for Joe's PhD
    """
    # Flag duplicates – indicating multiple welfare_types
    # Sort values to ensure the welfare_type consumption is marked as False when there are multiple welfare types
    tb = tb.sort_values(by=["ppp_version", "country", "year", "reporting_level", "welfare_type"], ignore_index=True)
    tb["duplicate_flag"] = tb.duplicated(subset=["ppp_version", "country", "year", "reporting_level"])

    # Drop income where income and consumption are available
    tb = tb[(~tb["duplicate_flag"]) | (tb["welfare_type"] == "consumption")]
    tb.drop(columns=["duplicate_flag"], inplace=True)

    return tb


def regional_headcount(tb: Table) -> Table:
    """
    Create regional headcount dataset, by patching missing values with the difference between world and regional headcount
    """

    # Keep only regional data: for regions, these are the reporting_level rows not in ['national', 'urban', 'rural']
    tb_regions = tb[~tb["reporting_level"].isin(["national", "urban", "rural"])].reset_index(drop=True).copy()

    # Remove Western and Central and Eastern and Southern Africa. It's redundant with Sub-Saharan Africa (PIP)
    tb_regions = tb_regions[
        ~tb_regions["country"].isin(
            [
                "Western and Central Africa (PIP)",
                "Eastern and Southern Africa (PIP)",
                "World (excluding China)",
                "World (excluding India)",
            ]
        )
    ].reset_index(drop=True)

    # Select needed columns and pivot
    tb_regions = tb_regions[["country", "year", "headcount_215"]]
    tb_regions = tb_regions.pivot(index="year", columns="country", values="headcount_215")

    # Drop rows with more than one region with null headcount
    tb_regions["check_total"] = tb_regions[tb_regions.columns].isnull().sum(axis=1)
    mask = tb_regions["check_total"] > 1

    tb_out = tb_regions[mask].reset_index()
    if len(tb_out) > 0:
        log.info(
            f"""There are {len(tb_out)} years with more than one null region value so we can't extract regional data for them. Years are:
            {list(tb_out.year.unique())}"""
        )
        tb_regions = tb_regions[~mask].reset_index()
        tb_regions = tb_regions.drop(columns="check_total")

    # Get difference between world and (total) regional headcount, to patch rows with one missing value
    cols_to_sum = [e for e in list(tb_regions.columns) if e not in ["year", "World"]]
    tb_regions["sum_regions"] = tb_regions[cols_to_sum].sum(axis=1)

    tb_regions["diff_world_regions"] = tb_regions["World"] - tb_regions["sum_regions"]

    # Fill null values with the difference and drop aux variables
    col_dictionary = dict.fromkeys(cols_to_sum, tb_regions["diff_world_regions"])
    tb_regions.loc[:, cols_to_sum] = tb_regions[cols_to_sum].fillna(col_dictionary)
    tb_regions = tb_regions.drop(columns=["World", "sum_regions", "diff_world_regions"])

    # NOTE: I am not extracting data for China and India at least for now, because we are only extracting non filled data
    # The data originally came from filled data to plot properly.

    # # Get headcount values for China and India
    # df_chn_ind = tb[(tb["country"].isin(["China", "India"])) & (tb["reporting_level"] == "national")].reset_index(
    #     drop=True
    # )
    # df_chn_ind = df_chn_ind[["country", "year", "headcount_215"]]

    # # Make table wide and merge with regional data
    # df_chn_ind = df_chn_ind.pivot(index="year", columns="country", values="headcount_215").reset_index()
    # tb_regions = pr.merge(tb_regions, df_chn_ind, on="year", how="left")

    # tb_regions["East Asia and Pacific excluding China"] = (
    #     tb_regions["East Asia and Pacific (PIP)"] - tb_regions["China"]
    # )
    # tb_regions["South Asia excluding India"] = tb_regions["South Asia (PIP)"] - tb_regions["India"]

    tb_regions = pr.melt(tb_regions, id_vars=["year"], var_name="country", value_name="headcount_215")
    tb_regions = tb_regions[["country", "year", "headcount_215"]]

    # Rename headcount_215 to headcount_215_region, to distinguish it from the original headcount_215 when merging
    tb_regions = tb_regions.rename(columns={"headcount_215": "headcount_215_regions"})

    # Merge with original table
    tb = pr.merge(tb, tb_regions, on=["country", "year"], how="outer")

    return tb


def survey_count(tb: Table) -> Table:
    """
    Create survey count indicator, by counting the number of surveys available for each country in the past decade
    """
    # Remove regions from the table
    tb_survey = tb[~tb["country"].isin(REGIONS_LIST)].reset_index(drop=True).copy()

    min_year = int(tb_survey["year"].min())
    max_year = int(tb_survey["year"].max())
    year_list = list(range(min_year, max_year + 1))
    country_list = list(tb_survey["country"].unique())

    # Create two tables with all the years and entities
    year_tb_survey = Table(year_list)
    entity_tb_survey = Table(country_list)

    # Make a cartesian product of both dataframes: join all the combinations between all the entities and all the years
    cross = pr.merge(entity_tb_survey, year_tb_survey, how="cross")
    cross = cross.rename(columns={"0_x": "country", "0_y": "year"})

    # Merge cross and df_country, to include all the possible rows in the dataset
    tb_survey = pr.merge(cross, tb_survey[["country", "year"]], on=["country", "year"], how="left", indicator=True)

    # Mark with 1 if there are surveys available, 0 if not (this is done by checking if the row is in both datasets)
    tb_survey["survey_available"] = 0
    tb_survey.loc[tb_survey["_merge"] == "both", "survey_available"] = 1

    # Sum for each entity the surveys available for the previous 9 years and the current year
    tb_survey["surveys_past_decade"] = (
        tb_survey["survey_available"]
        .groupby(tb_survey["country"], sort=False)
        .rolling(min_periods=1, window=10)
        .sum()
        .values
    )

    # Copy metadata
    tb_survey["surveys_past_decade"] = tb_survey["surveys_past_decade"].copy_metadata(tb["reporting_level"])

    # Keep columns needed
    tb_survey = tb_survey[["country", "year", "surveys_past_decade"]]

    # Merge with original table
    tb = pr.merge(tb_survey, tb, on=["country", "year"], how="outer")

    return tb


def drop_columns(tb: Table) -> Table:
    """
    Drop columns not needed
    """

    # Remove columns
    tb = tb.drop(
        columns=[
            "ppp_version",
            "reporting_pop",
            "is_interpolated",
            "distribution_type",
            "estimation_type",
            "survey_comparability",
            "comparable_spell",
        ]
    )

    return tb


def create_survey_spells(tb: Table) -> list:
    """
    Create tables for each indicator and survey spells, to be able to graph them in explorers.
    """

    tb = tb.copy()

    # drop rows where survey coverage = nan (This is just regions)
    tb = tb[tb["survey_comparability"].notna()].reset_index()

    # Add 1 to make comparability var run from 1, not from 0
    tb["survey_comparability"] += 1

    # Note the welfare type in the comparability spell
    tb["survey_comparability"] = (
        tb["welfare_type"].astype(str) + "_spell_" + tb["survey_comparability"].astype(int).astype(str)
    )

    # Remove columns not needed: stacked, above, etc
    drop_list = ["above", "between", "poverty_severity", "watts"]
    for var in drop_list:
        tb = tb[tb.columns.drop(list(tb.filter(like=var)))]

    vars = [
        i
        for i in tb.columns
        if i
        not in [
            "country",
            "year",
            "ppp_version",
            "reporting_level",
            "welfare_type",
            "reporting_pop",
            "is_interpolated",
            "distribution_type",
            "estimation_type",
            "survey_comparability",
            "comparable_spell",
            "headcount_215_regions",
            "surveys_past_decade",
        ]
    ]

    # Define spell table list
    spell_tables = []

    # Loop over the variables in the main dataset
    for select_var in vars:
        tb_var = tb[["country", "year", select_var, "survey_comparability"]].copy()

        # convert to wide
        tb_var = pr.pivot(
            tb_var,
            index=["country", "year"],
            columns=["survey_comparability"],
            values=select_var,
        )

        tb_var.metadata.short_name = f"{tb_var.metadata.short_name}_{select_var}"

        spell_tables.append(tb_var)

    return spell_tables


def create_survey_spells_inc_cons(tb_inc: Table, tb_cons: Table) -> list:
    """
    Create table for each indicator and survey spells, to be able to graph them in explorers.
    This version recombines income and consumption tables to not lose dropped rows.
    """

    tb_inc = tb_inc.reset_index().copy()
    tb_cons = tb_cons.reset_index().copy()

    # Concatenate the two tables
    tb_inc_or_cons_2017_spells = pr.concat([tb_inc, tb_cons], ignore_index=True, short_name="income_consumption_2017")

    # Set index and sort
    tb_inc_or_cons_2017_spells = tb_inc_or_cons_2017_spells.format(
        keys=["country", "year", "reporting_level", "welfare_type"]
    )

    # Create spells
    spell_tables = create_survey_spells(tb_inc_or_cons_2017_spells)

    return spell_tables


def combine_tables_2011_2017(tb_2011: Table, tb_2017: Table, short_name: str) -> Table:
    """
    Combine income and consumption tables from 2011 and 2017 PPPs.
    We will use this table for the Poverty Data Explorer: World Bank data - 2011 vs. 2017 prices.
    """

    # Identify columns to use (ID + indicators)
    id_cols = ["country", "year"]

    tb_2011 = define_columns_for_ppp_comparison(tb=tb_2011, id_cols=id_cols, ppp_version=2011)
    tb_2017 = define_columns_for_ppp_comparison(tb=tb_2017, id_cols=id_cols, ppp_version=2017)

    # Rename all the non-id columns with the suffix _ppp(year)
    # (the suffix option in merge only adds suffix when columns coincide)
    tb_2011 = tb_2011.rename(columns={c: c + "_ppp2011" for c in tb_2011.columns if c not in id_cols})
    tb_2017 = tb_2017.rename(columns={c: c + "_ppp2017" for c in tb_2017.columns if c not in id_cols})

    # Merge the two files (it's OK to have an inneer join, because we want to keep country-year pairs that are in both files)
    tb_2011_2017 = pr.merge(tb_2011, tb_2017, on=id_cols, validate="one_to_one", short_name=short_name)

    # Add index and sort
    tb_2011_2017 = tb_2011_2017.set_index(["country", "year"], verify_integrity=True).sort_index()

    return tb_2011_2017


def define_columns_for_ppp_comparison(tb: Table, id_cols: list, ppp_version: int) -> Table:
    """
    Define columns to use for the comparison of 2011 and 2017 PPPs
    """

    tb = tb.reset_index()
    # Define poverty lines
    povlines_list = POVLINES_DICT[ppp_version]

    # Define groups of columns
    headcount_absolute_cols = [f"headcount_{p}" for p in povlines_list]
    headcount_ratio_absolute_cols = [f"headcount_ratio_{p}" for p in povlines_list]

    headcount_relative_cols = [f"headcount_{rel}_median" for rel in [40, 50, 60]]
    headcount_ratio_relative_cols = [f"headcount_ratio_{rel}_median" for rel in [40, 50, 60]]

    # Define all the columns to filter

    cols_list = (
        id_cols
        + headcount_absolute_cols
        + headcount_ratio_absolute_cols
        + headcount_relative_cols
        + headcount_ratio_relative_cols
        + ["mean", "median", "decile1_thr", "decile9_thr"]
    )

    # Filter columns
    tb = tb[cols_list]

    return tb


def regional_data_from_1990(tb: Table, regions_list: list) -> Table:
    """
    Select regional data only from 1990 onwards, due to the uncertainty in 1980s data
    """
    # Create a regions table
    tb_regions = tb[(tb["year"] >= 1990) & (tb["country"].isin(regions_list))].reset_index(drop=True).copy()

    # Remove regions from tb
    tb = tb[~tb["country"].isin(regions_list)].reset_index(drop=True).copy()

    # Concatenate both tables
    tb = pr.concat([tb, tb_regions], ignore_index=True)
    return tb
