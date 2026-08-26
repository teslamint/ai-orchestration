"""Durable run-state tests: atomic persistence, resume, fresh-rerun default (U4).

Covers S3 (fresh rerun by default; --resume continues without re-running
completed stages) and the mutation matrix's "stage completion checkpoint"
and "run init" transitions.
"""

import json
import os

import pytest

from ai_orchestration.engine.state import RunState, load_state, save_state


def _make_state(**overrides) -> RunState:
    defaults = dict(
        goal="build a thing",
        project_name="build_a_thing",
        config_snapshot={"executor": "claude-sonnet-5"},
        completed_stages=[],
        current_stage="brainstormer",
        outputs={},
        logs=[],
        pause_reason=None,
    )
    defaults.update(overrides)
    return RunState(**defaults)


def test_run_state_schema_version_defaults_to_one():
    state = _make_state()
    assert state.schema_version == 1


def test_save_and_load_round_trips_all_fields(tmp_path):
    path = tmp_path / "state.json"
    state = _make_state(
        completed_stages=["brainstormer", "brainstorming_reviewer"],
        current_stage="planner",
        outputs={"brainstormer": "idea text"},
        logs=[{"stage": "brainstormer", "event": "completed"}],
    )
    save_state(state, path)
    loaded = load_state(path)
    assert loaded == state


def test_load_state_missing_file_returns_none(tmp_path):
    assert load_state(tmp_path / "does-not-exist.json") is None


def test_save_state_is_atomic_via_os_replace(tmp_path, monkeypatch):
    # Mutation-guarded: replacing os.replace with a plain rename-then-write
    # (or removing the temp-file step) must fail this test.
    path = tmp_path / "state.json"
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    save_state(_make_state(), path)
    assert len(calls) == 1
    tmp_src, dst = calls[0]
    assert dst == str(path)
    assert tmp_src != str(path)  # written to a distinct temp path first


def test_save_state_leaves_no_temp_file_after_success(tmp_path):
    path = tmp_path / "state.json"
    save_state(_make_state(), path)
    remaining = list(tmp_path.iterdir())
    assert remaining == [path]


def test_save_state_failure_before_replace_leaves_prior_state_valid(
    tmp_path, monkeypatch
):
    # Forced-failure matrix row: inject a write failure before os.replace;
    # the prior committed state file must remain valid and untouched.
    path = tmp_path / "state.json"
    initial = _make_state(current_stage="brainstormer")
    save_state(initial, path)

    def failing_replace(src, dst):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        save_state(_make_state(current_stage="planner"), path)

    # os.replace was never reached with a valid swap, so the original file
    # is untouched.
    reloaded = load_state(path)
    assert reloaded == initial


def test_load_state_corrupt_json_raises_state_error(tmp_path):
    from ai_orchestration.errors import StateError

    path = tmp_path / "state.json"
    path.write_text("{not valid json")
    with pytest.raises(StateError):
        load_state(path)


# --- Persisted-boundary type validation (finding #2, P0) -------------------


def test_load_state_rejects_wrong_typed_outputs_field(tmp_path):
    from ai_orchestration.errors import StateError

    path = tmp_path / "state.json"
    payload = {
        "goal": "g",
        "project_name": "p",
        "config_snapshot": {},
        "outputs": 12345,  # must be a dict, not an int
    }
    path.write_text(json.dumps(payload))
    with pytest.raises(StateError):
        load_state(path)


def test_load_state_rejects_wrong_typed_config_snapshot_field(tmp_path):
    from ai_orchestration.errors import StateError

    path = tmp_path / "state.json"
    payload = {
        "goal": "g",
        "project_name": "p",
        "config_snapshot": "not-a-dict",
    }
    path.write_text(json.dumps(payload))
    with pytest.raises(StateError):
        load_state(path)


def test_load_state_rejects_non_string_completed_stages_items(tmp_path):
    from ai_orchestration.errors import StateError

    path = tmp_path / "state.json"
    payload = {
        "goal": "g",
        "project_name": "p",
        "config_snapshot": {},
        "completed_stages": ["brainstormer", 7],
    }
    path.write_text(json.dumps(payload))
    with pytest.raises(StateError):
        load_state(path)


