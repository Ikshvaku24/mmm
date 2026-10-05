import requests
import os
import json

from src import perf

DATABRICKS_HOST = f"https://{os.environ.get('DATABRICKS_HOST', '')}"
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
TIMEOUT = 30   # seconds - a hung request would freeze the page


def _job_id(job_id):
    return int(job_id) if str(job_id).strip().isdigit() else job_id


@perf.timed("jobs.run_now")
def run_model_job(prior_file, data_file, job_id, config_file="", mapping_file="", share_file="",
                  bmc_name="", run_name=""):
    """Trigger the model job (it runs codebase 1's demo.ipynb).

    Every file value is a FILE NAME. With bmc_name and run_name the notebook
    reads them from the run's own folder,
    /dbfs/mnt/testuat/Secondary Modelling/<bmc_name>/<run_name>/<Data|Prior|
    Config|Mapping|Share>/, and writes the outputs to its Outputs/. The job's
    own parameter run_id = {{job.run_id}} is not sent from here.
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
    if bmc_name or run_name:
        payload["job_parameters"].update(bmc_name=str(bmc_name), run_name=str(run_name))
    response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=TIMEOUT)
    return response
@perf.timed("jobs.get_job")
def job_parameter_names(job_id):
    """The job parameters the job defines (jobs/get) - the app checks that
    bmc_name and run_name are among them before it starts a run."""
    url = f"{DATABRICKS_HOST}/api/2.1/jobs/get"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    response = requests.get(url, headers=headers, params={"job_id": _job_id(job_id)},
                            timeout=TIMEOUT)
    response.raise_for_status()
    settings = response.json().get("settings") or {}
    return {p.get("name") for p in settings.get("parameters") or [] if p.get("name")}


_JOB_PARAMS = perf.SharedCache("job_params", ttl=60, maxsize=8)


def job_parameter_names_cached(job_id):
    """job_parameter_names, asked of the Jobs API at most once a minute for
    everyone (the Run checklist reads it on every refresh)."""
    names, _hit = _JOB_PARAMS.get_or_compute(str(job_id), lambda: job_parameter_names(job_id))
    return names


@perf.timed("jobs.run_output")
def get_run_logs(run_id):
    url = f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get-output?run_id={run_id}"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    response = requests.get(url, headers=headers, timeout=TIMEOUT)
    return response.json()
@perf.timed("jobs.run_status")
def get_run_status(run_id):
    url = f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get?run_id={run_id}"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    response = requests.get(url, headers=headers, timeout=TIMEOUT)
    return response.json()
@perf.timed("jobs.cancel")
def cancel_run(run_id):
    """Cancel a running workflow run (asynchronous)."""
    url = f"{DATABRICKS_HOST}/api/2.1/jobs/runs/cancel"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    payload = {"run_id": int(run_id)}
    response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=TIMEOUT)
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
@perf.timed("jobs.list_runs")
def list_runs(job_id, limit=20):
    """The job's most recent runs, newest first (run_id, start_time, state,
    run_page_url, job_parameters) - so a run can be found again after its
    status panel was closed or the page reloaded."""
    url = f"{DATABRICKS_HOST}/api/2.1/jobs/runs/list"
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}
    params = {"job_id": _job_id(job_id), "limit": int(limit), "expand_tasks": "false"}
    response = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json().get("runs", []) or []
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
