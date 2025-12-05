##
##  This is the code for a Streamlit application able to visualize a database of benchmark experiments previously ran on Grid'5000

##  Each experiment, corresponding to a specific hardware+software configuration to be tested, is captured in a JSON file like this:
##        {
##          "machine": "gemini-2.lyon.grid5000.fr",
##          "date": "2025-06-12T16:45:14+02:00",
##          "initial_time": 2.794,
##          "compute_time": 0.319,
##          "test_result": true
##        }
##
##  The JSON files are stored in a Gitlab repo, in the "results/" folder, clustered in subfolders (one per application we want to benchmark). 
##  The resulting structure looks like this :
##        .
##        ├── results
##        │   ├── proxy-app-1
##        │   │   ├── some-config.json
##        │   │   ├── another-config.json
##        │   │   └── yet-another-config.json
##        │   └── proxy-app-2
##        │       └── only-one-config-for-this-app.json
##
##  We rely on Git versionning to keep the history of all experiments made for a particular configuration. Each new experiment corresponds to a new commit for the JSON file of the concerned configuration(s).
##  The Gitlab repo is self-hosted by Inria and has public read access ; it can be browsed through Gitlab REST API without authentication
##
##  Worklow:
##  1. The user will be asked to select an application among a list dynamically fetched by looking through all the subfolders of "results/" tree
##  2. The list of JSON files for that application will be read and visualized in the form of a dynamic table
##  3. If the user ticks one of the configurations, the Git history of the JSON file will be parsed through, and a graph showing the evolution of "initial_time" and "compute_time" in the different experiments.
##  
##
##  Development done in the context of https://gitlab.inria.fr/numpex-pc5/wp2-co-design/proxy-geos-hc/-/issues/32
##  See https://gitlab.inria.fr/numpex-pc5/wp2-co-design/g5k-testing/-/blob/main/ARCHITECTURE.md for a comprehensive description of the technical solution
##

import streamlit as st
import pandas as pd
import requests
import json
import urllib.parse
from st_aggrid import AgGrid, GridOptionsBuilder
import altair as alt

# 🔧 CONFIGURATION
NAMESPACE = "numpex-pc5/wp2-co-design"
REPO = "g5k-testing"
PROJECT_ID = "60556"
BRANCH = "main"
GITLAB_ROOT = "https://gitlab.inria.fr"
GITLAB_API = f"{GITLAB_ROOT}/api/v4/projects/{PROJECT_ID}/repository"
RESULTS_ROOT = "results"

# ------------------------------
#  Utility functions
# ------------------------------

def list_subfolders_with_json_files(path=RESULTS_ROOT):
    matching_folders = []

    def recurse(current_path):
        url = f"{GITLAB_API}/tree"
        params = {"path": current_path, "per_page": 100, "recursive": False}
        r = requests.get(url, params=params)
        r.raise_for_status()
        items = r.json()

        is_root = current_path == path
        has_json = any(item["type"] == "blob" and item["name"].endswith(".json") for item in items)
        if has_json and not is_root:
            relative_path = current_path[len(path) + 1:]
            matching_folders.append(relative_path)

        for item in items:
            if item["type"] == "tree":
                recurse(item["path"])

    recurse(path)
    return sorted(matching_folders)


def detect_step_trend(series, rel_threshold):
    if len(series) == 0:
        return pd.Series([], index=series.index)

    segments = []
    start_idx = 0
    for i in range(1, len(series)):
        change = abs(series.iloc[i] - series.iloc[i - 1]) / max(series.iloc[i - 1], 1e-8)
        if change > rel_threshold:
            seg_value = series.iloc[start_idx:i].mean()
            segments.extend([seg_value] * (i - start_idx))
            start_idx = i
    seg_value = series.iloc[start_idx:].mean()
    segments.extend([seg_value] * (len(series) - start_idx))
    return pd.Series(segments, index=series.index)

# ------------------------------
#  Caching layers
# ------------------------------

@st.cache_data
def get_apps():
    return list_subfolders_with_json_files()


@st.cache_data
def load_app_jsons(selected_app):
    tree_url = f"{GITLAB_API}/tree"
    params = {"path": f"{RESULTS_ROOT}/{selected_app}", "ref": BRANCH, "per_page": 100}
    resp = requests.get(tree_url, params=params)
    resp.raise_for_status()
    files = resp.json()
    json_files = [f["name"] for f in files if f["type"] == "blob" and f["name"].endswith(".json")]

    data = []
    for filename in json_files:
        raw_url = f"{GITLAB_ROOT}/{NAMESPACE}/{REPO}/-/raw/{BRANCH}/{RESULTS_ROOT}/{selected_app}/{filename}"
        try:
            r = requests.get(raw_url)
            r.raise_for_status()
            content = json.loads(r.text)
            content["config"] = filename
            data.append(content)
        except:
            continue

    df = pd.DataFrame(data)
    if not df.empty:
        cols = ["config"] + [c for c in df.columns if c != "config"]
        df = df[cols]
    return df


@st.cache_data
def load_config_history(file_path):
    commits_url = f"{GITLAB_API}/commits"
    commits_params = {"path": file_path}
    resp = requests.get(commits_url, params=commits_params)
    resp.raise_for_status()
    commits = resp.json()

    data = []
    for commit in commits:
        sha = commit["id"]
        encoded_path = urllib.parse.quote(file_path, safe='')
        file_url = f"{GITLAB_API}/files/{encoded_path}/raw"
        file_params = {"ref": sha}
        file_resp = requests.get(file_url, params=file_params)
        if file_resp.status_code == 200:
            try:
                json_data = file_resp.json()
                record = {k: v for k, v in json_data.items() if isinstance(v, (int, float, str, bool))}
                data.append(record)
            except:
                continue
    df = pd.DataFrame(data)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_convert(None)
        df = df.sort_values("date")
    return df

