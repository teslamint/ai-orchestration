"""Provider tests: CLI argv contracts, structured output, legacy ToolType/
LLMToolConfig compatibility surface, and typed failure classification (U3).
"""

import shutil
import subprocess

import pytest
from pydantic import BaseModel

from ai_orchestration.errors import ProviderError
from ai_orchestration.providers.base import (
    AuthenticationError,
    CLIProviderError,
    ModelFaultError,
    ProviderResult,
    StageRole,
    ToolType,
    TransportError,
    flatten_json_schema,
)
from ai_orchestration.providers.cli import (
    AgyProvider,
    ClaudeProvider,
    CodexProvider,
    LLMToolConfig,
    LLMToolFactory,
    load_tool_config,
    validate_tool_config,
)

# --- ToolType / StageRole (legacy-compatible enum surface) -----------------


def test_tool_type_values():
    assert ToolType.AGY.value == "agy"
    assert ToolType.CODEX.value == "codex"
    assert ToolType.CLAUDE.value == "claude"


def test_tool_type_from_string():
    assert ToolType("agy") == ToolType.AGY
    assert ToolType("codex") == ToolType.CODEX
    assert ToolType("claude") == ToolType.CLAUDE


def test_tool_type_invalid():
    with pytest.raises(ValueError):
        ToolType("invalid")


def test_tool_type_gemini_no_longer_selects_a_cli_provider():
    # error: A12 removes gemini; the historical enum value must not resolve.
    with pytest.raises(ValueError):
        ToolType("gemini")


def test_stage_role_values():
    assert StageRole.BRAINSTORMER.value == "brainstormer"
    assert StageRole.EXECUTOR.value == "executor"


# --- Typed provider failures -------------------------------------------------


def test_transport_error_is_a_provider_error():
    assert issubclass(TransportError, ProviderError)


def test_authentication_error_is_a_provider_error():
    assert issubclass(AuthenticationError, ProviderError)


def test_model_fault_error_is_a_provider_error():
    assert issubclass(ModelFaultError, ProviderError)


def test_cli_provider_error_is_a_provider_error():
    assert issubclass(CLIProviderError, ProviderError)


def test_provider_result_carries_content_and_provider_name():
    result = ProviderResult(content="hi", provider_name="agy")
    assert result.content == "hi"
    assert result.provider_name == "agy"
    assert result.used_structured_path is False


# --- flatten_json_schema -----------------------------------------------------


class _Inner(BaseModel):
    value: int


class _Outer(BaseModel):
    items: list[_Inner]


def test_flatten_json_schema_removes_defs_and_refs():
    schema = _Outer.model_json_schema()
    assert "$defs" in schema  # sanity: pydantic does emit $defs for nested models
    flat = flatten_json_schema(schema)
    assert "$defs" not in flat
    assert "$ref" not in str(flat)


def test_flatten_json_schema_preserves_inlined_structure():
    schema = _Outer.model_json_schema()
    flat = flatten_json_schema(schema)
    items_schema = flat["properties"]["items"]["items"]
    assert items_schema["properties"]["value"]["type"] == "integer"


def test_flatten_json_schema_is_a_no_op_for_already_flat_schema():
    class Flat(BaseModel):
        name: str

    schema = Flat.model_json_schema()
    flat = flatten_json_schema(schema)
    assert flat["properties"]["name"]["type"] == "string"


# --- AgyProvider argv (A12: -p flag, never positional) -----------------------


def test_agy_provider_build_command():
    provider = AgyProvider()
    cmd = provider.build_command("test prompt")
    assert cmd == [provider.get_binary_path(), "-p", "test prompt"]


def test_agy_provider_build_command_debug():
    provider = AgyProvider()
    cmd = provider.build_command("test prompt", debug=True)
    assert "-p" in cmd
    assert "test prompt" in cmd


def test_agy_provider_never_receives_a_positional_prompt():
    # error/acceptance: A12 — agy rejects a positional prompt; the argv must
    # place the prompt after an explicit -p flag, never as cmd[1].
    provider = AgyProvider()
    cmd = provider.build_command("some prompt")
    assert cmd[1] == "-p"
    assert cmd[1] != "some prompt"


def test_agy_provider_build_structured_command_uses_json_schema_flags():
    provider = AgyProvider()
    cmd = provider.build_structured_command("prompt", "/tmp/schema.json")
    assert "--json-schema" in cmd
    assert "/tmp/schema.json" in cmd
    assert "--output-format" in cmd
    assert "json" in cmd
    assert cmd[1] == "-p"


