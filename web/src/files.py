import os
from azure.identity import ClientSecretCredential
from azure.storage.filedatalake import DataLakeServiceClient, DataLakeFileClient
from datetime import datetime, timezone
import streamlit as st
import zipfile

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
    client_id = os.environ.get("CLIENT_ID", "")
    tenant_id = os.environ.get("TENANT_ID", "")
    client_secret = os.environ.get("CLIENT_SECRET", "")

    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )

    service_client = DataLakeServiceClient(
        account_url=f"https://{account_name}.dfs.core.windows.net",
        credential=credential,
    )

    file_system_client = service_client.get_file_system_client(file_system)
    directory_client = file_system_client.get_directory_client(location_path)
    file_client = directory_client.get_file_client(file_name)
    file_client.upload_data(file_data, overwrite=True)

    return f"https://{account_name}.blob.core.windows.net/{file_system}/{location_path}/{file_name}"


def download_from_adls(file_path):
    if LOCAL_STORAGE_DIR:
        with open(_local_path(file_path), "rb") as f:
            return f.read()

    account_name = os.environ.get("ACCOUNT_NAME", "").strip()
    file_system = os.environ.get("FILE_SYSTEM", "").strip()
    client_id = os.environ.get("CLIENT_ID", "")
    tenant_id = os.environ.get("TENANT_ID", "")
    client_secret = os.environ.get("CLIENT_SECRET", "")
    credential = ClientSecretCredential(
        tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
    )

    file_client = DataLakeFileClient(
        account_url=f"https://{account_name}.dfs.core.windows.net",
        file_system_name=file_system,
        file_path=file_path,
        credential=credential,
    )
    download = file_client.download_file()
    file_bytes = download.readall()
    return file_bytes


def download_folder(folder_path, local_folder, exclude=()):
    """Download every file under folder_path into local_folder.

    Returns the number of files. The old version returned after the FIRST
    file (the `return True` sat inside the loop), so a run's output zip held
    one file. `exclude` skips files by name (e.g. trace.nc, which is large).
    """
    os.makedirs(local_folder, exist_ok=True)
    count = 0

    if LOCAL_STORAGE_DIR:
        src = _local_path(folder_path)
        for root, _dirs, files in os.walk(src):
            for file in files:
                if file in exclude:
                    continue
                rel = os.path.relpath(os.path.join(root, file), src)
                local_path = os.path.join(local_folder, rel)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(os.path.join(root, file), "rb") as fin, open(local_path, "wb") as fout:
                    fout.write(fin.read())
                count += 1
        if count == 0:
            raise FileNotFoundError(f"No files found under {folder_path}")
        return count

    account_name = os.environ.get("ACCOUNT_NAME", "").strip()
    file_system = os.environ.get("FILE_SYSTEM", "").strip()

    credential = ClientSecretCredential(
        tenant_id=os.environ.get("TENANT_ID", ""),
        client_id=os.environ.get("CLIENT_ID", ""),
        client_secret=os.environ.get("CLIENT_SECRET", ""),
    )

    service_client = DataLakeServiceClient(
        account_url=f"https://{account_name}.dfs.core.windows.net",
        credential=credential,
    )

    fs_client = service_client.get_file_system_client(file_system)

    paths = fs_client.get_paths(path=folder_path)

    for path in paths:
        if not path.is_directory:
            if os.path.basename(path.name) in exclude:
                continue
            relative_path = os.path.relpath(path.name, folder_path)
            local_path = os.path.join(local_folder, relative_path)

            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            file_client = fs_client.get_file_client(path.name)

            with open(local_path, "wb") as f:
                f.write(file_client.download_file().readall())

            # st.write(f"Downloaded: {path.name}")
            count += 1

    if count == 0:
        raise FileNotFoundError(f"No files found under {folder_path}")
    return count


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
