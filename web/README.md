# BRIDGE - the web app for codebase 1

A Streamlit app, hosted as a Databricks App. It is the **frontend** only:
**codebase 1** (`../codebase1_hierarchical_mmm`) is the backend. The app
checks and prepares the inputs, and **Run Model** starts the Databricks job,
which runs codebase 1's `demo.ipynb`.

```
Input data  ->  Model settings  ->  Mapping / share files  ->  Prior file  ->  Run
(datacube)      (config.yaml)       (optional)                 generate or      status, errors,
                                                               upload, edit     outputs zip
```

---

## How the app finds codebase 1

The app keeps no copy of codebase 1. It loads it **live** (`src/codebase.py`)
and looks in this order:

| # | Where | When to use it |
|---|---|---|
| 1 | `CODEBASE1_DIR` (a local folder) | local development |
| 2 | `CODEBASE1_WORKSPACE_PATH` (a workspace folder) | to pin a folder, or when the job is Git-sourced |
| 3 | **the folder of the notebook the job `MDR_JOB_ID` runs** | the default: nothing to configure |
| 4 | `../codebase1_hierarchical_mmm` next to the app | the repository layout |

Option 3 means the app always uses the same codebase as the job. A workspace
copy is downloaded through the Workspace API and re-checked every 5 minutes.
To update everything, **re-upload the whole `codebase1_hierarchical_mmm`
folder to the workspace**, exactly as before:
- the job uses the new code on its next run;
- the app switches to it within 5 minutes, or at once with **Reload codebase 1**;
- anyone running it by hand from the workspace gets it too.

The app does not need redeploying.

The config editor and the prior table are built from codebase 1's own schema,
so a new setting or a new allowed value appears in the app by itself. The app
needs codebase 1 **2026.09.29 or later** (`MIN_CODEBASE` in `src/codebase.py`)
and says so plainly if the workspace copy is older.

---

## One-time Databricks setup

1. **Upload `codebase1_hierarchical_mmm/`** to the workspace, the whole
   folder. Its `demo.ipynb` is now the job notebook: fixed, and no longer
   writing into `config.yaml`.
2. **Edit the job `MDR_JOB_ID`.**
   - Task: the notebook `<workspace path>/codebase1_hierarchical_mmm/demo`.
   - Compute: the serverless GPU environment with codebase 1's packages
     (pymc, numpyro, `jax[cuda]`, arviz, netCDF4, openpyxl, pyyaml).
   - **Job parameters** (Job details -> Parameters):

     | Parameter | Default | Meaning |
     |---|---|---|
     | `config_file` | *(empty)* | the settings file the app uploads to `Config/` |
     | `data_file` | *(empty)* | the datacube in `Data/` |
     | `prior_file` | *(empty)* | the prior CSV in `Prior/` |
     | `mapping_file` | *(empty)* | optional, `Mapping/` |
     | `share_file` | *(empty)* | optional, `Share/` |
     | `run_id` | `{{job.run_id}}` | names the output folder `Outputs/<run_id>` |
     | `base_path` | *(empty)* | empty = `/dbfs/mnt/testuat/Secondary Modelling` |

   - **Delete `modelling_type`.** The intercept is now `model.include_intercept`
     in Model settings.
3. **The token** (`DATABRICKS_TOKEN`) must be able to:
   - run the job (it already does);
   - **read the job's definition**;
   - **read the codebase 1 workspace folder**.
4. **`app.yml`** is unchanged. Set `CODEBASE1_WORKSPACE_PATH` only if the job is
   Git-sourced, or to pin a different folder.
5. **`requirements.txt`** now also installs `pyyaml`.
6. Copy the real logo to `assest/aommm.png`. The header simply omits a missing
   logo.
7. Sync `web/` to the workspace and redeploy the app.

### Why `run_id = {{job.run_id}}`

The old notebook read the run id with `context.tags()`. On this compute that
raises `Py4JError: getContext().tags() is not a CommandContext accessor`. A job
parameter with the dynamic value `{{job.run_id}}` works on every compute type.
It is the same number the app gets back from *run-now*, so both sides agree on
the output folder.

### Serverless and `/dbfs/mnt`

The job reads and writes under `base_path`, which defaults to
`/dbfs/mnt/testuat/Secondary Modelling`, as the old notebook did. Serverless
compute may not allow DBFS mounts. If the job fails on a `/dbfs/mnt/...` path:
1. create a Unity Catalog volume over the same ADLS container;
2. set the job parameter `base_path` to
   `/Volumes/<catalog>/<schema>/<volume>/Secondary Modelling`.

Nothing else changes: the app keeps uploading to the same container folders.

Outputs are written to local disk first and then copied to `Outputs/<run_id>`,
**also when a run fails**, so a failed run's `00_warnings` can be read.
(Writing `trace.nc` straight to FUSE storage is fragile.)

---

## Where the files go

| Kind | In the app (ADLS, container-relative) | In the job (`base_path` = `/dbfs/mnt/testuat/Secondary Modelling`) |
|---|---|---|
| Datacube | `Secondary Modelling/Data/` | `<base_path>/Data/` |
| Prior file | `Secondary Modelling/Prior/` | `<base_path>/Prior/` |
| Settings | `Secondary Modelling/Config/` | `<base_path>/Config/` |
| Mapping file | `Secondary Modelling/Mapping/` | `<base_path>/Mapping/` |
| Share file | `Secondary Modelling/Share/` | `<base_path>/Share/` |
| Outputs | `Secondary Modelling/Outputs/<run_id>/` | `<base_path>/Outputs/<run_id>/` |

