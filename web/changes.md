# What changed in the BRIDGE app

The app still works the same way:
1. you upload files to ADLS;
2. you press **Run Model**;
3. a Databricks job runs the model.

What changed:
- it now runs **codebase 1**, not the old in-house model;
- it has a few new sections;
- several bugs are fixed.

**Codebase 1 is not copied into the app.** The app reads it live from the
workspace, from the same folder the job runs. Re-upload codebase 1 and the
app, the job and anyone running it by hand all get the new version. The app
needs no redeploy.

---

## The four pieces: before and now

### 1. Job trigger (`src/jobs.py`)

| Before | Now |
|---|---|
| Sent 3 parameters: `prior_file`, `data_file`, `modelling_type` (the Intercept tick box) | Sends 5 file names: `config_file`, `data_file`, `prior_file`, `mapping_file`, `share_file` |
| The notebook ignored `modelling_type` and always switched the intercept on | No tick box. The intercept is a normal setting (`model.include_intercept`) in Model settings |
| Only used the `status` and `cancel` calls. `get_task_run_id` was an unfinished stub | Also reads the notebook's result: the real error message if the run fails, the codebase version if it succeeds |
| URL had a double slash (`//api/2.1`) | Fixed |

Unchanged: only file **names** are sent, and the job finds the files in the
agreed ADLS folders.

### 2. Run Model button (`src/pages/model_setup_page.py`)

| Before | Now |
|---|---|
| Enabled when the prior and the input data were uploaded | Enabled when a checklist is complete: data uploaded, settings valid, prior file **validated** and uploaded, mapping/share file uploaded if you added one |
| Sent the files straight to the job | First writes the settings to a config file with **every** setting in it and uploads it to `Config/`, then starts the job |
| The popup only knew Pending / Running / Success / Terminated | Also handles Queued, Failed and Internal error, and shows why a run failed |

The popup is otherwise the same: a 1-second timer, the status every 5 seconds,
Cancel, Download.

### 3. ADLS upload (`src/files.py`)

| Before | Now |
|---|---|
| Kept the original file name (overwrite on) - two people uploading `prior.csv` overwrote each other | Every upload gets a time stamp: `prior_20260929-143210.csv` |
| Two folders: `Prior/`, `Data/` | Also `Config/`, `Mapping/`, `Share/` |
| Uploaded whatever you picked | Uploads only after the file passes the checks |
| Prior had to be CSV | Prior can be CSV or Excel; it is always uploaded as CSV |

The login (`ClientSecretCredential`) and the ADLS calls are unchanged.

### 4. Download (`src/files.py`, `src/app_functions.py`)

| Before | Now |
|---|---|
| **Bug:** `download_folder` stopped after the FIRST file, so the zip held one output | Downloads every file of the run |
| Every run and every user shared one `./local` folder and `./local.zip` | One folder and one zip per run, deleted after use |
| Only after a successful run | Also after a failed run - its warnings usually explain why it failed |
| Zip included everything | `trace.nc` (large) only if you tick **Include trace.nc** |
| "Model File" popup read a fixed `Model/summary.csv` | **Run outputs** opens any finished run by its run ID (warnings, convergence, fit, contributions, coefficients) |

---

## New in the app

- **Model settings.** Edit codebase 1's `config.yaml` in the app.
  - Dropdowns list the allowed values; hovering shows the help.
  - You can load, download or reset the file.
  - The file you see is the file the job runs; the job only fills in the file
    paths and the run name.
- **Mapping and share files** (optional).
  - Download a sample, or a template pre-filled with your datacube's variables.
  - Upload the file; it is checked, then uploaded.
- **Prior file.**
  - The old app built its own `b0/B0` table. Now codebase 1 builds the prior
    file (**Generate**) and you can download it.
  - Or upload your own - for example the generated file after editing it in Excel.
  - **Preview / Edit** has:
    - dropdown columns;
    - Ctrl+C / Ctrl+V to and from Excel;
    - "Fill or paste a whole column".
  - Upload is allowed only once codebase 1 says the file is valid.