def test_load_state_rejects_non_dict_logs_items(tmp_path):
    from ai_orchestration.errors import StateError

    path = tmp_path / "state.json"
    payload = {
        "goal": "g",
        "project_name": "p",
        "config_snapshot": {},
        "logs": [{"stage": "ok"}, "not-a-dict-entry"],
    }
    path.write_text(json.dumps(payload))
    with pytest.raises(StateError):
        load_state(path)


def test_load_state_rejects_boolean_schema_version(tmp_path):
    # Booleans are ints in Python; a persisted JSON `true`/`false` for
    # schema_version must never silently pass as 1/0.
    from ai_orchestration.errors import StateError

    path = tmp_path / "state.json"
    payload = {
        "goal": "g",
        "project_name": "p",
        "config_snapshot": {},
        "schema_version": True,
    }
    path.write_text(json.dumps(payload))
    with pytest.raises(StateError):
        load_state(path)


def test_load_state_rejects_non_object_root(tmp_path):
    from ai_orchestration.errors import StateError

    path = tmp_path / "state.json"
    path.write_text(json.dumps(["not", "an", "object"]))
    with pytest.raises(StateError):
        load_state(path)


def test_load_state_error_never_leaks_secret_looking_field_value(tmp_path):
    """A malformed field's raw value must never appear in the raised
    `StateError`'s message, since a wrong-typed field could itself carry
    a secret pasted into the wrong slot (finding #2's crafted-state
    disclosure repro).
    """
    from ai_orchestration.errors import StateError

    secret = "sk-ant-SECRET_TEST_KEY_XYZ_do_not_leak"
    path = tmp_path / "state.json"
    payload = {
        "goal": "g",
        "project_name": "p",
        "config_snapshot": {},
        # Wrong type (str instead of dict) but holds a secret-looking value.
        "outputs": secret,
    }
    path.write_text(json.dumps(payload))
    with pytest.raises(StateError) as excinfo:
        load_state(path)
    assert secret not in str(excinfo.value)


def test_run_state_records_completed_stages_in_order():
    state = _make_state(completed_stages=["brainstormer", "brainstorming_reviewer"])
    assert state.completed_stages == ["brainstormer", "brainstorming_reviewer"]


def test_run_state_with_pause_reason_is_resumable(tmp_path):
    path = tmp_path / "state.json"
    state = _make_state(
        current_stage="executor",
        pause_reason="command gate requires --auto-run",
    )
    save_state(state, path)
    loaded = load_state(path)
    assert loaded.pause_reason == "command gate requires --auto-run"


# --- Fresh rerun vs. resume (S3) --------------------------------------------


def test_resolve_run_start_fresh_by_default_ignores_stale_state(tmp_path):
    from ai_orchestration.engine.state import resolve_run_start

    path = tmp_path / "state.json"
    save_state(
        _make_state(
            completed_stages=["brainstormer"], current_stage="brainstorming_reviewer"
        ),
        path,
    )
    start = resolve_run_start(path, resume=False)
    assert start.completed_stages == []
    assert start.current_stage is None


def test_resolve_run_start_resume_continues_from_saved_state(tmp_path):
    from ai_orchestration.engine.state import resolve_run_start

    path = tmp_path / "state.json"
    saved = _make_state(
        completed_stages=["brainstormer"], current_stage="brainstorming_reviewer"
    )
    save_state(saved, path)
    start = resolve_run_start(path, resume=True)
    assert start.completed_stages == ["brainstormer"]
    assert start.current_stage == "brainstorming_reviewer"


def test_resolve_run_start_resume_with_no_saved_state_starts_fresh(tmp_path):
    from ai_orchestration.engine.state import resolve_run_start

    path = tmp_path / "state.json"
    start = resolve_run_start(path, resume=True)
    assert start.completed_stages == []
    assert start.current_stage is None


# --- Two-process resume fixture (integration) -------------------------------


