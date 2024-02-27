"""Game owidle."""
import datetime as dt
from pathlib import Path
from typing import List, Tuple

import geopandas as gpd
import pandas as pd
import plotly.express as px
import streamlit as st
from geographiclib.geodesic import Geodesic
from owid.catalog import Dataset, Table
from st_pages import add_indentation

from etl.paths import DATA_DIR

##########################################
# CONFIG PAGE & SESSION STATE INIT
##########################################
st.set_page_config(page_title="Wizard: owidle", layout="wide", page_icon="🪄")
add_indentation()

## Maximum number of guesses allowed to the user
NUM_GUESSES = 6
default_guess = [
    {
        "name": "",
        "distance": "",
        "direction": "",
        "score": "",
    }
    for i in range(NUM_GUESSES)
]
# Contains the list of guesses by the user
st.session_state.guesses = st.session_state.get("guesses", default_guess)
# Contains the number of guesses by the user
st.session_state.num_guesses = st.session_state.get("num_guesses", 0)
# Tells whether the user has succeded in guessing the correct country
st.session_state.user_has_succeded = st.session_state.get("user_has_succeded", False)
# Wether we are playing easy mode
st.session_state.owidle_easy_mode = st.session_state.get("owidle_easy_mode", False)
# Wether we are playing easy mode
st.session_state.guess_last = st.session_state.get("guess_last", None)
# Number of the minimum number of countries shown if set to EASY mode
NUM_COUNTRIES_EASY_MODE = 6

# TITLE
if st.session_state.owidle_easy_mode:
    title = "👶 :gray[(beginner)] :rainbow[owidle]"
else:
    title = "👾 :rainbow[owidle]"
st.title(title)
st.markdown(
    "Guess the country using the population and GDP hints. For each guess, you will get a geographical hint (distance and direction to the country). There is a daily challenge!"
)

st.toggle(
    "Beginner mode",
    value=False,
    key="owidle_easy_mode",
    disabled=st.session_state.num_guesses > 0,
    help=f"In beginner mode, the dropdown will only show countries within the radius of your most recent guess (or the nearest {NUM_COUNTRIES_EASY_MODE} countries).",
)

##########################################
## LOAD DATA
##########################################
COLORS = [
    "#C15065",
    "#2C8465",
    "#286BBB",
    "#6D3E91",
    "#996D39",
    "#BE5915",
]
MAX_DISTANCE_ON_EARTH = 20_000


