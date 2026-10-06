import functools
import json
import os
import posixpath
from azure.identity import ClientSecretCredential
from azure.storage.filedatalake import DataLakeServiceClient
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import zipfile

from src import perf

DOWNLOAD_THREADS = 8      # ADLS reads in parallel when a whole folder is fetched

# Root folder inside the ADLS file system. The Databricks job reads the same
# files at /dbfs/mnt/testuat/<ADLS_ROOT>/... (see codebase 1's mmm/app_job.py).
ADLS_ROOT = os.environ.get("ADLS_ROOT", "Secondary Modelling").strip().strip("/")

# Local testing only: when set, every "ADLS" read/write goes to this folder
# instead, so the app can be exercised without Azure credentials.
LOCAL_STORAGE_DIR = os.environ.get("LOCAL_STORAGE_DIR", "").strip()


def adls_dir(folder):
    """'Secondary Modelling/<folder>' - where a kind of file lives in ADLS."""
    return f"{ADLS_ROOT}/{folder}"


def unique_name(file_name):
    """name.ext -> name_YYYYmmdd-HHMMSS.ext (UTC), so two people uploading a
    file of the same name never overwrite each other's copy."""
    stem, ext = os.path.splitext(os.path.basename(str(file_name)))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stem}_{stamp}{ext}"


def _local_path(*parts):
    return os.path.join(LOCAL_STORAGE_DIR, *[p for part in parts for p in str(part).split("/") if p])


@functools.lru_cache(maxsize=4)
def _file_system_client(account_name, file_system, tenant_id, client_id, client_secret):
    # one client (and one token) for the whole app, instead of a new login per
    # file - listing a BMC's runs reads a few small files per run
    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    service_client = DataLakeServiceClient(
        account_url=f"https://{account_name}.dfs.core.windows.net",
        credential=credential,
    )
    return service_client.get_file_system_client(file_system)


def _file_system():
    return _file_system_client(
        os.environ.get("ACCOUNT_NAME", "").strip(),
        os.environ.get("FILE_SYSTEM", "").strip(),
        os.environ.get("TENANT_ID", ""),
        os.environ.get("CLIENT_ID", ""),
        os.environ.get("CLIENT_SECRET", ""),
    )


def is_not_found(exc):
    """True for 'this path does not exist' - from ADLS or the local folder."""
    return (type(exc).__name__ in {"ResourceNotFoundError", "FileNotFoundError"}
            or getattr(exc, "status_code", None) == 404
            or "PathNotFound" in str(exc) or "BlobNotFound" in str(exc))


