"""Run codebase 1 for the BRIDGE web app - everything behind `demo.ipynb`.

The Databricks job the web app triggers runs `demo.ipynb` (this codebase's
root folder). The notebook only reads its widgets and calls `run_app_job`;
the logic lives here so it can be tested on a laptop without Databricks.

Where the files are
-------------------
The web app uploads to ADLS under `Secondary Modelling/<Folder>/<name>`. On
the cluster the same files are `<base_path>/<Folder>/<name>`, where
`base_path` is the mount the original notebook used:

    /dbfs/mnt/testuat/Secondary Modelling

A Unity Catalog volume path (`/Volumes/<catalog>/<schema>/<volume>/...`)
works the same way - pass it as the `base_path` job parameter.

What it changes, and what it never changes
------------------------------------------
The job runs the config the app uploaded, with ONLY the keys in
`JOB_OWNED_KEYS` replaced - the input paths, where outputs go and the run
name. Everything the modeller chose stays exactly as uploaded. It writes that
config to the run's own folder; it never edits this folder's `config.yaml`,
so running the codebase by hand from the workspace is unaffected by app runs.
(The old notebook wrote back into config.yaml, which also meant two runs at
once overwrote each other's settings.)

Outputs are written to local disk first (HDF5 `trace.nc` and fast PNG writes
are fragile on FUSE storage) and then copied to `<base_path>/Outputs/<run_id>`
- also when the run fails, so the warnings of a failed run can be read.

Running by hand
---------------
With every widget blank, `demo.ipynb` runs this folder's `config.yaml` as it
stands, like `run_real_data.py`, and still publishes to `Outputs/manual_<time>`.
"""
from __future__ import annotations

__codebase__ = "2026.09.29"   # must equal mmm.__version__

import copy
import json
import os
import shutil
import tempfile
import time
import traceback

import yaml

DEFAULT_BASE_PATH = "/dbfs/mnt/testuat/Secondary Modelling"

# job parameter -> folder under base_path (and under the app's ADLS root)
FOLDERS = {
    "data_file": "Data",
    "prior_file": "Prior",
    "config_file": "Config",
    "mapping_file": "Mapping",
    "share_file": "Share",
}
OUTPUT_FOLDER = "Outputs"

# the job parameters demo.ipynb defines as widgets, all blank by default
PARAMS = ("config_file", "data_file", "prior_file", "mapping_file",
          "share_file", "run_id", "base_path")

# the ONLY config keys the job sets; a UI should show them read-only
JOB_OWNED_KEYS = ("data.input_path", "data.feature_priors", "data.mapping_file",
                  "data.share_file", "data.pre_model_dir", "run.output_dir",
                  "run.run_name")

# where each file parameter lands in the config
_PARAM_KEY = {"data_file": "input_path", "prior_file": "feature_priors",
              "mapping_file": "mapping_file", "share_file": "share_file"}
_PATH_KEYS = ("input_path", "feature_priors", "mapping_file", "share_file",
              "pre_model_dir")


def normalise_params(params: dict | None) -> dict:
    """Every known parameter as a stripped string ('' when absent)."""
    params = params or {}
    unknown = sorted(set(params) - set(PARAMS))
    if unknown:
        print(f"[app_job] ignoring unknown parameter(s): {unknown}")
    return {k: str(params.get(k) or "").strip() for k in PARAMS}


def input_path(base_path: str, param: str, name: str) -> str | None:
    """`<base_path>/<Folder>/<name>` for a file parameter, None when blank."""
    return os.path.join(base_path, FOLDERS[param], name) if name else None


def effective_config(raw: dict | None, params: dict, base_path: str,
                     local_root: str, config_dir: str) -> dict:
    """The config the job runs: `raw` with only the job-owned keys replaced.

    A file parameter that is set points its key at `<base_path>/<Folder>/`;
    a blank one keeps the config's own value, resolved against `config_dir`
    (the uploaded file is moved to the run folder, so a relative path would
    otherwise resolve against the wrong place).
    """
    params = normalise_params(params)
    run_id = params["run_id"]
    if not run_id:
        raise ValueError("effective_config needs params['run_id']")
    cfg = copy.deepcopy(raw or {})
    if not isinstance(cfg, dict):
        raise ValueError("the config must be a mapping of sections")
    data = dict(cfg.get("data") or {})
    run = dict(cfg.get("run") or {})

    for key in _PATH_KEYS:
        v = data.get(key)
        if v and not os.path.isabs(str(v)):
            data[key] = os.path.normpath(os.path.join(config_dir, str(v)))
    for param, key in _PARAM_KEY.items():
        if params[param]:
            data[key] = input_path(base_path, param, params[param])

    run["output_dir"] = local_root
    run["run_name"] = run_id
    data["pre_model_dir"] = os.path.join(local_root, run_id, "00_pre_model")
    cfg["data"], cfg["run"] = data, run
    return cfg


