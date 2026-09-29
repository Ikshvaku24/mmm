# Plan: bring the BRIDGE web app up to codebase 1

> **Revision after review (2026-09-29) - this replaces Phase 4 and the vendoring parts below.**
> codebase 1 stays a separate **backend** that anyone can still run from the workspace; `web/`
> is only the **frontend**. So:
> - There is **no copy** of codebase 1 in `web/`, and no `web/codebase1/` or `tools/sync_codebase.py`.
>   The app loads codebase 1 **live** (`src/codebase.py`) from the folder of the notebook the job
>   runs, found through the Jobs API. It downloads it through the Workspace API, re-checks it every
>   5 minutes and switches to a re-uploaded copy without a redeploy. `CODEBASE1_WORKSPACE_PATH` /
>   `CODEBASE1_DIR` override where it looks.
> - The job notebook stays **`demo.ipynb` inside codebase 1**. It calls `mmm/app_job.py`, not a
>   notebook in `web/`. The job uses serverless GPU, so the run id comes from the job parameter
>   `run_id = {{job.run_id}}`, and `base_path` can switch `/dbfs/mnt/...` to a `/Volumes/...` path.
> - The vendored-copy hash test is replaced by tests of the live loading against a fake
>   Workspace/Jobs API (`tests/test_v20_web_app.py`), plus an end-to-end UI run
>   (`tests/test_v21_web_ui_smoke.py`).
>
> **Second revision, after the first deployment (2026-09-29, "issues-1").** These parts of the
> plan below are replaced:
> - The status popup and the "previous-run outputs" viewer are replaced by a run panel that stays
>   on the page (status, Cancel, results, job log, zip) and a **Runs** list (`src/runs.py`).
> - "Use national / Use regional" is replaced by three steps: Generate (the case is shown
>   first), Download and fill in, Upload.
> - The datacube check shows errors only. The dust and constant-column warnings are left to the
>   run's `00_warnings`.
> - The config editor's paste box and YAML preview are removed.
> - Every block is a Streamlit fragment, so the page no longer greys out.
>
> What changed and why: `web/changes.md`, "Update 1 - after the first deployment".
>
> **Third revision (2026-09-29, "issues-2").** Storage is now one folder per run,
> `Secondary Modelling/<BMC>/<run name>/` (Config/ Data/ Prior/ [Mapping/ Share/] Outputs/ and
> `run_request.json`). This replaces the "Upload to ADLS" buttons of the plan below: at the
> user's choice, Run Model saves all the files. Any run's inputs can be reused; Run Model asks
> first when nothing changed; the run's zip holds its inputs and outputs. The job gets
> `bmc_name` / `run_name` (codebase 1 2026.09.29.2). The prior editor gets "Paste cells from
> Excel", because the grid's own Ctrl+V needs the browser's clipboard-read permission. See
> `web/changes.md`, "Update 2".
>
> What was built, and the Databricks setup steps: `web/README.md`.

## Context

`web/` is the Streamlit app ("BRIDGE") hosted as a Databricks App. It uploads files to ADLS and
triggers Databricks job `MDR_JOB_ID`, whose notebook (`demo.py`) edits `config.yaml` and runs the
model. It was built for the **old** model: its own `generate_prior.py` writes the old
`region, variable, b0, B0` prior, and the notebook sets only five config keys. Codebase 1 now has a
`config.yaml` front end, a pre-model prior builder (national + regional files + workbook), mapping
and share files, and a staged output tree.

The local `web/` holds only screenshots and OCR'd code, flat. The goal is to **change, not
rewrite**, the app so it runs the current codebase 1, and to add these features:
- download the generated prior, edit it, re-upload it;
- download a sample of, and upload, the mapping and share files;
- edit `config.yaml` in the UI, with dropdowns for enumerated options, and run with that version;
- edit the feature-prior table in the UI, including pasting whole columns.