@st.cache_data
def load_data(placeholder: str) -> Tuple[pd.DataFrame, gpd.GeoDataFrame, str]:
    """Load data for the game."""
    # Load population indicator
    ds = Dataset(DATA_DIR / "garden" / "un" / "2022-07-11" / "un_wpp")
    tb = ds["population"].reset_index()
    tb = tb.loc[
        (tb["metric"] == "population") & (tb["sex"] == "all") & (tb["age"] == "all") & (tb["variant"] == "estimates"),
        ["year", "location", "value"],
    ]
    # df = pd.DataFrame(tb)

    # Load GDP indicator(s)
    ## WDI
    ds = Dataset(DATA_DIR / "garden/worldbank_wdi/2023-05-29/wdi")
    tb_gdp_wdi = ds["wdi"].reset_index()[["year", "country", "ny_gdp_pcap_pp_kd"]]
    tb_gdp_wdi = tb_gdp_wdi.rename(
        columns={
            "ny_gdp_pcap_pp_kd": "gdp_per_capita_wdi",
            "country": "location",
        }
    )
    ## PWT
    ds = Dataset(DATA_DIR / "garden/ggdc/2022-11-28/penn_world_table")
    tb_gdp_pwt = ds["penn_world_table"].reset_index()[["year", "country", "rgdpo_pc"]]
    tb_gdp_pwt = tb_gdp_pwt.rename(
        columns={
            "rgdpo_pc": "gdp_per_capita_pwt",
            "country": "location",
        }
    )
    ## MDP
    ds = Dataset(DATA_DIR / "garden/ggdc/2020-10-01/ggdc_maddison")
    tb_gdp_mdp = ds["maddison_gdp"].reset_index()[["year", "country", "gdp_per_capita"]]
    tb_gdp_mdp = tb_gdp_mdp.rename(
        columns={
            "gdp_per_capita": "gdp_per_capita_mdp",
            "country": "location",
        }
    )
    tb_gdp_mdp = tb_gdp_mdp[tb_gdp_mdp["year"] >= 1950]

    # Load geographic info
    ## file from https://gist.github.com/metal3d/5b925077e66194551df949de64e910f6 (later harmonized)
    geo = pd.read_csv(
        Path(__file__).parent / "owidle_countries.csv",
    )
    geo = Table(geo)

    # Combine
    tb = tb.merge(geo, left_on="location", right_on="Country", how="left")
    tb = tb.merge(tb_gdp_wdi, on=["year", "location"], how="left")
    tb = tb.merge(tb_gdp_pwt, on=["year", "location"], how="left")
    tb = tb.merge(tb_gdp_mdp, on=["year", "location"], how="left")

    # Remove NaNs
    tb = tb.dropna(subset=["Latitude", "Longitude"])

    # Get indicator df
    tb_indicator = tb[
        ["year", "location", "value", "gdp_per_capita_wdi", "gdp_per_capita_pwt", "gdp_per_capita_mdp"]
    ].rename(
        columns={
            "value": "population",
        }
    )

    # Get geographic df
    df_geo = gpd.GeoDataFrame(
        tb,
        geometry=gpd.points_from_xy(tb.Longitude, y=tb.Latitude),
        crs="EPSG:4326",
    )
    df_geo = df_geo[
        [
            "location",
            "geometry",
        ]
    ].drop_duplicates()
    # df_geo = df_geo.to_crs(3310)

    # Arbitrary daily solution
    seed = (dt.datetime.now(dt.timezone.utc).date() - dt.date(1993, 7, 13)).days
    solution = tb["location"].sample(random_state=seed).item()

    return tb_indicator, df_geo, solution


@st.cache_data
def get_all_distances():
    # Build geodataframe with all possible combinations of countries
    distances = GEO.merge(GEO, how="cross", suffixes=("_1", "_2"))

    # Split dataframe in two, so that we can estimate distances
    distances_1 = gpd.GeoDataFrame(
        distances[["location_1", "geometry_1"]],
        crs={"init": "epsg:4326"},
        geometry="geometry_1",
    ).to_crs(epsg=32663)

    distances_2 = gpd.GeoDataFrame(
        distances[["location_2", "geometry_2"]],
        crs={"init": "epsg:4326"},
        geometry="geometry_2",
    ).to_crs(epsg=32663)

    # Filter own country
    distances = distances[distances["location_1"] != distances["location_2"]]

    # Estimate distances in km
    distances["distance"] = distances_1.distance(distances_2) // 1000

    # Rename columns
    distances = distances.rename(
        columns={
            "location_1": "origin",
            "location_2": "target",
        }
    )

    # Keep relevant columns, set index
    distances = distances[["origin", "target", "distance"]].set_index("origin")

    # Filter own country
    return distances


DATA, GEO, SOLUTION = load_data("cached")
# SOLUTION = "Spain"
OPTIONS = sorted(DATA["location"].unique())

# Find indicator for this solution (we prioritise PWD > MDP > WDI)
gdp_indicator_titles = {
    "gdp_per_capita_pwt": "GDP per capita (constant 2017 intl-$)",
    "gdp_per_capita_mdp": "GDP per capita (constant 2011 intl-$)",
    "gdp_per_capita_wdi": "GDP per capita (constant 2017 intl-$)",
}
gdp_indicators = list(gdp_indicator_titles.keys())
GDP_INDICATOR = None
for ind in gdp_indicators:
    s = set(
        DATA.dropna(
            subset=[ind],
        )["location"].unique()
    )
    if SOLUTION in s:
        GDP_INDICATOR = ind
        break
