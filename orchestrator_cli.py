import ast
import difflib
import json
import re
import selectors
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from agent_prompts import AGENT_PROMPTS
from llm_tools import (
    LLMToolConfig,
    LLMToolFactory,
    StageRole,
    load_tool_config,
    validate_tool_config,
)

# --- 사용자 정의 모듈 Import ---
# (orchestration_context.py, agent_prompts.py 파일이 같은 폴더에 있어야 합니다)
from orchestration_context import (
    ActionType,
    CodeReviewItem,
    CodeReviewResult,
    ExecutionLog,
    OrchestrationContext,
    ReviewItemType,
    ReviewSeverity,
    Task,
)

app = typer.Typer()
console = Console()
DEBUG = False
DEBUG_LOG_PATH: Path | None = None


def _generate_command_slug(command: str, max_length: int = 30) -> str:
    """Generate a short slug from command for use in log filenames."""
    slug = re.sub(r"[^\w\s-]", "", command.lower())
    slug = re.sub(r"[\s_-]+", "_", slug)
    slug = slug.strip("_")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("_")
    return slug if slug else "cmd"


def _generate_project_name(goal: str, max_length: int = 30) -> str:
    """goal 문자열에서 프로젝트명 slug를 생성합니다 (ASCII만 허용)."""
    slug = re.sub(r"[^a-z0-9\s-]", "", goal.lower())
    slug = re.sub(r"[\s_-]+", "_", slug)
    slug = slug.strip("_")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("_")
    return slug if slug else "project"


@dataclass
class CommandExecutionLog:
    """Structured log entry for a single command execution attempt."""

    timestamp: str
    command: str
    cwd: Optional[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    attempt: int


@dataclass
class CommandExecutionSummary:
    """Summary of all attempts for a single command execution."""

    command_id: str
    command: str
    cwd: Optional[str]
    started_at: str
    finished_at: str
    total_attempts: int
    final_status: str  # "success", "failed", "skipped"
    final_exit_code: Optional[int]
    attempts: List[Dict[str, Any]]


def _truncate_stderr(stderr: str, max_lines: int = 5, max_chars: int = 200) -> str:
    """Truncate stderr to a short excerpt for summary reports."""
    if not stderr:
        return "(no stderr)"
    lines = stderr.strip().splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    truncated = "\n".join(lines)
    if len(truncated) > max_chars:
        truncated = truncated[:max_chars] + "..."
    return truncated


def _print_failure_summary(
    command: str,
    total_attempts: int,
    final_exit_code: Optional[int],
    last_stderr: str,
) -> None:
    """Print a concise summary report for a failed command execution."""
    stderr_excerpt = _truncate_stderr(last_stderr)
    console.print("\n[bold red]━━━ Command Execution Failed ━━━[/bold red]")
    console.print(f"[bold]Command:[/bold] {command}")
    console.print(f"[bold]Attempts:[/bold] {total_attempts}")
    console.print(f"[bold]Exit Code:[/bold] {final_exit_code}")
    console.print(f"[bold]Stderr:[/bold]\n{stderr_excerpt}")
    console.print("[bold red]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold red]\n")


@dataclass
class CommandExecutor:
    """Executor for shell commands with auto-approve, retry, and structured logging."""

    auto_approve: bool = False
    retries: int = 1
    log_directory: Path = field(default_factory=lambda: Path("execution_logs"))
    _execution_counter: int = field(default=0, init=False)

    def __post_init__(self):
        self.log_directory = Path(self.log_directory)
        self.log_directory.mkdir(parents=True, exist_ok=True)

    def _generate_command_id(self, command: str) -> str:
        """Generate a unique command ID with timestamp, counter, and slug."""
        self._execution_counter += 1
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = _generate_command_slug(command)
        return f"{timestamp}_{self._execution_counter:04d}_{slug}"

    def _write_execution_log(self, summary: CommandExecutionSummary) -> Path:
        """Write a complete execution log (all attempts + final status) to a JSON file."""
        log_file = self.log_directory / f"{summary.command_id}.json"

        log_data = {
            "command_id": summary.command_id,
            "command": summary.command,
            "cwd": summary.cwd,
            "started_at": summary.started_at,
            "finished_at": summary.finished_at,
            "total_attempts": summary.total_attempts,
            "final_status": summary.final_status,
            "final_exit_code": summary.final_exit_code,
            "attempts": summary.attempts,
        }

        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)

        return log_file

    def run(
        self, command: str, cwd: Optional[str] = None
    ) -> tuple[bool, str, List[CommandExecutionLog]]:
        """
        Execute a command with optional confirmation, retries, and structured logging.

        Returns:
            tuple of (success: bool, output: str, logs: List[CommandExecutionLog])
        """
        command_id = self._generate_command_id(command)
        started_at = datetime.now().isoformat()
        logs: List[CommandExecutionLog] = []
        attempts_data: List[Dict[str, Any]] = []

        if not self.auto_approve:
            should_run = typer.confirm(
                f"Do you want to execute this command: {command}?"
            )
            if not should_run:
                finished_at = datetime.now().isoformat()
                summary = CommandExecutionSummary(
                    command_id=command_id,
                    command=command,
                    cwd=cwd,
                    started_at=started_at,
                    finished_at=finished_at,
                    total_attempts=0,
                    final_status="skipped",
                    final_exit_code=None,
                    attempts=[],
                )
                log_file = self._write_execution_log(summary)
                if DEBUG:
                    console.print(f"[dim]Execution log written to: {log_file}[/dim]")
                return False, "Command execution skipped by user.", []

        max_attempts = 1 + self.retries
        final_status = "failed"
        final_exit_code: Optional[int] = None
        final_output = ""
        last_stderr = ""

        for attempt in range(max_attempts):
            timestamp = datetime.now().isoformat()
            start_time = time.monotonic()

            try:
                command_args = shlex.split(command)
                result = subprocess.run(
                    command_args,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    cwd=cwd,
                )

                end_time = time.monotonic()
                duration_ms = int((end_time - start_time) * 1000)

                log_entry = CommandExecutionLog(
                    timestamp=timestamp,
                    command=command,
                    cwd=cwd,
                    exit_code=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    duration_ms=duration_ms,
                    attempt=attempt + 1,
                )
                logs.append(log_entry)

                attempt_data = {
                    "timestamp": log_entry.timestamp,
                    "attempt": log_entry.attempt,
                    "exit_code": log_entry.exit_code,
                    "stdout": log_entry.stdout,
                    "stderr": log_entry.stderr,
                    "duration_ms": log_entry.duration_ms,
                }
                attempts_data.append(attempt_data)

                last_stderr = result.stderr

                if result.returncode == 0:
                    final_status = "success"
                    final_exit_code = result.returncode
                    final_output = result.stdout.strip()
                    break

                final_exit_code = result.returncode
                final_output = f"Command failed with exit code {result.returncode}: {result.stderr}"

                if attempt < max_attempts - 1:
                    console.print(
                        f"[yellow]Command failed (attempt {attempt + 1}/{max_attempts}), retrying...[/yellow]"
                    )
                    continue

            except Exception as e:
                end_time = time.monotonic()
                duration_ms = int((end_time - start_time) * 1000)

                log_entry = CommandExecutionLog(
                    timestamp=timestamp,
                    command=command,
                    cwd=cwd,
                    exit_code=-1,
                    stdout="",
                    stderr=str(e),
                    duration_ms=duration_ms,
                    attempt=attempt + 1,
                )
                logs.append(log_entry)

                attempt_data = {
                    "timestamp": log_entry.timestamp,
                    "attempt": log_entry.attempt,
                    "exit_code": log_entry.exit_code,
                    "stdout": log_entry.stdout,
                    "stderr": log_entry.stderr,
                    "duration_ms": log_entry.duration_ms,
                }
                attempts_data.append(attempt_data)

                last_stderr = str(e)
                final_exit_code = -1
                final_output = f"Command execution error: {e}"

                if attempt < max_attempts - 1:
                    console.print(
                        f"[yellow]Command failed (attempt {attempt + 1}/{max_attempts}), retrying...[/yellow]"
                    )
                    continue

        finished_at = datetime.now().isoformat()

        summary = CommandExecutionSummary(
            command_id=command_id,
            command=command,
            cwd=cwd,
            started_at=started_at,
            finished_at=finished_at,
            total_attempts=len(attempts_data),
            final_status=final_status,
            final_exit_code=final_exit_code,
            attempts=attempts_data,
        )
        log_file = self._write_execution_log(summary)

        if DEBUG:
            console.print(f"[dim]Execution log written to: {log_file}[/dim]")

        if final_status == "failed":
            _print_failure_summary(
                command=command,
                total_attempts=len(attempts_data),
                final_exit_code=final_exit_code,
                last_stderr=last_stderr,
            )

        return final_status == "success", final_output, logs