**Confirmed decisions**
- Priors are generated **in the app**, by codebase 1's own `build_priors`, from a synced copy at
  `web/codebase1/`. The job notebook moves into `web/` and runs from the same copy, so the app and
  the job can never run different code versions. (This was the 2026-09-24 stale-cluster-copy
  failure.)
- `app.py`, `app.yml` and `requirements.txt` come from your screenshots.
- The existing look, dialogs, upload-to-ADLS buttons and run-status popup are kept.
- The feasibility and transformation pages stay disabled, as `app.py` already has them.
  `ui_components.py` is not imported by `app.py`, so it is left alone.
- ADLS keeps `Secondary Modelling/{Data,Prior,Outputs}` and adds `Config/`, `Mapping/` and `Share/`.
  On the cluster these are `/dbfs/mnt/testuat/Secondary Modelling/...`.

## Findings that the plan fixes

1. **Job notebook**: `demo.py` fails at `context.tags()` (Py4JError, which suggests a
   shared-access-mode cluster). Fix: pass the run id as a job parameter, `run_id = {{job.run_id}}`.
2. **Output download**: `files.download_folder` has `return True` inside the loop, so only the
   **first** output file is downloaded. This is in the original code, not an OCR error. Also, every
   run and every user shares `./local` and `./local.zip`, so zips mix runs together.
3. **OCR errors**:
   - `app.py` line 14 lost a quote, which is a SyntaxError.
   - Two files are misnamed: `feasability_check_page.py` should be `feasibility_check_page.py`,
     and `plot_charts.py` should be `plots_charts.py`.
   - The feasibility page's HTML strings contain `|` characters that are rendered indent guides.
   - The two tall page screenshots have not been checked line by line yet.
4. **Datacube validation**: `validation.py` lower-cases the first three columns in place and
   assumes they are date, region and dv. The frame it checks is therefore not the file that gets
   uploaded.
5. **Intercept**: the Intercept checkbox is sent as `modelling_type`, but the notebook ignores it
   and forces `include_intercept: True`. The shipped `config.yaml` says `false`. From now on, app
   runs follow whatever the config editor shows. **This is a behaviour change to be aware of.**
6. **Config defaults**: the shipped `config.yaml` is not the dataclass defaults (`likelihood`,
   `include_intercept`, `dv_scale`, `dv_center`, `chain_method`, `holdout_periods` all differ).
   Any key left out of a YAML falls back to the dataclass default, so the app must always write
   **every** key.
7. **Calling codebase 1 inside the app**:
   - The mapping and share readers raise `SystemExit`, not `ValueError`.
   - `build_priors` redirects printing and captures warnings for the whole process, while
     Streamlit runs each session as a thread.
   - So every in-app codebase call needs a lock and `SystemExit` handling.
8. **Writing to DBFS**: writing `trace.nc` (HDF5) straight to `/dbfs` is fragile. Write to local
   disk, then copy, as the Databricks notes in the folder's CLAUDE.md say.

## Phase 0: reference folder, OCR check, real layout

- `web/_reference/screenshots/`: every PNG, names unchanged.
- `web/_reference/ocr_original/`: the OCR'd `.py` files exactly as provided, plus transcriptions
  of the image-only files `demo.py`, `app.yml` and `requirements.txt`.
- Add these lines to `updating production code/.gitignore`:
  `web/_reference/`, `web/local/`, `web/src/local/`, `web/.databricks/`.
  Databricks sync also honours `.gitignore`, so the screenshots never reach the workspace.
- Rebuild the real layout from `directory.png`:
  - root: `web/app.py`, `app.yml`, `requirements.txt`
  - `web/src/*.py`, `web/src/components/app_header.py`
  - `web/src/pages/{feasibility_check_page, model_setup_page, transformation_page}.py`
  - apply the two renames above.
- Check every file against its screenshot; crop the two tall page shots into readable strips. Fix
  the differences and list them in `_reference/OCR_FIXES.md`.
