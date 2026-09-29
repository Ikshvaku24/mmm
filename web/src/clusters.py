
import pandas as pd
import requests
import os
import json
from datetime import datetime

DATABRICKS_HOST = f"https://{os.environ.get('DATABRICKS_HOST', '')}"
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
cluster_id = "0709-045209-ldovdlma"

def get_cluster_status():
    url = f"{DATABRICKS_HOST}/api/2.1/clusters/get?cluster_id={cluster_id}"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    response = requests.get(url, headers=headers, timeout=15)
    return response.json()["state"]

def start_cluster():
    url = f"{DATABRICKS_HOST}/api/2.1/clusters/start"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    payload = {"cluster_id": cluster_id}
    response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
    return response.status_code
