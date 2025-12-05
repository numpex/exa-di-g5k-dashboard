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
        file_url = f"{GITLAB_A

