# What changed in the BRIDGE app

The app still works the same way:
1. you choose the files and the settings;
2. you press **Run Model** - the files go to ADLS;
3. a Databricks job runs the model.

What changed:
- it now runs **codebase 1**, not the old in-house model;
- every run has its own folder, `Secondary Modelling/<BMC>/<run name>/`, and
  any run's inputs can be reused;
- it has a few new sections;
- several bugs are fixed.

**Codebase 1 is not copied into the app.** The app reads it live from the
workspace, from the same folder the job runs. Re-upload codebase 1 and the
app, the job and anyone running it by hand all get the new version. The app
needs no redeploy.

---

## Update 6 - codebase 1 checked under real PyMC (2026.10.01.1)

Codebase 1 was run end to end with PyMC 6.3 / ArviZ 1.3 - the versions the
job installs (its `requirements.txt` pins nothing; the client runs already
used PyMC 6.2 / ArviZ 1.2-1.3). Besides the `independent` + `free` crash
fixed in Update 5 (now confirmed on the real PyMC), it found:

| Problem | Effect | Now |
|---|---|---|
| `posterior_summary_full.csv` and the report's "worst parameters by R-hat" table put region x feature rows under the wrong labels with ArviZ 1.x, whenever the model has no intercept and no trend - the team's `config.yaml` | a reader of those two could attribute a coefficient's R-hat / ESS / mean to the wrong region or feature. The coefficient report, contributions and contraction file were always right | one parameter at a time, unrounded (the old rounding also left small raw-unit coefficients two or three digits) |
| `energy_plot.png`, `trace_worst_rhat.png` and the report's BFMI line were missing on ArviZ 1.x | three convergence diagnostics silently absent from every recent run | drawn and computed directly from the trace |
| A negative `global_prior_mean` on a `negative` variable (`-0.08`) was replaced by 0.05 | the model used a different prior than the one written | read as its size (0.08), with a note |
| Library deprecation notices filled `00_warnings` as "Uncategorised" | noise in the loudest category | kept apart in `library_notices.csv` |

A new suite, `tests/test_v24_real_pymc.py`, samples the real model and checks
all of this. It skips where PyMC is not installed; run it after any PyMC or
ArviZ upgrade.

**To get these changes:** re-upload the whole `codebase1_hierarchical_mmm`
folder (Step 3). It is now **2026.10.01.1**; the app's header shows it. The
app and the job need no change.

---

## Update 5 - deselect anything, access roles, the complete log, charts