- **Better datacube check.**
  - It uses the column names from Model settings and never renames anything.
    The old check lower-cased the first 3 columns.
  - It flags missing values, duplicate rows, text columns, "dust" columns and
    features that never change.

## Changes in the job notebook (`codebase1_hierarchical_mmm/demo.ipynb`)

| Before | Now |
|---|---|
| Crashed at `context.tags()` (Py4JError) | The run id comes from a job parameter: `run_id = {{job.run_id}}` |
| Rewrote the shared `config.yaml` on every run - two runs at once clashed, and hand runs got the app's settings | Never touches `config.yaml`; each run gets its own copy |
| Forced `include_intercept: True` | Runs your settings as they are |
| Wrote outputs straight to `/dbfs/mnt/...` | Writes locally, then copies to `Outputs/<run_id>`, also when the run fails |

---

## How to run it now

**One-time setup in Databricks**
1. Upload the whole `codebase1_hierarchical_mmm` folder to the workspace.
2. Open the job `MDR_JOB_ID`:
   - point it at `codebase1_hierarchical_mmm/demo`;
   - add these parameters:

     | Parameter | Value |
     |---|---|
     | `config_file`, `data_file`, `prior_file`, `mapping_file`, `share_file` | empty |
     | `run_id` | `{{job.run_id}}` |
     | `base_path` | optional; see note 1 |

   - delete `modelling_type`.
3. Check that the app's `DATABRICKS_TOKEN` can read the job's settings and the
   codebase 1 folder.
4. Put the logo in `web/assest/aommm.png`, sync `web/`, and deploy the app.

**Every run**
1. **Input data** - upload the datacube, then **Upload**.
2. **Model settings** - change what you need.
3. *(optional)* **Mapping / share file** - upload each, then **Upload**.
4. **Prior file** - **Generate**, then **Use national** or **Use regional**; or
   upload your own. **Preview / Edit** if needed, then **Upload prior file to ADLS**.
5. **Run** - once every checklist line is ticked, press **Run Model**.
6. Download the outputs from the popup, or later via **Run outputs** with the
   run ID.

**On your laptop** (no Databricks needed):
- `python tests/run_all.py` runs all tests, including 3 that cover the app.
- To click through the real UI, see "Testing locally" in `README.md`.

---

## Dependencies

**The app** (`requirements.txt`; only `pyyaml` is new):
`streamlit~=1.54`, `pandas~=2.2.3`, `openpyxl`, `pyyaml`, `azure-identity`,
`azure-storage-file-datalake`. Kept from before: `azure-keyvault-secrets`,
`databricks-sql-connector`, `seaborn`, `matplotlib` (used by the disabled
feasibility page).

**App settings** (`app.yml`, unchanged): `CLIENT_ID`, `TENANT_ID`,
`CLIENT_SECRET`, `ACCOUNT_NAME`, `FILE_SYSTEM`, `DATABRICKS_TOKEN`, `MDR_JOB_ID`.
Databricks supplies `DATABRICKS_HOST`. Optional new ones:

| Setting | Purpose |
|---|---|
| `CODEBASE1_WORKSPACE_PATH` | fix which codebase 1 folder to use |
| `ADLS_ROOT` | change the `Secondary Modelling` folder name |
| `LOCAL_STORAGE_DIR`, `CODEBASE1_DIR` | local testing only |

**The job** (the serverless GPU environment): `pymc`, `arviz`, `numpyro`,
`jax[cuda]`, `numpy`, `pandas`, `xarray`, `scipy`, `matplotlib`, `netCDF4`,
`openpyxl`, `pyyaml`.

**Codebase 1** must be version **2026.09.29 or newer**. The app says so if it is older.

---

**Notes**
1. If the job cannot read `/dbfs/mnt/...` on serverless compute, set the job
   parameter `base_path` to a Unity Catalog volume path
   (`/Volumes/<catalog>/<schema>/<volume>/Secondary Modelling`). No code change.
2. The OCR mistakes fixed while rebuilding the code are listed in
   `_reference/OCR_FIXES.md`.
3. Full setup details are in `README.md`.
