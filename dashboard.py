##
##  This is the code for a Streamlit application able to visualize a database of benchmark experiments previously ran on Grid'5000

##  Each experiment, corresponding to a specific hardware+software configuration to be tested, is captured in a JSON file like this:
##        {
##          "machine": "gemini-2.lyon.grid5000.fr",
##          "date": "2025-06-12T16:45:14+02:00",
##          "initial_time": 2.794,
##          "compute_time": 0.319
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
import base64
from st_aggrid import AgGrid, GridOptionsBuilder
import altair as alt
import urllib.parse

# 🔧 CONFIGURATION
NAMESPACE = "numpex-pc5/wp2-co-design"
REPO = "g5k-testing"
PROJECT_ID = "60556"
BRANCH = "main"
GITLAB_ROOT = "https://gitlab.inria.fr"
GITLAB_API = f"{GITLAB_ROOT}/api/v4/projects/{PROJECT_ID}/repository"
RESULTS_ROOT = "results"

# List all the subfolders inside the "path" folder that contain at least one JSON file
def list_subfolders_with_json_files(path=RESULTS_ROOT):
    matching_folders = []

    def recurse(current_path):
        url = f"{GITLAB_API}/tree"
        params = {"path": current_path, "per_page": 100, "recursive": False}
        r = requests.get(url, params=params)
        r.raise_for_status()
        items = r.json()

        # Don't include the root folder itself
        is_root = current_path == path

        has_json = any(item["type"] == "blob" and item["name"].endswith(".json") for item in items)
        if has_json and not is_root:
            # Strip the leading "results/" prefix
            relative_path = current_path[len(path) + 1:]  # +1 to remove the "/"
            matching_folders.append(relative_path)

        subfolders = [item["path"] for item in items if item["type"] == "tree"]
        for folder in subfolders:
            recurse(folder)

    recurse(path)
    return sorted(matching_folders)
    
# List all the subfolders inside the "path" folder of the Gitlab repo
def list_subfolders(path="results"):
    url = f"{GITLAB_API}/tree"
    params = {"path": path, "per_page": 100}
    r = requests.get(url, params=params)
    r.raise_for_status()
    items = r.json()
    # Filter folders only
    folders = [item["name"] for item in items if item["type"] == "tree"]
    return folders

# Plot Performance History Graph
def plot_history(df):
    """
    Plot a stacked bar chart showing initial_time and compute_time per commit date.

    Args:
        df (pd.DataFrame): DataFrame with columns ['date', 'initial_time', 'compute_time']
    """
    # Convert to long format for Altair stacking
    df_long = df.melt(id_vars=["date"], value_vars=["initial_time", "compute_time"],
                      var_name="Time Type", value_name="Time (s)")

    base = alt.Chart(df_long).encode(
        x=alt.X('date:T',
                title='Date',
                axis=alt.Axis(format='%Y-%m-%d %H:%M:%S', labelAngle=0)),
        y=alt.Y('Time (s):Q', title='Time (seconds)'),
        color=alt.Color('Time Type:N', title='Time Type'),
        tooltip=['date:T', 'Time Type', 'Time (s)']
    )

    bars = base.mark_bar()

    # Regression lines for each 'Time Type'
    trendlines = base.transform_regression(
        'date', 'Time (s)', groupby=['Time Type'], method='linear'
    ).mark_line(size=3, strokeDash=[5,5])

    chart = (bars + trendlines).properties(
        width=700,
        height=350,
        title="Perfomance History per Commit"
    )

    st.altair_chart(chart, use_container_width=True)

