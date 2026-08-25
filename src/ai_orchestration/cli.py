"""Typer CLI: `ai-orchestration` entrypoint.

Wires U1's config/routing, U2's models/prompts/utilities, U3's providers,
and U4's engine into the six-stage pipeline. Every existing CLI option is
preserved (§Interface). The six per-stage flags accept any proxy model id
in addition to `agy|codex|claude`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from ai_orchestration.config import (
    CatalogOutcome,
    CatalogStatus,
    OrchestratorConfig,
    ProviderConfig,
    StageConfig,
    resolve_stage_config,
    resolve_workspace_base,
)
from ai_orchestration.engine.gates import ApprovalGate, PausedRun, select_fix_items
from ai_orchestration.engine.loops import (
    run_executor_self_healing,
    run_main_review_fix_loop,
    run_ralph_wiggum_loop,
)
from ai_orchestration.engine.stages import CommandExecutor, parse_approach_options
from ai_orchestration.engine.state import RunState, resolve_run_start, save_state
from ai_orchestration.errors import ConfigError, OrchestrationError, RoutingError
from ai_orchestration.models.context import (
    ActionType,
    CodeReviewItem,
    CodeReviewResult,
    OrchestrationContext,
    RalphWiggumFeedback,
    Task,
)
from ai_orchestration.prompts.stages import AGENT_PROMPTS
from ai_orchestration.providers.cli import AgyProvider, ClaudeProvider, CodexProvider
from ai_orchestration.providers.http import HttpProvider, probe_catalog
from ai_orchestration.providers.routing import (
    complete_structured_with_fallback,
    complete_with_fallback,
)
from ai_orchestration.utils.extract import extract_json_list
from ai_orchestration.utils.slug import generate_project_name

app = typer.Typer()
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


class _OfflineSmokeProvider:
    """Deterministic offline stand-in used only when
    ``AI_ORCHESTRATION_FAKE_PROVIDERS=1`` is set in the environment.
    """

    def complete(self, prompt: str, *, system=None, **kwargs) -> str:
        return (
            '[{"step_id": 1, "file_path": "hello.py", '
            '"action_type": "create_file", "instruction": "write hello world"}]'
        )

    def complete_structured(self, prompt: str, *, schema, **kwargs):
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


def _http_provider_factory(model: str):
    """Default HTTP provider factory: real CLIProxyAPI via the configured endpoint."""
    if os.environ.get(_FAKE_PROVIDERS_ENV_VAR):
        return _OfflineSmokeProvider()
    provider_config = ProviderConfig.from_raw(None)
    return HttpProvider(
        model=model, base_url=provider_config.base_url, api_key=provider_config.api_key
    )


def _cli_provider_factory(binary: str):
    """Default CLI provider factory: real subprocess provider for `binary`."""
    if os.environ.get(_FAKE_PROVIDERS_ENV_VAR):
        return _OfflineSmokeProvider()
    provider_class = _CLI_PROVIDER_CLASSES[binary]
    return provider_class()


def _probe_catalog_for_startup(base_url: str, api_key: Optional[str]) -> CatalogStatus:
    """Default startup catalog probe: real network request."""
    if os.environ.get(_FAKE_PROVIDERS_ENV_VAR):
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


# --- Stage runners: thin wrappers over routing + prompts --------------------


def _complete_stage_text(stage_name: str, stage: StageConfig, prompt: str) -> str:
    text, _provider_used = complete_with_fallback(
        stage,
        prompt,
        http_provider_factory=_http_provider_factory,
        cli_provider_factory=_cli_provider_factory,
        system=AGENT_PROMPTS[stage_name].get("system", ""),
    )
    return text


def _complete_stage_structured(stage: StageConfig, prompt: str, *, schema):
    result, _provider_used = complete_structured_with_fallback(
        stage,
        prompt,
        schema=schema,
        http_provider_factory=_http_provider_factory,
        cli_provider_factory=_cli_provider_factory,
    )
    return result


def _run_brainstormer(context: OrchestrationContext, stage: StageConfig) -> None:
    prompt = AGENT_PROMPTS["brainstormer"]["user"].format(
        user_goal=context.user_goal, tooling_context="unknown"
    )
    output = _complete_stage_text("brainstormer", stage, prompt)
    context.brainstorming_ideas = output


def _run_brainstorming_reviewer(
    context: OrchestrationContext, stage: StageConfig
) -> None:
    ideas = context.brainstorming_ideas
    ideas_text = ideas if isinstance(ideas, str) else "\n".join(ideas)
    prompt = AGENT_PROMPTS["brainstorming_reviewer"]["user"].format(
        user_goal=context.user_goal,
        tooling_context="unknown",
        brainstorming_ideas=ideas_text,
    )
    output = _complete_stage_text("brainstorming_reviewer", stage, prompt)
    context.refined_brainstorming = output


def _select_approach(context: OrchestrationContext, *, auto_select: bool) -> None:
    ideas_to_use = context.refined_brainstorming or context.brainstorming_ideas
    ideas_text = (
        ideas_to_use if isinstance(ideas_to_use, str) else "\n".join(ideas_to_use)
    )
    options = parse_approach_options(ideas_text)
    if auto_select:
        context.selected_approach = options[0] if options else ideas_text
    else:
        context.selected_approach = options[0] if options else ideas_text


def _run_planner(context: OrchestrationContext, stage: StageConfig) -> None:
    prompt = AGENT_PROMPTS["planner"]["user"].format(
        user_goal=context.user_goal,
        tooling_context="unknown",
        brainstorming_ideas=context.refined_brainstorming or "",
        selected_approach=context.selected_approach or "",
    )
    output = _complete_stage_text("planner", stage, prompt)
    json_plan = extract_json_list(output)
    tasks = []
    for item in json_plan:
        try:
            tasks.append(Task(**item))
        except Exception:
            continue
    context.implementation_plan = tasks


def _run_executor(
    context: OrchestrationContext, stage: StageConfig, command_executor: CommandExecutor
) -> None:
    for task in context.implementation_plan:
        if task.action_type == ActionType.RUN_COMMAND:
            command_executor.run(task.instruction, cwd=str(context.workspace_path))
            continue

        def _complete(prompt: str) -> str:
            full_prompt = AGENT_PROMPTS["executor"]["user"].format(
                user_goal=context.user_goal,
                step_id=task.step_id,
                action_type=task.action_type.value,
                file_path=str(task.file_path),
                instruction=prompt,
                existing_code="",
            )
            return _complete_stage_text("executor", stage, full_prompt)

        run_executor_self_healing(context, task, complete=_complete)


def _run_code_reviewer(context: OrchestrationContext, stage: StageConfig) -> None:
    prompt = AGENT_PROMPTS["code_reviewer"]["user"].format(
        user_goal=context.user_goal,
        plan_summary=str(len(context.implementation_plan)),
        file_list=", ".join(str(t.file_path) for t in context.implementation_plan),
        execution_summary=str(len(context.execution_logs)),
        code_diffs=str(context.generated_diffs),
        file_contents="",
    )
    result = _complete_stage_structured(stage, prompt, schema=CodeReviewResult)
    context.code_review_result = result


def _run_fixer(
    context: OrchestrationContext, stage: StageConfig, item: CodeReviewItem
) -> None:
    target_path = context.workspace_path / item.file_path
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
    output = _complete_stage_text("fixer", stage, prompt)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(output, encoding="utf-8")


def _run_ralph_wiggum_reviewer(
    context: OrchestrationContext, stage: StageConfig
) -> None:
    prompt = AGENT_PROMPTS["ralph_wiggum_reviewer"]["user"].format(
        user_goal=context.user_goal,
        self_reference_context=context.get_self_reference_context(),
        worker_output="",
        file_list=", ".join(str(k) for k in context.generated_diffs),
        completion_promise=context.ralph_wiggum_completion_promise or "",
    )
    feedback = _complete_stage_structured(stage, prompt, schema=RalphWiggumFeedback)
    context.ralph_wiggum_feedback = feedback


# --- Startup validation ------------------------------------------------------


def _resolve_and_validate_stage(
    stage_name: str, cli_value: Optional[str], file_stages: dict, catalog: CatalogStatus
) -> StageConfig:
    try:
        return resolve_stage_config(
            stage_name, cli_value=cli_value, file_stages=file_stages, catalog=catalog
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

    workspace_base = resolve_workspace_base(workspace, dict(__import__("os").environ))
    project_workspace = workspace_base / project_name

    file_stages_raw = _load_tool_config_file(tool_config_file)
    stage_cli_values = {
        "brainstormer": brainstormer,
        "reviewer": reviewer,
        "planner": planner,
        "executor": executor,
        "code_reviewer": code_reviewer,
        "fixer": fixer,
    }

    provider_config = ProviderConfig.from_raw(file_stages_raw.get("provider"))
    catalog = _probe_catalog_for_startup(
        provider_config.base_url, provider_config.api_key
    )

    stages: dict[str, StageConfig] = {}
    for stage_name, cli_value in stage_cli_values.items():
        stages[stage_name] = _resolve_and_validate_stage(
            stage_name, cli_value, file_stages_raw, catalog
        )

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
        debug_log_path=Path(debug_log) if debug else None,
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

    console.print(
        Panel.fit(
            f"[bold blue]Goal:[/bold blue] {request}\n"
            f"[bold green]Project:[/bold green] {project_name}\n"
            f"[bold yellow]Workspace:[/bold yellow] {project_workspace}",
            title="🚀 Orchestrator Started",
        )
    )

    context = OrchestrationContext(
        project_name=project_name,
        user_goal=request,
        workspace_path=project_workspace,
        ralph_wiggum_enabled=enable_ralph_wiggum,
        ralph_wiggum_threshold=ralph_wiggum_threshold,
        ralph_wiggum_completion_promise=completion_promise,
    )

    state_path = project_workspace / ".ai_orchestration" / "run_state.json"
    run_state = resolve_run_start(state_path, resume=resume)
    if run_state.completed_stages and "context" in run_state.outputs:
        # Resume: restore the full context (implementation_plan, diffs,
        # review results, etc.) so stages after the resumed point have the
        # data earlier stages produced, not an empty context.
        context = OrchestrationContext.model_validate(run_state.outputs["context"])

    command_executor = CommandExecutor(
        auto_approve=auto_approve, retries=1, log_directory=Path("execution_logs")
    )
    command_gate = ApprovalGate(is_tty=_is_tty(), ask=_confirm_interactively)

    def _gated_run_stage(stage_name: str, runner) -> None:
        if stage_name in run_state.completed_stages:
            return
        try:
            runner()
        except PausedRun as exc:
            save_state(
                RunState(
                    goal=request,
                    project_name=project_name,
                    config_snapshot={"stages": {k: v.model for k, v in stages.items()}},
                    completed_stages=run_state.completed_stages,
                    current_stage=stage_name,
                    outputs={"context": context.model_dump(mode="json")},
                    pause_reason=exc.pause_reason,
                ),
                state_path,
            )
            console.print(
                f"[bold red]Paused:[/bold red] {exc.pause_reason} "
                f"(requires {exc.authorizing_flag})"
            )
            raise typer.Exit(code=exc.exit_code) from exc
        run_state.completed_stages.append(stage_name)
        save_state(
            RunState(
                goal=request,
                project_name=project_name,
                config_snapshot={"stages": {k: v.model for k, v in stages.items()}},
                completed_stages=run_state.completed_stages,
                current_stage=None,
                outputs={"context": context.model_dump(mode="json")},
                pause_reason=None,
            ),
            state_path,
        )

    try:
        _gated_run_stage(
            "brainstormer", lambda: _run_brainstormer(context, stages["brainstormer"])
        )
        _gated_run_stage(
            "brainstorming_reviewer",
            lambda: _run_brainstorming_reviewer(context, stages["reviewer"]),
        )
        _select_approach(context, auto_select=auto_select)
        _gated_run_stage("planner", lambda: _run_planner(context, stages["planner"]))

        def _run_executor_gated():
            if not auto_run and any(
                t.action_type == ActionType.RUN_COMMAND
                for t in context.implementation_plan
            ):
                command_gate.request(
                    "execute plan commands?",
                    authorizing_flag="--auto-run",
                    authorized=auto_run,
                )
            _run_executor(context, stages["executor"], command_executor)

        _gated_run_stage("executor", _run_executor_gated)

        if not skip_review:

            def _run_review(ctx):
                _run_code_reviewer(ctx, stages["code_reviewer"])

            def _run_fix(ctx, item):
                _run_fixer(ctx, stages["fixer"], item)

            def _select(items):
                if auto_fix:
                    return items
                if not _is_tty():
                    return []
                choice = typer.prompt("Enter your choice", default="a")
                return select_fix_items(items, choice=choice, auto_fix=False)

            run_main_review_fix_loop(
                context,
                run_review=_run_review,
                run_fix=_run_fix,
                max_fix_iterations=max_fix_iterations,
                auto_fix=auto_fix,
                select_items=_select,
            )

        if enable_ralph_wiggum:
            run_ralph_wiggum_loop(
                context,
                run_review=lambda ctx: _run_ralph_wiggum_reviewer(
                    ctx, stages["code_reviewer"]
                ),
                write_state_file=ralph_wiggum_state_file,
                run_fix=lambda ctx, item: _run_fixer(ctx, stages["fixer"], item),
                run_code_review=lambda ctx: _run_code_reviewer(
                    ctx, stages["code_reviewer"]
                ),
            )
    except OrchestrationError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        Panel.fit(
            "[bold green]All Done![/bold green]", title="6-Stage Workflow Finished"
        )
    )


if __name__ == "__main__":
    app()