def test_two_process_resume_skips_completed_stages(tmp_path):
    """A second process resuming from saved state must not re-run
    completed stages and must continue from the persisted current_stage.
    """
    path = tmp_path / "state.json"

    # Process 1: completes two stages then "crashes" (never writes further).
    state_after_two_stages = _make_state(
        completed_stages=["brainstormer", "brainstorming_reviewer"],
        current_stage="planner",
        outputs={"brainstormer": "ideas", "brainstorming_reviewer": "refined"},
    )
    save_state(state_after_two_stages, path)

    # Process 2: resumes. It must see exactly the completed stages saved by
    # process 1 and continue from "planner", never re-running the first two.
    from ai_orchestration.engine.state import resolve_run_start

    resumed = resolve_run_start(path, resume=True)
    assert resumed.completed_stages == ["brainstormer", "brainstorming_reviewer"]
    assert resumed.current_stage == "planner"
    assert resumed.outputs["brainstormer"] == "ideas"


def test_state_file_json_is_stable_and_readable(tmp_path):
    # edge: written state must be plain JSON, not a pickled/opaque format,
    # so an operator can inspect it directly during recovery.
    path = tmp_path / "state.json"
    save_state(_make_state(current_stage="executor"), path)
    raw = json.loads(path.read_text())
    assert raw["current_stage"] == "executor"
    assert raw["schema_version"] == 1


# --- Concurrent-run lock (finding #5) ---------------------------------------


def test_acquire_run_lock_blocks_second_concurrent_holder(tmp_path):
    from ai_orchestration.engine.state import RunLockedError, acquire_run_lock

    path = tmp_path / "run_state.json"
    with acquire_run_lock(path):
        with pytest.raises(RunLockedError):
            with acquire_run_lock(path):
                pass  # pragma: no cover - must not be reached


def test_acquire_run_lock_releases_on_exit_for_next_holder(tmp_path):
    from ai_orchestration.engine.state import acquire_run_lock

    path = tmp_path / "run_state.json"
    with acquire_run_lock(path):
        pass
    # Must not raise: the first holder released on context exit.
    with acquire_run_lock(path):
        pass


def test_acquire_run_lock_releases_even_when_body_raises(tmp_path):
    from ai_orchestration.engine.state import acquire_run_lock

    path = tmp_path / "run_state.json"
    with pytest.raises(ValueError):
        with acquire_run_lock(path):
            raise ValueError("boom")
    # The lock must still be released despite the exception.
    with acquire_run_lock(path):
        pass


def test_acquire_run_lock_creates_parent_directories(tmp_path):
    from ai_orchestration.engine.state import acquire_run_lock

    path = tmp_path / "nested" / "dir" / "run_state.json"
    with acquire_run_lock(path):
        pass
    assert path.parent.exists()


def test_acquire_run_lock_survives_stray_lock_file_deletion(tmp_path):
    """finding #7 repro: the pre-fix lock lived on a disposable sibling
    `.lock` file; deleting that file out from under the holder let a
    second acquirer's `os.open(..., O_CREAT)` mint a fresh inode and
    believe it held its own exclusive lock, while the first holder still
    believed it held the (now orphaned) original. The fix locks the
    run-state directory itself -- a load-bearing object, not a disposable
    marker -- so deleting an incidental `.lock`-named file next to it has
    zero effect on the held lock: a second acquirer must still be refused.
    """
    from ai_orchestration.engine.state import RunLockedError, acquire_run_lock

    path = tmp_path / "run_state.json"
    with acquire_run_lock(path):
        stray_lock_file = path.parent / f".{path.name}.lock"
        stray_lock_file.write_text("")
        stray_lock_file.unlink()  # the old vulnerable file, deleted mid-hold
        with pytest.raises(RunLockedError):
            with acquire_run_lock(path):
                pass  # pragma: no cover - must not be reached


def test_acquire_run_lock_survives_state_directory_recreation(tmp_path):
    from ai_orchestration.engine.state import RunLockedError, acquire_run_lock

    path = tmp_path / "project" / ".ai_orchestration" / "run_state.json"
    with acquire_run_lock(path):
        os.rmdir(path.parent)
        path.parent.mkdir()
        with pytest.raises(RunLockedError):
            with acquire_run_lock(path):
                pass
