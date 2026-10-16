"""ADLS for the job on compute without the /dbfs/mnt mount (serverless).

On a classic cluster the job reads and writes the run folder through the
mount (`/dbfs/mnt/testuat/Secondary Modelling`, app_job.DEFAULT_BASE_PATH).
Serverless compute - the serverless GPU the job now runs on - has no DBFS
mounts, so there the job talks to the storage account itself, as the web app
does: with a service principal whose credentials sit in a secret scope
(SECRET_SCOPE / SECRET_KEYS - demo.ipynb reads them with dbutils.secrets).
Same storage account, same folders:

    <file system>/Secondary Modelling/<bmc>/<run group>/<run>/...

`AdlsStore` is all the job needs: the run's inputs copied to local disk
before the model runs, the outputs and the live log copied back.
`app_job.run_app_job(..., adls=store)` does both; nothing else in the
codebase knows about ADLS. The Azure libraries are imported only by
`connect`, so the rest of the package runs without them.

The account and file-system names are never printed: they come from the
secret scope, and the job log is shown to everyone in the web app.
"""
from __future__ import annotations

__codebase__ = "2026.10.16.2"   # must equal mmm.__version__ (python -m mmm.release)

import os
import posixpath
from concurrent.futures import ThreadPoolExecutor

DEFAULT_ROOT = "Secondary Modelling"      # the web app's ADLS_ROOT
THREADS = 8                               # files copied in parallel

# Where demo.ipynb finds the service principal - names only; the values stay in
# the secret scope. The job parameter adls_secret_scope overrides the scope;
# edit the keys here if the scope names them differently. The principal needs
# read AND write on the file system (e.g. Storage Blob Data Contributor), and
# whoever the job runs as needs READ on the scope.
SECRET_SCOPE = "am03-mmm-uat-adb-secret-scope"
SECRET_KEYS = {
    "tenant_id": "am03-mmm-uat-sp-tenantid",
    "client_id": "am03-mmm-uat-sp-appid",
    "client_secret": "am03-mmm-uat-sp-secret",
    "account_name": "uat-adls-account-name",
    "file_system": "adls-model-container-name",
}


def is_not_found(exc) -> bool:
    """True for 'this path does not exist' (azure.core's ResourceNotFoundError)."""
    return (type(exc).__name__ in {"ResourceNotFoundError", "FileNotFoundError"}
            or getattr(exc, "status_code", None) == 404
            or "PathNotFound" in str(exc) or "BlobNotFound" in str(exc))


def _rel(rel: str) -> str:
    return str(rel or "").replace("\\", "/").strip("/")


class AdlsStore:
    """One ADLS file system, seen from `root` ("Secondary Modelling").
    `file_system` is an azure FileSystemClient - or anything with its
    get_paths / get_file_client (the tests use an in-memory one)."""

    def __init__(self, file_system, root: str = DEFAULT_ROOT):
        self.fs = file_system
        self.root = _rel(root)

    def path(self, rel: str = "") -> str:
        """The path in the file system: `<root>/<rel>`."""
        rel = _rel(rel)
        return "/".join(x for x in (self.root, rel) if x)

    def where(self, rel: str = "") -> str:
        """For people and run_info.json: "ADLS:<root>/<rel>"."""
        return f"ADLS:{self.path(rel)}"

    def files(self, rel: str) -> list[str]:
        """Every file below `rel`, relative to it, sorted ([] when it does not
        exist)."""
        top = self.path(rel)
        try:
            paths = list(self.fs.get_paths(path=top, recursive=True))
        except Exception as e:  # noqa: BLE001 - only "not found" is an answer
            if is_not_found(e):
                return []
            raise
        prefix = top + "/"
        return sorted(p.name[len(prefix):] for p in paths
                      if not p.is_directory and p.name.startswith(prefix))

    def download_file(self, rel: str, local_path: str) -> None:
        """One file to `local_path` (its folder is created)."""
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        stream = self.fs.get_file_client(self.path(rel)).download_file()
        with open(local_path, "wb") as fh:
            stream.readinto(fh)

    def download_tree(self, rel: str, local_dir: str, skip=()) -> int:
        """Every file below `rel` into `local_dir` - except those under the
        first-level folders named in `skip` (a run folder's Outputs/).
        Returns how many."""
        skip = set(skip)
        names = [n for n in self.files(rel) if n.split("/", 1)[0] not in skip]

        def one(name):
            self.download_file(f"{_rel(rel)}/{name}",
                               os.path.join(local_dir, *name.split("/")))

        _parallel(one, names)
        return len(names)

    def upload_file(self, local_path: str, rel: str) -> None:
        """One file to `rel`, replacing it; streamed, so a large trace.nc is
        never read into memory at once."""
        with open(local_path, "rb") as fh:
            self.fs.get_file_client(self.path(rel)).upload_data(fh, overwrite=True)

    def upload_tree(self, local_dir: str, rel: str) -> int:
        """Every file below `local_dir` to `rel`, replacing what is there.
        Returns how many."""
        pairs = []
        for here, _dirs, files in os.walk(local_dir):
            for name in files:
                full = os.path.join(here, name)
                sub = os.path.relpath(full, local_dir).replace(os.sep, "/")
                pairs.append((full, posixpath.join(_rel(rel), sub)))
        _parallel(lambda pair: self.upload_file(*pair), pairs)
        return len(pairs)


def _parallel(fn, items) -> None:
    items = list(items)
    if not items:
        return
    with ThreadPoolExecutor(max_workers=max(1, min(THREADS, len(items)))) as pool:
        list(pool.map(fn, items))


def connect(tenant_id: str, client_id: str, client_secret: str, account_name: str,
            file_system: str, root: str = DEFAULT_ROOT) -> AdlsStore:
    """An AdlsStore on the storage account, signed in as the service
    principal - as the web app's src/files.py does."""
    from azure.identity import ClientSecretCredential
    from azure.storage.filedatalake import DataLakeServiceClient

    credential = ClientSecretCredential(tenant_id=str(tenant_id).strip(),
                                        client_id=str(client_id).strip(),
                                        client_secret=str(client_secret).strip())
    service = DataLakeServiceClient(
        account_url=f"https://{str(account_name).strip()}.dfs.core.windows.net",
        credential=credential)
    return AdlsStore(service.get_file_system_client(str(file_system).strip()), root)


def connect_from_secrets(get_secret, scope: str | None = None, keys: dict | None = None,
                         root: str = DEFAULT_ROOT) -> AdlsStore:
    """`connect` with the credentials `get_secret(scope, key)` returns -
    dbutils.secrets.get in demo.ipynb. A blank scope is SECRET_SCOPE; `keys`
    replaces any of SECRET_KEYS."""
    scope = str(scope or "").strip() or SECRET_SCOPE
    names = {**SECRET_KEYS, **(keys or {})}
    return connect(root=root, **{arg: get_secret(scope, key) for arg, key in names.items()})