| You asked for | Now |
|---|---|
| Deselect the feature prior after **Use this file** or **Reuse inputs** | The *Prior file in use* line has a **✕**, whatever the file came from (uploaded, generated or reused). The prior's upload box empties too |
| A ✕ on every file (mapping, input, share, config ...) | A file in an upload box is deselected with the box's own ✕: the datacube, prior, mapping, share file and config.yaml (taking an uploaded config.yaml out puts the team's settings back). A file that did not come from a box (a reused run's, a generated one) has a *Using … from …* line with a **✕**. Reused settings say *Settings from <run>*, with a **✕** that goes back to the team's config.yaml |
| **Remove all** in the prior editor's Remove column | **☑ Remove all** under the grid ticks every row; untick the rows to keep and press **Save Changes**. **☐ Keep all** unticks them all again. Unsaved edits in the grid are kept |
| A blank `global_prior_mean` should be 0 | The fill rule (**Use**, and **Fill blanks with the defaults**) now fills it with **0** - or **0.05** for a variable with a positive or negative sign, because codebase 1 takes that mean as a size and does not allow 0 there |
| **Reload codebase 1** only for me | Shown only to the people under `full_access:` in codebase 1's new `app_access.yaml` - your company e-mail is there. The same goes for the backend's folder path and **Open in Databricks** |
| Remove **Open in Databricks** (the log is in the app), and make sure the complete log is shown | Nobody else sees it any more. The run panel shows the **complete** job log - also while the run runs: the job now copies its log into the run's `Outputs/` every 30 seconds, and the panel re-reads it every 15, with the newest line above it. A failed run shows its **full** traceback (it used to be cut to the last 60 lines), and its job log opens by itself |
| One RBAC file that merges the config one with the above, and two full-access lists | `codebase1_hierarchical_mmm/app_access.yaml` replaces `config_ui.yaml`. It has `full_access` (every setting and the admin tools), `config_full_access` (every setting), `editable` (what everyone else may change) and `show_fixed` |
| Nobody can change a non-editable setting, even by uploading a config.yaml; the run folder keeps only the editable settings | An uploaded config.yaml gives only its editable settings, and the app lists the ones it ignored. The run folder's `Config/config.yaml` now holds only the settings that person may change. The job lays it over the team's `config.yaml`, so the fixed settings always come from there. `run_request.json` records the person's role |
| Charts instead of the CSV tables - the important ones, not cluttered | The results are six views: **Fit** (actual vs fitted, the 90% band, the holdout shaded, and the headline fit numbers as tiles); **Contributions** (each driver's share of sales, coloured by pillar - by variable or by pillar, for any region and period); **Decomposition** (weekly sales split into the baseline core and each pillar - *Drivers only* zooms in); **Prior vs posterior** (every variable as a point, contraction against shift, with the ones to read labelled - **click a point** for its prior, data and posterior curves); **Convergence** (the report as text); **Warnings**. Every chart has a *Table* under it with its numbers and a download of the file. A pillar has the same colour in every view |

Also found and fixed:
- **A run with a `pooling: independent` + `sign_constraint: free` variable could
  not start.** codebase 1 gave two model variables the same name
  (`beta_ifree`), and PyMC refuses that. The fill rule gives exactly this
  combination to a variable that has per-region rows and no sign, so a run
  would have hit it. Fixed in codebase 1 (`mmm/modelling/model.py`). The new
  suite `tests/test_v23_model_buckets.py` builds every pooling × sign
  combination.
- A finished run's panel could say *job_log.txt is not there (yet)* for 20
  seconds if it had been open while the run was going. It now reads the
  complete log at once.

**To get these changes:**
1. Re-upload the whole `codebase1_hierarchical_mmm` folder (Step 3). It is now
   **2026.09.30.1**, and the app needs it. `app_access.yaml` replaces
   `config_ui.yaml`: nothing reads the old file, so delete it from the
   workspace copy if it is still there.
2. In `app_access.yaml`, add anyone else who should see every setting
   (`config_full_access`) or everything (`full_access`). A change takes
   effect within 5 minutes of re-uploading the file, or at once with
   **Reload codebase 1**.
3. Copy `web/` to the app's source folder again and press **Deploy**:
   `requirements.txt` now has `plotly`, for the charts. The job needs no
   change.

---

## Update 4 - which settings an analyst can change (`config_ui.yaml`, now `app_access.yaml`)

| You asked for | Now |
|---|---|
| A file in codebase 1 where the coder decides which `config.yaml` settings analysts may change; the rest use the defaults | `codebase1_hierarchical_mmm/config_ui.yaml`, next to `config.yaml`. List a setting under its section and analysts can change it in **③ Model settings**. Every other setting is **fixed** at its value in the team's `config.yaml`: it gets no widget, and it is put back when an analyst loads another `config.yaml`, reuses an older run, or starts a run. The app says which fixed settings it kept |

How the file works:
- **An allow-list.** A setting codebase 1 adds later stays fixed until you
  list it. A section can be opened whole (`sampler: all`), or everything
  (`editable: all`).
- **Spelling is checked.** A name codebase 1 does not have is shown as a
  warning in the app and ignored. A file that cannot be read fixes EVERY
  setting (and says why) - it never opens them.
- **`show_fixed: true`** lists the fixed settings read-only, with their values,
  under "Fixed by the team". It is off in the shipped file.
- **`full_access:`** - the Databricks login e-mails of people (e.g. whoever
  maintains `config.yaml`) who may see and change every setting in the app.
- The shipped file opens 18 of the 107 settings:

  | Section | Opened |
  |---|---|
  | data | `sheet`, `date_format`, `dv_aggregation`, `national_basis` |
  | model | `likelihood`, `fourier_order`, `include_trend`, `include_intercept` |
  | run | `date_col`, `region_col`, `dv_col`, `holdout_periods`, `holdout_fraction` |
  | sampler | `draws`, `tune`, `target_accept` |
  | output | `period_split` |
  | cv | `enabled` |

  Edit it to suit the team.
- It travels with codebase 1: re-upload the folder and the app follows within
  5 minutes (or at once with **Reload codebase 1**). No app redeploy.

**To get these changes:**
1. Re-upload the whole `codebase1_hierarchical_mmm` folder (Step 3). It is
   now **2026.09.30**, and the app needs it.
2. Copy `web/` to the app's source folder again and press **Deploy**. The job
   is unchanged.

---

## Update 3 - the generated prior file is a draft; readable setting changes

| You asked for | Now |
|---|---|
| "Open in the editor" made the generated file the prior file straight away - wrong, it is not filled in yet | A generated prior file is a **draft**. Each one offers: **Download** (fill it in Excel, choose it in step 3); **Preview / Edit** (fill it in the app - edit, paste from Excel, fill a column - then **Use this file** or **Download CSV**; saving keeps the dialog open and marks the draft *edited here, not used yet*); **Use** (as it is). Nothing becomes the run's prior file until you press Use. Step 3 still takes your own file |
| Blanks: pooling global, sign constraint free, prior sd 1, regional sd 0, the rest as is | **Use** fills every blank cell of the feature rows with these, and step 3 says what it filled, e.g. *pooling → global (32), global_prior_sd → 1 (32)*. Two exceptions, because codebase 1 would otherwise reject the file: a variable with per-region rows gets pooling **independent** (global cannot carry per-region priors), and a row set to **hierarchical** gets regional sd **0.5** (hierarchical needs more than 0). Region rows are left as they are. The editor's new tab **Fill blanks with the defaults** does the same for any prior file, uploaded ones included - but only when you press it: an uploaded file is never changed by itself |
| Changed settings were a comma-separated line | A table: *setting · base config · now* under Model settings, and *setting · in <reused run> · now* in the Run block |
| What is the Refresh button that appears after Run Model? | **↻ Refresh list**, in ① next to *Runs in <BMC>*, reads the BMC's runs and their status again. The list is otherwise re-read at most every 30 s, while the panel of a running run updates itself every 5 s. **↻ Re-read files**, in a finished run's panel, reads that run's files from ADLS again - rarely needed. Both buttons now say what they do |

**To get these changes:** copy `web/` to the app's source folder again and
press **Deploy**. codebase 1 and the job are unchanged since Update 2.

---

## Update 2 - one folder per run, reuse, the run's zip, paste

| You asked for | Now |
|---|---|
| A BMC name, inside it the run name, inside that the config, feature prior, datacube and outputs | Every run gets its own folder: `Secondary Modelling/<BMC>/<run name>/` with `Config/config.yaml`, `Data/<datacube>`, `Prior/<prior>.csv` (and `Mapping/`, `Share/` when used), `Outputs/` (written by the job) and `run_request.json`. Block **①** at the top: pick a BMC (or type a new one) and name the run |
| Show a BMC's runs; reuse a run's datacube, config and feature prior, edit them, run again | Block ① lists the BMC's runs: status, when, by whom, which run they reused, what changed. Select one to see its results, job log and zip. **Reuse inputs** loads its datacube, settings and prior file (and mapping/share files) into the page. Change what you need; **Run Model** saves them as a **new** run, and a new run name is proposed (`baseline` → `baseline_2`) |
| A warning when nothing was edited | If the datacube, settings, prior, mapping and share files are all exactly those of the run you reused, **Run Model** asks *"Nothing has changed - run it again anyway?"*. With the same seed (`sampler.seed`) the result would be the same. The Run block also lists what did change: *"Changed since baseline: settings (2), prior file"* |
| Run logs | The BMC's run list is the log of runs. Each run's `run_request.json` records who started it, when, the job run, which run it came from and what changed (setting by setting). Each run keeps its `Outputs/job_log.txt` |
| Download a run as a zip: the feature prior, config and datacube used, and the outputs | **Prepare run zip** in every run's panel: `Config/`, `Data/`, `Prior/` (and `Mapping/`, `Share/`), `Outputs/` and `run_request.json`. A run from before the run folders gets its outputs plus the input files its job parameters name |
| Ctrl+C in Excel, Ctrl+V in the prior editor did nothing, for one value or many | See *Why Ctrl+V did nothing* below. **Paste cells from Excel** (in Preview / Edit) always works: one cell or a block, with or without the header row. With the header row, rows are matched by variable name, so the row order in Excel does not matter |
| The prior's Upload-to-ADLS button sat far from its file picker | There are no per-file Upload buttons any more (your choice): each block only checks its file, and **Run Model** saves everything into the run's folder. **Preview / Edit** now sits next to the prior's file picker. The blocks are numbered ① to ⑥, and a block's title gets a ✅ when it is ready |

**To get these changes:**
1. Upload the whole `codebase1_hierarchical_mmm` folder again (Step 3), while
   no run is in progress. It is now version **2026.09.29.2**, and the app
   needs it: an older job would write the outputs where the app does not look.
   The header should say 2026.09.29.2; if not, press **Reload codebase 1**.
2. Add two **job parameters** to the job, both with an empty default:
   `bmc_name` and `run_name` (Jobs & Pipelines → the job → Job parameters →
   Edit). Until they exist, the Run checklist says so.
3. Copy `web/` to the app's source folder again (Step 6.1; keep `assest/`)
   and press **Deploy**.
4. In ADLS, the job now writes into folders the app creates, and the app reads
   what the job writes there. See Step 1: nothing to do with the *Storage Blob
   Data Contributor* role; with folder permissions (ACLs), use default
   permissions on `Secondary Modelling`.

Older runs stay where they are (`Secondary Modelling/Outputs/<run id>`, with
their inputs in the shared `Data/`, `Prior/` ... folders). **All recent runs**,
at the bottom of the page, still opens them, zips them and can reuse their
inputs.

**Why Ctrl+V did nothing.** The editor's grid (Streamlit's data editor) does
not take the paste from the keyboard itself. It asks the browser for the
clipboard, and the browser only answers if you allowed the page to *"see text
and images copied to the clipboard"*. If that is blocked - by a company
policy, a permission prompt that was missed or dismissed, or the site
settings - the paste is dropped silently, for one cell or many. There are
three ways round it:
- **Paste cells from Excel** in the editor: a normal text box, which needs no
  permission;
- for one value: double-click the cell, Ctrl+V, Enter;
- allow the clipboard for the app's site (the icon left of the address bar →
  Site settings → Clipboard → Allow), if your company permits it.