if GDP_INDICATOR is None:
    SOLUTION_HAS_GDP = False
    GDP_INDICATOR = gdp_indicators[0]
else:
    SOLUTION_HAS_GDP = True
# print(f"Going with indicator {GDP_INDICATOR}")
# st.write(SOLUTION)
# st.write(GDP_INDICATOR)

# Distances
DISTANCES = get_all_distances()


##########################################
# COMPARE GUESS WITH SOLUTION
##########################################
def distance_to_solution(country_selected: str) -> Tuple[str, str, str]:
    """Estimate distance (km) from guessed to solution, including direction.

    ref: https://stackoverflow.com/a/47780264
    """
    # If user has guessed the correct country
    if country_selected == SOLUTION:
        st.session_state.user_has_succeded = True
        return "0", "🎉", "100"
    # Estimate distance
    # st.write(GEO)
    # GEO_DIST = cast(gpd.GeoDataFrame, GEO.to_crs(3310))
    # GEO = cast(gpd.GeoDataFrame, GEO.to_crs(3310))
    solution = GEO.loc[GEO["location"] == SOLUTION, "geometry"]
    guess = GEO.loc[GEO["location"] == country_selected, "geometry"]

    # Estimate direction
    guess = guess.item()
    solution = solution.item()

    # Use Geodesic to calculate distance
    # More details:
    # - https://geographiclib.sourceforge.io/Python/doc/examples.html#initializing
    geod = Geodesic.WGS84  # type: ignore
    # geod.Inverse returns a Geodesic dictionary (https://geographiclib.sourceforge.io/Python/doc/interface.html#dict)
    print("----------------")
    print(guess.y, guess.x, solution.y, solution.x)
    print("----------------")
    g = geod.Inverse(
        lat1=guess.y,
        lon1=guess.x,
        lat2=solution.y,
        lon2=solution.x,
    )
    print(g)
    # Option 1 (using geographiclib)
    # distance = g["s12"] / 1000
    # print(distance)
    # Option 2 (using pre-calculated distances)
    dist = DISTANCES.loc[SOLUTION]
    distance = dist[dist["target"] == country_selected]["distance"].item()
    # Initial bearing angle (angle from guess to solution in degrees)
    bearing = g["azi1"]

    if (bearing > -22.5) & (bearing <= 22.5):
        arrow = "⬆️"
    elif (bearing > 22.5) & (bearing <= 67.5):
        arrow = "↗️"
    elif (bearing > 67.5) & (bearing <= 112.5):
        arrow = "➡️"
    elif (bearing > 112.5) & (bearing <= 157.5):
        arrow = "↘️"
    elif (bearing > 157.5) | (bearing <= -157.5):
        arrow = "⬇️"
    elif (bearing > -157.5) & (bearing <= -112.5):
        arrow = "↙️"
    elif (bearing > -112.5) & (bearing <= -67.5):
        arrow = "⬅️"
    else:
        arrow = "↖️"

    # Estimate score
    score = int(round(100 - (distance / MAX_DISTANCE_ON_EARTH) * 100, 0))

    # Ensure string types
    score = str(score)
    distance = str(int(distance))

    return distance, arrow, score


# Actions once user clicks on "GUESS" button
def guess() -> None:
    """Actions performed once the user clicks on 'GUESS' button."""
    # If guess is None, don't do anything
    if st.session_state.guess_last_submitted is None:
        pass
    else:
        st.session_state.user_has_guessed = True
        # Check if guess was already submitted
        if st.session_state.guess_last_submitted in [guess["name"] for guess in st.session_state.guesses]:
            st.toast("⚠️ You have already guessed this country!")
        else:
            # Estimate distance from correct answer
            distance, direction, score = distance_to_solution(st.session_state.guess_last_submitted)
            # Add to session state
            st.session_state.guess_last = {
                "name": st.session_state.guess_last_submitted,
                "distance": distance,
                "direction": direction,
                "score": score,
            }
            st.session_state.guesses[st.session_state.num_guesses] = st.session_state.guess_last
            # Increment number of guesses
            st.session_state.num_guesses += 1


