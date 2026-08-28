"""Typer CLI: `ai-orchestration` entrypoint.

Wires U1's config/routing, U2's models/prompts/utilities, U3's providers,
and U4's engine into the six-stage pipeline. Every existing CLI option is
preserved (§Interface). Each stage flag accepts a proxy model, an exact CLI
binary, or one reserved direct-vendor literal.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel

from ai_orchestration.config import (
    DIRECT_VENDOR_VALUES,
    CatalogOutcome,
    CatalogStatus,
    OrchestratorConfig,
    ProviderConfig,
    SlotKind,
    StageConfig,
    classify_slot,
    requires_catalog_probe,
    resolve_workspace_base,
    select_stage_config,
    validate_stage_config,
    warn_reserved_catalog_collisions,
)
from ai_orchestration.engine.gates import ApprovalGate, PausedRun, select_fix_items
from ai_orchestration.engine.loops import (
    run_executor_self_healing,
    run_main_review_fix_loop,
    run_ralph_wiggum_loop,
)
from ai_orchestration.engine.stages import CommandExecutor, parse_approach_options
from ai_orchestration.engine.state import (
    RunLockedError,
    RunState,
    acquire_run_lock,
    resolve_run_start,
    save_state,
)
from ai_orchestration.errors import (
    ConfigError,
    OrchestrationError,
    RoutingError,
    StateError,
    TaskExecutionError,
)
from ai_orchestration.models.context import (
    ActionType,
    CodeReviewItem,
    CodeReviewResult,
    IterationMetadata,
    OrchestrationContext,
    RalphWiggumFeedback,
    Task,
)
from ai_orchestration.prompts.stages import AGENT_PROMPTS
from ai_orchestration.providers.base import ToolType, TransportError
from ai_orchestration.providers.call_control import (
    AuthorizationGate,
    NoOpAuthorizationGate,
    SocketAuthorizationGate,
)
from ai_orchestration.providers.call_events import CallRecorder
from ai_orchestration.providers.cli import AgyProvider, ClaudeProvider, CodexProvider
from ai_orchestration.providers.http import HttpProvider, probe_catalog
from ai_orchestration.providers.legacy_api import (
    create_direct_vendor_provider,
    validate_direct_vendor,
)
from ai_orchestration.providers.routing import (
    complete_structured_with_fallback,
    complete_with_fallback,
)
from ai_orchestration.utils.extract import extract_code_content, extract_json_list
from ai_orchestration.utils.slug import generate_project_name

app = typer.Typer(pretty_exceptions_show_locals=False)
console = Console()

_STAGE_FLAG_NAMES = {
    "brainstormer": "--brainstormer",
    "reviewer": "--reviewer",
    "planner": "--planner",
    "executor": "--executor",
    "code_reviewer": "--code-reviewer",
    "fixer": "--fixer",
}

_CLI_PROVIDER_CLASSES: dict[str, type] = {
    "agy": AgyProvider,
    "codex": CodexProvider,
    "claude": ClaudeProvider,
}

# Env var read once at process start: enables an offline smoke path for the
# installed console script without a live proxy or real CLI binaries.
# U6's integration suite is the only consumer that needs the real network;
# this keeps U5's own smoke test (subprocess-launched) offline too.
_FAKE_PROVIDERS_ENV_VAR = "AI_ORCHESTRATION_FAKE_PROVIDERS"
_TEST_CONTROL_DIR_ENV_VAR = "AI_ORCHESTRATION_TEST_CONTROL_DIR"
_TEST_ROLE_ENV_VAR = "AI_ORCHESTRATION_TEST_ROLE"
_active_test_stage: ContextVar[str | None] = ContextVar(
    "active_test_stage", default=None
)


def _test_control_dir() -> Path | None:
    value = os.environ.get(_TEST_CONTROL_DIR_ENV_VAR)
    if value is None:
        return None
    if os.environ.get(_FAKE_PROVIDERS_ENV_VAR) != "1":
        raise ConfigError(
            f"{_TEST_CONTROL_DIR_ENV_VAR} requires {_FAKE_PROVIDERS_ENV_VAR}=1"
        )
    control = Path(value)
    try:
        metadata = control.lstat()
    except OSError as exc:
        raise ConfigError("test control directory is unavailable") from exc
    if (
        control.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ConfigError("test control directory must be a non-symlink mode 0700")
    return control


def _test_role() -> str:
    role = os.environ.get(_TEST_ROLE_ENV_VAR)
    if role not in {"holder", "contender", "resumer"}:
        raise ConfigError("test process role is invalid")
    return role


def _open_control_file(path: Path, flags: int) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags | no_follow, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise StateError("test control file must be regular")
    return fd


def _record_test_provider_call(stage: str) -> None:
    control = _test_control_dir()
    if control is None:
        return
    role = _test_role()
    path = control / f"{role}.provider.jsonl"
    try:
        fd = _open_control_file(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            event = json.dumps(
                {"role": role, "stage": stage, "pid": os.getpid()},
                separators=(",", ":"),
            )
            os.write(fd, f"{event}\n".encode())
        finally:
            os.close(fd)
    except OSError as exc:
        raise StateError("could not write the test provider spy") from exc


def _wait_at_test_planner_barrier(stage: str) -> None:
    control = _test_control_dir()
    if control is None or stage != "planner":
        return
    role = _test_role()
    ready = control / f"{role}.ready"
    release = control / f"{role}.release"
    try:
        fd = _open_control_file(ready, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            event = json.dumps(
                {"role": role, "stage": stage, "pid": os.getpid()},
                separators=(",", ":"),
            )
            os.write(fd, event.encode())
        finally:
            os.close(fd)
    except OSError as exc:
        raise StateError("could not publish the test planner barrier") from exc
    deadline = time.monotonic() + 15
    while True:
        try:
            fd = _open_control_file(release, os.O_RDONLY)
        except FileNotFoundError:
            fd = None
        except OSError as exc:
            raise StateError("test planner release marker is invalid") from exc
        if fd is not None:
            os.close(fd)
            break
        if time.monotonic() >= deadline:
            raise StateError("test planner barrier timed out")
        time.sleep(0.02)
    failure = control / f"{role}.fail"
    try:
        fd = _open_control_file(failure, os.O_RDONLY)
    except FileNotFoundError:
        fd = None
    except OSError as exc:
        raise StateError("test provider failure marker is invalid") from exc
    if fd is not None:
        os.close(fd)
        raise TransportError("test provider failure")


class _OfflineSmokeProvider:
    """Deterministic offline stand-in used only when
    ``AI_ORCHESTRATION_FAKE_PROVIDERS=1`` is set in the environment.
    """

    def complete(self, prompt: str, *, system=None, **kwargs) -> str:
        stage = _active_test_stage.get()
        if stage is None:
            raise StateError("fake provider stage is unavailable")
        _record_test_provider_call(stage)
        _wait_at_test_planner_barrier(stage)
        return (
            '[{"step_id": 1, "file_path": "hello.py", '
            '"action_type": "create_file", "instruction": "write hello world"}]'
        )

    def complete_structured(self, prompt: str, *, schema, **kwargs):
        stage = _active_test_stage.get()
        if stage is None:
            raise StateError("fake provider stage is unavailable")
        _record_test_provider_call(stage)
        _wait_at_test_planner_barrier(stage)
        values = {}
        for field_name, field_info in schema.model_fields.items():
            if not field_info.is_required():
                continue
            annotation = str(field_info.annotation)
            if field_name in ("step_id", "total_files_reviewed"):
                values[field_name] = 1
            elif field_name == "requires_fixes":
                values[field_name] = False
            elif annotation.startswith("typing.List") or annotation.startswith("list"):
                values[field_name] = []
            else:
                values[field_name] = "x"
        return schema(**values)

    def is_available(self) -> bool:
        return True


# --- Provider factory seams (overridden by tests, real by default) ---------

# Startup-resolved endpoint config, read by the real (non-monkeypatched)
# `_http_provider_factory` below. Tests always replace the whole factory,
# so they never observe this global; it exists solely so a custom
# `--tool-config` `"provider"` block reaches every real completion call,
# not just startup catalog validation (finding #3).
_active_provider_config = ProviderConfig()
_active_authorization_gate: AuthorizationGate = NoOpAuthorizationGate()


def _authorization_gate_from_environment(environ: dict[str, str]) -> AuthorizationGate:
    socket_path = environ.get("AI_ORCHESTRATION_AUTH_SOCKET")
    payload_path = environ.get("AI_ORCHESTRATION_AUTH_PAYLOAD")
    if not socket_path and not payload_path:
        return NoOpAuthorizationGate()
    if not socket_path or not payload_path:
        raise ConfigError(
            "AI_ORCHESTRATION_AUTH_SOCKET and AI_ORCHESTRATION_AUTH_PAYLOAD "
            "must be set together"
        )
    try:
        timeout = float(environ.get("AI_ORCHESTRATION_AUTH_TIMEOUT", "300"))
    except ValueError as exc:
        raise ConfigError("AI_ORCHESTRATION_AUTH_TIMEOUT must be numeric") from exc
    try:
        return SocketAuthorizationGate(Path(socket_path), Path(payload_path), timeout)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _http_provider_factory(model: str):
    """Default HTTP provider factory: real CLIProxyAPI via the resolved endpoint."""
    if os.environ.get(_FAKE_PROVIDERS_ENV_VAR) == "1":
        return _OfflineSmokeProvider()
    return HttpProvider(
        model=model,
        base_url=_active_provider_config.base_url,
        api_key=_active_provider_config.api_key,
        authorization_gate=_active_authorization_gate,
    )


def _cli_provider_factory(binary: str):
    """Default CLI provider factory: real subprocess provider for `binary`."""
    if os.environ.get(_FAKE_PROVIDERS_ENV_VAR) == "1":
        return _OfflineSmokeProvider()
    provider_class = _CLI_PROVIDER_CLASSES[binary]
    return provider_class(authorization_gate=_active_authorization_gate)


def _direct_vendor_provider_factory(tool_type: ToolType):
    """Default direct-vendor factory with the active authorization gate."""
    if os.environ.get(_FAKE_PROVIDERS_ENV_VAR) == "1":
        return _OfflineSmokeProvider()
    return create_direct_vendor_provider(
        tool_type, authorization_gate=_active_authorization_gate
    )


def _probe_catalog_for_startup(base_url: str, api_key: Optional[str]) -> CatalogStatus:
    """Default startup catalog probe: real network request."""
    if os.environ.get(_FAKE_PROVIDERS_ENV_VAR) == "1":
        return CatalogStatus(
            outcome=CatalogOutcome.REACHABLE_WITH_MODELS,
            models=frozenset(
                {"gpt-5.5", "gemini-3.1-pro-low", "claude-sonnet-5", "opus-5"}
            ),
        )
    return probe_catalog(base_url, api_key)


def _is_tty() -> bool:
    return sys.stdin.isatty()


def _confirm_interactively(prompt: str) -> bool:
    return typer.confirm(prompt)


# --- Debug logging: append-only file, mirrors legacy _write_debug_log ------


def _write_debug_log(
    debug: bool, debug_log_path: Optional[Path], stage: str, content: str
) -> None:
    if not debug or debug_log_path is None:
        return
    try:
        debug_log_path.parent.mkdir(parents=True, exist_ok=True)
        with debug_log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n\n==== {stage} ====\n")
            handle.write(content)
    except OSError as exc:
        console.print(f"[yellow]Debug log write failed: {exc}[/yellow]")


def _resolve_debug_log_path(debug: bool, debug_log: str) -> Optional[Path]:
    if not debug:
        return None
    log_path = Path(debug_log)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if log_path.suffix:
        return log_path.with_name(
            f"{log_path.stem}-{timestamp}{log_path.suffix}"
        ).resolve()
    return (log_path / f"orchestrator_debug-{timestamp}.log").resolve()


# --- Stage runners: thin wrappers over routing + prompts --------------------


def _complete_stage_text(
    stage_name: str,
    stage: StageConfig,
    prompt: str,
    *,
    debug: bool,
    debug_log_path: Optional[Path],
    recorder: CallRecorder | None = None,
    executor_step_id: int | None = None,
) -> str:
    event_stage = (
        "reviewer"
        if "reviewer" in stage_name and stage_name != "code_reviewer"
        else stage_name
    )
    token = _active_test_stage.set(event_stage)
    try:
        text, _provider_used = complete_with_fallback(
            stage,
            prompt,
            http_provider_factory=_http_provider_factory,
            cli_provider_factory=_cli_provider_factory,
            direct_provider_factory=_direct_vendor_provider_factory,
            recorder=recorder,
            stage_name=event_stage,
            executor_step_id=executor_step_id,
            system=AGENT_PROMPTS[stage_name].get("system", ""),
        )
    finally:
        _active_test_stage.reset(token)
    _write_debug_log(debug, debug_log_path, f"{stage_name} raw output", text)
    return text


def _complete_stage_structured(
    stage: StageConfig,
    prompt: str,
    *,
    schema,
    debug: bool,
    debug_log_path: Optional[Path],
    stage_name: str,
    recorder: CallRecorder | None = None,
    executor_step_id: int | None = None,
):
    event_stage = (
        "reviewer"
        if "reviewer" in stage_name and stage_name != "code_reviewer"
        else stage_name
    )
    token = _active_test_stage.set(event_stage)
    try:
        result, _provider_used = complete_structured_with_fallback(
            stage,
            prompt,
            schema=schema,
            http_provider_factory=_http_provider_factory,
            cli_provider_factory=_cli_provider_factory,
            direct_provider_factory=_direct_vendor_provider_factory,
            recorder=recorder,
            stage_name=event_stage,
            executor_step_id=executor_step_id,
            system=AGENT_PROMPTS[stage_name].get("system", ""),
        )
    finally:
        _active_test_stage.reset(token)
    _write_debug_log(
        debug, debug_log_path, f"{stage_name} structured output", repr(result)
    )
    return result


def _run_brainstormer(context: OrchestrationContext, stage: StageConfig, **d) -> None:
    prompt = AGENT_PROMPTS["brainstormer"]["user"].format(
        user_goal=context.user_goal, tooling_context="unknown"
    )
    output = _complete_stage_text("brainstormer", stage, prompt, **d)
    context.brainstorming_ideas = output


def _run_brainstorming_reviewer(
    context: OrchestrationContext, stage: StageConfig, **d
) -> None:
    ideas = context.brainstorming_ideas
    ideas_text = ideas if isinstance(ideas, str) else "\n".join(ideas)
    prompt = AGENT_PROMPTS["brainstorming_reviewer"]["user"].format(
        user_goal=context.user_goal,
        tooling_context="unknown",
        brainstorming_ideas=ideas_text,
    )
    output = _complete_stage_text("brainstorming_reviewer", stage, prompt, **d)
    context.refined_brainstorming = output


def _select_approach(
    context: OrchestrationContext,
    *,
    auto_select: bool,
    prompt_choice: Optional[callable] = None,
    prompt_custom: Optional[callable] = None,
) -> None:
    """Select an implementation approach, preserving the legacy interactive
    numbered-menu path (auto-select was previously the only branch wired).

    `prompt_choice`/`prompt_custom` are injectable so tests never need a
    real TTY; the CLI wires them to `typer.prompt` in non-auto-select mode.
    """
    ideas_to_use = context.refined_brainstorming or context.brainstorming_ideas
    ideas_text = (
        ideas_to_use if isinstance(ideas_to_use, str) else "\n".join(ideas_to_use)
    )
    options = parse_approach_options(ideas_text)

    if auto_select or prompt_choice is None:
        context.selected_approach = options[0] if options else ideas_text
        return

    console.print("\n[bold]Please select an approach:[/bold]")
    for i, opt in enumerate(options):
        console.print(f"  {i + 1}: {opt}")
    console.print(f"  {len(options) + 1}: [dim]Custom (enter your own)[/dim]")

    choice = prompt_choice()
    if 1 <= choice <= len(options):
        context.selected_approach = options[choice - 1]
    elif choice == len(options) + 1 and prompt_custom is not None:
        context.selected_approach = prompt_custom()
    else:
        console.print(
            "[yellow]Invalid selection. Using the first approach as default.[/yellow]"
        )
        context.selected_approach = options[0] if options else ideas_text


def _run_planner(context: OrchestrationContext, stage: StageConfig, **d) -> None:
    prompt = AGENT_PROMPTS["planner"]["user"].format(
        user_goal=context.user_goal,
        tooling_context="unknown",
        brainstorming_ideas=context.refined_brainstorming or "",
        selected_approach=context.selected_approach or "",
    )
    output = _complete_stage_text("planner", stage, prompt, **d)
    json_plan = extract_json_list(output)
    tasks = []
    step_ids = set()
    for item in json_plan:
        try:
            task = Task(**item)
        except Exception:
            continue
        if task.step_id in step_ids:
            raise StateError(f"planner returned duplicate task step_id {task.step_id}")
        step_ids.add(task.step_id)
        tasks.append(task)
    if json_plan and not tasks:
        raise StateError(
            "planner returned a non-empty plan but no valid tasks: "
            "every item failed Task validation"
        )
    context.implementation_plan = tasks


def _read_existing_code(context: OrchestrationContext, task: Task) -> str:
    """Read the current on-disk content of `task.file_path`, if any.

    `EDIT_FILE` tasks must see real file content to produce a real edit
    (legacy behavior); `CREATE_FILE` tasks on a not-yet-existing path
    correctly see an empty string.
    """
    target_path = context.resolve_workspace_file(task.file_path)
    if not target_path.exists():
        return ""
    try:
        return target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _run_executor(
    context: OrchestrationContext,
    stage: StageConfig,
    command_executor: CommandExecutor,
    *,
    checkpoint_task: Callable[[int], None],
    **d,
) -> None:
    completed_task_ids = set(context.completed_executor_task_ids)
    for task in context.implementation_plan:
        if task.step_id in completed_task_ids:
            continue
        if task.action_type == ActionType.RUN_COMMAND:
            success, output, _logs = command_executor.run(
                task.instruction, cwd=str(context.workspace_path)
            )
            if not success and "skipped by user" not in output.lower():
                raise TaskExecutionError(
                    f"run_command task {task.step_id} failed: {output}"
                )
            if success:
                context.completed_executor_task_ids.append(task.step_id)
                completed_task_ids.add(task.step_id)
                checkpoint_task(task.step_id)
            continue

        existing_code = _read_existing_code(context, task)

        def _complete(prompt: str, *, _task=task, _existing=existing_code) -> str:
            full_prompt = AGENT_PROMPTS["executor"]["user"].format(
                user_goal=context.user_goal,
                step_id=_task.step_id,
                action_type=_task.action_type.value,
                file_path=str(_task.file_path),
                instruction=prompt,
                existing_code=_existing,
            )
            return _complete_stage_text(
                "executor", stage, full_prompt, executor_step_id=_task.step_id, **d
            )

        result = run_executor_self_healing(context, task, complete=_complete)
        if not result.success:
            raise TaskExecutionError(f"executor task {task.step_id} failed")
        context.completed_executor_task_ids.append(task.step_id)
        completed_task_ids.add(task.step_id)
        checkpoint_task(task.step_id)


def _run_code_reviewer(context: OrchestrationContext, stage: StageConfig, **d) -> None:
    prompt = AGENT_PROMPTS["code_reviewer"]["user"].format(
        user_goal=context.user_goal,
        plan_summary=str(len(context.implementation_plan)),
        file_list=", ".join(str(t.file_path) for t in context.implementation_plan),
        execution_summary=str(len(context.execution_logs)),
        code_diffs=str(context.generated_diffs),
        file_contents="",
    )
    result = _complete_stage_structured(
        stage, prompt, schema=CodeReviewResult, stage_name="code_reviewer", **d
    )
    context.code_review_result = result


def _run_fixer(
    context: OrchestrationContext, stage: StageConfig, item: CodeReviewItem, **d
) -> None:
    target_path = context.resolve_workspace_file(item.file_path)
    current_code = (
        target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    )
    prompt = AGENT_PROMPTS["fixer"]["user"].format(
        user_goal=context.user_goal,
        file_path=str(item.file_path),
        current_code=current_code,
        review_type=item.review_type.value,
        severity=item.severity.value,
        description=item.description,
        suggestion=item.suggestion,
        line_range=f"{item.line_start}-{item.line_end}",
        code_snippet=item.code_snippet or "",
    )
    output = extract_code_content(_complete_stage_text("fixer", stage, prompt, **d))
    if not output:
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(output, encoding="utf-8")


def _run_ralph_wiggum_reviewer(
    context: OrchestrationContext, stage: StageConfig, **d
) -> None:
    prompt = AGENT_PROMPTS["ralph_wiggum_reviewer"]["user"].format(
        user_goal=context.user_goal,
        self_reference_context=context.get_self_reference_context(),
        worker_output="",
        file_list=", ".join(str(k) for k in context.generated_diffs),
        completion_promise=context.ralph_wiggum_completion_promise or "",
    )
    feedback = _complete_stage_structured(
        stage,
        prompt,
        schema=RalphWiggumFeedback,
        stage_name="ralph_wiggum_reviewer",
        **d,
    )
    context.ralph_wiggum_feedback = feedback


# --- Startup validation ------------------------------------------------------


def _select_stage(
    stage_name: str, cli_value: Optional[str], file_stages: dict
) -> StageConfig:
    try:
        return select_stage_config(
            stage_name, cli_value=cli_value, file_stages=file_stages
        )
    except ConfigError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


def _validate_selected_stage(
    stage_name: str,
    stage: StageConfig,
    catalog: CatalogStatus,
    *,
    validate_fallback_binary: bool,
) -> None:
    try:
        validate_stage_config(
            stage_name,
            stage,
            catalog,
            validate_fallback_binary=validate_fallback_binary,
        )
    except (ConfigError, RoutingError) as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


def _load_tool_config_file(path: Optional[Path]) -> dict:
    if path is None:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        console.print(f"[bold red]Error:[/bold red] malformed tool config file: {exc}")
        raise typer.Exit(code=1) from exc


def _resolve_provider_config(file_stages_raw: dict) -> ProviderConfig:
    try:
        return ProviderConfig.from_raw(file_stages_raw.get("provider"))
    except ConfigError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


# --- Typer command ------------------------------------------------------------


@app.command()
def main(
    request: str = typer.Argument(..., help="프로젝트 요구사항"),
    workspace: Optional[str] = typer.Option(None, help="작업 파일이 생성될 폴더 경로"),
    debug: bool = typer.Option(False, "--debug", help="상세 진행 로그 출력"),
    debug_log: str = typer.Option(
        "./orchestrator_debug_logs",
        "--debug-log",
        help="디버그 전체 출력 로그 경로(디렉터리 또는 파일).",
    ),
    auto_run: bool = typer.Option(
        False, "--auto-run", help="run_command 단계 자동 실행"
    ),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", help="run_command 확인을 전역으로 자동 승인"
    ),
    skip_review: bool = typer.Option(
        False, "--skip-review", help="코드 리뷰 단계(Stage 5-6) 건너뛰기"
    ),
    max_fix_iterations: int = typer.Option(
        1, "--max-fix-iterations", help="최대 리뷰-수정 반복 횟수"
    ),
    auto_fix: bool = typer.Option(
        False, "--auto-fix", help="리뷰 항목 자동 수정 (확인 없이)"
    ),
    auto_select: bool = typer.Option(
        False, "--auto-select", help="접근 방식 자동 선택 (기본값 또는 추천)"
    ),
    project_name: Optional[str] = typer.Option(
        None, "--project-name", help="프로젝트 이름 (생략 시 goal에서 자동 생성)"
    ),
    brainstormer: Optional[str] = typer.Option(
        None,
        "--brainstormer",
        help="Stage 1 브레인스토밍 도구 (agy/codex/claude 또는 프록시 모델 id)",
    ),
    reviewer: Optional[str] = typer.Option(
        None, "--reviewer", help="Stage 2 브레인스토밍 리뷰 도구"
    ),
    planner: Optional[str] = typer.Option(
        None, "--planner", help="Stage 3 계획 수립 도구"
    ),
    executor: Optional[str] = typer.Option(
        None, "--executor", help="Stage 4 코드 실행 도구"
    ),
    code_reviewer: Optional[str] = typer.Option(
        None, "--code-reviewer", help="Stage 5 코드 리뷰 도구"
    ),
    fixer: Optional[str] = typer.Option(None, "--fixer", help="Stage 6 코드 수정 도구"),
    tool_config_file: Optional[Path] = typer.Option(
        None, "--tool-config", help="LLM 도구 설정 파일 경로 (JSON)"
    ),
    enable_ralph_wiggum: bool = typer.Option(
        False, "--enable-ralph-wiggum", help="Ralph Wiggum 피드백 루프 활성화"
    ),
    ralph_wiggum_threshold: float = typer.Option(
        0.8, "--ralph-wiggum-threshold", help="Ralph Wiggum 승인 임계값 (0.0-1.0)"
    ),
    ralph_wiggum_max_iterations: int = typer.Option(
        3, "--ralph-wiggum-max-iterations", help="Ralph Wiggum 최대 반복 횟수"
    ),
    completion_promise: Optional[str] = typer.Option(
        None, "--completion-promise", help="완료 시 출력할 promise 텍스트 (예: 'DONE')"
    ),
    ralph_wiggum_state_file: bool = typer.Option(
        True,
        "--ralph-wiggum-state-file/--no-ralph-wiggum-state-file",
        help="자체 참조용 상태 파일 사용 여부",
    ),
    resume: bool = typer.Option(
        False, "--resume", help="이전에 중단된 실행을 이어서 재개 (기본값: 새로 시작)"
    ),
) -> None:
    """
    AI Orchestration Tool (6-Stage):
    agy -> Codex Review -> Codex Plan -> Claude Exec -> Codex Review -> Claude Fix
    """
    if project_name is None:
        project_name = generate_project_name(request)

    if any(ord(character) < 32 or ord(character) == 127 for character in project_name):
        console.print(
            "[bold red]Error:[/bold red] --project-name contains a control character"
        )
        raise typer.Exit(code=1)

    workspace_base = resolve_workspace_base(workspace, dict(os.environ))
    project_workspace = (workspace_base / project_name).resolve()
    resolved_workspace_base = workspace_base.resolve()
    if (
        resolved_workspace_base != project_workspace
        and resolved_workspace_base not in project_workspace.parents
    ):
        console.print(
            "[bold red]Error:[/bold red] --project-name "
            f"{project_name!r} escapes the workspace anchor"
        )
        raise typer.Exit(code=1)

    file_stages_raw = _load_tool_config_file(tool_config_file)
    stage_cli_values = {
        "brainstormer": brainstormer,
        "reviewer": reviewer,
        "planner": planner,
        "executor": executor,
        "code_reviewer": code_reviewer,
        "fixer": fixer,
    }

    provider_config = _resolve_provider_config(file_stages_raw)
    stages: dict[str, StageConfig] = {
        stage_name: _select_stage(stage_name, cli_value, file_stages_raw)
        for stage_name, cli_value in stage_cli_values.items()
    }

    for stage_name, stage in stages.items():
        if stage.fallback_model in DIRECT_VENDOR_VALUES:
            console.print(
                "[bold red]Error:[/bold red] "
                f"{stage_name}: fallback_model '{stage.fallback_model}' is reserved "
                "for direct-vendor routing"
            )
            raise typer.Exit(code=1)

    if requires_catalog_probe(stages):
        catalog = _probe_catalog_for_startup(
            provider_config.base_url, provider_config.api_key
        )
        warn_reserved_catalog_collisions(catalog)
    else:
        catalog = CatalogStatus(outcome=CatalogOutcome.REACHABLE_WITHOUT_ID)

    for stage_name, cli_value in stage_cli_values.items():
        _validate_selected_stage(
            stage_name,
            stages[stage_name],
            catalog,
            validate_fallback_binary=(
                cli_value is not None or stage_name in file_stages_raw
            ),
        )

    try:
        validated_direct_tools: set[ToolType] = set()
        for stage in stages.values():
            if classify_slot(stage.model) is not SlotKind.DIRECT_VENDOR:
                continue
            tool_type = ToolType(stage.model)
            if tool_type in validated_direct_tools:
                continue
            validate_direct_vendor(tool_type, os.environ)
            validated_direct_tools.add(tool_type)
    except ConfigError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    debug_log_path = _resolve_debug_log_path(debug, debug_log)

    config = OrchestratorConfig(
        workspace_path=project_workspace,
        provider=provider_config,
        stages=stages,
        auto_approve=auto_approve,
        auto_run=auto_run,
        auto_fix=auto_fix,
        auto_select=auto_select,
        skip_review=skip_review,
        max_fix_iterations=max_fix_iterations,
        debug=debug,
        debug_log_path=debug_log_path,
        enable_ralph_wiggum=enable_ralph_wiggum,
        ralph_wiggum_threshold=ralph_wiggum_threshold,
        ralph_wiggum_max_iterations=ralph_wiggum_max_iterations,
        ralph_wiggum_completion_promise=completion_promise,
        ralph_wiggum_state_file=ralph_wiggum_state_file,
    )

    if config.debug:
        console.print(
            f"[dim]Auto-approve mode: {config.auto_approve}[/dim]\n"
            f"[dim]Auto-run mode: {config.auto_run}[/dim]\n"
            f"[dim]Stage models: "
            f"{', '.join(f'{k}={v.model}' for k, v in config.stages.items())}[/dim]"
        )
        if debug_log_path is not None:
            console.print(f"[dim]Debug log file: {debug_log_path}[/dim]")

    console.print(
        Panel.fit(
            f"[bold blue]Goal:[/bold blue] {request}\n"
            f"[bold green]Project:[/bold green] {project_name}\n"
            f"[bold yellow]Workspace:[/bold yellow] {project_workspace}",
            title="🚀 Orchestrator Started",
        )
    )

    state_path = project_workspace / ".ai_orchestration" / "run_state.json"

    global _active_authorization_gate, _active_provider_config
    _active_provider_config = provider_config
    try:
        _active_authorization_gate = _authorization_gate_from_environment(
            dict(os.environ)
        )
    except ConfigError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        with acquire_run_lock(state_path):
            _run(
                request=request,
                project_name=project_name,
                state_path=state_path,
                config=config,
                resume=resume,
            )
    except RunLockedError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        console.print(
            "[bold red]Error:[/bold red] could not persist orchestration run state"
        )
        raise typer.Exit(code=1) from exc
    except OrchestrationError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        Panel.fit(
            "[bold green]All Done![/bold green]", title="6-Stage Workflow Finished"
        )
    )


def _run(
    *,
    request: str,
    project_name: str,
    state_path: Path,
    config: OrchestratorConfig,
    resume: bool,
) -> None:
    """Run the lock-protected pipeline using one constructed configuration."""
    context = OrchestrationContext(
        project_name=project_name,
        user_goal=request,
        workspace_path=config.workspace_path,
        ralph_wiggum_enabled=config.enable_ralph_wiggum,
        ralph_wiggum_threshold=config.ralph_wiggum_threshold,
        ralph_wiggum_completion_promise=config.ralph_wiggum_completion_promise,
        ralph_wiggum_iteration=IterationMetadata(
            max_attempts=config.ralph_wiggum_max_iterations
        ),
    )

    resuming_existing = resume and state_path.exists()
    try:
        run_state = resolve_run_start(state_path, resume=resume)
    except StateError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if not resuming_existing:
        run_state.goal = request
        run_state.project_name = project_name
        run_state.config_snapshot = {
            "stages": {key: value.model for key, value in config.stages.items()}
        }
        run_state.outputs = {"context": context.model_dump(mode="json")}
        save_state(run_state, state_path)

    if resuming_existing:
        if "context" not in run_state.outputs:
            raise StateError(
                f"run state at {state_path} is missing required 'outputs.context'"
            )
        # Resume: restore the full context (implementation_plan, diffs,
        # review results, etc.) so stages after the resumed point have the
        # data earlier stages produced. Fields explicitly supplied on this
        # invocation must win over the persisted snapshot, so a resuming
        # run does not silently revert to stale flag values.
        try:
            restored = OrchestrationContext.model_validate(run_state.outputs["context"])
        except OSError as exc:
            raise StateError("could not prepare restored workspace directory") from exc
        except (TypeError, ValidationError) as exc:
            raise StateError(
                f"run state at {state_path} field 'outputs.context' is invalid"
            ) from exc
        restored.ralph_wiggum_enabled = config.enable_ralph_wiggum
        restored.ralph_wiggum_threshold = config.ralph_wiggum_threshold
        restored.ralph_wiggum_completion_promise = (
            config.ralph_wiggum_completion_promise
        )
        restored.ralph_wiggum_iteration.max_attempts = (
            config.ralph_wiggum_max_iterations
        )
        context = restored

        # A resumed run must not silently keep executing under a model
        # swapped out from under it: revalidate the persisted config
        # snapshot's stage models against this invocation's resolved
        # stages before any further stage runs.
        persisted_models = run_state.config_snapshot.get("stages", {})
        if not isinstance(persisted_models, dict):
            raise StateError(
                f"run state at {state_path} field 'config_snapshot.stages' must be an object"
            )
        for stage_name, persisted_model in persisted_models.items():
            current_model = config.stages.get(stage_name)
            if current_model is not None and current_model.model != persisted_model:
                console.print(
                    "[bold red]Error:[/bold red] --resume: stage "
                    f"'{stage_name}' was previously running "
                    f"'{persisted_model}' but is now configured for "
                    f"'{current_model.model}'. Re-run without --resume, or "
                    "restore the original stage flags/config."
                )
                raise typer.Exit(code=1)

    command_executor = CommandExecutor(
        auto_approve=config.auto_approve,
        retries=1,
        log_directory=config.workspace_path / "execution_logs",
    )
    command_gate = ApprovalGate(is_tty=_is_tty(), ask=_confirm_interactively)
    recorder = CallRecorder(
        state_path.with_name("call_events.jsonl"), run_id=run_state.run_id
    )
    stage_kwargs = dict(
        debug=config.debug,
        debug_log_path=config.debug_log_path,
        recorder=recorder,
    )

    def _state_snapshot(
        *, current_stage: Optional[str], pause_reason: Optional[str]
    ) -> RunState:
        return RunState(
            goal=request,
            project_name=project_name,
            config_snapshot={"stages": {k: v.model for k, v in config.stages.items()}},
            completed_stages=run_state.completed_stages,
            current_stage=current_stage,
            outputs={"context": context.model_dump(mode="json")},
            pause_reason=pause_reason,
            run_id=run_state.run_id,
        )

    def _pause(stage_name: str, exc: PausedRun) -> None:
        save_state(
            _state_snapshot(current_stage=stage_name, pause_reason=exc.pause_reason),
            state_path,
        )
        console.print(
            f"[bold red]Paused:[/bold red] {exc.pause_reason} "
            f"(requires {exc.authorizing_flag})"
        )
        raise typer.Exit(code=exc.exit_code) from exc

    def _gated_run_stage(stage_name: str, runner: Callable[[], None]) -> None:
        if stage_name in run_state.completed_stages:
            return
        try:
            runner()
        except PausedRun as exc:
            _pause(stage_name, exc)
        run_state.completed_stages.append(stage_name)
        save_state(_state_snapshot(current_stage=None, pause_reason=None), state_path)

    _gated_run_stage(
        "brainstormer",
        lambda: _run_brainstormer(
            context, config.stages["brainstormer"], **stage_kwargs
        ),
    )
    _gated_run_stage(
        "brainstorming_reviewer",
        lambda: _run_brainstorming_reviewer(
            context, config.stages["reviewer"], **stage_kwargs
        ),
    )

    def _prompt_choice() -> int:
        return typer.prompt("Enter the number of your choice", type=int, default=1)

    def _prompt_custom() -> str:
        return typer.prompt("Enter your custom approach")

    if "planner" not in run_state.completed_stages:
        _select_approach(
            context,
            auto_select=config.auto_select,
            prompt_choice=_prompt_choice
            if _is_tty() and not config.auto_select
            else None,
            prompt_custom=_prompt_custom
            if _is_tty() and not config.auto_select
            else None,
        )
        if os.environ.get(_TEST_CONTROL_DIR_ENV_VAR):
            _test_control_dir()
            save_state(
                _state_snapshot(current_stage="planner", pause_reason=None), state_path
            )
    _gated_run_stage(
        "planner",
        lambda: _run_planner(context, config.stages["planner"], **stage_kwargs),
    )

    def _checkpoint_executor_task(_task_id: int) -> None:
        save_state(
            _state_snapshot(current_stage="executor", pause_reason=None), state_path
        )

    def _run_executor_gated() -> None:
        has_run_command = any(
            task.action_type == ActionType.RUN_COMMAND
            for task in context.implementation_plan
        )
        if has_run_command:
            if not config.auto_run:
                command_gate.request(
                    "execute plan commands?",
                    authorizing_flag="--auto-run",
                    authorized=config.auto_run,
                )
            if not config.auto_approve and not _is_tty():
                command_gate.request(
                    "confirm each executed command?",
                    authorizing_flag="--auto-approve",
                    authorized=config.auto_approve,
                )
        _run_executor(
            context,
            config.stages["executor"],
            command_executor,
            checkpoint_task=_checkpoint_executor_task,
            **stage_kwargs,
        )

    _gated_run_stage("executor", _run_executor_gated)

    if not config.skip_review:

        def _run_review(ctx):
            _run_code_reviewer(ctx, config.stages["code_reviewer"], **stage_kwargs)

        def _run_fix(ctx, item):
            _run_fixer(ctx, config.stages["fixer"], item, **stage_kwargs)

        def _select(items):
            if config.auto_fix:
                return items
            if not _is_tty():
                command_gate.request(
                    "apply selected review fixes?",
                    authorizing_flag="--auto-fix",
                    authorized=False,
                )
            choice = typer.prompt("Enter your choice", default="a")
            return select_fix_items(items, choice=choice, auto_fix=False)

        _gated_run_stage(
            "review_fix",
            lambda: run_main_review_fix_loop(
                context,
                run_review=_run_review,
                run_fix=_run_fix,
                max_fix_iterations=config.max_fix_iterations,
                auto_fix=config.auto_fix,
                select_items=_select,
            ),
        )

    if config.enable_ralph_wiggum:
        _gated_run_stage(
            "ralph_wiggum",
            lambda: run_ralph_wiggum_loop(
                context,
                run_review=lambda ctx: _run_ralph_wiggum_reviewer(
                    ctx, config.stages["code_reviewer"], **stage_kwargs
                ),
                write_state_file=config.ralph_wiggum_state_file,
                run_fix=lambda ctx, item: _run_fixer(
                    ctx, config.stages["fixer"], item, **stage_kwargs
                ),
                run_code_review=lambda ctx: _run_code_reviewer(
                    ctx, config.stages["code_reviewer"], **stage_kwargs
                ),
            ),
        )


if __name__ == "__main__":
    app()