def local_output_root() -> str:
    """/local_disk0 on classic clusters, the temp folder elsewhere (serverless)."""
    for cand in ("/local_disk0/mmm_outputs",
                 os.path.join(tempfile.gettempdir(), "mmm_outputs")):
        try:
            os.makedirs(cand, exist_ok=True)
            probe = os.path.join(cand, ".write_probe")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
            return cand
        except OSError:
            continue
    return tempfile.mkdtemp(prefix="mmm_outputs_")


def copy_tree(src: str, dst: str) -> int:
    """Copy a folder file by file - no chmod/utime, which FUSE storage refuses
    (shutil.copytree would copy everything and then fail on copystat)."""
    n = 0
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for f in files:
            shutil.copyfile(os.path.join(root, f), os.path.join(target, f))
            n += 1
    return n


def _tail(text: str, n: int = 40) -> str:
    return "\n".join(str(text).rstrip().splitlines()[-n:])


def run_app_job(params: dict | None = None, *, base_path: str | None = None,
                config_dir: str | None = None, local_root: str | None = None,
                publish: bool = True, runner=None, copier=None) -> dict:
    """Run one model for the web app and publish its output folder.

    `params` are the job parameters (see PARAMS). `runner` defaults to
    `run_from_yaml` and `copier` to `copy_tree`; both are arguments so a test
    can run the whole path without PyMC. Returns the `run_info` dict (also
    written to `<run>/run_info.json`). A failed run is published first and
    then RE-RAISED, so the Databricks job fails with the real error.
    """
    import mmm

    t0 = time.time()
    p = normalise_params(params)
    run_id = p["run_id"] or time.strftime("manual_%Y%m%d-%H%M%S")
    p["run_id"] = run_id
    base_path = p["base_path"] or base_path or DEFAULT_BASE_PATH
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_dir = config_dir or here
    local_root = local_root or local_output_root()
    copier = copier or copy_tree

    if p["config_file"]:
        cfg_src = input_path(base_path, "config_file", p["config_file"])
        cfg_dir = os.path.dirname(cfg_src)
    else:
        cfg_src = os.path.join(config_dir, "config.yaml")
        cfg_dir = config_dir
    print(f"[app_job] codebase {mmm.__version__}  run_id={run_id}")
    print(f"[app_job] config   {cfg_src}")
    with open(cfg_src, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    cfg = effective_config(raw, p, base_path, local_root, cfg_dir)
    run_dir = os.path.join(local_root, run_id)
    os.makedirs(run_dir, exist_ok=True)
    cfg_path = os.path.join(run_dir, "app_config.yaml")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False, default_flow_style=False)
    for k in JOB_OWNED_KEYS:
        sec, key = k.split(".")
        print(f"[app_job]   {k:22s} = {cfg[sec].get(key)}")

    published = os.path.join(base_path, OUTPUT_FOLDER, run_id)
    info = {"status": "running", "run_id": run_id,
            "codebase": mmm.__version__, "config_source": cfg_src,
            "params": p, "local_dir": run_dir,
            "output_dir": published if publish else run_dir,
            "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    error = None
    try:
        if runner is None:
            from mmm.core.settings import run_from_yaml as runner
        result = runner(cfg_path)
        info["status"] = "success"
        if isinstance(result, dict) and result.get("output_dir"):
            info["model_output_dir"] = str(result["output_dir"])
    except Exception as e:  # noqa: BLE001 - recorded, published, re-raised
        error = e
        info["status"] = "failed"
        info["error"] = f"{type(e).__name__}: {e}"
        info["traceback"] = _tail(traceback.format_exc())
    finally:
        info["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        info["seconds"] = round(time.time() - t0, 1)
        with open(os.path.join(run_dir, "run_info.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(info, fh, indent=2, default=str)
        if publish:
            try:
                n = copier(run_dir, published)
                print(f"[app_job] published {n} files -> {published}")
            except Exception as e:  # noqa: BLE001 - keep the local copy
                info["publish_error"] = f"{type(e).__name__}: {e}"
                info["output_dir"] = run_dir
                with open(os.path.join(run_dir, "run_info.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump(info, fh, indent=2, default=str)
                print(f"[app_job] !!! could not publish to {published}: {e}\n"
                      f"[app_job] the outputs are still in {run_dir}")
    if error is not None:
        raise error
    return info