@dataclass
class OrchestratorConfig:
    """Configuration object for orchestrator settings."""

    auto_approve: bool = False
    auto_run: bool = False
    debug: bool = False
    debug_log_path: Optional[Path] = None
    workspace_path: Path = field(default_factory=lambda: Path("./workspace"))
    command_executor: Optional[CommandExecutor] = None
    tool_config: LLMToolConfig = field(default_factory=LLMToolConfig)


# Global config instance
_config: Optional[OrchestratorConfig] = None


def get_config() -> OrchestratorConfig:
    """Get the current orchestrator configuration."""
    global _config
    if _config is None:
        _config = OrchestratorConfig()
    return _config


def set_config(config: OrchestratorConfig) -> None:
    """Set the orchestrator configuration."""
    global _config
    _config = config


def get_command_executor() -> CommandExecutor:
    """Get the CommandExecutor from current config, creating one if needed."""
    config = get_config()
    if config.command_executor is None:
        config.command_executor = CommandExecutor(
            auto_approve=config.auto_approve,
            retries=1,
            log_directory=Path("execution_logs"),
        )
    return config.command_executor


# --- Configuration: 명령어 경로 설정 ---
GEMINI_BIN = shutil.which("gemini") or "gemini"
CHATGPT_BIN = shutil.which("codex") or "codex"
CLAUDE_BIN = shutil.which("claude") or "claude"

# --- 1. Helper Functions ---


def _run_shell_command(
    args: list[str],
    cwd: str = None,
    stage: str = "",
    parse_stream_json: bool = False,
) -> str:
    """터미널 명령어 실행 및 결과 반환"""
    cmd = args[0]
    if not shutil.which(cmd) and not cmd.startswith("/"):
        raise RuntimeError(
            f"명령어를 찾을 수 없습니다: '{cmd}'. 설치 상태를 확인해주세요."
        )

    try:
        if DEBUG:
            console.print(f"[dim]Running command: {' '.join(args)}[/dim]")
            if cwd:
                console.print(f"[dim]Working directory: {cwd}[/dim]")
            merged_output: list[str] = []
            parsed_chunks: list[str] = []
            # encoding='utf-8'로 한글 처리 보장
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                cwd=cwd,
            )
            assert process.stdout is not None
            sel = selectors.DefaultSelector()
            sel.register(process.stdout, selectors.EVENT_READ)
            last_output = time.monotonic()
            last_heartbeat = last_output
            while sel.get_map():
                events = sel.select(timeout=1.0)
                if not events:
                    now = time.monotonic()
                    if now - last_output >= 10 and now - last_heartbeat >= 10:
                        heartbeat = "[waiting for output]"
                        console.print(
                            f"[dim]{stage} | {heartbeat}[/dim]"
                            if stage
                            else f"[dim]{heartbeat}[/dim]"
                        )
                        _append_debug_log_line(stage or cmd, heartbeat)
                        last_heartbeat = now
                    if process.poll() is not None and not events:
                        break
                    continue
                for key, _ in events:
                    line = key.fileobj.readline()
                    if line == "":
                        sel.unregister(key.fileobj)
                        continue
                    line = line.rstrip("\n")
                    merged_output.append(line)
                    console.print(
                        f"[dim]{stage} | {line}[/dim]"
                        if stage
                        else f"[dim]{line}[/dim]"
                    )
                    _append_debug_log_line(stage or cmd, line)
                    last_output = time.monotonic()
                    if parse_stream_json:
                        parsed_chunks.extend(_extract_stream_json_text(line))
            returncode = process.wait()
            combined = "\n".join(merged_output)
            if DEBUG:
                console.print(
                    f"[dim]Command finished: returncode={returncode}, "
                    f"stdout_len={len(combined)}, stderr_len=0[/dim]"
                )
            if returncode != 0:
                raise RuntimeError(f"명령어 실행 실패: returncode={returncode}")
            if parse_stream_json:
                if not parsed_chunks:
                    parsed_chunks = _extract_stream_json_from_combined(combined)
                if parsed_chunks:
                    return "".join(parsed_chunks).strip()
                return ""
            return combined.strip()
        else:
            # encoding='utf-8'로 한글 처리 보장
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=True,
                encoding="utf-8",
                cwd=cwd,
            )
            return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]CLI Error ({cmd}):[/bold red] {e.stderr}")
        raise RuntimeError(f"명령어 실행 실패: {e.stderr}")


def _run_api_tool(
    tool,
    prompt: str,
    stage: str = "",
    system_prompt: str = "",
) -> str:
    """API 기반 LLM 도구 실행 (스트리밍 출력 지원)"""
    from rich.live import Live
    from rich.text import Text

    if DEBUG:
        console.print(f"[dim]Running API tool: {tool.__class__.__name__}[/dim]")
        console.print(f"[dim]Prompt length: {len(prompt)} chars[/dim]")

    console.print(
        f"[bold blue]▶ {stage}[/bold blue] (API)"
        if stage
        else "[bold blue]▶ API Call[/bold blue]"
    )

    full_response = ""
    try:
        with Live(
            Text(""), console=console, refresh_per_second=10, transient=True
        ) as live:
            for chunk in tool.generate_stream(prompt, system_prompt or None):
                full_response += chunk
                # Show last 500 chars to keep display manageable
                display_text = (
                    full_response[-500:] if len(full_response) > 500 else full_response
                )
                live.update(Text(display_text, style="dim"))
                _append_debug_log_line(stage or "API", chunk.replace("\n", "\\n")[:100])
    except Exception as e:
        console.print(f"[bold red]API Error:[/bold red] {e}")
        raise RuntimeError(f"API 호출 실패: {e}")

    if DEBUG:
        console.print(f"[dim]API response length: {len(full_response)} chars[/dim]")
        _write_debug_log(f"{stage} API response", full_response)

    return full_response.strip()


