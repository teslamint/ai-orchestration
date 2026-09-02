"""Install a content-free audit hook for live run-lock child processes."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

_DENIED_EVENTS = {
    "socket.connect",
    "socket.connect_ex",
    "socket.getaddrinfo",
    "socket.gethostbyaddr",
    "socket.gethostbyname",
    "socket.gethostbyname_ex",
    "subprocess.Popen",
}


def install() -> None:
    control = Path(os.environ["AI_ORCHESTRATION_TEST_CONTROL_DIR"])
    role = os.environ["AI_ORCHESTRATION_TEST_ROLE"]
    metadata = control.lstat()
    if (
        control.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or role not in {"holder", "contender", "resumer"}
    ):
        raise RuntimeError("invalid test audit control")
    path = control / f"{role}.audit.jsonl"
    ready = control / f"{role}.audit-ready"
    no_follow = getattr(os, "O_NOFOLLOW", 0)

    def audit(event: str, _args: tuple[object, ...]) -> None:
        if event not in _DENIED_EVENTS:
            return
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | no_follow,
            0o600,
        )
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RuntimeError("audit log must be regular")
            record = json.dumps({"role": role, "event": event}, separators=(",", ":"))
            os.write(fd, f"{record}\n".encode())
        finally:
            os.close(fd)
        raise RuntimeError(f"test audit denied {event}")

    sys.addaudithook(audit)
    ready_fd = os.open(ready, os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow, 0o600)
    os.close(ready_fd)