# ------------------------------
#  Plotting helpers
# ------------------------------

@st.cache_data
def compute_step_trends(df, compute_pct, total_pct):
    df = df.copy()
    df["compute_step"] = detect_step_trend(df["compute_time"], compute_pct / 100)
    df["total_step"] = detect_step_trend(df["total_time"], total_pct / 100)
    return df


@st.cache_data
def make_bar_df(df):
    bar_df = pd.DataFrame({
        "date": df["date"].tolist() * 2,
        "Time Type": ["compute_time"] * len(df) + ["initial_time"] * len(df),
        "Time (s)": df["compute_time"].tolist() + df["initial_time"].tolist(),
        "test_result": df["test_result"].tolist() * 2
    })
    stack_order = {"compute_time": 0, "initial_time": 1}
    bar_df["stack_order"] = bar_df["Time Type"].map(stack_order)
    return bar_df


def plot_history(df):
    if df.empty:
        st.warning("No data to plot")
        return

    # 🔥 Ensure missing fields exist
    if "initial_time" not in df.columns:
        df["initial_time"] = 0.0

    if "compute_time" not in df.columns:
        st.error("Missing compute_time in data. Cannot plot.")
        return

    # Always ensure consistent total_time
    df["total_time"] = df["compute_time"].astype(float) + df["initial_time"].astype(float)
    df["compute_time"] = df["compute_time"].astype(float)
    df["initial_time"] = df["initial_time"].astype(float)

    if "test_result" not in df.columns:
        df["test_result"] = True
    else:
        df["test_result"] = df["test_result"].fillna(True)

    # Sidebar sliders
    compute_pct = st.sidebar.slider("Threshold for compute_time steps (%)", 0, 100, 10, 1)
    total_pct = st.sidebar.slider("Threshold for total_time steps (%)", 0, 100, 10, 1)

    # Step trends
    df = compute_step_trends(df, compute_pct, total_pct)
    bar_df = make_bar_df(df)

    # Stacked bars
    bar_chart = alt.Chart(bar_df).mark_bar().encode(
        x=alt.X("date:T", title="Date", axis=alt.Axis(format="%d/%m", labelAngle=0)),
        y=alt.Y("Time (s):Q", stack="zero", title="Time (s)"),
        color=alt.Color("Time Type:N",
                        scale=alt.Scale(domain=["compute_time", "initial_time"],
                                        range=["lightblue", "orange"])),
        order=alt.Order("stack_order:O"),
        opacity=alt.condition(alt.datum.test_result==True, alt.value(1.0), alt.value(0.4)),
        tooltip=["date:T", "Time Type:N", "Time (s):Q", "test_result"]
    )

    # Step trendlines
    compute_line = alt.Chart(df).mark_line(size=3).encode(
        x="date:T",
        y="compute_step:Q",
        color=alt.value("cyan"),
        strokeDash=alt.value([5, 2]),
        tooltip=["date:T", "compute_step:Q"]
    )

    total_line = alt.Chart(df).mark_line(size=3).encode(
        x="date:T",
        y="total_step:Q",
        color=alt.value("yellow"),
        strokeDash=alt.value([4, 4]),
        tooltip=["date:T", "total_step:Q"]
    )

    chart = (bar_chart + compute_line + total_line).properties(
        width=900,
        height=450,
        title="Performance History with Step Trendlines (Relative Threshold)"
    )
    st.altair_chart(chart, use_container_width=True)

# ------------------------------
#  MAIN
# ------------------------------

st.set_page_config(layout="wide")
st.title("📊 NumPEx Exa-DI: Continuous Performance Benchmark on Grid5000")

# Step 1: Select app
apps = get_apps()
selected_app = st.selectbox("Select an application:", apps)

if selected_app:
    # Step 2: Load JSON files for selected app
    df_app = load_app_jsons(selected_app)
    if df_app.empty:
        st.warning("No JSON files found for this app.")
    else:
        # Display table with AgGrid
        gb = GridOptionsBuilder.from_dataframe(df_app)
        gb.configure_selection(selection_mode="single", use_checkbox=True)
        gridOptions = gb.build()
        grid_response = AgGrid(df_app, gridOptions=gridOptions, height=300, fit_columns_on_grid_load=True)

        # Ensure selected_rows is always a list of dicts
        selected_rows = grid_response.get("selected_rows", [])

        if isinstance(selected_rows, pd.DataFrame):
            # Convert to list of dicts
            selected_rows = selected_rows.to_dict("records")

        if isinstance(selected_rows, list) and len(selected_rows) > 0:
            selected_row = selected_rows[0]  # dict
            config_name = selected_row.get("config")
            
            if config_name:
                # Use session_state to cache history per selected config
                key = f"history_{selected_app}_{config_name}"
                if key not in st.session_state:
                    st.session_state[key] = load_config_history(f"results/{selected_app}/{config_name}")
                
                df_history = st.session_state[key]
                
                if df_history.empty:
                    st.warning("No history found for this configuration.")
                else:
                    plot_history(df_history)
            else:
                st.warning("Selected row has no 'config' field.")
        else:
            st.info("Select a row to see details.")

