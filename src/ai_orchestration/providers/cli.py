"""CLI subprocess providers: agy, codex, claude.

Each provider owns its own argv builder (decision 3b): flattening these into
one shape breaks at runtime, most visibly for `agy`, which per A12 rejects a
positional prompt and requires `-p <prompt>`. This module also ports the
legacy `LLMToolConfig`/`LLMToolFactory`/`load_tool_config`/
`validate_tool_config` compatibility surface, with `ToolType.GEMINI` ->
`ToolType.AGY` as the explicit approved rename and `validate_tool_config`
preserved byte-for-byte as a non-fatal warning API.
"""

from __future__ import annotations

import codecs
import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, ValidationError

from ai_orchestration.errors import ConfigError
from ai_orchestration.providers.base import (
    CLIProviderError,
    ToolType,
    flatten_json_schema,
)
from ai_orchestration.utils.extract import extract_code_content, extract_json_object
from ai_orchestration.utils.subprocess_diag import truncate_stderr as _truncate_stderr

_HEARTBEAT_INTERVAL_DEFAULT = 10.0

# A hung CLI subprocess must not stall the run indefinitely (§Failure
# classes: "exceeds the stage timeout"). This is the default for every real
# `complete()`/`complete_structured()` call site; callers may override it.
DEFAULT_CLI_TIMEOUT_SECONDS = 300.0


def _extract_stream_json_text(line: str) -> list[str]:
    """Extract displayable text fragments from one stream-JSON line."""
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


def _run_cli_subprocess(
    args: list[str],
    *,
    binary_name: str,
    timeout: Optional[float] = None,
    heartbeat_interval: float = _HEARTBEAT_INTERVAL_DEFAULT,
    on_heartbeat: Optional[Callable[[], None]] = None,
    parse_stream_json: bool = False,
) -> str:
    """Run a CLI binary, emitting heartbeats and optionally extracting
    stream-JSON chunks, then return combined stdout.

    Raises `CLIProviderError` naming the binary for every failure row in the
    spec's CLI-attempts table: missing binary, spawn failure, nonzero exit,
    timeout, and unparseable output (the last is the caller's job once this
    returns text that fails validation). Runs in its own process group
    (`start_new_session=True`) so a timeout kills the whole descendant
    chain via `os.killpg`, not just the immediate child. Both stdout and
    stderr are registered with the selector: watching stdout alone risks a
    deadlock once a child fills the stderr pipe buffer after closing
    stdout.
    """
    if not shutil.which(args[0]):
        raise CLIProviderError(f"{binary_name}: binary not found on PATH")

    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            start_new_session=True,
        )
    except OSError as exc:
        raise CLIProviderError(f"{binary_name}: failed to start ({exc})") from exc

    assert process.stdout is not None
    assert process.stderr is not None
    merged_output: list[str] = []
    stderr_chunks: list[str] = []
    parsed_chunks: list[str] = []
    stdout_buffer = ""
    stream_decoders = {
        "stdout": codecs.getincrementaldecoder("utf-8")(errors="replace"),
        "stderr": codecs.getincrementaldecoder("utf-8")(errors="replace"),
    }
    sel = selectors.DefaultSelector()
    sel.register(process.stdout, selectors.EVENT_READ, "stdout")
    sel.register(process.stderr, selectors.EVENT_READ, "stderr")
    start = time.monotonic()
    last_output = start
    last_heartbeat = start

    def _kill_process_group() -> None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass

    while sel.get_map():
        if timeout is not None and time.monotonic() - start > timeout:
            _kill_process_group()
            raise CLIProviderError(f"{binary_name}: exceeded timeout of {timeout}s")
        remaining = (
            None if timeout is None else max(0.0, timeout - (time.monotonic() - start))
        )
        events = sel.select(
            timeout=min(heartbeat_interval, 1.0, remaining)
            if remaining is not None
            else min(heartbeat_interval, 1.0)
        )
        if not events:
            now = time.monotonic()
            if now - last_output >= heartbeat_interval and (
                now - last_heartbeat >= heartbeat_interval
            ):
                if on_heartbeat is not None:
                    on_heartbeat()
                last_heartbeat = now
            if process.poll() is not None and not sel.get_map():
                break
            continue
        for key, _ in events:
            stream = key.fileobj
            raw_chunk = os.read(stream.fileno(), 65536)
            stream_name = key.data
            text_chunk = stream_decoders[stream_name].decode(
                raw_chunk, final=not raw_chunk
            )
            if not raw_chunk:
                if text_chunk:
                    if stream_name == "stderr":
                        stderr_chunks.append(text_chunk)
                    else:
                        merged_output.append(text_chunk)
                        if parse_stream_json:
                            stdout_buffer += text_chunk
                if parse_stream_json and stream_name == "stdout" and stdout_buffer:
                    parsed_chunks.extend(_extract_stream_json_text(stdout_buffer))
                sel.unregister(stream)
                continue
            if stream_name == "stderr":
                stderr_chunks.append(text_chunk)
                continue
            merged_output.append(text_chunk)
            last_output = time.monotonic()
            if parse_stream_json:
                stdout_buffer += text_chunk
                lines = stdout_buffer.splitlines(keepends=True)
                stdout_buffer = ""
                if lines and not lines[-1].endswith(("\n", "\r")):
                    stdout_buffer = lines.pop()
                for line in lines:
                    parsed_chunks.extend(_extract_stream_json_text(line))

    remaining = (
        None if timeout is None else max(0.0, timeout - (time.monotonic() - start))
    )
    try:
        returncode = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        _kill_process_group()
        raise CLIProviderError(
            f"{binary_name}: exceeded timeout of {timeout}s"
        ) from exc
    stderr_text = "".join(stderr_chunks)
    combined = "".join(merged_output)

    if returncode != 0:
        raise CLIProviderError(
            f"{binary_name}: exited with code {returncode}: "
            f"{_truncate_stderr(stderr_text)}"
        )

    if parse_stream_json:
        if not parsed_chunks:
            parsed_chunks = _extract_stream_json_from_combined(combined)
        return "".join(parsed_chunks).strip()
    return combined.strip()