##########################################
# PLOT CHART
##########################################


def _plot_chart(
    countries_guessed: List[str],
    column_indicator: str,
    title: str,
    column_country: str,
):
    # Filter out solution countri if given within guessed countries
    countries_guessed = [c for c in countries_guessed if c != SOLUTION]
    countries_to_plot = [SOLUTION] + countries_guessed

    # Filter only data for countries to plot
    df = DATA[DATA["location"].isin(countries_to_plot)].reset_index(drop=True)

    # Sort
    priority = {c: i for i, c in enumerate(countries_to_plot)}
    df["priority"] = df[column_country].map(priority)
    df = df.sort_values(["priority", "year"])

    # Map locations to lcolor
    color_map = dict(zip(countries_to_plot, COLORS))
    color_map["?"] = color_map[SOLUTION]

    # Map locations to line dash
    line_dash_map = {
        **{c: "dashdot" for c in countries_guessed},
        SOLUTION: "solid",
        "?": "solid",
    }

    # Hide country name if user has not succeded yet.
    if not st.session_state.user_has_succeded:
        df[column_country] = df[column_country].replace({SOLUTION: "?"})

    # Change location column name to "Country"
    df["Country"] = df[column_country]

    # Drop NaN
    df = df.dropna(subset=[column_indicator])

    # Create plotly figure & plot
    fig = px.line(
        df,
        x="year",
        y=column_indicator,
        title=title,
        color="Country",
        color_discrete_map=color_map,
        line_dash="Country",
        line_dash_map=line_dash_map,
        # labels={
        #     "year": "Year",
        #     column_indicator: indicator_name if indicator_name else column_indicator,
        # },
        # markers=True,
        line_shape="spline",
    )

    # Remove axis labels
    fig.update_layout(xaxis_title=None, yaxis_title=None)
    # Legends
    fig.update_layout(
        legend=dict(
            orientation="h",
            # yanchor="bottom",
            # y=1.02,  # Places the legend above the chart
            # xanchor="center",
            # x=0.5,
        )
    )

    st.plotly_chart(
        fig,
        theme="streamlit",
        use_container_width=True,
    )


@st.cache_data
def plot_chart_population(countries_guessed: List[str]):
    """Plot timeseries."""
    _plot_chart(
        countries_guessed,
        column_indicator="population",
        title="Population",
        column_country="location",
    )


@st.cache_data
def plot_chart_gdp_pc(countries_guessed: List[str]):
    """Plot timeseries."""
    _plot_chart(
        countries_guessed,
        column_indicator=GDP_INDICATOR,
        title=gdp_indicator_titles[GDP_INDICATOR],
        column_country="location",
    )


def display_metadata(metadata):
    if not isinstance(metadata, dict):
        metadata = metadata.to_dict()
    ds = pd.Series(metadata).astype(str)
    ds.name = "value"
    st.table(data=ds)


##########################################
# PLOT CHARTS
##########################################
with st.container(border=True):
    countries_guessed = [guess["name"] for guess in st.session_state.guesses if guess["name"] != ""]
    if not SOLUTION_HAS_GDP:
        st.warning("We don't have GDP data for this country!")
    col1, col2 = st.columns(2)
    with col1:
        plot_chart_population(countries_guessed)
        with st.expander("Sources"):
            try:
                metadata = DATA["population"].metadata.to_dict()
                origins = metadata.pop("origins", None)
                _ = metadata.pop("display", None)
                # display_metadata(metadata)
                if origins:
                    for origin in origins:
                        display_metadata(origin)
            except Exception:
                st.info("Metadata couldn't be accssed")
    with col2:
        plot_chart_gdp_pc(countries_guessed)
        with st.expander("Sources"):
            try:
                metadata = DATA[GDP_INDICATOR].metadata.to_dict()
                origins = metadata.pop("origins", None)
                _ = metadata.pop("display", None)
                display_metadata(metadata)
                if origins:
                    for origin in origins:
                        display_metadata(origin)
                # else:
                #     st.info("")
            except Exception:
                st.info("Metadata couldn't be accssed")