Every upload gets a `_YYYYmmdd-HHMMSS` suffix (UTC), so two people uploading
`feature_priors_national.csv` never overwrite each other. The folder names
come from codebase 1 (`mmm/app_job.py :: FOLDERS`); `ADLS_ROOT` overrides
`Secondary Modelling`.

---

## Using it

1. **Input data.** Upload the datacube (xlsx or csv). It is checked with
   codebase 1's rules, and nothing in the file is renamed:
   - the date / region / KPI columns named in Model settings exist;
   - no missing values, duplicate region × date rows or text features;
   - warnings for dust columns (the Coupon case) and for columns that are
     constant within a region.

   You see a panel summary with the period plan. **Upload** is enabled once the
   checks pass.
2. **Model settings.** codebase 1's `config.yaml` with one widget per key and
   dropdowns for the allowed values.
   - A changed value is marked ●.
   - You can load, download or paste a `config.yaml`, and reset to the base.
   - Validation runs on every change.
   - The paths and the run name are set by the job, so they are read-only.
   - The file sent with the run carries **every** key.
3. **Mapping and share files** (optional). Each has:
   - codebase 1's sample, and a template pre-filled with your datacube's
     variables;
   - an upload box, checked by codebase 1's own reader (names, regions, one
     contribution per vendor cell);
   - an **Upload** button.
4. **Prior file.**
   - **Generate** runs codebase 1's `build_priors`: case a / b / c / d
     depending on the mapping and share files. Download the national and
     regional files, `prior_calculation.xlsx` and the warnings, then click
     **Use national** or **Use regional**.
   - Or upload your own (csv or xlsx), for example a generated file you edited
     in Excel.
   - **Preview / Edit Prior File** opens the editor:
     - dropdowns for sign, pooling, centre and scale modes and the two bases,
       and a region dropdown listing your datacube's regions;
     - Ctrl+C / Ctrl+V between the grid and Excel;
     - **Fill or paste a whole column**.
   - The table is validated by codebase 1's loader. **Upload** is enabled only
     when it is valid.
5. **Run.** Works through a checklist, then **Run Model**. The status popup shows:
   - on failure, the notebook's actual error;
   - on success, which codebase version ran;
   - the outputs zip (`trace.nc` only if you tick it).

   **Run outputs from ADLS** reopens any finished run by its ID.

---

## Testing locally

```bash
python tests/run_all.py        # from "updating production code/" - includes:
#   test_v19_config_schema.py  codebase 1's schema, YAML writer, app_job (demo.ipynb)
#   test_v20_web_app.py        src/codebase.py, incl. live loading from a fake workspace
#   test_v21_web_ui_smoke.py   the whole app.py flow against a scripted streamlit stand-in
```

None of these need Streamlit, Azure or Databricks. To click through the real
UI:

```bash
pip install streamlit~=1.54 openpyxl pyyaml azure-identity azure-storage-file-datalake
set LOCAL_STORAGE_DIR=C:\temp\bridge_store     # a folder instead of ADLS
set CODEBASE1_DIR=..\codebase1_hierarchical_mmm
streamlit run app.py
```

Everything works except the job itself: **Run Model** needs Databricks.

---

## Layout

```
web/
├── app.py                 page wiring
├── app.yml                Databricks App config (env vars from app resources)
├── requirements.txt
├── assest/aommm.png       the logo (copy it in)
├── src/
│   ├── codebase.py        the ONLY module that imports codebase 1 (no Streamlit)
│   ├── config_editor.py   Model settings
│   ├── app_functions.py   prior editor dialog, run-outputs viewer
│   ├── pages/model_setup_page.py   the page's blocks, run status popup
│   ├── generate_prior.py  hands generation to codebase 1
│   ├── validation.py      shows the datacube checks
│   ├── files.py           ADLS (or LOCAL_STORAGE_DIR), unique names
│   ├── jobs.py            Jobs API: run-now, status, output, cancel
│   └── ...                clusters, styles, header; feasibility pages (disabled)
└── _reference/            git-ignored: screenshots, OCR originals, OCR_FIXES.md
```

## What changed from the old app

- **Priors**: the app no longer writes its own `b0/B0` table. codebase 1
  generates the file, and you can download, edit and re-upload it.
- **New inputs**:
  - Model settings (`config.yaml`) is edited in the app and sent with the run.
    The Intercept checkbox is gone; it is now `model.include_intercept`.
  - Mapping and share files: a sample, a template, validation and upload.
- **Job notebook** (`demo.ipynb`):
  - the `context.tags()` Py4JError is fixed;
  - it no longer rewrites the shared `config.yaml`, which also stopped
    concurrent runs from overwriting each other's settings;
  - it no longer forces `include_intercept: True`;
  - it writes outputs locally, then publishes them;
  - it returns its status to the app.
- **Output download**: it used to fetch only the first file of a run (a
  `return` inside the loop), and every run shared one `./local.zip`.
- **OCR fixes** from the screenshots are listed in `_reference/OCR_FIXES.md`.