- Put the real `aommm.png` in `web/assest/`. It was not provided; the header already skips a
  missing logo.

## Phase 1: codebase 1 additions (additive; the fit and the reports are unchanged)

**`mmm/core/config.py`**
- Give names to the enum sets that `__post_init__` checks inline: `VALID_LIKELIHOOD`,
  `VALID_SAMPLER`, `VALID_CHAIN_METHOD`, `VALID_SCOPE`, `VALID_WINDOW`, `VALID_ON_FAILURE`,
  `VALID_PERIOD_SPLIT`.
- Move `VALID_DV_AGG` and `VALID_NATIONAL_BASIS` here from `prior_builder.py`, which imports them
  back, so the old import path still works.

**`mmm/core/settings.py`**
- `CHOICES`: a map from `"section.key"` to its allowed values. Built from the constants above plus
  `VALID_CENTER`, `VALID_SCALE` and `VALID_CADENCE`.
- `settings_from_dict(raw, base_dir, features=None)`: the body of `load_settings`, moved out.
  `load_settings` becomes "read the YAML, then call it", with the same signature and behaviour.
- `config_schema()`: one row per key, with section, key, kind, default, choices and help (from
  `HELP`). The kind comes from the field annotation: bool, int, float, str, choice, int-or-null,
  float-or-null, or mapping. The data keys come from `DATA_KEYS` and `DEFAULT_DATA`.
- `settings_text(values=None, header=None)`: a generalised `default_settings_text`, which becomes
  `settings_text()`. It writes the given values with the same help comments and adds
  `(default: x)` wherever a value differs from the default.

**Version and docs**
- Bump `mmm.__version__` and all five `__codebase__` stamps together; `test_v18` checks they agree.
- `docs/CONFIG_GUIDE.md`: a short "editing from the web app" note.
- `docs/PROJECT_STRUCTURE.md` and the folder `CLAUDE.md`: point to `web/`.

## Phase 2: `web/src/codebase.py` (new; the only module that imports `mmm`; no Streamlit)

**Setup and safety**
- Puts `web/codebase1` on `sys.path`, found from `__file__`. If the folder is missing, says so
  plainly: "run tools/sync_codebase.py".
- Exposes `mmm.__version__` and `check_sync()`.
- Wraps every codebase call in one `threading.Lock`. Every call returns
  `(result, errors, warnings, log)` and catches both `ValueError` and `SystemExit`.

**Config**
- `base_config()`, `config_schema()`, `validate_config(cfg)`, `config_yaml(cfg)`.
- Validation uses `settings_from_dict(features=[])`, so no prior CSV is needed to validate a
  config.

**Datacube**
- `read_datacube(bytes, name, cfg)` reads through `settings.load_panel`, honouring `data.sheet`.
- `check_datacube(df, cfg)` checks:
  - the configured `date_col`, `region_col` and `dv_col` exist, with a hint when only the case
    differs;
  - no duplicate region × date rows, no non-numeric features, no NaNs;
  - features with zero variance within a region (the old check);
  - "dust" columns (every value ~1e-15, the Coupon case);
  - the date cadence, via `config.infer_cadence`.

  It also returns a panel summary and the region list.

**Prior table**
- `prior_columns()`: `PRIOR_COLUMNS`, the kind of each column, and its allowed values.
- `read_prior_file(bytes, name)`: CSV or xlsx. Uses `utf-8-sig` and sniffs the delimiter, because
  Excel re-saves CSVs with a BOM and sometimes semicolons.
- `validate_prior_table(df, datacube_df, cfg)`:
  - runs `load_feature_config` on a temp CSV;
  - checks every name against the datacube, suggesting the closest spelling as
    `run_real_data.py` does;
  - runs `validate_region_priors`;
  - rejects a `center` column and flags unknown columns.

