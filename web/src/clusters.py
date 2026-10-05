import requests
import os
import json

from src import perf

DATABRICKS_HOST = f"https://{os.environ.get('DATABRICKS_HOST', '')}"
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
cluster_id = "0709-045209-ldovdlma"

# Every open page polls the cluster badge every 5 s. The state is asked of the
# Clusters API at most once per STATE_SECONDS for EVERYONE (perf.SharedCache),
# not once per page.
STATE_SECONDS = 5
_STATE_CACHE = perf.SharedCache("cluster_state", ttl=STATE_SECONDS, maxsize=8)


@perf.timed("jobs.cluster_status")
def get_cluster_status():
    url = f"{DATABRICKS_HOST}/api/2.1/clusters/get?cluster_id={cluster_id}"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    response = requests.get(url, headers=headers, timeout=15)
    return response.json()["state"]


def cluster_state():
    """The cluster's state ("RUNNING", "TERMINATED", ...; "UNKNOWN" when the
    API cannot say), shared by every session for STATE_SECONDS."""
    def ask():
        try:
            return str(get_cluster_status() or "UNKNOWN").upper()
        except Exception:  # noqa: BLE001 - the badge shows UNKNOWN
            return "UNKNOWN"
    state, _hit = _STATE_CACHE.get_or_compute(cluster_id, ask,
                                              cache_if=lambda s: s != "UNKNOWN")
    return state


def forget_cluster_state():
    _STATE_CACHE.invalidate()


@perf.timed("jobs.cluster_start")
def start_cluster():
    url = f"{DATABRICKS_HOST}/api/2.1/clusters/start"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    payload = {"cluster_id": cluster_id}
    response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
    forget_cluster_state()
    return response.status_code