class BaseCLIProvider:
    """Base for CLI subprocess providers. Subclasses set `_binary_name`."""

    _binary_name: str = ""

    def get_binary_path(self) -> str:
        return shutil.which(self._binary_name) or self._binary_name

    def is_available(self) -> bool:
        return shutil.which(self._binary_name) is not None

    def build_command(self, prompt: str, debug: bool = False) -> list[str]:
        raise NotImplementedError

    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        debug: bool = False,
        timeout: Optional[float] = DEFAULT_CLI_TIMEOUT_SECONDS,
        on_heartbeat: Optional[Callable[[], None]] = None,
    ) -> str:
        """Run the CLI binary and return its output.

        Always routes through `_run_cli_subprocess`, the process-group-safe
        runner (its own session via `start_new_session=True`, killed with
        `os.killpg` on timeout). `debug` no longer selects a different
        subprocess path -- it only tells `build_command` to request the
        CLI's stream-JSON output format where supported, so this parses
        that format back into plain text; timeout/descendant cleanup never
        depends on it.
        """
        if system:
            prompt = f"{system}\n\n{prompt}"
        cmd = self.build_command(prompt, debug=debug)
        return _run_cli_subprocess(
            cmd,
            binary_name=self._binary_name,
            timeout=timeout,
            on_heartbeat=on_heartbeat,
            parse_stream_json=debug,
        )

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: type[BaseModel],
        system: Optional[str] = None,
        debug: bool = False,
    ) -> BaseModel:
        """Extract-only structured completion (Codex/Claude, decision 3)."""
        text = self.complete(prompt, system=system, debug=debug)
        content = extract_code_content(text)
        payload = extract_json_object(content) or extract_json_object(text)
        if payload is None:
            raise CLIProviderError(
                f"{self._binary_name}: no JSON object found in output"
            )
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            raise CLIProviderError(
                f"{self._binary_name}: output failed schema validation ({exc})"
            ) from exc