##########################################
# INPUT FROM USER
##########################################
with st.form("form_guess", border=False, clear_on_submit=True):
    col_guess_1, col_guess_2 = st.columns([4, 1])
    # EASY MODE: Filter options
    ## Only consider options within the radius of the last guess
    ## If there are less than NUM_COUNTRIES_EASY_MODE, show the NUM_COUNTRIES_EASY_MODE closest ones.
    if st.session_state.owidle_easy_mode and (st.session_state.guess_last is not None):
        distances = DISTANCES.loc[st.session_state.guess_last["name"]]
        options = distances.loc[
            distances["distance"] <= int(st.session_state.guess_last["distance"]), "target"
        ].tolist()
        if len(options) <= NUM_COUNTRIES_EASY_MODE:
            options = distances.sort_values("distance").head(NUM_COUNTRIES_EASY_MODE)["target"].tolist()
        else:
            options = distances.loc[
                distances["distance"] <= int(st.session_state.guess_last["distance"]), "target"
            ].tolist()
        options = sorted(options)
    else:
        options = OPTIONS

    # Show dropdown
    value = st.selectbox(
        label="Guess a country",
        placeholder="Choose a country... ",
        options=options,
        label_visibility="collapsed",
        index=None,
        key="guess_last_submitted",
    )
    # Disable button if user has finished their guesses or has succeeded
    disabled = (st.session_state.num_guesses >= NUM_GUESSES) or st.session_state.user_has_succeded
    # Label for button
    if st.session_state.user_has_succeded:
        label = "YOU GUESSED IT!"
    elif st.session_state.num_guesses >= NUM_GUESSES:
        label = "MAYBE NEXT TIME!"
    else:
        label = f"GUESS {st.session_state.num_guesses + 1}/6"

    # Show button
    btn = st.form_submit_button(
        label=label,
        type="primary",
        use_container_width=True,
        on_click=lambda: guess(),
        disabled=disabled,
    )

##########################################
# SHOW GUESSES (this far)
##########################################
guesses_display = []
num_guesses_bound = min(st.session_state.num_guesses, NUM_GUESSES)
for i in range(num_guesses_bound):
    with st.container(border=True):
        col1, col2, col3, col4 = st.columns([30, 10, 7, 7])
        with col1:
            # st.text(st.session_state.guesses[i]["name"])
            st.markdown(f"**{st.session_state.guesses[i]['name']}**")
        with col2:
            st.markdown(f"{st.session_state.guesses[i]['distance']}km")
        with col3:
            st.markdown(st.session_state.guesses[i]["direction"])
        with col4:
            st.markdown(f"{st.session_state.guesses[i]['score']}%")


##########################################
# SHOW REMAINING GUESSES BOXES
##########################################
for i in range(num_guesses_bound, NUM_GUESSES):
    with st.container(border=True):
        st.empty()

##########################################
# FINAL MESSAGE
##########################################
## Successful
if st.session_state.user_has_succeded:
    st.balloons()
    st.success("🎉 You have guessed the correct country! 🎉")
    col, _ = st.columns(2)
    with col:
        s = []
        for count, i in enumerate(range(num_guesses_bound)):
            _s = f"{st.session_state.guesses[i]['distance']}km"
            s.append(_s)

        r = "round" if num_guesses_bound == 1 else "rounds"
        s = (
            f"I did the daily owidle challenge in {num_guesses_bound} {r}!\n\n"
            + " → ".join(s)
            + "\n\nVisit http://etl.owid.io/wizard/owidle"
        )
        st.subheader("Share it")
        st.code(s)
    st.stop()
## Unsuccessful
elif st.session_state.num_guesses >= NUM_GUESSES:
    st.error(f"The correct answer was **{SOLUTION}**. Better luck next time!")
    st.stop()