def _write_debug_log(stage: str, content: str) -> None:
    if not DEBUG or DEBUG_LOG_PATH is None:
        return
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"\n\n==== {stage} ====\n")
            f.write(content)
    except Exception as e:
        console.print(f"[yellow]Debug log write failed: {e}[/yellow]")


def _append_debug_log_line(stage: str, line: str) -> None:
    if not DEBUG or DEBUG_LOG_PATH is None:
        return
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {stage}: {line}\n")
    except Exception as e:
        console.print(f"[yellow]Debug log write failed: {e}[/yellow]")


def _normalize_run_command(instruction: str) -> str:
    """Extract a raw command string from a run_command instruction."""
    text = instruction.strip()
    backtick = re.findall(r"`([^`]+)`", text)
    if backtick:
        return backtick[-1].strip()
    if ":" in text:
        after_colon = text.split(":")[-1].strip()
        if after_colon:
            return after_colon
    match = re.findall(r"\buv\s+[^\n]+", text)
    if match:
        return match[-1].strip()
    return text


def _extract_stream_json_text(line: str) -> list[str]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return []
    texts: list[str] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("result"), str):
            texts.append(payload["result"])
        delta = payload.get("delta")
        if isinstance(delta, dict):
            text = delta.get("text")
            if isinstance(text, str):
                texts.append(text)
        content_block = payload.get("content_block")
        if isinstance(content_block, dict):
            text = content_block.get("text")
            if isinstance(text, str):
                texts.append(text)
        if isinstance(payload.get("text"), str):
            texts.append(payload["text"])
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    texts.append(item["text"])
    return texts


def _extract_stream_json_from_combined(combined: str) -> list[str]:
    texts: list[str] = []
    for line in combined.splitlines():
        texts.extend(_extract_stream_json_text(line))
    return texts


def _extract_json_list(text: str) -> List[Dict[str, Any]]:
    """텍스트에서 JSON List ([...]) 추출"""
    try:
        return json.loads(text)
    except:
        pass
    decoder = json.JSONDecoder()
    candidates: list[list[dict[str, Any]]] = []
    idx = 0
    while True:
        idx = text.find("[", idx)
        if idx == -1:
            break
        try:
            parsed, end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            idx += 1
            continue
        if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
            candidates.append(parsed)
        idx = idx + end
    if candidates:
        return candidates[-1]

    return []


def _extract_code_content(text: str) -> str:
    """Claude 출력에서 마크다운 코드 블록 내부만 추출"""
    start_match = re.search(r"```(?:[\w\+\-\.]+)?\s*\n", text)
    if not start_match:
        return text.strip()
    start_index = start_match.end()
    end_index = text.rfind("\n```")
    if end_index != -1 and end_index > start_index:
        return text[start_index:end_index].strip()
    return text[start_index:].strip()


def _detect_tooling(repo_root: Path) -> str:
    pyproject_path = repo_root / "pyproject.toml"
    poetry_lock = repo_root / "poetry.lock"
    pdm_config = repo_root / "pdm.toml"
    uv_lock = repo_root / "uv.lock"
    tooling = []

    if uv_lock.exists():
        tooling.append("uv")

    if pyproject_path.exists():
        content = pyproject_path.read_text(encoding="utf-8", errors="ignore").lower()
        if "[tool.poetry]" in content:
            tooling.append("poetry")
        if "[tool.pdm]" in content:
            tooling.append("pdm")
        if "[tool.uv]" in content and "uv" not in tooling:
            tooling.append("uv")

    if poetry_lock.exists() and "poetry" not in tooling:
        tooling.append("poetry")
    if pdm_config.exists() and "pdm" not in tooling:
        tooling.append("pdm")

    if not tooling:
        return "unknown"
    if len(tooling) == 1:
        return tooling[0]
    return ", ".join(tooling)


def _generate_diff(old_content: str, new_content: str, file_path: str) -> str:
    """두 코드 버전 간의 unified diff 생성"""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
    )
    return "".join(diff)


# --- 2. Agent Runners (기존 파일 활용) ---


def run_gemini_brainstorm(context: OrchestrationContext):
    """Stage 1: Brainstorming (configurable tool)"""
    config = get_config()
    tool_type = config.tool_config.get_tool_for_stage(StageRole.BRAINSTORMER)

    tooling_context = _detect_tooling(Path.cwd())
    if DEBUG:
        console.print(f"[dim]Tooling context: {tooling_context}[/dim]")
        console.print(f"[dim]Using tool: {tool_type.value}[/dim]")
    prompt = AGENT_PROMPTS["brainstormer"]["user"].format(
        user_goal=context.user_goal,
        tooling_context=tooling_context,
    )
    if DEBUG:
        console.print("[dim]Brainstormer prompt prepared[/dim]")

    # API vs CLI tool branching
    if LLMToolFactory.is_api_tool(tool_type):
        tool = LLMToolFactory.create_api_tool(tool_type)
        system_prompt = AGENT_PROMPTS["brainstormer"].get("system", "")
        output = _run_api_tool(
            tool, prompt, stage="BRAINSTORM", system_prompt=system_prompt
        )
    else:
        tool = LLMToolFactory.get_tool_for_stage(
            config.tool_config, StageRole.BRAINSTORMER
        )
        cmd = tool.build_command(prompt, debug=DEBUG)
        output = _run_shell_command(cmd, stage="BRAINSTORM")

    if DEBUG:
        output_preview = (
            output if len(output) <= 2000 else f"{output[:2000]}...\n[truncated]"
        )
        console.print(
            Panel(
                output_preview,
                title="Brainstorm raw output (preview)",
                border_style="cyan",
            )
        )
        _write_debug_log("Brainstorm raw output", output)

    # 결과 저장 (OrchestrationContext 필드에 맞게)
    context.brainstorming_ideas = output