class AgyProvider(BaseCLIProvider):
    """Antigravity CLI provider. Replaces the legacy `gemini` tool (A12)."""

    _binary_name = "agy"

    def build_command(self, prompt: str, debug: bool = False) -> list[str]:
        # A12: agy rejects a positional prompt; it reads only from -p/-i/stdin.
        return [self.get_binary_path(), "-p", prompt]

    def build_structured_command(self, prompt: str, schema_path: str) -> list[str]:
        return [
            self.get_binary_path(),
            "-p",
            prompt,
            "--json-schema",
            schema_path,
            "--output-format",
            "json",
        ]

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: type[BaseModel],
        system: Optional[str] = None,
        debug: bool = False,
        timeout: Optional[float] = DEFAULT_CLI_TIMEOUT_SECONDS,
    ) -> BaseModel:
        """Native structured output first, extraction fallback second.

        agy enforces a schema natively via `--json-schema`/`--output-format
        json`, returning a `structured_output` object in its JSON envelope
        (A12). On malformed, missing, or invalid output this falls back to
        the same extraction path Codex/Claude use. Routes through
        `_run_cli_subprocess` (process-group-safe timeout/descendant kill),
        the same runner `BaseCLIProvider.complete` uses -- this is not a
        text-completion call so it cannot simply delegate to `complete()`.
        """
        if system:
            prompt = f"{system}\n\n{prompt}"
        flat_schema = flatten_json_schema(schema.model_json_schema())
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as handle:
            json.dump(flat_schema, handle)
            schema_path = handle.name
        try:
            cmd = self.build_structured_command(prompt, schema_path)
            text = _run_cli_subprocess(
                cmd, binary_name=self._binary_name, timeout=timeout
            )

            envelope = extract_json_object(text)
            if envelope is not None and "structured_output" in envelope:
                try:
                    return schema.model_validate(envelope["structured_output"])
                except ValidationError:
                    pass  # fall through to extraction

            payload = extract_json_object(text)
            if payload is not None:
                try:
                    return schema.model_validate(payload)
                except ValidationError:
                    pass

            raise CLIProviderError(
                "agy: no valid structured output or extractable JSON in response"
            )
        finally:
            Path(schema_path).unlink(missing_ok=True)


class CodexProvider(BaseCLIProvider):
    """OpenAI Codex CLI provider."""

    _binary_name = "codex"

    def build_command(self, prompt: str, debug: bool = False) -> list[str]:
        return [self.get_binary_path(), "exec", prompt]


class ClaudeProvider(BaseCLIProvider):
    """Claude CLI provider."""

    _binary_name = "claude"

    def build_command(self, prompt: str, debug: bool = False) -> list[str]:
        cmd = [
            self.get_binary_path(),
            prompt,
            "--print",
            "--tools",
            "",
            "--disable-slash-commands",
            "--permission-mode",
            "dontAsk",
        ]
        if debug:
            cmd.extend(["--output-format", "stream-json"])
        return cmd


# --- Legacy-compatible LLMToolConfig / LLMToolFactory surface --------------


@dataclass
class LLMToolConfig:
    """Configuration for LLM tools used in each stage.

    Ported from `llm_tools.py`'s `LLMToolConfig`. `brainstormer` defaults to
    `ToolType.AGY` (A12); the other five defaults are unchanged.
    """

    brainstormer: ToolType = ToolType.AGY
    reviewer: ToolType = ToolType.CODEX
    planner: ToolType = ToolType.CODEX
    executor: ToolType = ToolType.CLAUDE
    code_reviewer: ToolType = ToolType.CODEX
    fixer: ToolType = ToolType.CLAUDE

    def get_tool_for_stage(self, stage) -> ToolType:
        return getattr(self, stage.value)