# Parse the history of commits for a JSON file and then call plot_history()
def parse_file_history(file):
    # 1. Get commits touching the file
    commits_url = f"{GITLAB_API}/commits"
    commits_params = {"path": file}
    resp = requests.get(commits_url,  params=commits_params)
    resp.raise_for_status()
    commits = resp.json()

    data = []
    for commit in commits:
        sha = commit["id"]
        commit_date = commit["committed_date"]

        # 2. Get raw JSON file content at this commit
        encoded_path = urllib.parse.quote(file, safe='')
        file_url = f"{GITLAB_API}/files/{encoded_path}/raw"
        file_params = {"ref": sha}

        file_resp = requests.get(file_url,  params=file_params)

        if file_resp.status_code == 200:
            try:
                json_data = file_resp.json()
                initial_time = float(json_data.get("initial_time", 0))
                compute_time = float(json_data.get("compute_time", 0))
                data.append({
                    "date": commit_date,
                    "initial_time": initial_time,
                    "compute_time": compute_time
                })
            except Exception as e:
                # Could not parse JSON or fields; skip this commit
                print(f"Skipping commit {sha} due to parse error: {e}")
        else:
            print(f"Skipping commit {sha} - file not found")

    df = pd.DataFrame(data)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        # Sort by date ascending
        df = df.sort_values("date")
        plot_history(df)


###############################################################################
# MAIN 
##############################################################################

# Use the full page width layout (recommended at the top of your app)
st.set_page_config(layout="wide")

st.title("📊 NumPEx Exa-DI: Continuous Performance Benchmark on Grid5000")

# Step 1/ allow the user to select a particular app/subfolder
apps = list_subfolders_with_json_files()
if not apps:
    st.error("No app folders found under 'results' tree.")
    st.stop()

selected_app = st.selectbox("Select an application: ", apps)

# Step 2: List the JSON files corresponding to that app/subfolder using GitLab API
tree_url = f"{GITLAB_API}/tree"
params = {
    "path": f"results/{selected_app}",
    "ref": BRANCH,
    "per_page": 100,
}

file_list_resp = requests.get(tree_url, params=params)
if file_list_resp.status_code != 200:
    st.error(f"Error fetching file list: {file_list_resp.status_code}")
    st.stop()

files = file_list_resp.json()
json_files = [f["name"] for f in files if f["type"] == "blob" and f["name"].endswith(".json")]

if not json_files:
    st.warning("No JSON files found in the folder.")
    st.stop()


# Step 3: Download each JSON using raw URLs
data = []
for filename in json_files:
    raw_url = f"{GITLAB_ROOT}/{NAMESPACE}/{REPO}/-/raw/{BRANCH}/{RESULTS_ROOT}/{selected_app}/{filename}"
    try:
        response = requests.get(raw_url)
        response.raise_for_status()
        content = json.loads(response.text)
        content["config"] = filename
        data.append(content)
    except Exception as e:
        st.warning(f"Failed to load {filename}: {e}")

# Step 4: Display table
if data:
    df = pd.DataFrame(data)
    cols = ["config"] + [c for c in df.columns if c != "config"]
    df = df[cols]    # Configure grid options to enable single row selection
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_selection(selection_mode="single", use_checkbox=True)
    
    # Add conditional cell styles for columns ending with 'time'
    highlight_zero = JsCode("""
    function(params) {
         if (params.value === 0) {
             return { backgroundColor: 'rgba(255, 0, 0, 0.3)' };
         }
            return {};
    }
    """)

    for col in df.columns:
        if col.endswith("time"):
            gb.configure_column(col, cellStyle=highlight_zero)
            
    gridOptions = gb.build()

    # Display the grid
    grid_response = AgGrid(df, gridOptions=gridOptions, height=300, fit_columns_on_grid_load=True)
    
    # Step 5: allow the user to select a row, and trigger the plot of history graph for the selected configuration
    selected = grid_response.get('selected_rows', [])
    
    if  selected is not None and not selected.empty:  # True if list is non-empty
        selected_row = selected.iloc[0]
        parse_file_history (f"results/{selected_app}/{selected_row['config']}")
    else:
        st.write("Select a row to see details.")
else:
    st.info("No valid JSON files loaded.")