def run_codex_brainstorm_review(context: OrchestrationContext):
    """Stage 2: Brainstorming Review (configurable tool)"""
    config = get_config()
    tool_type = config.tool_config.get_tool_for_stage(StageRole.REVIEWER)

    tooling_context = _detect_tooling(Path.cwd())
    if DEBUG:
        console.print(f"[dim]Tooling context: {tooling_context}[/dim]")
        console.print(f"[dim]Using tool: {tool_type.value}[/dim]")

    prompt = AGENT_PROMPTS["brainstorming_reviewer"]["user"].format(
        user_goal=context.user_goal,
        tooling_context=tooling_context,
        brainstorming_ideas=context.brainstorming_ideas,
    )

    if DEBUG:
        console.print("[dim]Reviewer prompt prepared[/dim]")

    # API vs CLI tool branching
    if LLMToolFactory.is_api_tool(tool_type):
        tool = LLMToolFactory.create_api_tool(tool_type)
        system_prompt = AGENT_PROMPTS["brainstorming_reviewer"].get("system", "")
        output = _run_api_tool(
            tool, prompt, stage="REVIEW", system_prompt=system_prompt
        )
    else:
        tool = LLMToolFactory.get_tool_for_stage(config.tool_config, StageRole.REVIEWER)
        cmd = tool.build_command(prompt, debug=DEBUG)
        output = _run_shell_command(cmd, stage="REVIEW")

    if DEBUG:
        output_preview = (
            output if len(output) <= 2000 else f"{output[:2000]}...\n[truncated]"
        )
        console.print(
            Panel(
                output_preview,
                title="Brainstorm Review (preview)",
                border_style="yellow",
            )
        )
        _write_debug_log("Brainstorm Review output", output)

    # 결과 저장
    context.refined_brainstorming = output

    # "Recommended Approach" 섹션에서 추천 접근법 추출
    recommended_match = re.search(
        r"##\s*Recommended\s+Approach\s*\n+(.*?)(?:\n##|\Z)",
        output,
        re.DOTALL | re.IGNORECASE,
    )
    if recommended_match:
        context.brainstorming_review_notes = recommended_match.group(1).strip()


def run_codex_planning(context: OrchestrationContext):
    """Stage 3: Planning (configurable tool)"""
    config = get_config()
    tool_type = config.tool_config.get_tool_for_stage(StageRole.PLANNER)

    tooling_context = _detect_tooling(Path.cwd())
    if DEBUG:
        console.print(f"[dim]Tooling context: {tooling_context}[/dim]")
        console.print(f"[dim]Using tool: {tool_type.value}[/dim]")

    # refined_brainstorming이 있으면 사용, 없으면 원본 사용
    brainstorming_to_use = (
        context.refined_brainstorming
        if context.refined_brainstorming
        else context.brainstorming_ideas
    )

    prompt = AGENT_PROMPTS["planner"]["user"].format(
        user_goal=context.user_goal,
        tooling_context=tooling_context,
        brainstorming_ideas=brainstorming_to_use,
        selected_approach=context.selected_approach,
    )
    if DEBUG:
        console.print("[dim]Planner prompt prepared[/dim]")

    # API vs CLI tool branching
    if LLMToolFactory.is_api_tool(tool_type):
        tool = LLMToolFactory.create_api_tool(tool_type)
        system_prompt = AGENT_PROMPTS["planner"].get("system", "")
        output = _run_api_tool(tool, prompt, stage="PLAN", system_prompt=system_prompt)
    else:
        tool = LLMToolFactory.get_tool_for_stage(config.tool_config, StageRole.PLANNER)
        cmd = tool.build_command(prompt, debug=DEBUG)
        output = _run_shell_command(cmd, stage="PLAN")

    if DEBUG:
        output_preview = (
            output if len(output) <= 2000 else f"{output[:2000]}...\n[truncated]"
        )
        console.print(
            Panel(
                output_preview,
                title="Planner raw output (preview)",
                border_style="green",
            )
        )
        _write_debug_log("Planner raw output", output)

    # JSON Parsing 및 Pydantic Task 변환
    json_plan = _extract_json_list(output)

    tasks = []
    for item in json_plan:
        try:
            # Pydantic 모델(Task)로 변환
            task = Task(**item)
            tasks.append(task)
        except Exception as e:
            console.print(f"[yellow]Task 파싱 실패 (건너뜀): {e}[/yellow]")

    context.implementation_plan = tasks


def run_claude_executor(
    context: OrchestrationContext, task: Task, max_retries: int = 3
):
    """Stage 4: Executor (configurable tool, Self-Healing 및 diff 수집 포함)"""
    config = get_config()
    tool_type = config.tool_config.get_tool_for_stage(StageRole.EXECUTOR)

    # 파일 읽기 (수정 작업인 경우 기존 코드 필요)
    existing_code = ""
    target_path = context.workspace_path / task.file_path

    # 기존 내용 읽기 (diff 생성용)
    if target_path.exists():
        try:
            existing_code = target_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # agent_prompts.py의 템플릿 사용
    prompt = AGENT_PROMPTS["executor"]["user"].format(
        user_goal=context.user_goal,
        step_id=task.step_id,
        action_type=task.action_type.value,
        file_path=str(task.file_path),
        instruction=task.instruction,
        existing_code=existing_code,
    )

    # Self-Healing Loop
    current_prompt = prompt
    is_api = LLMToolFactory.is_api_tool(tool_type)
    for attempt in range(max_retries + 1):
        if DEBUG:
            console.print(
                f"[dim]Executor attempt {attempt + 1}/{max_retries + 1} "
                f"for {task.action_type.value} {task.file_path} "
                f"(using {tool_type.value})[/dim]"
            )
        # 1. 실행 (API vs CLI)
        if is_api:
            tool = LLMToolFactory.create_api_tool(tool_type)
            system_prompt = AGENT_PROMPTS["executor"].get("system", "")
            raw_output = _run_api_tool(
                tool,
                current_prompt,
                stage=f"EXECUTOR task {task.step_id}",
                system_prompt=system_prompt,
            )
        else:
            tool = LLMToolFactory.get_tool_for_stage(
                config.tool_config, StageRole.EXECUTOR
            )
            cmd = tool.build_command(current_prompt, debug=DEBUG)
            if DEBUG:
                console.print(f"[dim]Executor command: {cmd[0]} <prompt>[/dim]")
            raw_output = _run_shell_command(
                cmd,
                stage=f"CLAUDE task {task.step_id}",
                parse_stream_json=DEBUG,
            )
        if DEBUG:
            output_preview = (
                raw_output
                if len(raw_output) <= 2000
                else f"{raw_output[:2000]}...\n[truncated]"
            )
            console.print(
                Panel(
                    output_preview,
                    title="Claude raw output (preview)",
                    border_style="magenta",
                )
            )
            _write_debug_log(
                f"Claude raw output (task {task.step_id}, attempt {attempt + 1})",
                raw_output,
            )
        code_content = _extract_code_content(raw_output)
        if code_content.lstrip().startswith("{") and '"type"' in code_content:
            if DEBUG:
                console.print(
                    "[yellow]Claude output appears to be stream JSON; skipping file write.[/yellow]"
                )
            context.execution_logs.append(
                ExecutionLog(
                    step_id=task.step_id,
                    success=False,
                    message="Claude output looked like stream JSON; skipped write.",
                )
            )
            return
        if DEBUG and not code_content.strip():
            console.print(
                "[yellow]Claude output empty after parsing; skipping file write.[/yellow]"
            )
            context.execution_logs.append(
                ExecutionLog(
                    step_id=task.step_id,
                    success=False,
                    message="Claude output empty after parsing; skipped write.",
                )
            )
            return
        if DEBUG:
            console.print(
                f"[dim]Claude output length: {len(raw_output)} "
                f"chars, extracted length: {len(code_content)} chars[/dim]"
            )

        # 2. 문법 검사 (Python 파일인 경우만)
        if str(task.file_path).endswith(".py") and code_content:
            try:
                ast.parse(code_content)
            except SyntaxError as e:
                console.print(
                    f"[bold yellow]⚠️ Syntax Error (Attempt {attempt + 1}):[/bold yellow] {e.msg}"
                )
                if attempt < max_retries:
                    current_prompt = (
                        f"The code has a SyntaxError: {e.msg} at line {e.lineno}.\n"
                        f"Your code:\n```python\n{code_content}\n```\n"
                        "Please FIX it and return ONLY the corrected code."
                    )
                    continue
                else:
                    console.print("[bold red]❌ 문법 수정 실패[/bold red]")

        # 3. 파일 저장 (성공 시 loop 탈출)
        if task.action_type in [ActionType.CREATE_FILE, ActionType.EDIT_FILE]:
            try:
                # workspace_path 기준 절대 경로 생성 (context의 validator가 이미 처리했으나 안전장치)
                full_path = context.workspace_path / task.file_path
                full_path.parent.mkdir(parents=True, exist_ok=True)

                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(code_content)

                # diff 생성 및 저장
                diff = _generate_diff(existing_code, code_content, str(task.file_path))
                context.generated_diffs[str(task.file_path)] = diff

                # 로그 기록
                log = ExecutionLog(
                    step_id=task.step_id,
                    success=True,
                    message=f"파일 작성 완료: {task.file_path}",
                )
                context.execution_logs.append(log)
                console.print(f"[bold green]Saved:[/bold green] {full_path}")
                return  # 성공 종료
            except Exception as e:
                console.print(f"[red]파일 쓰기 오류: {e}[/red]")
                return

    # 실패 로그
    context.execution_logs.append(
        ExecutionLog(
            step_id=task.step_id, success=False, message="최대 재시도 초과 또는 오류"
        )
    )


