# BRIDGE - the web app for codebase 1

A Streamlit app, hosted as a Databricks App. It is the **frontend** only:
**codebase 1** (`../codebase1_hierarchical_mmm`) is the backend. The app
checks and prepares the inputs. **Run Model** saves them in the run's own
folder in ADLS and starts the Databricks job, which runs codebase 1's
`demo.ipynb`.

```
① BMC and run  ->  ② Input data  ->  ③ Model settings  ->  ④ Mapping / share  ->  ⑤ Prior file  ->  ⑥ Run
  pick a BMC,       (datacube)       (config.yaml)         (optional)            1 generate       saves the inputs in
  name the run;                                                                   2 fill in        <BMC>/<run>/, starts
  its runs, each                                                                  3 choose, edit,  the job; status, cancel,
  reusable                                                                          paste          results, log, zip
```

Every run lives in `Secondary Modelling/<BMC>/<run name>/`: the inputs it
used, its outputs and `run_request.json` (who, when, reused from what, what
changed). Any run's inputs can be loaded back into the page, edited and run
again as a new run.

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

The config editor, the prior table and the run-folder layout come from
codebase 1 itself (its schema, and `mmm/app_job.py`'s folder names and name
rule), so a new setting or a new allowed value appears in the app by itself.
The app needs codebase 1 **2026.09.30.1 or later** (`MIN_CODEBASE` in
`src/codebase.py`): 2026.09.29.2 brought the run folders (`bmc_name` /
`run_name`), 2026.09.30.1 `app_access.yaml` (who may do what), the partial
run config and the live job log.
It says so plainly if the workspace copy is older.

---

## One-time Databricks setup

1. **Upload `codebase1_hierarchical_mmm/`** to the workspace, the whole
   folder. Its `demo.ipynb` is now the job notebook: fixed, and no longer
   writing into `config.yaml`.