# --- CodexProvider argv -------------------------------------------------------


def test_codex_provider_build_command():
    provider = CodexProvider()
    cmd = provider.build_command("test prompt")
    assert cmd == [provider.get_binary_path(), "exec", "test prompt"]


def test_codex_provider_build_command_debug():
    provider = CodexProvider()
    cmd = provider.build_command("test prompt", debug=True)
    assert "exec" in cmd
    assert cmd[-1] == "test prompt"


# --- ClaudeProvider argv -------------------------------------------------------


def test_claude_provider_build_command():
    provider = ClaudeProvider()
    cmd = provider.build_command("test prompt")
    assert "--print" in cmd
    assert "--tools" in cmd
    assert "--disable-slash-commands" in cmd
    assert "--permission-mode" in cmd
    assert "dontAsk" in cmd
    assert "test prompt" in cmd


def test_claude_provider_build_command_debug():
    provider = ClaudeProvider()
    cmd = provider.build_command("test prompt", debug=True)
    assert "--output-format" in cmd
    assert "stream-json" in cmd


# --- Provider creation via LLMToolFactory (legacy-compatible) ---------------


def test_create_agy_provider():
    provider = LLMToolFactory.create(ToolType.AGY)
    assert isinstance(provider, AgyProvider)


def test_create_codex_provider():
    provider = LLMToolFactory.create(ToolType.CODEX)
    assert isinstance(provider, CodexProvider)


def test_create_claude_provider():
    provider = LLMToolFactory.create(ToolType.CLAUDE)
    assert isinstance(provider, ClaudeProvider)


# --- LLMToolConfig / brainstormer defaults to AGY ----------------------------


def test_llm_tool_config_brainstormer_defaults_to_agy():
    config = LLMToolConfig()
    assert config.brainstormer == ToolType.AGY
    assert config.reviewer == ToolType.CODEX
    assert config.planner == ToolType.CODEX
    assert config.executor == ToolType.CLAUDE
    assert config.code_reviewer == ToolType.CODEX
    assert config.fixer == ToolType.CLAUDE


def test_load_tool_config_default_brainstormer_is_agy():
    config = load_tool_config()
    assert config.brainstormer == ToolType.AGY


def test_load_tool_config_cli_options_override_file():
    config = load_tool_config(brainstormer="claude", planner="codex")
    assert config.brainstormer == ToolType.CLAUDE
    assert config.planner == ToolType.CODEX


# --- validate_tool_config: unchanged non-fatal compatibility API -----------


def test_validate_tool_config_returns_warnings_list():
    config = LLMToolConfig()
    warnings = validate_tool_config(config)
    assert isinstance(warnings, list)