def execute_run_command(
    task: Task,
    context: OrchestrationContext,
) -> None:
    """Execute a RUN_COMMAND task using the global CommandExecutor."""
    console.print(f"[bold yellow]Command to run:[/bold yellow] {task.instruction}")
    if DEBUG:
        console.print(f"[dim]RUN_COMMAND task id: {task.step_id}[/dim]")

    executor = get_command_executor()
    current_config = get_config()
    command_text = _normalize_run_command(task.instruction)

    if current_config.auto_run and not executor.auto_approve:
        executor.auto_approve = True

    success, output, exec_logs = executor.run(
        command_text,
        cwd=str(context.workspace_path),
    )

    if success:
        context.execution_logs.append(
            ExecutionLog(
                step_id=task.step_id,
                success=True,
                message=f"Command executed: {task.instruction}",
                output=output,
            )
        )
        console.print("[green]✔ Command executed successfully.[/green]")
        console.print(f"[dim]{output}[/dim]")
    else:
        context.execution_logs.append(
            ExecutionLog(
                step_id=task.step_id,
                success=False,
                message=f"Command failed: {output}",
                output=output,
            )
        )
        if "skipped by user" in output.lower():
            console.print("[yellow]Skipped command execution by user.[/yellow]")
        else:
            console.print(f"[bold red]❌ Command execution failed: {output}[/bold red]")


def run_codex_code_review(context: OrchestrationContext):
    """Stage 5: Code Review (configurable tool)"""
    config = get_config()
    tool_type = config.tool_config.get_tool_for_stage(StageRole.CODE_REVIEWER)

    # 실행된 파일 목록 수집
    file_list = []
    file_contents = {}
    for log in context.execution_logs:
        if log.success:
            # Task에서 파일 경로 찾기
            for task in context.implementation_plan:
                if task.step_id == log.step_id:
                    if task.action_type in [
                        ActionType.CREATE_FILE,
                        ActionType.EDIT_FILE,
                    ]:
                        file_path = str(task.file_path)
                        file_list.append(file_path)
                        # 파일 내용 읽기
                        full_path = context.workspace_path / task.file_path
                        if full_path.exists():
                            try:
                                file_contents[file_path] = full_path.read_text(
                                    encoding="utf-8"
                                )
                            except Exception:
                                file_contents[file_path] = "[Error reading file]"

    # 실행 요약 생성
    success_count = sum(1 for log in context.execution_logs if log.success)
    fail_count = sum(1 for log in context.execution_logs if not log.success)
    execution_summary = f"Total tasks: {len(context.execution_logs)}, Success: {success_count}, Failed: {fail_count}"

    # 계획 요약 생성
    plan_summary = "\n".join(
        [
            f"- Step {t.step_id}: {t.action_type.value} {t.file_path}"
            for t in context.implementation_plan
        ]
    )

    # Diff 문자열 생성
    code_diffs = (
        "\n\n".join(
            [
                f"=== {path} ===\n{diff}"
                for path, diff in context.generated_diffs.items()
            ]
        )
        if context.generated_diffs
        else "(No diffs available)"
    )

    # 파일 내용 문자열 생성
    file_contents_str = (
        "\n\n".join(
            [
                f"=== {path} ===\n```\n{content}\n```"
                for path, content in file_contents.items()
            ]
        )
        if file_contents
        else "(No files)"
    )

    prompt = AGENT_PROMPTS["code_reviewer"]["user"].format(
        user_goal=context.user_goal,
        plan_summary=plan_summary,
        file_list="\n".join([f"- {f}" for f in file_list]),
        execution_summary=execution_summary,
        code_diffs=code_diffs,
        file_contents=file_contents_str,
    )

    if DEBUG:
        console.print("[dim]Code review prompt prepared[/dim]")
        console.print(f"[dim]Using tool: {tool_type.value}[/dim]")

    # API vs CLI tool branching
    if LLMToolFactory.is_api_tool(tool_type):
        tool = LLMToolFactory.create_api_tool(tool_type)
        system_prompt = AGENT_PROMPTS["code_reviewer"].get("system", "")
        output = _run_api_tool(
            tool, prompt, stage="CODE_REVIEW", system_prompt=system_prompt
        )
    else:
        tool = LLMToolFactory.get_tool_for_stage(
            config.tool_config, StageRole.CODE_REVIEWER
        )
        cmd = tool.build_command(prompt, debug=DEBUG)
        output = _run_shell_command(cmd, stage="CODE_REVIEW")

    if DEBUG:
        output_preview = (
            output if len(output) <= 2000 else f"{output[:2000]}...\n[truncated]"
        )
        console.print(
            Panel(
                output_preview,
                title="Codex Code Review (preview)",
                border_style="red",
            )
        )
        _write_debug_log("Codex Code Review output", output)

    # JSON 파싱
    try:
        # JSON 추출 시도
        json_match = re.search(r"\{[\s\S]*\}", output)
        if json_match:
            review_data = json.loads(json_match.group())

            # CodeReviewResult 생성
            items = []
            for item_data in review_data.get("items", []):
                try:
                    items.append(
                        CodeReviewItem(
                            item_id=item_data["item_id"],
                            file_path=Path(item_data["file_path"]),
                            line_start=item_data.get("line_start"),
                            line_end=item_data.get("line_end"),
                            review_type=ReviewItemType(item_data["review_type"]),
                            severity=ReviewSeverity(item_data["severity"]),
                            description=item_data["description"],
                            suggestion=item_data["suggestion"],
                            code_snippet=item_data.get("code_snippet"),
                        )
                    )
                except Exception as e:
                    if DEBUG:
                        console.print(
                            f"[yellow]Review item parsing failed: {e}[/yellow]"
                        )

            context.code_review_result = CodeReviewResult(
                reviewed_at=review_data.get("reviewed_at", datetime.now().isoformat()),
                total_files_reviewed=review_data.get(
                    "total_files_reviewed", len(file_list)
                ),
                items=items,
                overall_assessment=review_data.get("overall_assessment", ""),
                requires_fixes=review_data.get("requires_fixes", len(items) > 0),
            )
    except json.JSONDecodeError as e:
        console.print(f"[red]Code review JSON parsing failed: {e}[/red]")
        # 빈 리뷰 결과 생성
        context.code_review_result = CodeReviewResult(
            reviewed_at=datetime.now().isoformat(),
            total_files_reviewed=len(file_list),
            items=[],
            overall_assessment="Review parsing failed",
            requires_fixes=False,
        )


