"""Real-process evidence for project-directory run exclusion and recovery."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import signal
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INTEGRATION_ROOT = Path(__file__).resolve().parent
WAIT_SECONDS = 10


def test_ci_runs_live_lock_suite_on_linux_and_macos():
    workflow = (REPO_ROOT / ".github/workflows/orchestrator-tests.yml").read_text(
        encoding="utf-8"
    )

    assert "ubuntu-latest" in workflow
    assert "macos-latest" in workflow
    assert "pytest -v tests/integration/test_live_run_lock.py" in workflow


@dataclass
class OwnedProcess:
    process: subprocess.Popen[str]
    pgid: int
    output_path: Path
    output_handle: object

    def output(self) -> str:
        self.output_handle.flush()
        return self.output_path.read_text(encoding="utf-8")[-4000:]


def _wait_for(path: Path, *, owned: OwnedProcess | None = None) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if path.exists():
            return
        if owned is not None and owned.process.poll() is not None:
            pytest.fail(
                f"process exited before {path.name}: {owned.process.returncode}\n"
                f"output={owned.output()}"
            )
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {path}")


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _cleanup(owned: OwnedProcess) -> None:
    if _group_exists(owned.pgid):
        os.killpg(owned.pgid, signal.SIGTERM)
    try:
        owned.process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    deadline = time.monotonic() + 1
    while _group_exists(owned.pgid) and time.monotonic() < deadline:
        time.sleep(0.02)
    if _group_exists(owned.pgid):
        os.killpg(owned.pgid, signal.SIGKILL)
        try:
            owned.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        deadline = time.monotonic() + 2
        while _group_exists(owned.pgid) and time.monotonic() < deadline:
            time.sleep(0.02)
    owned.output_handle.close()
    assert not _group_exists(owned.pgid), f"owned process group {owned.pgid} survived"


def _entry_metadata(path: Path) -> dict[str, object]:
    metadata = path.lstat()
    common: dict[str, object] = {
        "mode": stat.S_IMODE(metadata.st_mode),
        "size": metadata.st_size,
    }
    if stat.S_ISREG(metadata.st_mode):
        common.update(type="file", sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    elif stat.S_ISDIR(metadata.st_mode):
        common.update(type="directory")
    elif stat.S_ISLNK(metadata.st_mode):
        common.update(type="symlink", target=os.readlink(path))
    else:
        common.update(type="other")
    return common


def _tree_manifest(root: Path) -> dict[str, dict[str, object]]:
    manifest = {".": _entry_metadata(root)}
    for path in sorted(root.rglob("*")):
        manifest[path.relative_to(root).as_posix()] = _entry_metadata(path)
    return manifest


def _prepare_control(tmp_path: Path) -> Path:
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    control.chmod(0o700)
    (control / "sitecustomize.py").write_text(
        "from live_run_lock_child import install\ninstall()\n", encoding="utf-8"
    )
    return control


def _environment(tmp_path: Path, control: Path, role: str) -> dict[str, str]:
    env = dict(os.environ)
    python_path = os.pathsep.join((str(control), str(INTEGRATION_ROOT)))
    if env.get("PYTHONPATH"):
        python_path = os.pathsep.join((python_path, env["PYTHONPATH"]))
    env.update(
        {
            "AI_ORCHESTRATION_FAKE_PROVIDERS": "1",
            "AI_ORCHESTRATION_TEST_CONTROL_DIR": str(control),
            "AI_ORCHESTRATION_TEST_ROLE": role,
            "PYTHONPATH": python_path,
            "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        }
    )
    return env


def _command(workspace: Path, *, resume: bool = False) -> list[str]:
    command = [
        "uv",
        "run",
        "--offline",
        "--no-sync",
        "ai-orchestration",
        "prove live lock",
        "--workspace",
        str(workspace),
        "--project-name",
        "locked",
        "--planner",
        "gpt-5.5",
        "--auto-select",
        "--auto-run",
        "--auto-approve",
        "--skip-review",
    ]
    if resume:
        command.append("--resume")
    return command


def _start(
    tmp_path: Path,
    command: list[str],
    env: dict[str, str],
    role: str,
) -> OwnedProcess:
    output_path = tmp_path / f"{role}.output.log"
    output_handle = output_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=output_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return OwnedProcess(process, process.pid, output_path, output_handle)


def _events(control: Path, role: str) -> list[dict[str, object]]:
    path = control / f"{role}.provider.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _assert_no_audit_events(control: Path) -> None:
    assert list(control.glob("*.audit.jsonl")) == []


@pytest.mark.parametrize(
    ("code", "event"),
    [
        ("import socket; socket.socket().connect(('127.0.0.1', 9))", "socket.connect"),
        (
            "import socket; socket.getaddrinfo('example.invalid', 80)",
            "socket.getaddrinfo",
        ),
        ("import subprocess; subprocess.run(['true'])", "subprocess.Popen"),
    ],
)
def test_child_audit_hook_denies_external_effects(tmp_path, code, event):
    control = _prepare_control(tmp_path)
    env = _environment(tmp_path, control, "holder")

    result = subprocess.run(
        ["uv", "run", "--offline", "--no-sync", "python", "-c", code],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=WAIT_SECONDS,
    )

    assert result.returncode != 0
    assert (control / "holder.audit-ready").exists()
    records = [
        json.loads(line)
        for line in (control / "holder.audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert records == [{"role": "holder", "event": event}]


def test_contender_cannot_mutate_held_run(tmp_path):
    workspace = tmp_path / "workspace"
    control = _prepare_control(tmp_path)
    processes: list[OwnedProcess] = []
    holder = _start(
        tmp_path,
        _command(workspace),
        _environment(tmp_path, control, "holder"),
        "holder",
    )
    processes.append(holder)
    try:
        _wait_for(control / "holder.audit-ready", owned=holder)
        _wait_for(control / "holder.ready", owned=holder)
        project = workspace / "locked"
        state_path = project / ".ai_orchestration" / "run_state.json"
        before_tree = _tree_manifest(project)
        before_state = state_path.read_bytes()

        contender = _start(
            tmp_path,
            _command(workspace),
            _environment(tmp_path, control, "contender"),
            "contender",
        )
        processes.append(contender)
        contender.process.wait(timeout=WAIT_SECONDS)

        assert contender.process.returncode == 1
        assert (control / "contender.audit-ready").exists()
        assert "another run already holds the lock" in contender.output()
        assert _tree_manifest(project) == before_tree
        assert state_path.read_bytes() == before_state
        assert _events(control, "contender") == []
        _assert_no_audit_events(control)

        (control / "holder.release").touch()
        holder.process.wait(timeout=WAIT_SECONDS)
        assert holder.process.returncode == 0, holder.output()
    finally:
        for process in reversed(processes):
            _cleanup(process)


@pytest.mark.parametrize("release_mode", ["provider_failure", "forced_kill"])
def test_resume_after_holder_release(tmp_path, release_mode):
    workspace = tmp_path / "workspace"
    control = _prepare_control(tmp_path)
    processes: list[OwnedProcess] = []
    holder = _start(
        tmp_path,
        _command(workspace),
        _environment(tmp_path, control, "holder"),
        "holder",
    )
    processes.append(holder)
    try:
        _wait_for(control / "holder.audit-ready", owned=holder)
        _wait_for(control / "holder.ready", owned=holder)
        state_path = workspace / "locked" / ".ai_orchestration" / "run_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        run_id = state["run_id"]
        assert str(uuid.UUID(run_id)) == run_id
        assert state["schema_version"] == 2
        assert state["completed_stages"] == [
            "brainstormer",
            "brainstorming_reviewer",
        ]
        assert state["current_stage"] == "planner"
        assert [event["stage"] for event in _events(control, "holder")] == [
            "brainstormer",
            "reviewer",
            "planner",
        ]

        contender = _start(
            tmp_path,
            _command(workspace),
            _environment(tmp_path, control, "contender"),
            "contender",
        )
        processes.append(contender)
        contender.process.wait(timeout=WAIT_SECONDS)
        assert contender.process.returncode == 1
        assert (control / "contender.audit-ready").exists()
        assert _events(control, "contender") == []

        if release_mode == "provider_failure":
            (control / "holder.fail").touch()
            (control / "holder.release").touch()
            holder.process.wait(timeout=WAIT_SECONDS)
            assert holder.process.returncode == 1, holder.output()
            assert "test provider failure" in holder.output()
        else:
            os.killpg(holder.pgid, signal.SIGKILL)
            holder.process.wait(timeout=WAIT_SECONDS)

        resumer = _start(
            tmp_path,
            _command(workspace, resume=True),
            _environment(tmp_path, control, "resumer"),
            "resumer",
        )
        processes.append(resumer)
        _wait_for(control / "resumer.audit-ready", owned=resumer)
        _wait_for(control / "resumer.ready", owned=resumer)
        resumed_at_boundary = json.loads(state_path.read_text(encoding="utf-8"))
        assert resumed_at_boundary["run_id"] == run_id
        assert [event["stage"] for event in _events(control, "resumer")] == ["planner"]

        (control / "resumer.release").touch()
        resumer.process.wait(timeout=WAIT_SECONDS)
        assert resumer.process.returncode == 0, resumer.output()
        final_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert final_state["run_id"] == run_id
        _assert_no_audit_events(control)
    finally:
        for process in reversed(processes):
            try:
                _cleanup(process)
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    raise