---

## Update 1 - after the first deployment

| You reported | Now |
|---|---|
| The datacube check listed every constant variable as one long line | Only what would **stop** the run is shown (missing columns, missing values, duplicates, text columns, bad dates), plus a one-line summary. Per-variable notes are in the run's `00_warnings` |
| "Use national / Use regional" was unclear | The **Prior file** block is now 3 steps: **1 Generate** (it says beforehand which case applies), **2 Download and fill in** (each file explained: national = pooling hierarchical, regional = pooling independent; what is filled and what is left blank), **3 Upload** the filled file. "Open in the editor" is the in-app alternative to Excel |
| The page went grey, with STOP, on every upload | Each block refreshes on its own: a click or an upload redraws only that block. There is no grey-out and no Running/Stop badge; slow steps show a spinner. Downloads no longer reload the page. The ~100 settings widgets are drawn only while **Edit settings** is on |
| Expanded warnings were squeezed into one column | Warnings are a full-width table (severity, category, count). Pick a category to see its rows and codebase 1's explanation |
| Closing the run popup lost the run | No popup: the run's panel **stays on the page** (status every 5 s, **Cancel**) until you dismiss it. **Runs** lists the job's recent runs, from any session, to reopen at any time |
| Run outputs said "not found", although the job had written them | The old viewer remembered "not found" for good if it was opened before the run finished. A file that isn't there yet is now looked up again. Any other storage error (e.g. a permission error) is shown as it is. See also Step 1 - ADLS below |
| Show the job's log | Every run writes `job_log.txt` (everything the job printed) next to its outputs. The run panel shows it, and it can be downloaded. Needs codebase 1 **2026.09.29.1** - re-upload it |
| Remove paste / preview of config.yaml | Gone. Load, Download and the settings widgets remain |