class LLMToolFactory:
    """Factory for creating provider instances, ported from `llm_tools.py`."""

    _tools: dict[ToolType, type[BaseCLIProvider]] = {
        ToolType.AGY: AgyProvider,
        ToolType.CODEX: CodexProvider,
        ToolType.CLAUDE: ClaudeProvider,
    }

    _api_tool_types: set[ToolType] = {
        ToolType.GEMINI_API,
        ToolType.OPENAI_API,
        ToolType.ANTHROPIC_API,
    }

    @classmethod
    def is_api_tool(cls, tool_type: ToolType) -> bool:
        return tool_type in cls._api_tool_types

    @classmethod
    def create(cls, tool_type: ToolType) -> BaseCLIProvider:
        tool_class = cls._tools.get(tool_type)
        if tool_class is None:
            raise ValueError(f"Unknown tool type: {tool_type}")
        return tool_class()

    @classmethod
    def create_api_tool(cls, tool_type: ToolType):
        from ai_orchestration.providers.legacy_api import (
            AnthropicTool,
            GoogleAITool,
            OpenAITool,
        )

        api_tools = {
            ToolType.GEMINI_API: GoogleAITool,
            ToolType.OPENAI_API: OpenAITool,
            ToolType.ANTHROPIC_API: AnthropicTool,
        }
        tool_class = api_tools.get(tool_type)
        if tool_class is None:
            raise ValueError(f"Unknown API tool type: {tool_type}")
        return tool_class()

    @classmethod
    def get_tool_for_stage(cls, config: LLMToolConfig, stage) -> BaseCLIProvider:
        tool_type = config.get_tool_for_stage(stage)
        return cls.create(tool_type)


def load_tool_config(
    config_file: Optional[Path] = None,
    brainstormer: Optional[str] = None,
    reviewer: Optional[str] = None,
    planner: Optional[str] = None,
    executor: Optional[str] = None,
    code_reviewer: Optional[str] = None,
    fixer: Optional[str] = None,
) -> LLMToolConfig:
    """Load tool configuration from file or CLI options.

    Priority: CLI options > config file > defaults. Ported unchanged from
    `llm_tools.py` except the `"gemini"` file-default literal, which is
    replaced with `"agy"` (A12).
    """
    config = LLMToolConfig()

    if config_file and config_file.exists():
        try:
            with open(config_file) as f:
                data = json.load(f)
            config = LLMToolConfig(
                brainstormer=ToolType(data.get("brainstormer", "agy")),
                reviewer=ToolType(data.get("reviewer", "codex")),
                planner=ToolType(data.get("planner", "codex")),
                executor=ToolType(data.get("executor", "claude")),
                code_reviewer=ToolType(data.get("code_reviewer", "codex")),
                fixer=ToolType(data.get("fixer", "claude")),
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ConfigError(f"tool config '{config_file}' is invalid") from exc

    if brainstormer:
        config.brainstormer = ToolType(brainstormer)
    if reviewer:
        config.reviewer = ToolType(reviewer)
    if planner:
        config.planner = ToolType(planner)
    if executor:
        config.executor = ToolType(executor)
    if code_reviewer:
        config.code_reviewer = ToolType(code_reviewer)
    if fixer:
        config.fixer = ToolType(fixer)

    return config


def validate_tool_config(config: LLMToolConfig) -> list[str]:
    """Validate tool configuration and return warnings.

    Ported unchanged from `llm_tools.py` as a non-fatal compatibility API:
    returns PATH warnings for configured CLI tools and never raises. New
    fail-fast startup validation is `resolve_stage_config`'s job (U1), a
    separate caller with a different (raising) contract.
    """
    warnings = []
    used_tools = {
        config.brainstormer,
        config.reviewer,
        config.planner,
        config.executor,
        config.code_reviewer,
        config.fixer,
    }

    for tool_type in used_tools:
        tool = LLMToolFactory.create(tool_type)
        if not tool.is_available():
            warnings.append(
                f"Tool '{tool_type.value}' not found in PATH. "
                f"Ensure it is installed before running."
            )

    return warnings