2. **Edit the job `MDR_JOB_ID`.**
   - Task: the notebook `<workspace path>/codebase1_hierarchical_mmm/demo`.
   - Compute: as the old job, the all-purpose cluster `meridian_model_mmm_test`
     (DBR 17.3 LTS ML), with codebase 1's `requirements.txt` as a dependent
     library.
   - **Job parameters** (the job's side panel -> Job parameters). They must be
     JOB parameters: the old job had its keys as TASK parameters, and the app
     sends `job_parameters`. Delete the old task-level keys:

     | Parameter | Default | Meaning |
     |---|---|---|
     | `bmc_name` | *(empty)* | the BMC folder under `Secondary Modelling/` |
     | `run_name` | *(empty)* | the run's folder inside it |
     | `config_file` | *(empty)* | the settings file in the run's `Config/` |
     | `data_file` | *(empty)* | the datacube in the run's `Data/` |
     | `prior_file` | *(empty)* | the prior CSV in the run's `Prior/` |
     | `mapping_file` | *(empty)* | optional, the run's `Mapping/` |
     | `share_file` | *(empty)* | optional, the run's `Share/` |
     | `run_id` | `{{job.run_id}}` | the local work folder; recorded in `run_info.json` |
     | `base_path` | *(empty)* | empty = `/dbfs/mnt/testuat/Secondary Modelling` |

     With `bmc_name` and `run_name` both blank, the job uses the old shared
     layout (inputs in `Data/`, `Prior/` ...; outputs in `Outputs/<run_id>`),
     so hand runs and older runs still work. The app checks that the job has
     both parameters and says so in the Run checklist if it doesn't.
   - **Delete `modelling_type`.** The intercept is now `model.include_intercept`
     in Model settings.
3. **The token** (`DATABRICKS_TOKEN`) must be able to:
   - run the job (it already does);
   - **read the job's definition**;
   - **read the codebase 1 workspace folder**.
4. **`app.yml`** is unchanged. Set `CODEBASE1_WORKSPACE_PATH` only if the job is
   Git-sourced, or to pin a different folder.
5. **`requirements.txt`** now also installs `pyyaml` and `plotly` (the result
   charts; without it the app shows their tables).
6. Copy the real logo to `assest/aommm.png`. The header simply omits a missing
   logo.
7. Sync `web/` to the workspace and redeploy the app.

### Why `run_id = {{job.run_id}}`

The old notebook read the run id with `context.tags()`. On this compute that
raises `Py4JError: getContext().tags() is not a CommandContext accessor`. A job
parameter with the dynamic value `{{job.run_id}}` works on every compute type.
It is the same number the app gets back from *run-now*, and both record it
(`run_request.json` and `run_info.json`).

### If the job cannot read `/dbfs/mnt`

The job reads and writes under `base_path`, which defaults to
`/dbfs/mnt/testuat/Secondary Modelling`, as the old notebook did. The
`context.tags()` error suggests the cluster runs in Unity Catalog standard
(shared) access mode. There, and on serverless compute, DBFS mounts may be
blocked. If the job fails on a `/dbfs/mnt/...` path:
1. create a Unity Catalog volume over the same ADLS container;
2. set the job parameter `base_path` to
   `/Volumes/<catalog>/<schema>/<volume>/Secondary Modelling`.

Nothing else changes: the app keeps saving to the same container folders.

Outputs are written to local disk first and then copied to the run folder's
`Outputs/`, **also when a run fails**, so a failed run's `00_warnings` can be
read. (Writing `trace.nc` straight to FUSE storage is fragile.)

---

## Where the files go

One folder per run (`src/projects.py`; the names come from codebase 1's
`mmm/app_job.py`):

```
Secondary Modelling/                   ADLS, container-relative (the job: <base_path>/)
  <BMC>/
    <run name>/
      run_request.json                 written by the app: who, when, job run, reused from, what changed
      Config/config.yaml               the settings - every key
      Data/<datacube>                  the input datacube
      Prior/<prior>.csv                the feature-prior file (always saved as CSV)
      Mapping/<file>  Share/<file>     only when the run had them
      Outputs/                         written by the job: stage folders, run_info.json,
                                       job_log.txt, app_config.yaml
```

- The files are saved when **Run Model** is pressed, never block by block. A
  run name must not exist yet in its BMC; the app proposes one.
- Names (BMC and run) are one folder level: letters, digits, spaces, `_ - .`,
  starting with a letter or digit, at most 80 characters. The job enforces the
  same rule (`app_job.NAME_PATTERN`). The shared folders below cannot be BMC
  names.
- **Runs from before the run folders** stay where they were: inputs in the
  shared `Secondary Modelling/Data/`, `Prior/`, `Config/`, `Mapping/`,
  `Share/` (names with a `_YYYYmmdd-HHMMSS` suffix), outputs in
  `Secondary Modelling/Outputs/<run_id>/`. The app finds their inputs through
  the run's job parameters.
- `ADLS_ROOT` overrides `Secondary Modelling`.

---

## Using it

1. **① BMC and run.** Pick a BMC folder, or type a new name (its folder is
   created with the first run), and name the run. The block says where the run
   will be saved. Below it: **Runs in <BMC>**, newest first, showing status,
   when, by whom, **reused from** and **changed**. The list is re-read at most
   every 30 s; **↻ Refresh list** re-reads it now. Select a run to open its
   panel (below). **Reuse inputs** loads its datacube, settings and prior file
   (and mapping/share files) into the blocks below, and proposes a new run
   name (`baseline` → `baseline_2`, `run_7` → `run_8`).
2. **② Input data.** Choose the datacube (xlsx or csv), or keep a reused one
   (the block says which run it came from). Nothing in the file is renamed.
   Only what would stop the run is shown:
   - a date / region / KPI column named in Model settings is missing;
   - missing values, duplicate region × date rows, text features, dates that
     cannot be read.

   Then a one-line summary (rows, regions, period plan). Notes on single
   variables (dust, constant within a region) are not repeated here: the run
   writes them to `00_warnings`.
3. **③ Model settings.** codebase 1's `config.yaml`, with one widget per key
   and dropdowns for the allowed values. Switch on **Edit settings** to show
   them.
   - **Who may change what is codebase 1's `app_access.yaml`** (role-based;
     `settings.app_access` reads it, `config_editor.access()` applies it to
     the viewer's login e-mail, from the `X-Forwarded-Email` header):

     | Role | Settings | Admin tools* |
     |---|---|---|
     | `full_access` | every setting | yes |
     | `config_full_access` | every setting | no |
     | everyone else (analyst) | only those under `editable:` | no |

     *Reload codebase 1, the backend's folder path, Open in Databricks.

     For an analyst every other setting is fixed at the team's
     `config.yaml` value, and `config_editor.enforce_fixed` puts it back when
     a `config.yaml` is loaded (only its editable settings are taken; the app
     lists the ones it ignored), a run is reused or a run starts.
     `show_fixed: true` lists the fixed ones read-only. A file that cannot
     be read fixes every setting and grants nobody full access. The file is
     part of the backend's fingerprint, so a re-upload that only edits it
     still reaches open sessions.
   - A changed value is marked ● (changed from the team's base config), and a
     table under the block lists every change: *setting · base config · now*.
   - You can load or download a `config.yaml`, and reset to the base. A
     loaded file, or a reused run's settings, shows where the settings came
     from; its ✕ goes back to the team's `config.yaml`.
   - Validation runs on every change.
   - The paths and the run name are set by the job, so they are read-only.
   - The file saved with the run (and Download) holds **only the settings
     the person may change** (`settings_text(only=...)`); the job lays it
     over the team's `config.yaml` (`app_job.merge_config`).
     `run_request.json` records the person's role.
4. **④ Mapping and share files** (optional). Each has:
   - codebase 1's sample, and a template pre-filled with your datacube's
     variables;
   - a file picker, checked by codebase 1's own reader (names, regions, one
     contribution per vendor cell);
   - a ✕ on every file: the upload box's own ✕, or, for a reused file, the
     ✕ on its *Using … from …* line - the run then goes without one.
5. **⑤ Prior file**, in three steps (`docs/FEATURE_PRIOR_GUIDE.md` §5 in
   codebase 1 explains the arithmetic). Steps 1-2 sit in an expander, open
   while there is no prior file yet.
   1. **Generate.** Before you press it, the block names the case, from
      codebase 1's own `decide_case`: a = mapping with contributions, b =
      mapping without contributions + shares, c = shares only, d = neither
      (a blank template, one row per datacube variable). In cases a-c, once a
      prior table is loaded, **List only the variables of my current prior
      file** limits the output to those variables.
   2. **Fill it in.** One row per file, each saying what it is for:
      - `feature_priors_national.csv`: pooling **hierarchical**;
      - `feature_priors_regional.csv`: pooling **independent**, with one
        override row per region (only when the mapping has contributions);
      - `prior_calculation.xlsx`: the arithmetic;
      - the warnings, also shown as a table.

      A note lists what was filled and what was left blank on purpose
      (`global_prior_sd`, `pooling`, `center_mode`, ...). A generated prior
      file is a **draft** (`gen_drafts` in the session); nothing becomes the
      run's prior file until **Use**. Each prior file has:
      - **Download**: the draft as it is now, edits included;
      - **Preview / Edit**: the editor on the draft. Save keeps the dialog
        open and marks the draft *edited here, not used yet*; then **Use this
        file** or **Download CSV**;
      - **Use**: the draft becomes the run's prior file, and every blank cell
        of the feature rows gets the defaults (`app_functions.fill_blank_priors`):
        pooling `global`, sign_constraint `free`, global_prior_mean `0`
        (`0.05` for a positive/negative sign, whose mean is a size and must
        be above 0), global_prior_sd `1`, regional_sd_prior `0`. There are
        two exceptions that codebase 1's
        loader forces: a variable with per-region rows gets pooling
        `independent` (global cannot carry per-region priors), and a
        hierarchical row gets regional sd `0.5` (hierarchical needs more than
        0). Step 3 then says what was filled.
   3. **Your prior file**: the file picker (csv or xlsx), with **Preview /
      Edit** next to it. An uploaded file is used as it is: codebase 1's own
      defaults apply to its blanks. The editor has:
      - dropdowns for sign, pooling, centre and scale modes and the two bases,
        and a region dropdown listing your datacube's regions;
      - **Paste cells from Excel**: a text box for one cell or a block, with
        or without the header row. With the header, columns are matched by
        name, and rows by `variable` (and `region`) when that column is
        pasted. Without it, the block goes right and down from a top-left cell
        you pick. Values are checked (numbers, `0,5` read as 0.5, dropdown
        values in any case), and nothing changes if any cell is wrong;
      - **Fill a column**;
      - **Fill blanks with the defaults**: the same fill as Use, on request;
      - **☑ Remove all** / **☐ Keep all** under the grid: tick (or untick)
        Remove on every row - then untick the rows to keep and Save.

      The *Prior file in use* line has a ✕ whatever the file came from
      (uploaded, generated or reused); the upload box's own ✕ does the same
      for an uploaded file.

      Ctrl+V straight onto the grid works only where the browser lets the page
      read the clipboard (see "Why Ctrl+V on the grid can do nothing"). The
      table is validated by codebase 1's loader.
6. **⑥ Run.** A checklist: BMC and run name, datacube, settings, prior file,
   mapping/share if any, and the job's `bmc_name` / `run_name` parameters.
   After a reuse it also lists what changed since that run, e.g. *settings
   (2), prior file*, with a table of the changed settings (*setting · in <run>
   · now*). **Run Model** saves the
   inputs in the run's folder, writes `run_request.json`, then starts the job.
   If **nothing** changed since the run the inputs came from, it asks *Run it
   again anyway?* first. The run's panel stays on the page until
   **Dismiss**:
   - while it runs: the status every 5 s, **Cancel run**, and the **job log
     as it grows** - the job copies `job_log.txt` into `Outputs/` every 30 s
     (`app_job.LIVE_LOG_SECONDS`) and the panel re-reads it every 15 s, with
     the newest line above it;
   - on failure: the notebook's actual error, its **full** traceback, and the
     job log opened;
   - on success: which codebase version ran;
   - then **Download run (zip)** (the run folder: the inputs, `Outputs/` and
     `run_request.json` - built on click, on Streamlit's download thread, and
     cached on the app's disk; `trace.nc` is its own download), the complete **Job
     log** (`job_log.txt`, everything the run printed - never the copy read
     while it ran), **Results** and **Reuse inputs**. **↻ Re-read files**
     reads the run's files from ADLS again (rarely needed: a missing file is
     looked up again by itself after 20 s).
   - **Open in Databricks** only for `full_access` (`app_access.yaml`);
     everyone else reads the log in the app.

   **Results** (`src/charts.py`, Plotly; the viewer's light or dark theme):

   | View | What it shows | Reads |
   |---|---|---|
   | Fit | actual vs fitted, the 90% prediction band (one region) and the holdout shaded; R² within region, MAPE and holdout coverage as tiles | `04_fit/fit_metrics.csv`, `actual_vs_predicted.csv` |
   | Contributions | each driver's % of sales, coloured by pillar, by variable or by pillar, any region and period; the baseline core and residual as tiles (the three add to 100%) | `05_contributions/contribution_summary.csv` |
   | Decomposition | weekly sales stacked by baseline core and pillar, with the actual line; *Drivers only* zooms in | `05_contributions/contribution_timeseries.csv` |
   | Prior vs posterior | one point per variable (contraction vs shift; the flagged ones labelled) - click a point, or pick it, for its prior / data / posterior curves | `02_convergence/prior_posterior_contraction.csv` |
   | Convergence | the report as text | `02_convergence/convergence_report.txt` |
   | Warnings | the warnings table, each category explained | `00_warnings/all_warnings.csv` |

   Every chart has its table underneath, with a download of the file. A
   pillar's colour comes from its size over the whole run, so it is the
   same in every region, period and view; past seven pillars the rest are
   *Other*. Without Plotly the views show the tables.

   If the job does not start, the inputs stay saved in that run folder
   (listed as *not started*), the error is shown and a new run name is
   proposed.
7. **All recent runs** (an expander at the bottom): the job's runs from the
   Jobs API - every BMC, other people's runs, and runs from before the run
   folders. Select one (or type its job run ID) to open the same panel. For
   an old run, the zip gathers its outputs and the input files its job
   parameters name, and **Reuse inputs** works too.

A result file that is not there yet is looked up again after 20 s. Any other
storage error is shown as it is. A 403 usually means one identity cannot read
a folder the other created (see `changes.md`, Step 1).

### Why Ctrl+V on the grid can do nothing

`st.data_editor`'s grid (Glide Data Grid) handles Ctrl+V by asking the browser
for the clipboard (`navigator.clipboard.read()`). That needs the page's
"clipboard read" permission, and the grid never falls back to the paste
event's own data. When a company policy, a dismissed prompt or the site
settings block that permission, the paste is dropped silently, for one cell or
many. Copying out of the grid works, and so does pasting ONE value into a cell
you are editing (double-click, then Ctrl+V). For everything else, **Paste
cells from Excel** uses a plain text box, which needs no permission.

### How the page refreshes

Every block (BMC and run, input data, settings, mapping/share, prior, run,
all runs) is an `@st.fragment`: a click or an upload reruns only that block. A
block asks for a full rerun (`st.rerun()`) only when it changes what another
block shows:
- the BMC or the run name;
- a new or removed datacube, mapping or share file;
- a new prior table;
- a reused run;
- a started run;
- a change to one of the settings other blocks read (`config_editor.WATCHED`).

Values that "Reuse inputs" or a started run choose for the BMC and run-name
widgets are applied through `_pending_*` keys at the top of block ①, because a
widget's value cannot change after it has been drawn. CSS in `src/styles.py`
turns off Streamlit's grey "stale" fade and hides the Running/Stop badge.
Downloads use `on_click="ignore"`, so they don't rerun anything.

### Speed: worker processes, shared caches, zips

The app is ONE Python process; Streamlit runs each user's session as a
thread in it. Three things keep users from waiting on each other
(`changes.md`, Update 7):

- **Worker processes** (`src/codebase.py`). Codebase 1 captures printing and
  warnings for the whole process, so in-process calls into it share one lock.
  The heavy ones (`_HEAVY`: reading/checking the datacube, checking the prior,
  mapping and share files, generating priors) run instead in a
  `ProcessPoolExecutor` of `BRIDGE_WORKERS` (2) "spawn" processes, which load
  codebase 1 from the same folder and never take the lock. A spawned worker
  re-imports the program's entry script; that is safe for `streamlit run
  app.py` and `python -c`, so the pool is only used from those
  (`_spawn_safe`) - a test script would run again in every worker, so tests
  run in-process. A dead worker: that call runs in-process and the pool
  restarts; 3 failures in 10 min switch the workers off. The 5-minute check
  for a re-uploaded codebase 1 runs on a background thread
  (`start_background_refresh`, called from `app.py`), which also warms the
  workers.
- **Shared caches** (`src/perf.py :: SharedCache`): a thread-safe TTL + LRU
  cache with a byte budget, a per-key "single flight" (simultaneous askers
  share one computation) and deep copies on the way out (no session can
  change another's value). Used for the cluster state and run status (5 s),
  job parameters (1 min), run lists (30 s), BMC folders (1 min), recent runs
  (20 s), run files and parsed tables (until "Re-read files"), and every
  codebase 1 result, keyed by `perf.content_key(name, backend fingerprint,
  arguments)`. `BRIDGE_CACHE_TTL_SCALE=0` turns them off (the UI tests do).
- **Zips** (`src/runs.py`): `st.download_button(data=callable)` - Streamlit
  1.54 runs the callable when the button is clicked, on its download thread.
  `build_run_zip` lists the run (`files.list_tree_meta`: names, sizes, dates;
  cached 1 min), fetches the files in parallel and writes the zip to
  `BRIDGE_ZIP_CACHE_DIR` under a key made of that listing, so an unchanged
  run's next download is read from disk. `build_trace` does the same for
  `trace.nc`. The cache is kept under `BRIDGE_ZIP_CACHE_MB`.

`perf.timed` / `perf.log_timing` print `[timing] <call> <seconds> <where>` for
every call slower than `BRIDGE_TIMING_MIN` (0.3 s) - the app's Logs tab.

---

## Testing locally

```bash
python tests/run_all.py        # from "updating production code/" - includes:
#   test_v19_config_schema.py  codebase 1's schema, YAML writer, app_job (run folders, demo.ipynb)
#   test_v20_web_app.py        src/codebase.py (live loading from a fake workspace), src/projects.py
#                              (run folders, run lists, reuse, change detection), and the speed parts:
#                              perf.SharedCache, the background refresh, the worker pool (in a child
#                              `python -c` process - the same safe entry point as Streamlit's launcher)
#   test_v21_web_ui_smoke.py   the whole app.py flow against a scripted streamlit stand-in:
#                              BMC/run, paste from Excel, run, live log, results, zip, reuse,
#                              "nothing changed", app_access roles, every ✕, the chart data,
#                              and (step 19) the shared caches and the zip disk cache
#   test_v22_web_apptest.py    the same flow under REAL streamlit (AppTest) - the charts too,
#                              with plotly; SKIPs without streamlit
#   fixture_run_outputs.py     (not a suite) a synthetic Outputs/ tree in codebase 1's formats
```

v19-v21 need no Streamlit, Azure or Databricks. v22 runs the app in
Streamlit's own headless runtime, so API misuse fails as it would on
Databricks. Uploads are patched in, storage is a temp folder and the Jobs API
is faked. AppTest reruns the whole script, so it cannot click inside a dialog;
dialogs are checked to open cleanly. It needs Streamlit:

```bash
pip install "streamlit~=1.54.0" plotly
python tests/test_v22_web_apptest.py
```

To click through the real UI:

```bash
pip install streamlit~=1.54 openpyxl pyyaml plotly azure-identity azure-storage-file-datalake
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
│   ├── codebase.py        the ONLY module that imports codebase 1 (no Streamlit): cache, worker pool
│   ├── perf.py            shared caches (one copy for every session) and the timing log (no Streamlit)
│   ├── projects.py        run folders: paths, names, run lists, a run's inputs, what changed (no Streamlit)
│   ├── config_editor.py   Model settings
│   ├── app_functions.py   prior editor dialog, paste from Excel
│   ├── runs.py            the run panel (status, cancel, live/complete job log, results, zip, reuse) and All recent runs
│   ├── charts.py          the result charts: data from the output CSVs (pandas) + Plotly figures
│   ├── pages/model_setup_page.py   the page's blocks (one fragment each), reuse, Run Model
│   ├── generate_prior.py  hands generation to codebase 1
│   ├── validation.py      shows the datacube checks
│   ├── files.py           ADLS (or LOCAL_STORAGE_DIR): read, write, list; one cached client
│   ├── jobs.py            Jobs API: run-now, status, output, cancel, the job's parameters
│   └── ...                clusters, styles, header; feasibility pages (disabled)
└── _reference/            git-ignored: screenshots, OCR originals, OCR_FIXES.md
```

## What changed from the old app

- **Run folders**: every run lives in `Secondary Modelling/<BMC>/<run name>/`
  with its inputs and outputs, and can be reused. The per-file Upload buttons
  are gone; Run Model saves the files.
- **Priors**: the app no longer writes its own `b0/B0` table. codebase 1
  generates the file, and you can download, edit and re-upload it.
- **New inputs**:
  - Model settings (`config.yaml`) is edited in the app and saved with the
    run. The Intercept checkbox is gone; it is now `model.include_intercept`.
  - Mapping and share files: a sample, a template and validation.
- **Job notebook** (`demo.ipynb`):
  - the `context.tags()` Py4JError is fixed;
  - it no longer rewrites the shared `config.yaml`, which also stopped
    concurrent runs from overwriting each other's settings;
  - it no longer forces `include_intercept: True`;
  - it reads the run's folder (`bmc_name`, `run_name`), writes outputs
    locally, then publishes them to the run's `Outputs/`;
  - it returns its status to the app.
- **Output download**: it used to fetch only the first file of a run (a
  `return` inside the loop), and every run shared one `./local.zip`. Now the
  run's zip holds its inputs and all its outputs.
- **OCR fixes** from the screenshots are listed in `_reference/OCR_FIXES.md`.
- **Change log** for the rounds after the first deployment: `changes.md`.
