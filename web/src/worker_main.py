"""A BRIDGE worker process: runs the heavy codebase 1 calls for the app.

src/codebase.py starts it as `python -m src.worker_main` (cwd = web/) and
talks to it over its stdin/stdout: each message is an 8-byte length and a
pickled object - a request in, ("ok", result) or ("error", text) out. It runs
one request at a time and exits when the app closes the pipe.

Anything the code prints goes to stderr (the app's log): file descriptor 1 is
moved to stderr before anything else runs, so not even output from a C
library can land in the reply stream.
"""
import os
import pickle
import struct
import sys


def main() -> None:
    replies = os.fdopen(os.dup(1), "wb")       # the reply pipe, kept private
    os.dup2(2, 1)                              # fd 1 (print, C libraries) -> stderr
    sys.stdout = sys.stderr
    requests_in = sys.stdin.buffer
    os.environ["BRIDGE_WORKER_PROCESS"] = "1"
    from src import codebase
    codebase._worker_init()
    while True:
        head = requests_in.read(8)
        if not head or len(head) < 8:
            return                             # the app closed the pipe
        size = struct.unpack(">Q", head)[0]
        body = requests_in.read(size)
        try:
            reply = ("ok", codebase._worker_handle(pickle.loads(body)))
        except BaseException as e:  # noqa: BLE001 - reported, the worker carries on
            reply = ("error", f"{type(e).__name__}: {e}")
        data = pickle.dumps(reply, protocol=pickle.HIGHEST_PROTOCOL)
        replies.write(struct.pack(">Q", len(data)))
        replies.write(data)
        replies.flush()


if __name__ == "__main__":
    main()