**To get these changes** (Update 2's steps above include them):
1. Upload the whole `codebase1_hierarchical_mmm` folder again, over the one
   the new job runs (Step 3), while no run is in progress. The app's header
   should then say codebase 1 **2026.09.29.1**; press **Reload codebase 1** if
   it still shows the old version.
2. Copy `web/` to the app's source folder again (Step 6.1; keep `assest/`)
   and press **Deploy**.
3. If a finished run's panel shows a permission error instead of its results,
   see "read what the job writes" in Step 1.

**Why "not found" happened.** The job had written the files: the app and the
job use the same folder, `Secondary Modelling/Outputs/<run id>`. The old viewer
looked for them once. If it was opened before the run finished (easy to do
after closing the run popup), it remembered "not found" for the rest of the
session. It also reported every storage error as "not found". Now the files
are fetched only when the run has finished, a miss is retried, and any other
error is shown as it is.

---

## The four pieces: before and now

### 1. Job trigger (`src/jobs.py`)

| Before | Now |
|---|---|
| Sent 3 parameters: `prior_file`, `data_file`, `modelling_type` (the Intercept tick box) | Sends 5 file names (`config_file`, `data_file`, `prior_file`, `mapping_file`, `share_file`) and where the run lives: `bmc_name`, `run_name` |
| The notebook ignored `modelling_type` and always switched the intercept on | No tick box. The intercept is a normal setting (`model.include_intercept`) in Model settings |
| Only used the `status` and `cancel` calls. `get_task_run_id` was an unfinished stub | Also reads the notebook's result: the real error message if the run fails, the codebase version if it succeeds |
| URL had a double slash (`//api/2.1`) | Fixed |

Unchanged: only file **names** are sent. The job finds the files in the run's
folder, `Secondary Modelling/<bmc_name>/<run_name>/`.

### 2. Run Model button (`src/pages/model_setup_page.py`)

| Before | Now |
|---|---|
| Enabled when the prior and the input data were uploaded | Enabled when a checklist is complete: BMC and run name, datacube checked, settings valid, prior file **validated**, mapping/share file valid if you added one, and the job has the `bmc_name` / `run_name` parameters |
| Sent the files straight to the job | First saves the datacube, the settings (a config file with the settings you may change - the job fills in the rest from the team's `config.yaml`) and the prior file (and mapping/share files) into the run's folder, and writes `run_request.json`. Then it starts the job. If nothing changed since the run you reused, it asks first |
| A popup showed the run; closing it lost the run | A panel under **Run Model** shows the run and stays until you press **Dismiss**: the status every 5 seconds, **Cancel run**, the job log as it grows (and **Open in Databricks** for full access) |
| The popup only knew Pending / Running / Success / Terminated | Also handles Queued, Failed and Internal error, and shows why a run failed |
| After the run: a Download button | After the run: the run's zip, the complete **job log**, and the results as charts - fit, contributions, decomposition, prior vs posterior - with the convergence report and the warnings (Update 5) |

A BMC's runs are listed in block ①, and **All recent runs**, at the bottom of
the page, lists every run of the job: every BMC, runs started by other people
or in other sessions, and runs from before the run folders. Click one (or type
its job run ID) to open the same panel.

### 3. ADLS upload (`src/files.py`, `src/projects.py`)

| Before | Now |
|---|---|
| Kept the original file name (overwrite on) - two people uploading `prior.csv` overwrote each other | Each run has its own folder, so names never clash; the files keep their names |
| Two shared folders: `Prior/`, `Data/` | One folder per run, `<BMC>/<run name>/`, with `Config/`, `Data/`, `Prior/` (and `Mapping/`, `Share/`) and `Outputs/` |
| Uploaded whatever you picked, file by file | Saved when you press **Run Model**, and only files that passed the checks |
| Prior had to be CSV | Prior can be CSV or Excel; it is always saved as CSV |

The login (`ClientSecretCredential`) is unchanged. The app now keeps one ADLS
client instead of logging in again for every file.

### 4. Download (`src/files.py`, `src/app_functions.py`)

| Before | Now |
|---|---|
| **Bug:** `download_folder` stopped after the FIRST file, so the zip held one output | Downloads every file of the run |
| Every run and every user shared one `./local` folder and `./local.zip` | One folder and one zip per run, deleted after use |
| Only after a successful run | Also after a failed run - its warnings usually explain why it failed |
| Zip held the outputs only | The run's zip also holds the inputs it used: `Config/`, `Data/`, `Prior/` (and `Mapping/`, `Share/`) next to `Outputs/` |
| Zip included everything | `trace.nc` (large) only if you tick **Include trace.nc** |
| "Model File" popup read a fixed `Model/summary.csv` | Any run (in its BMC's list, or in **All recent runs**) shows its results (warnings, convergence, fit, contributions, coefficients), its job log and its zip |

---

## New in the app

- **BMC and run (①).** Pick a BMC folder, or type a new name, and name the
  run. The page says where it will be saved:
  `Secondary Modelling/<BMC>/<run name>/`. Below: the BMC's runs, each with its
  results, job log, zip and **Reuse inputs**.
- **Reuse a run.** Its datacube, settings and prior file (and mapping/share
  files) are loaded, and each block says which run its file came from. Change
  what you need and run it as a new run. If nothing changed, **Run Model** asks
  first.
- **Model settings.** Edit codebase 1's `config.yaml` in the app.
  - Switch on **Edit settings** to show them. Dropdowns list the allowed
    values; hovering shows the help.
  - You can load, download or reset the file.
  - You see and change the settings `app_access.yaml` gives you; the rest
    are the team's. The job fills in the file paths and the run name.
- **Mapping and share files** (optional).
  - Download a sample, or a template pre-filled with your datacube's variables.
  - Choose the file; it is checked, and saved with the run.
- **Prior file**, in 3 steps. The old app built its own `b0/B0` table; now
  codebase 1 builds the prior file.
  1. **Generate.** Before you press it, the block says which case your files
     lead to:

     | Case | You gave | The means come from |
     |---|---|---|
     | a | a mapping file with the vendor's contributions | the contributions, inverted |
     | b | a mapping file without contributions, and a share file | the share file |
     | c | a share file only | the share file |
     | d | neither | nowhere: a **blank** file, one row per datacube variable, for you to fill in |

  2. **Fill it in.** You get the prior file(s), `prior_calculation.xlsx` (the
     arithmetic) and the generator's warnings:
     - `feature_priors_national.csv` is for **pooling: hierarchical** (the
       regions share one prior);
     - `feature_priors_regional.csv` is for **pooling: independent** (each
       region gets its own prior). It is only written when the mapping file
       has contributions.

     A generated prior file is a draft: columns such as `global_prior_sd` are
     blank on purpose, and the block lists what to consider for each one. For
     each file: **Download** it (fill it in Excel, choose it in step 3),
     **Preview / Edit** it in the app (then **Use this file** or download
     it), or **Use** it as it is. Use fills the blanks with the defaults
     (pooling global, sign free, mean 0, sd 1, regional sd 0; see Updates 3
     and 5).
  3. **Choose** your filled file, or keep the one you used; **Preview / Edit**
     sits next to it. It is checked, and the run can start only once codebase 1
     says it is valid.

  **Preview / Edit** has dropdown columns, **Paste cells from Excel**, **Fill a
  column** and **Fill blanks with the defaults**. For why Ctrl+V straight onto
  the grid can do nothing, see Update 2.
- **Datacube check.**
  - It uses the column names from Model settings and never renames anything.
    The old check lower-cased the first 3 columns.
  - It shows only what would stop the run: missing columns, missing values,
    duplicate rows, text columns and dates it cannot read. Notes about single
    variables (constant, near zero) are in the run's `00_warnings` folder.
- **Run panel, the BMC's runs, All recent runs.** Described under "2. Run
  Model button" above. The **job log** is `job_log.txt`, in the run's
  `Outputs/` in ADLS: everything the job printed. The job copies it there
  every 30 seconds while it runs, so the panel shows it live.
- **Results.** Six views of a finished run - Fit, Contributions,
  Decomposition, Prior vs posterior, Convergence, Warnings - each chart with
  its numbers in a table underneath (Update 5).

## Changes in the job notebook (`codebase1_hierarchical_mmm/demo.ipynb`)

| Before | Now |
|---|---|
| Crashed at `context.tags()` (Py4JError) | The run id comes from a job parameter: `run_id = {{job.run_id}}` |
| Rewrote the shared `config.yaml` on every run - two runs at once clashed, and hand runs got the app's settings | Never touches `config.yaml`; each run gets its own copy |
| Forced `include_intercept: True` | Runs your settings as they are |
| Read the files from the shared `Data/`, `Prior/` folders | Reads them from the run's folder `<bmc_name>/<run_name>/` (both parameters blank: the shared folders, as before) |
| Wrote outputs straight to `/dbfs/mnt/...` | Writes locally, then copies to the run folder's `Outputs/`, also when the run fails (old layout: `Outputs/<run_id>`) |

---

## How to run it now

Keep the old app and job working while you test: create a **new** job and a
**new** app next to them, in the same workspace. Both apps use the same ADLS
container. The new app saves each run in its own folder, so the two never
overwrite each other.

| | Old (leave as it is) | New |
|---|---|---|
| Backend folder | `/Workspace/Modelling/Backend/mmm_v4/codebase1_hierarchical_mmm` | `/Workspace/Modelling/Backend/mmm_v5/codebase1_hierarchical_mmm` |
| Job | the `HRM` task, runs `mmm_v4/.../demo` | a new job, runs `mmm_v5/.../demo` |
| App | `model-app` (source `/Workspace/Users/<creator>/model-app`) | `model-app-v2` (source `/Workspace/Users/<you>/model-app-v2`) |
| ADLS | `Secondary Modelling/Data`, `Prior`, `Outputs` | one folder per BMC and run: `Secondary Modelling/<BMC>/<run name>/`. The old shared folders are only read, for older runs |

`mmm_v5` and `model-app-v2` are only suggested names.

### Step 1 - Check your permissions

**In Databricks.** Ask a workspace admin for anything missing.

| You need | Where to check |
|---|---|
| to create apps | Compute → Apps: the **Create app** button works |
| to create jobs, and **Can Attach To** on the cluster `meridian_model_mmm_test` | Compute → the cluster → ⋮ → Permissions |
| **Can Edit** on `/Workspace/Modelling/Backend` (to add `mmm_v5`) | Workspace → the folder → Share |
| **Can Manage** on the secret scope `am03-mmm…` that holds all seven of `model-app`'s secrets. Giving a new app access to a secret needs *Can Manage* on its scope | `model-app` → Settings → Resources: the first box on each row is the scope (the full name also shows in `databricks secrets list-scopes`). If you don't have it, ask the scope's owner to add the resources to your app for you |
| to create personal access tokens | Settings → Developer → Access tokens |

**The token owner.** The app talks to Databricks as the person whose token is
in `DATABRICKS_TOKEN`. That person needs:
- **Can Manage Run** on the new job (start it, watch it, cancel it, read its result);
- **Can Read** on the new backend folder (the app loads codebase 1 from it);
- **Can Attach To** on the cluster (the status badge), and **Can Restart** if
  the app's **Start Cluster** button should work.

**In Azure (ADLS).**
- The service principal behind `CLIENT_ID`, which the app uploads and
  downloads with, can write to the container (`FILE_SYSTEM`) in the storage
  account (`ACCOUNT_NAME`). Either:
  - the role **Storage Blob Data Contributor** (storage account → Access
    control (IAM)); or
  - folder permissions (ACLs) that let it create and list folders under
    `Secondary Modelling` (the BMC and run folders).

  It already writes `Data` and `Prior` for the old app.
- **The app and the job share each run folder.** The app creates
  `Secondary Modelling/<BMC>/<run name>/` and saves the inputs in it. The job
  reads those inputs and writes `Outputs/` into the same folder, through the
  cluster's mount. The app then reads `Outputs/`. So each identity needs access
  to folders the other one created:
  - with the **Storage Blob Data Contributor** role for both, there is nothing
    to do;
  - with ACLs, and a mount that uses a different identity, give **default**
    permissions on `Secondary Modelling`: the app's service principal
    **Read**, **Write** and **Execute**, and the mount's identity **Read**,
    **Write** and **Execute**. Azure portal → the storage account → Containers
    → the container → `Secondary Modelling` → ⋯ → Manage ACL → Default
    permissions. New BMC and run folders inherit them; folders that already
    exist do not.

  If it is missing, a run fails at once reading its inputs, or a finished
  run's panel shows a permission error (403) instead of the results.
- Its client secret has not expired (Azure portal → App registrations → the
  app → Certificates & secrets).
- The cluster can still read the mount the job uses. In a notebook on
  `meridian_model_mmm_test`, run:

  ```python
  display(dbutils.fs.ls("/mnt/testuat/Secondary Modelling"))
  ```

### Step 2 - Credentials

Nothing secret goes into the code or `app.yml`, only the names of secrets.

| App setting | For the new app |
|---|---|
| `CLIENT_ID`, `CLIENT_SECRET`, `TENANT_ID` | the same secrets as `model-app`: `model-client-id`, `model-secret`, `am03-mmm-devtest-sp-tenantid` |
| `ACCOUNT_NAME`, `FILE_SYSTEM` | the same: `model-account-name`, `model-file-system` |
| `DATABRICKS_TOKEN` | the same `model-adb-token`, if its owner has the permissions above; otherwise a new token (see below) |
| `MDR_JOB_ID` | the **new** job. `model-app` keeps its job ID as a secret (`model-job-id`), which holds the OLD job's ID - do **not** reuse it. For the new app, add the job as a *Job* resource in step 6: Databricks fills in the ID, with no secret to write. (Or store the new ID as a secret `model-job-id-v2`.) |

`model-app` gives its app *Can manage* on these secrets (*Can read* on
`TENANT_ID`). The app only reads them, so *Can read* is enough for the new one.

**Whose token?** A token belongs to one person, and the app acts as that
person. Only the creator of `model-adb-token` knows whose it is. If it isn't
yours, a new token of your own is simpler. You create the job, upload the
backend folder and use the cluster, so you already hold everything the token
owner needs.

**A new token:**
1. Settings → Developer → Access tokens → **Generate new token**. Add a
   comment ("bridge app") and a lifetime. Copy the token: it is shown only once.
2. Store it as a secret:
   - in `am03-mmm…`: if that scope is backed by an Azure Key Vault (its key
     names suggest so), add the secret in that Key Vault in the Azure portal;
     if it is Databricks-managed, run
     `databricks secrets put-secret <scope> model-adb-token-v2`;
   - **or** in a scope of your own, if you can't write to `am03-mmm…`. You
     manage any scope you create:

     ```
     databricks secrets create-scope bridge-app
     databricks secrets put-secret bridge-app adb-token
     ```
3. Note when it expires. On that date the app can no longer start the job.

**The app's own service principal.** Every app gets one automatically; see its
Authorization tab (for `model-app` it is `app-y8z6nb model-app`). Databricks
puts its credentials in `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`,
and the resources you add grant permissions to it. The app's code does not use
it: it calls Databricks with `DATABRICKS_TOKEN`, so the **token owner's**
permissions are the ones that count.

### Step 3 - Upload the backend

Upload the **whole** `codebase1_hierarchical_mmm` folder to
`/Workspace/Modelling/Backend/mmm_v5/`. Leave `mmm_v4` alone: the old job uses
it. The app needs version **2026.09.30.1** or later: it refuses an older one
(2026.09.29.2 brought the run folders, 2026.09.30.1 `app_access.yaml` and the
live job log).

For a later update, upload the whole folder again the same way, while no run
is in progress. If only some files get replaced, the app's header warns that
codebase 1 is "only partly updated" and names the modules still on the old
version.

### Step 4 - Create the job

1. Jobs & Pipelines → open the old job → ⋮ → **Clone job**, and rename the copy.
2. In its task, change:

   | Field | New value |
   |---|---|
   | Path | `/Workspace/Modelling/Backend/mmm_v5/codebase1_hierarchical_mmm/demo` |
   | Compute | `meridian_model_mmm_test`, as before |
   | Dependent libraries | `/Workspace/Modelling/Backend/mmm_v5/codebase1_hierarchical_mmm/requirements.txt` |
   | Parameters | **delete** `prior_file`, `data_file` and `modelling_type` here |

3. In the job's side panel, open **Job parameters** → Edit and add:

   | Key | Default value |
   |---|---|
   | `bmc_name`, `run_name` | empty (the app fills them: the run's folder) |
   | `config_file`, `data_file`, `prior_file`, `mapping_file`, `share_file` | empty |
   | `run_id` | `{{job.run_id}}` |
   | `base_path` | empty (see note 1) |

   These must be **job** parameters, not task parameters. The app sends them as
   job parameters, and Databricks passes them on to the notebook.
4. If the token owner is not you: Job → **Permissions** → give them **Can
   Manage Run**.
5. Copy the **Job ID** from the job's side panel. You need it in step 6.

### Step 5 - Test the job on its own

Use **Run now with different parameters**:
- `data_file` = `sample_datacube_test.xlsx`
- `prior_file` = `feature_priors_test.csv`
- leave the rest blank - with `bmc_name` and `run_name` blank the job reads
  the old shared folders, where these files are

These are the old test files. The run should finish and create
`Secondary Modelling/Outputs/<run id>/`. If it fails, the notebook output says
why. Fix that before you create the app.

### Step 6 - Create the app

1. Copy the `web/` folder to `/Workspace/Users/<you>/model-app-v2`, with either:
   - VS Code and the Databricks extension, as the old app was synced; or
   - `databricks sync ./web /Workspace/Users/<you>/model-app-v2`, run from
     `updating production code/`.

   Leave out `_reference/`: it only holds the screenshots and is git-ignored,
   so both tools skip it. Copy the logo from the old app
   (`.../model-app/assest/aommm.png`) into `assest/`.
2. In the copied `app.yml`, delete the `MAPPING_JOB_ID` and `FEASIBILITY_JOB_ID`
   entries. `model-app` has no resources with those names, and its Environment
   tab shows neither variable, so they were never set. Only the disabled
   feasibility page used them.
3. Compute → Apps → **Create app** → Custom. Name it `model-app-v2` (the name
   cannot be changed later). Match `model-app`'s settings:

   | Setting | Value |
   |---|---|
   | Git repository | leave empty: you deploy from the workspace folder |
   | Serverless usage policy | None |
   | Compute → Instance size | Medium (up to 2 vCPU, 6 GB) |
   | User authorization | add no scopes. The app only reads the user's email, for the "Hello ..." in the corner, and Databricks always sends it |
   | App telemetry | optional; `model-app` has none |

4. Under **Resources** → **Add resource**, add one per `app.yml` entry. Each
   *resource key* must match the `valueFrom` name exactly. The secrets are in
   the scope `am03-mmm…`, or your own scope for a new token:

   | Resource key | Type | Points to | Permission |
   |---|---|---|---|
   | `CLIENT_ID` | Secret | `model-client-id` | Can read |
   | `CLIENT_SECRET` | Secret | `model-secret` | Can read |
   | `TENANT_ID` | Secret | `am03-mmm-devtest-sp-tenantid` | Can read |
   | `ACCOUNT_NAME` | Secret | `model-account-name` | Can read |
   | `FILE_SYSTEM` | Secret | `model-file-system` | Can read |
   | `DATABRICKS_TOKEN` | Secret | `model-adb-token` (or your new one) | Can read |
   | `MDR_JOB_ID` | Job | the new job | Can manage run |

5. **Deploy**, using the source path from step 1. From the command line:

   ```
   databricks apps deploy model-app-v2 --source-code-path /Workspace/Users/<you>/model-app-v2
   ```

   The Deployments tab should end with "Packages installed" and "App
   started". If it doesn't, the Logs tab shows the error.

### Step 7 - Check the app

On the app's page:
- **Environment**: `ACCOUNT_NAME`, `CLIENT_ID`, `CLIENT_SECRET`,
  `DATABRICKS_TOKEN`, `FILE_SYSTEM`, `MDR_JOB_ID` and `TENANT_ID` are listed,
  their values shown as `***`. A missing one means its resource key doesn't
  match `app.yml`.
- `DATABRICKS_HOST` is filled in by Databricks
  (`adb-4238848035289420.0.azuredatabricks.net` in this workspace). Don't set
  it yourself; the code adds the `https://`.
- **Authorization**: the new app's own service principal is listed. Nothing to
  do there.

Then open the app's URL.
- The header should say *Backend: codebase 1 2026.09.30.1* - and, for the
  people under `full_access` in `app_access.yaml`, *from the workspace
  `/Modelling/Backend/mmm_v5/codebase1_hierarchical_mmm`*, with **Reload
  codebase 1**. If it says codebase 1 could not be loaded, check two things:
  - `MDR_JOB_ID` points to the **new** job (the old one runs `mmm_v4`, which is
    too old);
  - the token owner can read the `mmm_v5` folder.
- The cluster badge shows the cluster whose ID is written into
  `src/clusters.py` (`cluster_id`). If the new job uses a different cluster,
  change that ID.

### Every run
1. **① BMC and run** - pick the BMC (or type a new one) and name the run.
   To start from an earlier run: select it in the BMC's run list and press
   **Reuse inputs** - its datacube, settings and prior (and mapping/share)
   files fill the blocks below, and a new run name is proposed.
2. **② Input data** - choose the datacube (or keep the reused one). Only
   problems that would stop the run are listed.
3. **③ Model settings** - switch on **Edit settings** and change what you
   need, or **Load** a `config.yaml`. Only the settings codebase 1's
   `app_access.yaml` opens for you can change - a loaded config.yaml gives
   only those - and the rest keep the team's values. The ✕ next to a loaded
   or reused config goes back to the team's.
4. **④ Mapping / share file** *(optional)* - choose each; it is checked.
5. **⑤ Prior file**:
   1. **Generate** - the block says beforehand which case (a-d) applies;
   2. for `feature_priors_national.csv` (pooling hierarchical) or
      `feature_priors_regional.csv` (pooling independent): **Download** it and
      fill in the blanks in Excel; or **Preview / Edit** it here, then **Use
      this file**; or **Use** it as it is (blanks get the defaults);
   3. choose the filled file (or keep the reused one); **Preview / Edit** next
      to it shows and edits it. To paste from Excel, use **Paste cells from
      Excel** in the editor.
6. **⑥ Run** - once every checklist line is ticked, press **Run Model**. It
   saves the files in `Secondary Modelling/<BMC>/<run name>/` and starts the
   job. If nothing changed since the run you reused, it asks first. The run's
   panel stays on the page: **Cancel run** stops it, **Dismiss** hides it.
7. When the run ends, the same panel shows the results and the job log, and
   **Prepare run zip** gives the inputs and the outputs in one file. Every
   run stays in its BMC's list; **All recent runs**, at the bottom of the
   page, also has runs from before the run folders.

### Or: update the old app and job instead

This uses the same steps, applied to the existing objects:
- upload codebase 1 into the folder the `HRM` task runs;
- swap its task parameters for the job parameters (step 4, `bmc_name` and
  `run_name` included);
- replace the files in `model-app`'s source folder with `web/` (keep `assest/`),
  then redeploy `model-app`. Its resources stay as they are, because
  `model-job-id` already points to that job.

It is quicker, but you lose the old version to fall back on while you test.

### On your laptop (no Databricks needed)
- `python tests/run_all.py` runs all tests, including 4 that cover the app.
  The last of them clicks through the real Streamlit UI, and is skipped when
  Streamlit is not installed.
- To click through the real UI, see "Testing locally" in `README.md`.

---

## Dependencies

**The app** (`requirements.txt`; `pyyaml` and, since Update 5, `plotly` are
new): `streamlit~=1.54`, `pandas~=2.2.3`, `openpyxl`, `pyyaml`, `plotly`,
`azure-identity`, `azure-storage-file-datalake`. Kept from before: `azure-keyvault-secrets`,
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

**The job** installs codebase 1's `requirements.txt`, attached to the task as
a dependent library: `pymc`, `arviz`, `numpyro`, `jax[cuda]`, `numpy`,
`pandas`, `xarray`, `scipy`, `matplotlib`, `netCDF4`, `openpyxl`. `pyyaml`
comes with the Databricks runtime. The old job ran on the all-purpose cluster
`meridian_model_mmm_test` (DBR 17.3 LTS ML).

**Codebase 1** must be version **2026.09.30.1 or newer** (the run folders came
in 2026.09.29.2, `app_access.yaml` and the live job log in 2026.09.30.1);
the app says so if it is older. Runs made with a version older than
2026.09.29.1 have no job log; their panel says so.

---

**Notes**
1. If the job cannot read `/dbfs/mnt/...` on its compute, set the job parameter
   `base_path` to a Unity Catalog volume path
   (`/Volumes/<catalog>/<schema>/<volume>/Secondary Modelling`). No code change.
2. The OCR mistakes fixed while rebuilding the code are listed in
   `_reference/OCR_FIXES.md`.
3. Full setup details are in `README.md`.