**Mapping and share files**
- `validate_mapping()` and `validate_share()` call `load_mapping_table` + `align_regions` and
  `load_share_file`, checked against the datacube's columns.
- `mapping_template()` and `share_template()` build starter files pre-filled with the datacube's
  variables.

**Generating priors**
- `generate_priors(datacube, mapping, share, cfg, restrict_to=None)`:
  1. writes the inputs and a config into a temp dir;
  2. calls `prior_builder.build_priors`;
  3. returns the bytes of the national CSV, the regional CSV and `prior_calculation.xlsx`, a zip of
     `00_warnings`, the text of `00_INDEX.md`, the case (a–d), the printed log, and any
     `check_units` problems.

## Phase 3: web UI changes (existing files edited in place)

| File | Change |
|---|---|
| `src/generate_prior.py` | Same module and function name. The old b0/B0 body becomes a call to `codebase.generate_priors`, so the app has no prior logic of its own |
| `src/validation.py` | `validate_input_data(df, cfg)` displays `check_datacube` results in the same `st.error`/`st.warning` style, and no longer changes the frame |
| `src/config_editor.py` **(new)** | The config editor, detailed below |
| `src/app_functions.py` | `show_prior_file_popup` becomes the new prior editor, detailed below. `read_file_bytes_as_table` gains BOM and delimiter handling |
| `src/pages/model_setup_page.py` | Five blocks inside the existing containers, detailed below |
| `src/jobs.py` | `run_model_job(config_file, data_file, prior_file, mapping_file="", share_file="", job_id)`; `modelling_type` is dropped. Finish the stub `get_task_run_id` (`runs/get` returns the task run id) and add `get_run_output`, because `runs/get-output` needs the task run id |
| `src/files.py` | Fix `download_folder` (loop over every file, return the count, raise if there are none). One folder and zip per run: `./local/<run_id>/` and `./local/<run_id>.zip`. `zip_folder` takes an exclude list. Optional `LOCAL_STORAGE_DIR` env var switches storage to a local folder, for local testing only |
| `app.py` | Fix the OCR quote. Add calls for the new blocks between the existing calls. `modelling_type` becomes `run_config`. Nothing else changes |
| `requirements.txt` | Add `pyyaml`. `app.yml` is unchanged; the env vars stay the same |

**Config editor (`src/config_editor.py`)**
- Starts from `codebase1/config.yaml`. One tab per section: Model, Run, Sampler,
  Cross-validation, Output, Assumptions, Data.
- One widget per kind:

  | Kind | Widget |
  |---|---|
  | choice | selectbox (dropdown) |
  | bool | toggle |
  | int / float | number input |
  | int-or-null / float-or-null | "auto (null)" checkbox + number input |
  | mapping | YAML text box |
  | str | text box |

- The tooltip is the codebase help text plus the dataclass default. Keys changed from the base
  are marked.
- Read-only because the job sets them: `data.input_path`, `data.feature_priors`,
  `data.mapping_file`, `data.share_file`, `data.pre_model_dir`, `run.output_dir`, `run.run_name`.
- Buttons: Upload config.yaml, Download config.yaml (written with `settings_text`), Reset to base,
  and an "edit raw YAML" expander.
- Validation runs on every change. Errors block Run; warnings are shown.
- If the priors were generated and `dv_scale` is not `mean` or the scope is not `region`, show the
  `check_units` warning with a one-click fix.
- Widget keys carry a version number that is bumped on upload or reset, the same pattern as the
  existing `prior_popup_editor_version`.