def _prompt_fix_selection(items: List[CodeReviewItem]) -> List[CodeReviewItem]:
    """사용자에게 수정할 리뷰 항목을 선택하도록 요청"""
    if not items:
        return []

    # 테이블 형식으로 리뷰 항목 표시
    table = Table(title="Code Review Items")
    table.add_column("#", style="cyan", width=4)
    table.add_column("Severity", style="bold")
    table.add_column("Type", style="dim")
    table.add_column("File", style="green")
    table.add_column("Description")

    severity_colors = {
        "critical": "bold red",
        "high": "red",
        "medium": "yellow",
        "low": "blue",
        "info": "dim",
    }

    for i, item in enumerate(items, 1):
        color = severity_colors.get(item.severity.value, "white")
        table.add_row(
            str(i),
            f"[{color}]{item.severity.value.upper()}[/{color}]",
            item.review_type.value,
            str(item.file_path),
            item.description[:60] + "..."
            if len(item.description) > 60
            else item.description,
        )

    console.print(table)
    console.print("\nOptions:")
    console.print("  [bold]a[/bold] - Apply all fixes")
    console.print("  [bold]n[/bold] - Skip all fixes")
    console.print("  [bold]1,2,3[/bold] - Select specific items (comma-separated)")
    console.print("  [bold]c[/bold] - Critical and High only")

    choice = typer.prompt("Enter your choice", default="a")

    if choice.lower() == "a":
        return items
    elif choice.lower() == "n":
        return []
    elif choice.lower() == "c":
        return [
            item
            for item in items
            if item.severity in [ReviewSeverity.CRITICAL, ReviewSeverity.HIGH]
        ]
    else:
        # 쉼표로 구분된 숫자 파싱
        try:
            indices = [int(x.strip()) - 1 for x in choice.split(",")]
            return [items[i] for i in indices if 0 <= i < len(items)]
        except (ValueError, IndexError):
            console.print("[yellow]Invalid selection. Skipping fixes.[/yellow]")
            return []


def run_claude_fixer(
    context: OrchestrationContext, review_item: CodeReviewItem, max_retries: int = 2
):
    """Stage 6: Fixer (configurable tool, 리뷰 피드백 기반으로 코드 수정)"""
    config = get_config()
    tool_type = config.tool_config.get_tool_for_stage(StageRole.FIXER)

    # 현재 파일 내용 읽기
    target_path = context.workspace_path / review_item.file_path
    current_code = ""
    if target_path.exists():
        try:
            current_code = target_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # 라인 범위 문자열 생성
    line_range = "N/A"
    if review_item.line_start:
        if review_item.line_end and review_item.line_end != review_item.line_start:
            line_range = f"{review_item.line_start}-{review_item.line_end}"
        else:
            line_range = str(review_item.line_start)

    prompt = AGENT_PROMPTS["fixer"]["user"].format(
        user_goal=context.user_goal,
        file_path=str(review_item.file_path),
        current_code=current_code,
        review_type=review_item.review_type.value,
        severity=review_item.severity.value,
        description=review_item.description,
        suggestion=review_item.suggestion,
        line_range=line_range,
        code_snippet=review_item.code_snippet or "(no snippet)",
    )

    current_prompt = prompt
    is_api = LLMToolFactory.is_api_tool(tool_type)
    for attempt in range(max_retries + 1):
        if DEBUG:
            console.print(
                f"[dim]Fixer attempt {attempt + 1}/{max_retries + 1} "
                f"for review item {review_item.item_id} "
                f"(using {tool_type.value})[/dim]"
            )

        # API vs CLI tool branching
        if is_api:
            tool = LLMToolFactory.create_api_tool(tool_type)
            system_prompt = AGENT_PROMPTS["fixer"].get("system", "")
            raw_output = _run_api_tool(
                tool,
                current_prompt,
                stage=f"FIX item {review_item.item_id}",
                system_prompt=system_prompt,
            )
        else:
            tool = LLMToolFactory.get_tool_for_stage(
                config.tool_config, StageRole.FIXER
            )
            cmd = tool.build_command(current_prompt, debug=DEBUG)
            raw_output = _run_shell_command(
                cmd,
                stage=f"FIX item {review_item.item_id}",
                parse_stream_json=DEBUG,
            )

        if DEBUG:
            _write_debug_log(
                f"Claude fix output (item {review_item.item_id}, attempt {attempt + 1})",
                raw_output,
            )

        code_content = _extract_code_content(raw_output)

        # 문법 검사 (Python 파일인 경우)
        if str(review_item.file_path).endswith(".py") and code_content:
            try:
                ast.parse(code_content)
            except SyntaxError as e:
                console.print(
                    f"[bold yellow]Syntax Error in fix (Attempt {attempt + 1}):[/bold yellow] {e.msg}"
                )
                if attempt < max_retries:
                    current_prompt = (
                        f"The fixed code has a SyntaxError: {e.msg} at line {e.lineno}.\n"
                        f"Your code:\n```python\n{code_content}\n```\n"
                        "Please FIX it and return ONLY the corrected code."
                    )
                    continue
                else:
                    console.print("[bold red]Fix syntax correction failed[/bold red]")

        # 파일 저장
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(code_content)

            log = ExecutionLog(
                step_id=review_item.item_id,
                success=True,
                message=f"Fix applied: {review_item.file_path} (item {review_item.item_id})",
            )
            context.fix_execution_logs.append(log)
            console.print(f"[bold green]Fixed:[/bold green] {target_path}")
            return
        except Exception as e:
            console.print(f"[red]File write error during fix: {e}[/red]")
            return

    # 실패 로그
    context.fix_execution_logs.append(
        ExecutionLog(
            step_id=review_item.item_id,
            success=False,
            message=f"Fix failed for review item {review_item.item_id}",
        )
    )


# --- 3. Main Workflow ---


