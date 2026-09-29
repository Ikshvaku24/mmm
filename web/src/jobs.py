import pandas as pd
import requests
import os
import json
from datetime import datetime
DATABRICKS_HOST = f"https://{os.environ.get('DATABRICKS_HOST', '')}"
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")


def _job_id(job_id):
    return int(job_id) if str(job_id).strip().isdigit() else job_id


def run_model_job(prior_file, data_file, job_id, config_file="", mapping_file="", share_file=""):
    """Trigger the model job (it runs codebase 1's demo.ipynb).

    Every value is a FILE NAME; the notebook reads it from its folder under
    /dbfs/mnt/testuat/Secondary Modelling/ (Data, Prior, Config, Mapping,
    Share). The job's own parameter run_id = {{job.run_id}} names the output
    folder, so it is not sent from here.
    """
    url = f"{DATABRICKS_HOST}/api/2.1/jobs/run-now"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "job_id": _job_id(job_id),
        "job_parameters": {
            "config_file": str(config_file or ""),
            "data_file": str(data_file),
            "prior_file": str(prior_file),
            "mapping_file": str(mapping_file or ""),
            "share_file": str(share_file or ""),
        }
    }
    response = requests.post(url, headers=headers, data=json.dumps(payload))
    return response
def get_run_logs(run_id):
    url = f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get-output?run_id={run_id}"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    response = requests.get(url, headers=headers)
    return response.json()
def get_run_status(run_id):
    url = f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get?run_id={run_id}"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    response = requests.get(url, headers=headers)
    return response.json()
def cancel_run(run_id):
    """Cancel a running workflow run (asynchronous)."""
    url = f"{DATABRICKS_HOST}/api/2.1/jobs/runs/cancel"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    payload = {"run_id": int(run_id)}
    response = requests.post(url, headers=headers, data=json.dumps(payload))
    return response
def get_task_run_id(run_id, task_key=None):
    """The run id of one TASK of a job run (the first task when task_key is None).

    runs/get-output only answers for a task run, not for the job run that
    run-now returns - which is what this stub was started for."""
    run = get_run_status(run_id)
    for task in run.get("tasks") or []:
        if task_key is None or task.get("task_key") == task_key:
            return task.get("run_id")
    return None
def get_run_output(run_id):
    """What the notebook returned (notebook_output.result) or raised (error,
    error_trace) for a job run."""
    task_run_id = get_task_run_id(run_id) or run_id
    return get_run_logs(task_run_id)
# def run_mapping_job/vendor, channel, bmc, channel_list, start_date, end_date, kpi, attributes, job_id):
#     url = f"{DATABRICKS_HOST}/api/2.1/jobs/run-now"
#     headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
#     payload = {
#         "job_id": job_id,
#         "job_parameters": {
#             "vendor": str/vendor),
#             "channel": str(channel),
#             "bmc": str(bmc),
#             "channel_list": str(channel_list),
#             "start_date": str(start_date),
#             "end_date": str(end_date),
#             "kpi": str(kpi),
#             "attributes": str(attributes)
#         }
#     }
#     response = requests.post(url, headers=headers, data=json.dumps(payload))
#     return response
# def run_feasibility_check(mapping, id, job_id):
#     url = f"{DATABRICKS_HOST}/api/2.1/jobs/run-now"
#     headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
#     payload = {
#         "job_id": job_id,
#         "job_parameters": {
#             "mapping": str(mapping),
#             "id": str(id)
#         }
#     }
#     response = requests.post(url, headers=headers, data=json.dumps(payload))
#     return response