**Prior editor (`show_prior_file_popup`)**
- Grid column types:
  - dropdown (SelectboxColumn): `sign_constraint`, `pooling`, `center_mode`, `scale_mode`,
    `prior_sd_basis`, `prior_mean_basis`, and `region` (listing the datacube's regions);
  - number: the means, the sds and `baseline`;
  - text: `variable`, `pillar`, `contribution_reference`.
- Template columns missing from the file are added blank. The "Remove" column moves to the front;
  it used to sit after `B0`.
- **Column copy and paste**
  - Native grid clipboard: click a cell, shift+click another to select a range, Ctrl+C. To paste
    a column copied from Excel, click the first target cell and press Ctrl+V; it fills downward.
  - Two helpers below the grid, applied to the grid's current unsaved state:
    - **Fill a column**: pick a column and a value; apply to all rows, blank cells only,
      feature-level rows only, or rows whose name contains some text.
    - **Paste a column**: paste a copied Excel column into a text box. The row count is checked,
      enum values are case-normalised (`Positive` becomes `positive`), and bad lines are listed.
  - Helpers bump the editor version and call `st.rerun(scope="fragment")`, so the dialog stays
    open and unsaved grid edits survive.
- Save runs `validate_prior_table`. Both "Upload to ADLS" buttons (in the dialog and on the page)
  are enabled only for a validated table. Errors show the codebase's own message.

**Model setup page: five blocks, same look**
1. **Input data**: the existing uploader and Upload button (now xlsx or csv), the new validation
   and a panel summary. A prior is no longer generated automatically on upload.
2. **Model settings**: the config editor, in an expander. The Intercept checkbox is removed; the
   setting is now `model.include_intercept`.
3. **Mapping and share files** (optional), each with:
   - a download of the static sample (`codebase1/samples/`);
   - a download of the template pre-filled from the datacube;
   - an uploader, codebase validation and a preview;
   - an Upload button to `Mapping/` or `Share/`.
4. **Prior file**
   - **Generate from datacube** shows the case, then offers downloads (national, regional,
     `prior_calculation.xlsx`, warnings) and "Use national (hierarchical)" / "Use regional
     (independent)".
   - Or upload your own CSV with the existing uploader.
   - Then the existing Preview/Edit popup and Upload button.
   - A "stale" badge appears if the datacube, mapping, share file, or a generation-relevant config
     key (column names, holdout, cadence, scaling window, dv aggregation, national basis) changed
     since generation.
   - An option restricts generation to the variables of the current table.
5. **Run**
   - Enabled when the data and prior are uploaded, the config is valid, and any chosen mapping or
     share file is uploaded.
   - On click it uploads the config (`settings_text`) to `Config/`, then calls `run_model_job`.
   - Every upload gets a `_YYYYmmdd-HHMMSS` suffix, so teammates never overwrite each other's files.
   - The status popup also handles QUEUED, INTERNAL_ERROR and FAILED:
     - on failure it shows the notebook's error, via `get_run_output`;
     - on success it shows the cluster's codebase version, with a warning if it differs from the
       app's, and the fixed zip download. An "include trace.nc" toggle is off by default.
- `render_input_upload_section()` still returns five items; the fifth is the config dict instead of
  the Intercept flag.

**Optional, not requested: previous-run outputs**
- Enter a run id to get its zip and a preview of `00_warnings/00_INDEX.md`,
  `convergence_report.txt`, `fit_metrics.csv` and `contribution_summary.csv`.
- It would reuse `show_model_file_adls_popup`, which `app.py` imports but never shows.
- Without it, a run's outputs can only be downloaded from the status popup, and are lost from the
  app once the popup is closed.

## Phase 4: the synced codebase and the job notebook

**`web/tools/sync_codebase.py`**
- Copies these from `codebase1_hierarchical_mmm/` into `web/codebase1/`: `mmm/`, `samples/`,
  `config.yaml`, `docs/CONFIG_GUIDE.md` and `docs/FEATURE_PRIOR_GUIDE.md`.
- Replaces only that folder, skips `__pycache__`, and writes `SYNCED_FROM.txt` with the version and
  the time.
- The copy is **tracked** in git, because Databricks sync skips ignored files. Run the script before
  every deploy.

**`web/databricks_job/run_model_job.py`** (Databricks notebook source; replaces `demo.py`)
1. Defines widgets with defaults: `config_file`, `data_file`, `prior_file`, `mapping_file`,
   `share_file`, `run_id`, and an optional `codebase_dir`.
2. Puts `../codebase1` on `sys.path` and calls `mmm.announce()`.
3. Loads the uploaded config, falling back to `codebase1/config.yaml`. It overrides only:
   - the data paths under `/dbfs/mnt/testuat/Secondary Modelling/...`;
   - `pre_model_dir`, set to `<run>/00_pre_model`;
   - `run.output_dir`, set to `/local_disk0/mmm_outputs`;
   - `run.run_name`, set to `run_id`.

   There is no forced `include_intercept` any more.
4. Calls `run_from_yaml`.
5. Always copies the run folder to `Outputs/<run_id>`, even on failure: `dbutils.fs.cp(recurse=True)`
   first, falling back to a FUSE copy. It also writes `run_info.json` and saves the input config.
6. On success, `dbutils.notebook.exit` returns JSON with the status, codebase version and output
   path. On failure it re-raises, so the job shows FAILED with the message.
7. Inputs are read through FUSE, falling back to `dbutils.fs.cp` to local disk if the mount is
   blocked.

**Manual Databricks steps (yours)**
1. Run `sync_codebase.py`, sync `web/` to the workspace, and redeploy the app.
2. Edit job `MDR_JOB_ID`:
   - task notebook: `<app folder>/databricks_job/run_model_job`;
   - job parameters: `config_file`, `data_file`, `prior_file`, `mapping_file`, `share_file`
     (default `""`), and `run_id` = `{{job.run_id}}`;
   - delete `modelling_type`.
3. Confirm the job cluster still has codebase 1's requirements: pymc, numpyro, `jax[cuda]`, arviz,
   netCDF4, openpyxl.

## Tests (flat `check()` scripts; auto-found by `tests/run_all.py`; need no Streamlit or PyMC)

**`tests/test_v19_config_schema.py`**
- `config_schema` covers every settable key.
- Every value in `CHOICES` builds its dataclass, and an off-list value raises.
- `settings_text(values)` round-trips non-default values with the right types.
- `settings_from_dict` gives the same result as `load_settings`.
- `test_v11` and `test_v18` still pass unchanged.

**`tests/test_v20_web_app.py`**
- `validate_prior_table`:
  - accepts a good table;
  - rejects a bad sign, bad pooling, an unknown region, a `center` column and an unknown variable,
    with the codebase's message;
  - captures the mapping reader's `SystemExit`.
- `check_datacube` on synthetic panels: duplicate rows, NaNs, dust, and case-mismatched column
  names.
- The templates list every feature.
- `generate_priors` produces files for case d (skeleton) and case c (shares). The xlsx check is
  skipped where openpyxl is absent, as it is locally.
- The notebook's path helper overrides only the keys the job owns.
- `web/codebase1` hashes equal to codebase 1's. If not, the test fails with "run sync_codebase.py".

## Verification

1. `python tests/run_all.py`: every suite passes, the new ones included.
2. `python -m py_compile` over `web/`, to catch any remaining OCR syntax errors.
3. Optional local UI smoke test:
   1. `pip install streamlit~=1.54 openpyxl`
   2. set `LOCAL_STORAGE_DIR`, then run `streamlit run web/app.py`
   3. upload `input_datacube.xlsx` and the samples; generate priors; paste a column; validate; edit
      the config; download everything
   4. Run should fail gracefully without Databricks.
4. Databricks end-to-end, on the retailer datacube:
   1. use a generated and edited prior, and change one config key (for example `fourier_order: 2`);
   2. check that `01_data/resolved_config.yaml` matches the UI;
   3. check that the zip holds every stage folder;
   4. check that the popup shows success and the codebase version;
   5. then upload a deliberately bad prior and confirm the error text appears in the popup.