@perf.timed("adls.upload", lambda data, name, folder: f"{folder}/{name}")
def upload_to_adls(file_data, file_name, location_path):
    """Upload a file to ADLS Gen2 at {location_path}/{file_name}."""
    if LOCAL_STORAGE_DIR:
        target = _local_path(location_path, file_name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(file_data)
        return target

    account_name = os.environ.get("ACCOUNT_NAME", "").strip()
    file_system = os.environ.get("FILE_SYSTEM", "").strip()

    directory_client = _file_system().get_directory_client(location_path)
    file_client = directory_client.get_file_client(file_name)
    file_client.upload_data(file_data, overwrite=True)

    return f"https://{account_name}.blob.core.windows.net/{file_system}/{location_path}/{file_name}"


@perf.timed("adls.download", lambda path: path)
def download_from_adls(file_path):
    if LOCAL_STORAGE_DIR:
        with open(_local_path(file_path), "rb") as f:
            return f.read()

    file_client = _file_system().get_file_client(file_path)
    download = file_client.download_file()
    file_bytes = download.readall()
    return file_bytes


def read_json(file_path):
    """A small JSON file as a dict - None when it does not exist."""
    try:
        data = json.loads(download_from_adls(file_path).decode("utf-8"))
    except Exception as e:
        if is_not_found(e):
            return None
        raise
    return data if isinstance(data, dict) else None


def write_json(obj, file_name, location_path):
    return upload_to_adls(json.dumps(obj, indent=2, default=str).encode("utf-8"),
                          file_name, location_path)


@perf.timed("adls.list_dir", lambda path: path)
def list_dir(folder_path):
    """The folder's direct children as (name, is_folder), sorted; [] when the
    folder does not exist."""
    if LOCAL_STORAGE_DIR:
        path = _local_path(folder_path)
        if not os.path.isdir(path):
            return []
        return sorted((e.name, e.is_dir()) for e in os.scandir(path))
    try:
        paths = list(_file_system().get_paths(path=folder_path, recursive=False))
    except Exception as e:
        if is_not_found(e):
            return []
        raise
    return sorted((posixpath.basename(p.name.rstrip("/")), bool(p.is_directory))
                  for p in paths)


@perf.timed("adls.list_tree", lambda path: path)
def list_tree(folder_path):
    """Every file below the folder, as paths relative to it (sorted); [] when
    the folder does not exist."""
    if LOCAL_STORAGE_DIR:
        src = _local_path(folder_path)
        out = []
        for root, _dirs, files in os.walk(src):
            for f in files:
                out.append(os.path.relpath(os.path.join(root, f), src).replace(os.sep, "/"))
        return sorted(out)
    try:
        paths = list(_file_system().get_paths(path=folder_path, recursive=True))
    except Exception as e:
        if is_not_found(e):
            return []
        raise
    prefix = folder_path.rstrip("/") + "/"
    return sorted(p.name[len(prefix):] if p.name.startswith(prefix) else p.name
                  for p in paths if not p.is_directory)


@perf.timed("adls.list_tree_meta", lambda path: path)
def list_tree_meta(folder_path):
    """Every file below the folder as (path relative to it, size in bytes,
    last modified as text), sorted - what tells a changed run from an
    unchanged one. [] when the folder does not exist."""
    if LOCAL_STORAGE_DIR:
        src = _local_path(folder_path)
        out = []
        for root, _dirs, files in os.walk(src):
            for f in files:
                full = os.path.join(root, f)
                st_ = os.stat(full)
                out.append((os.path.relpath(full, src).replace(os.sep, "/"),
                            int(st_.st_size), str(st_.st_mtime_ns)))
        return sorted(out)
    try:
        paths = list(_file_system().get_paths(path=folder_path, recursive=True))
    except Exception as e:
        if is_not_found(e):
            return []
        raise
    prefix = folder_path.rstrip("/") + "/"
    return sorted((p.name[len(prefix):] if p.name.startswith(prefix) else p.name,
                   int(getattr(p, "content_length", 0) or 0),
                   str(getattr(p, "last_modified", "") or ""))
                  for p in paths if not p.is_directory)


def download_files(pairs, threads=DOWNLOAD_THREADS):
    """Download [(ADLS path, local path), ...] in parallel; returns the count."""
    def one(pair):
        src, dst = pair
        data = download_from_adls(src)
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(data)
        return 1
    pairs = list(pairs)
    if not pairs:
        return 0
    with ThreadPoolExecutor(max_workers=max(1, min(threads, len(pairs)))) as pool:
        return sum(pool.map(one, pairs))


@perf.timed("adls.exists", lambda path: path)
def path_exists(path):
    """Does this file or folder exist?"""
    if LOCAL_STORAGE_DIR:
        return os.path.exists(_local_path(path))
    return bool(_file_system().get_directory_client(path).exists())


@perf.timed("adls.move", lambda src, dst: f"{src} -> {dst}")
def move_dir(src_path, dst_path):
    """Move a folder (with everything in it) to `dst_path` - one rename in
    ADLS Gen2, so the files are never copied. The destination's parent folder
    is created when missing; a destination that exists is an error (nothing
    is ever overwritten), and so is a missing source."""
    if LOCAL_STORAGE_DIR:
        src, dst = _local_path(src_path), _local_path(dst_path)
        if not os.path.isdir(src):
            raise FileNotFoundError(f"{src_path} does not exist")
        if os.path.exists(dst):
            raise FileExistsError(f"{dst_path} already exists")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.rename(src, dst)
        return dst_path
    fs = _file_system()
    source = fs.get_directory_client(src_path)
    if not source.exists():
        raise FileNotFoundError(f"{src_path} does not exist")
    if fs.get_directory_client(dst_path).exists():
        raise FileExistsError(f"{dst_path} already exists")
    parent = posixpath.dirname(dst_path.rstrip("/"))
    if parent and not fs.get_directory_client(parent).exists():
        fs.get_directory_client(parent).create_directory()
    source.rename_directory(new_name=f"{fs.file_system_name}/{dst_path}")
    return dst_path


@perf.timed("adls.download_folder", lambda path, *a, **k: path)
def download_folder(folder_path, local_folder, exclude=()):
    """Download every file under folder_path into local_folder - in parallel.

    Returns the number of files. The old version returned after the FIRST
    file (the `return True` sat inside the loop), so a run's output zip held
    one file. `exclude` skips files by name (e.g. trace.nc, which is large).
    """
    os.makedirs(local_folder, exist_ok=True)
    rels = [rel for rel in list_tree(folder_path)
            if posixpath.basename(rel) not in exclude]
    if not rels:
        raise FileNotFoundError(f"No files found under {folder_path}")
    return download_files((f"{folder_path.rstrip('/')}/{rel}",
                           os.path.join(local_folder, *rel.split("/"))) for rel in rels)


def zip_folder(folder_path, zip_path, exclude=()):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file in exclude:
                    continue
                file_path = os.path.join(root, file)

                # Preserve folder structure inside zip
                arcname = os.path.relpath(file_path, folder_path)

                zipf.write(file_path, arcname)