def test_validate_tool_config_warns_for_missing_cli(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    config = LLMToolConfig()
    warnings = validate_tool_config(config)
    assert any("agy" in w for w in warnings)
    assert all("not found in PATH" in w for w in warnings)


def test_validate_tool_config_empty_when_all_tools_available(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    config = LLMToolConfig()
    warnings = validate_tool_config(config)
    assert warnings == []


def test_validate_tool_config_never_raises_startup_error(monkeypatch):
    # acceptance: validate_tool_config is non-fatal; it must return, not raise,
    # even when every CLI is missing. New fail-fast validation is a separate
    # caller (U1's resolve_stage_config / RoutingError), tested elsewhere.
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    config = LLMToolConfig()
    warnings = validate_tool_config(config)
    assert len(warnings) == 3  # agy, codex, claude (API tools have no binary)


# --- CLI provider execution: subprocess spies, no real process -------------


class _FakeCompletedProcess:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_codex_provider_complete_returns_extracted_text(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_run(args, **kwargs):
        assert args == ["/usr/bin/codex", "exec", "hello"]
        return _FakeCompletedProcess(stdout="Codex response text\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = CodexProvider()
    result = provider.complete("hello")
    assert result == "Codex response text"


def test_claude_provider_complete_extracts_text(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/claude")

    def fake_run(args, **kwargs):
        return _FakeCompletedProcess(stdout="Claude output\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = ClaudeProvider()
    result = provider.complete("hello")
    assert result == "Claude output"


def test_cli_provider_missing_binary_raises_cli_provider_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    provider = CodexProvider()
    with pytest.raises(CLIProviderError, match="codex"):
        provider.complete("hello")


def test_cli_provider_nonzero_exit_raises_with_exit_code_and_stderr(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_run(args, **kwargs):
        result = _FakeCompletedProcess(stdout="", returncode=2)
        result.stderr = "boom"
        raise subprocess.CalledProcessError(2, args, output="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = CodexProvider()
    with pytest.raises(CLIProviderError, match="codex"):
        provider.complete("hello")


def test_cli_provider_spawn_failure_raises_cli_provider_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_run(args, **kwargs):
        raise PermissionError("not executable")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = CodexProvider()
    with pytest.raises(CLIProviderError, match="codex"):
        provider.complete("hello")


def test_cli_provider_timeout_raises_cli_provider_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = CodexProvider()
    with pytest.raises(CLIProviderError, match="codex"):
        provider.complete("hello", timeout=1)


def test_cli_provider_empty_stderr_reports_no_stderr_diagnostic(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(3, args, output="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = CodexProvider()
    with pytest.raises(CLIProviderError):
        provider.complete("hello")


# --- agy structured output (native schema, then extraction fallback) -------


class _Plan(BaseModel):
    step_id: int
    name: str


def test_agy_provider_complete_structured_uses_native_structured_output(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/agy")

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _FakeCompletedProcess(
            stdout='{"structured_output": {"step_id": 1, "name": "alpha"}}\n'
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = AgyProvider()
    result = provider.complete_structured("build a plan", schema=_Plan)
    assert result == _Plan(step_id=1, name="alpha")
    assert "--json-schema" in captured["args"]


def test_agy_provider_complete_structured_falls_back_to_extraction(
    monkeypatch, tmp_path
):
    # edge: agy ignores/malforms the schema and returns prose containing JSON;
    # the extraction fallback must still validate a Plan.
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/agy")

    def fake_run(args, **kwargs):
        return _FakeCompletedProcess(
            stdout='Sure! Here is the plan: {"step_id": 2, "name": "beta"} done.\n'
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = AgyProvider()
    result = provider.complete_structured("build a plan", schema=_Plan)
    assert result == _Plan(step_id=2, name="beta")


def test_agy_provider_complete_structured_raises_when_both_paths_fail(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/agy")

    def fake_run(args, **kwargs):
        return _FakeCompletedProcess(stdout="no json anywhere in this response\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = AgyProvider()
    with pytest.raises(CLIProviderError, match="agy"):
        provider.complete_structured("build a plan", schema=_Plan)


def test_codex_provider_complete_structured_uses_extraction_only(monkeypatch):
    # Codex/Claude are extract-only per decision 3; no native schema path.
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_run(args, **kwargs):
        assert "--json-schema" not in args
        return _FakeCompletedProcess(stdout='{"step_id": 3, "name": "gamma"}\n')

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = CodexProvider()
    result = provider.complete_structured("build a plan", schema=_Plan)
    assert result == _Plan(step_id=3, name="gamma")


# --- Heartbeat / stream-JSON extraction (behavior inventory row) -----------


def test_stream_json_extraction_reads_claude_style_result_events(monkeypatch):
    from ai_orchestration.providers.cli import _extract_stream_json_from_combined

    combined = "\n".join(
        [
            '{"type": "system", "subtype": "init"}',
            '{"type": "result", "result": "final answer text"}',
        ]
    )
    texts = _extract_stream_json_from_combined(combined)
    assert texts == ["final answer text"]


def test_stream_json_extraction_ignores_non_json_lines():
    from ai_orchestration.providers.cli import _extract_stream_json_text

    assert _extract_stream_json_text("not json at all") == []


def test_stream_json_extraction_reads_delta_and_content_block_text():
    from ai_orchestration.providers.cli import _extract_stream_json_text

    delta_line = '{"delta": {"text": "partial"}}'
    block_line = '{"content_block": {"text": "block text"}}'
    assert _extract_stream_json_text(delta_line) == ["partial"]
    assert _extract_stream_json_text(block_line) == ["block text"]


def test_heartbeat_callback_fires_when_subprocess_is_silent():
    # Mutation-guarded: deleting the heartbeat callback invocation, or
    # raising the interval past the sleep below, must fail this test. Uses a
    # real short-lived subprocess (python -c "import time; time.sleep(...)")
    # so the selector-based read loop is exercised for real, not mocked.
    import sys

    from ai_orchestration.providers.cli import _run_cli_subprocess

    heartbeats = []
    _run_cli_subprocess(
        [sys.executable, "-c", "import time; time.sleep(0.3)"],
        binary_name="python",
        heartbeat_interval=0.05,
        on_heartbeat=lambda: heartbeats.append(True),
    )
    assert len(heartbeats) >= 1