@app.command()
def main(
    request: str = typer.Argument(..., help="프로젝트 요구사항"),
    workspace: str = typer.Option("./workspace", help="작업 파일이 생성될 폴더 경로"),
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
        None, "--brainstormer", help="Stage 1 브레인스토밍 도구 (gemini/codex/claude)"
    ),
    reviewer: Optional[str] = typer.Option(
        None, "--reviewer", help="Stage 2 브레인스토밍 리뷰 도구 (gemini/codex/claude)"
    ),
    planner: Optional[str] = typer.Option(
        None, "--planner", help="Stage 3 계획 수립 도구 (gemini/codex/claude)"
    ),
    executor: Optional[str] = typer.Option(
        None, "--executor", help="Stage 4 코드 실행 도구 (gemini/codex/claude)"
    ),
    code_reviewer: Optional[str] = typer.Option(
        None, "--code-reviewer", help="Stage 5 코드 리뷰 도구 (gemini/codex/claude)"
    ),
    fixer: Optional[str] = typer.Option(
        None, "--fixer", help="Stage 6 코드 수정 도구 (gemini/codex/claude)"
    ),
    tool_config_file: Optional[Path] = typer.Option(
        None, "--tool-config", help="LLM 도구 설정 파일 경로 (JSON)"
    ),
):
    """
    AI Orchestration Tool (6-Stage):
    Gemini -> Codex Review -> Codex Plan -> Claude Exec -> Codex Review -> Claude Fix
    """
    # 프로젝트명 결정
    if project_name is None:
        project_name = _generate_project_name(request)

    # 프로젝트별 workspace 경로
    project_workspace = Path(workspace) / project_name

    console.print(
        Panel.fit(
            f"[bold blue]Goal:[/bold blue] {request}\n"
            f"[bold green]Project:[/bold green] {project_name}\n"
            f"[bold yellow]Workspace:[/bold yellow] {project_workspace}",
            title="🚀 Orchestrator Started",
        )
    )

    global DEBUG, DEBUG_LOG_PATH
    DEBUG = debug
    if debug:
        log_path = Path(debug_log)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if log_path.suffix:
            DEBUG_LOG_PATH = log_path.with_name(
                f"{log_path.stem}-{timestamp}{log_path.suffix}"
            ).resolve()
        else:
            DEBUG_LOG_PATH = (
                log_path / f"orchestrator_debug-{timestamp}.log"
            ).resolve()
        if DEBUG:
            console.print(f"[dim]Debug log file: {DEBUG_LOG_PATH}[/dim]")
    else:
        DEBUG_LOG_PATH = None

    command_executor = CommandExecutor(
        auto_approve=auto_approve,
        retries=1,
        log_directory=Path("execution_logs"),
    )

    # Load LLM tool configuration
    tool_cfg = load_tool_config(
        config_file=tool_config_file,
        brainstormer=brainstormer,
        reviewer=reviewer,
        planner=planner,
        executor=executor,
        code_reviewer=code_reviewer,
        fixer=fixer,
    )

    # Validate and warn about missing tools
    tool_warnings = validate_tool_config(tool_cfg)
    for warning in tool_warnings:
        console.print(f"[yellow]Warning: {warning}[/yellow]")

    if DEBUG:
        console.print(
            f"[dim]Tool config: brainstormer={tool_cfg.brainstormer.value}, "
            f"reviewer={tool_cfg.reviewer.value}, planner={tool_cfg.planner.value}, "
            f"executor={tool_cfg.executor.value}, code_reviewer={tool_cfg.code_reviewer.value}, "
            f"fixer={tool_cfg.fixer.value}[/dim]"
        )

    # Initialize configuration object
    config = OrchestratorConfig(
        auto_approve=auto_approve,
        auto_run=auto_run,
        debug=debug,
        debug_log_path=DEBUG_LOG_PATH,
        workspace_path=project_workspace,
        command_executor=command_executor,
        tool_config=tool_cfg,
    )
    set_config(config)

    if DEBUG:
        console.print(f"[dim]Auto-approve mode: {auto_approve}[/dim]")
        console.print(f"[dim]Auto-run mode: {auto_run}[/dim]")

    # 1. 초기 Context 생성
    context = OrchestrationContext(
        project_name=project_name, user_goal=request, workspace_path=project_workspace
    )

    # ===== Stage 1: Gemini (Brainstorming) =====
    console.print("\n[bold cyan]Stage 1: Gemini Brainstorming[/bold cyan]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(description="[cyan]Gemini thinking...[/cyan]", total=None)
        run_gemini_brainstorm(context)

    console.print(
        Panel(
            str(context.brainstorming_ideas),
            title="Stage 1: Gemini Ideas",
            border_style="cyan",
        )
    )

    # ===== Stage 2: Codex (Brainstorming Review) =====
    console.print("\n[bold yellow]Stage 2: Codex Brainstorming Review[/bold yellow]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(
            description="[yellow]Codex reviewing brainstorming...[/yellow]", total=None
        )
        run_codex_brainstorm_review(context)

    console.print(
        Panel(
            str(context.refined_brainstorming)
            if context.refined_brainstorming
            else "(No refined output)",
            title="Stage 2: Refined Brainstorming",
            border_style="yellow",
        )
    )

    # 사용자에게 접근 방식 선택 요청 (refined_brainstorming 기반)
    ideas_to_use = (
        context.refined_brainstorming
        if context.refined_brainstorming
        else context.brainstorming_ideas
    )
    option_pattern = re.compile(
        r"^###?\s*(approach|option|plan|접근\s*방식)\s*\d*:?\s*\S", re.IGNORECASE
    )
    # 접근 방식 제목에서 실제 이름 추출용 패턴
    title_extract_pattern = re.compile(
        r"^###?\s*(?:approach|option|plan|접근\s*방식)\s*\d*:?\s*(.*)$", re.IGNORECASE
    )
    # 템플릿 플레이스홀더 패턴 (예: [Name], [...])
    placeholder_pattern = re.compile(r"\[.*?\]")
    lines = [line.strip() for line in ideas_to_use.split("\n")]
    options = []
    seen = set()
    for line in lines:
        if option_pattern.match(line):
            # 접근 방식 제목에서 이름 부분만 추출하여 플레이스홀더 검사
            title_match = title_extract_pattern.match(line)
            if title_match:
                title_part = title_match.group(1)
                title_without_placeholder = placeholder_pattern.sub(
                    "", title_part
                ).strip()
                # 플레이스홀더 제거 후 실제 내용이 없으면 제외
                if not title_without_placeholder or not re.search(
                    r"[가-힣a-zA-Z]", title_without_placeholder
                ):
                    continue
            # 중복 제거
            if line not in seen:
                seen.add(line)
                options.append(line)
    if not options:
        # 대안: "### Approach" 패턴 시도 (실제 내용이 있는 것만)
        for line in lines:
            if line.startswith("### ") and line not in seen:
                title_match = title_extract_pattern.match(line)
                if title_match:
                    title_part = title_match.group(1)
                    title_without_placeholder = placeholder_pattern.sub(
                        "", title_part
                    ).strip()
                    if not title_without_placeholder or not re.search(
                        r"[가-힣a-zA-Z]", title_without_placeholder
                    ):
                        continue
                seen.add(line)
                options.append(line)
    if not options:
        for line in lines:
            if line.startswith("- **") and line not in seen:
                seen.add(line)
                options.append(line)
    if DEBUG:
        console.print(f"[dim]Approach options detected: {len(options)}[/dim]")

    console.print("\n[bold]Please select an approach:[/bold]")
    for i, opt in enumerate(options):
        console.print(f"  {i + 1}: {opt}")
    console.print(f"  {len(options) + 1}: [dim]Custom (enter your own)[/dim]")

    default_choice = 1
    user_goal_lower = context.user_goal.lower()
    if "uv add --dev pytest" in user_goal_lower:
        for idx, opt in enumerate(options, start=1):
            opt_lower = opt.lower()
            if "uv add --dev pytest" in opt_lower or "uv add --dev" in opt_lower:
                default_choice = idx
                break

    if auto_select:
        choice = default_choice
        console.print(f"[dim]Auto-selected option: {choice}[/dim]")
    else:
        choice = typer.prompt(
            "Enter the number of your choice",
            type=int,
            default=default_choice,
        )
        if DEBUG:
            console.print(f"[dim]User selected option: {choice}[/dim]")

    if 1 <= choice <= len(options):
        context.selected_approach = options[choice - 1]
    elif choice == len(options) + 1:
        # 사용자 직접 입력
        custom_approach = typer.prompt("Enter your custom approach")
        context.selected_approach = custom_approach
    else:
        console.print(
            "[yellow]Invalid selection. Using the first approach as default.[/yellow]"
        )
        context.selected_approach = options[0] if options else ideas_to_use

    console.print(
        f"[bold green]Selected Approach:[/bold green] {context.selected_approach}"
    )

    # ===== Stage 3: Codex (Planning) =====
    console.print("\n[bold green]Stage 3: Codex Planning[/bold green]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(description="[green]Codex planning...[/green]", total=None)
        run_codex_planning(context)

    if not context.implementation_plan:
        console.print("[red]계획 생성 실패[/red]")
        return

    # Pydantic 모델을 JSON 예쁘게 출력
    plan_json = json.dumps(
        [t.model_dump(mode="json") for t in context.implementation_plan],
        indent=2,
        ensure_ascii=False,
    )
    console.print(
        Panel(plan_json, title="Stage 3: Implementation Plan", border_style="green")
    )

    # ===== Stage 4: Claude (Implementation) =====
    console.print("\n[bold magenta]Stage 4: Claude Implementation[/bold magenta]")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        task_count = len(context.implementation_plan)
        task_id = progress.add_task(
            description="[magenta]Claude coding...[/magenta]", total=task_count
        )

        for task in context.implementation_plan:
            if task.action_type in [ActionType.CREATE_FILE, ActionType.EDIT_FILE]:
                progress.update(task_id, description=f"Writing {task.file_path}...")
                if DEBUG:
                    console.print(
                        f"[dim]Executing file task: {task.action_type.value} "
                        f"{task.file_path}[/dim]"
                    )
                run_claude_executor(context, task)

            elif task.action_type == ActionType.RUN_COMMAND:
                execute_run_command(task, context)

            progress.advance(task_id)

    console.print(
        Panel.fit(
            "[bold]Stage 4 Complete[/bold]",
            title="Implementation Done",
            border_style="magenta",
        )
    )

    # ===== Stage 5-6: Code Review and Fixes =====
    if not skip_review:
        fix_iteration = 0
        while fix_iteration < max_fix_iterations:
            fix_iteration += 1
            context.fix_iteration_count = fix_iteration

            # ===== Stage 5: Codex (Code Review) =====
            console.print(
                f"\n[bold red]Stage 5: Codex Code Review (iteration {fix_iteration}/{max_fix_iterations})[/bold red]"
            )
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                progress.add_task(
                    description="[red]Codex reviewing code...[/red]", total=None
                )
                run_codex_code_review(context)

            if context.code_review_result:
                review_summary = f"""Overall: {context.code_review_result.overall_assessment}
Files Reviewed: {context.code_review_result.total_files_reviewed}
Issues Found: {len(context.code_review_result.items)}
Requires Fixes: {context.code_review_result.requires_fixes}"""
                console.print(
                    Panel(
                        review_summary,
                        title="Stage 5: Code Review Summary",
                        border_style="red",
                    )
                )

                if context.code_review_result.items:
                    for item in context.code_review_result.items:
                        severity_color = {
                            "critical": "bold red",
                            "high": "red",
                            "medium": "yellow",
                            "low": "blue",
                            "info": "dim",
                        }.get(item.severity.value, "white")
                        console.print(
                            f"  [{severity_color}][{item.severity.value.upper()}][/{severity_color}] "
                            f"{item.review_type.value}: {item.file_path} - {item.description[:60]}..."
                        )

                # ===== Stage 6: Claude (Fixes) =====
                if (
                    context.code_review_result.requires_fixes
                    and context.code_review_result.items
                ):
                    console.print(
                        f"\n[bold blue]Stage 6: Claude Fixes (iteration {fix_iteration}/{max_fix_iterations})[/bold blue]"
                    )

                    # 수정할 항목 선택
                    if auto_fix:
                        items_to_fix = context.code_review_result.items
                    else:
                        items_to_fix = _prompt_fix_selection(
                            context.code_review_result.items
                        )

                    if items_to_fix:
                        # 심각도 순으로 정렬 (critical > high > medium > low > info)
                        severity_order = ["critical", "high", "medium", "low", "info"]
                        sorted_items = sorted(
                            items_to_fix,
                            key=lambda x: severity_order.index(x.severity.value),
                        )

                        with Progress(
                            SpinnerColumn(),
                            TextColumn("[progress.description]{task.description}"),
                            transient=True,
                        ) as progress:
                            fix_count = len(sorted_items)
                            fix_task_id = progress.add_task(
                                description="[blue]Claude fixing...[/blue]",
                                total=fix_count,
                            )

                            for review_item in sorted_items:
                                progress.update(
                                    fix_task_id,
                                    description=f"Fixing {review_item.file_path} ({review_item.review_type.value})...",
                                )
                                run_claude_fixer(context, review_item)
                                progress.advance(fix_task_id)

                        console.print(
                            Panel.fit(
                                f"[bold]Stage 6 Complete - {len(sorted_items)} fixes applied[/bold]",
                                title="Fixes Applied",
                                border_style="blue",
                            )
                        )

                        # 반복 리뷰가 필요한 경우 계속
                        if fix_iteration < max_fix_iterations:
                            continue
                    else:
                        console.print("[dim]No fixes selected. Skipping Stage 6.[/dim]")
                        break
                else:
                    console.print("[green]No fixes required![/green]")
                    break
            else:
                console.print("[yellow]Code review did not produce results.[/yellow]")
                break
    else:
        console.print("[dim]Code review skipped (--skip-review)[/dim]")

    console.print(
        Panel.fit(
            "[bold green]All Done![/bold green]", title="6-Stage Workflow Finished"
        )
    )


if __name__ == "__main__":
    app()
