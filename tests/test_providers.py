"""Provider tests: CLI argv contracts, structured output, legacy ToolType/
LLMToolConfig compatibility surface, and typed failure classification (U3).
"""

import shutil

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


class _SimpleSchema(BaseModel):
    value: str


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


def test_load_tool_config_malformed_file_raises_config_error(tmp_path):
    from ai_orchestration.errors import ConfigError

    path = tmp_path / "tools.json"
    path.write_text("{invalid")
    with pytest.raises(ConfigError, match="tool config"):
        load_tool_config(path)


def test_load_tool_config_unknown_file_tool_raises_config_error(tmp_path):
    from ai_orchestration.errors import ConfigError

    path = tmp_path / "tools.json"
    path.write_text('{"planner": "unknown"}')
    with pytest.raises(ConfigError, match="tool config"):
        load_tool_config(path)


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


def test_extract_json_list_rejects_direct_non_list_of_objects():
    from ai_orchestration.utils.extract import extract_json_list

    assert extract_json_list('{"tasks": []}') == []
    assert extract_json_list("[1, 2]") == []


@pytest.mark.parametrize("structured", [False, True])
def test_http_provider_empty_choices_raises_model_fault(structured):
    from ai_orchestration.providers.http import HttpProvider

    class _Completions:
        @staticmethod
        def create(**_kwargs):
            return type("Response", (), {"choices": []})()

    provider = HttpProvider(model="test", base_url="http://stub/v1", api_key="key")
    provider._client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": _Completions()})()}
    )()

    with pytest.raises(ModelFaultError, match="empty choices"):
        if structured:
            provider.complete_structured("prompt", schema=_SimpleSchema)
        else:
            provider.complete("prompt")


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


# --- Legacy *_API ToolType support (CodeRabbit PR #2) -----------------------


def test_get_tool_for_stage_constructs_api_adapter_for_legacy_api_tool_type():
    # error: before the fix, LLMToolFactory.create only recognizes CLI tool
    # types, so a legacy config selecting a `*_API` value raised
    # `ValueError: Unknown tool type` instead of constructing the API
    # adapter LLMToolFactory.create_api_tool already knows how to build.
    from ai_orchestration.providers.legacy_api import OpenAITool

    config = LLMToolConfig(brainstormer=ToolType.OPENAI_API)
    tool = LLMToolFactory.get_tool_for_stage(config, StageRole.BRAINSTORMER)
    assert isinstance(tool, OpenAITool)


def test_get_tool_for_stage_still_constructs_cli_provider_for_cli_tool_type():
    config = LLMToolConfig(brainstormer=ToolType.AGY)
    tool = LLMToolFactory.get_tool_for_stage(config, StageRole.BRAINSTORMER)
    assert isinstance(tool, AgyProvider)


def test_validate_tool_config_accepts_legacy_api_tool_types_without_raising(
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = LLMToolConfig(brainstormer=ToolType.OPENAI_API)
    warnings = validate_tool_config(config)
    assert isinstance(warnings, list)


def test_validate_tool_config_never_reports_path_warning_for_api_tool_types(
    monkeypatch,
):
    # error: a `*_API` tool has no binary on PATH by definition; the generic
    # "not found in PATH" CLI warning is the wrong diagnostic for a missing
    # API key and must not be emitted for it.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = LLMToolConfig(brainstormer=ToolType.OPENAI_API)
    warnings = validate_tool_config(config)
    api_warnings = [w for w in warnings if "openai_api" in w]
    assert api_warnings
    assert all("not found in PATH" not in w for w in api_warnings)


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


# --- CLI provider execution: real short-lived subprocesses -----------------
#
# Production no longer calls `subprocess.run` at all (finding #3):
# `BaseCLIProvider.complete` and `AgyProvider.complete_structured` both
# route through `_run_cli_subprocess`, which uses `subprocess.Popen` in its
# own process group. These tests exercise that real process, not a
# `subprocess.run` spy, so they prove the actual runtime behavior instead
# of a mocked seam that production no longer reaches.


def test_codex_provider_complete_returns_extracted_text(monkeypatch):
    import sys

    provider = CodexProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: [
            sys.executable,
            "-c",
            "print('Codex response text')",
        ],
    )
    result = provider.complete("hello")
    assert result == "Codex response text"


def test_claude_provider_complete_extracts_text(monkeypatch):
    import sys

    provider = ClaudeProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: [sys.executable, "-c", "print('Claude output')"],
    )
    result = provider.complete("hello")
    assert result == "Claude output"


def test_cli_provider_missing_binary_raises_cli_provider_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    provider = CodexProvider()
    with pytest.raises(CLIProviderError, match="codex"):
        provider.complete("hello")


def test_cli_provider_nonzero_exit_raises_with_exit_code_and_stderr(monkeypatch):
    import sys

    provider = CodexProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('boom'); sys.exit(2)",
        ],
    )
    with pytest.raises(CLIProviderError, match="exited with code 2") as exc_info:
        provider.complete("hello")
    assert "boom" in str(exc_info.value)


def test_cli_provider_spawn_failure_raises_cli_provider_error(tmp_path, monkeypatch):
    # Real spawn failure (not mocked): an executable-bit file that is not a
    # valid binary makes the OS reject exec() with a genuine OSError.
    bad_binary = tmp_path / "not-a-real-binary"
    bad_binary.write_bytes(b"not a real executable\n")
    bad_binary.chmod(0o755)

    provider = CodexProvider()
    monkeypatch.setattr(provider, "_binary_name", "not-a-real-binary")
    monkeypatch.setattr(
        provider, "build_command", lambda prompt, debug=False: [str(bad_binary)]
    )
    with pytest.raises(CLIProviderError, match="failed to start"):
        provider.complete("hello")


def test_cli_provider_prepends_system_prompt_to_user_prompt(monkeypatch):
    import ai_orchestration.providers.cli as cli_module

    captured = {}
    provider = CodexProvider()
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: captured.setdefault("prompt", prompt) or ["true"],
    )
    monkeypatch.setattr(
        cli_module, "_run_cli_subprocess", lambda *_args, **_kwargs: "ok"
    )

    assert provider.complete("user prompt", system="system prompt") == "ok"
    assert captured["prompt"] == "system prompt\n\nuser prompt"


def test_cli_provider_timeout_raises_cli_provider_error(monkeypatch):
    import sys

    provider = CodexProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: [
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
        ],
    )
    with pytest.raises(CLIProviderError, match="timeout"):
        provider.complete("hello", timeout=0.3)


def test_cli_provider_empty_stderr_reports_no_stderr_diagnostic(monkeypatch):
    import sys

    provider = CodexProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: [sys.executable, "-c", "import sys; sys.exit(3)"],
    )
    with pytest.raises(CLIProviderError, match="no stderr"):
        provider.complete("hello")


# --- agy structured output (native schema, then extraction fallback) -------


class _Plan(BaseModel):
    step_id: int
    name: str


def test_agy_provider_complete_structured_uses_native_structured_output(
    monkeypatch, tmp_path
):
    import sys

    provider = AgyProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    envelope = '{"structured_output": {"step_id": 1, "name": "alpha"}}'
    monkeypatch.setattr(
        provider,
        "build_structured_command",
        lambda prompt, schema_path: [sys.executable, "-c", f"print({envelope!r})"],
    )
    result = provider.complete_structured("build a plan", schema=_Plan)
    assert result == _Plan(step_id=1, name="alpha")


def test_agy_provider_writes_flattened_structured_schema(monkeypatch):
    import ai_orchestration.providers.cli as cli_module

    captured = {}
    provider = AgyProvider()
    monkeypatch.setattr(
        provider, "build_structured_command", lambda _prompt, _path: ["true"]
    )
    monkeypatch.setattr(
        cli_module,
        "_run_cli_subprocess",
        lambda *_args, **_kwargs: '{"structured_output": {"items": [{"value": 1}]}}',
    )
    real_dump = cli_module.json.dump

    def capture_dump(value, handle):
        captured["schema"] = value
        return real_dump(value, handle)

    monkeypatch.setattr(cli_module.json, "dump", capture_dump)

    provider.complete_structured("plan", schema=_Outer)

    assert "$defs" not in captured["schema"]
    assert "$ref" not in str(captured["schema"])


def test_agy_provider_complete_structured_falls_back_to_extraction(
    monkeypatch, tmp_path
):
    # edge: agy ignores/malforms the schema and returns prose containing JSON;
    # the extraction fallback must still validate a Plan.
    import sys

    provider = AgyProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    prose = 'Sure! Here is the plan: {"step_id": 2, "name": "beta"} done.'
    monkeypatch.setattr(
        provider,
        "build_structured_command",
        lambda prompt, schema_path: [sys.executable, "-c", f"print({prose!r})"],
    )
    result = provider.complete_structured("build a plan", schema=_Plan)
    assert result == _Plan(step_id=2, name="beta")


def test_agy_provider_complete_structured_raises_when_both_paths_fail(
    monkeypatch, tmp_path
):
    import sys

    provider = AgyProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_structured_command",
        lambda prompt, schema_path: [
            sys.executable,
            "-c",
            "print('no json anywhere in this response')",
        ],
    )
    with pytest.raises(CLIProviderError, match="agy"):
        provider.complete_structured("build a plan", schema=_Plan)


def test_codex_provider_complete_structured_uses_extraction_only(monkeypatch):
    # Codex/Claude are extract-only per decision 3; no native schema path.
    import sys

    provider = CodexProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: [
            sys.executable,
            "-c",
            'print(\'{"step_id": 3, "name": "gamma"}\')',
        ],
    )
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


# --- Default CLI timeout wiring (finding #10) -------------------------------


def test_base_cli_provider_complete_default_timeout_is_wired(monkeypatch):
    # Mutation-guarded: a default of None (no timeout) must fail this test.
    import inspect

    from ai_orchestration.providers.cli import (
        DEFAULT_CLI_TIMEOUT_SECONDS,
        BaseCLIProvider,
    )

    sig = inspect.signature(BaseCLIProvider.complete)
    assert sig.parameters["timeout"].default == DEFAULT_CLI_TIMEOUT_SECONDS
    assert DEFAULT_CLI_TIMEOUT_SECONDS is not None
    assert DEFAULT_CLI_TIMEOUT_SECONDS > 0


def test_base_cli_provider_complete_passes_default_timeout_to_run_cli_subprocess(
    monkeypatch,
):
    # Real timeout enforcement (kill on expiry) is proven separately by
    # test_run_cli_subprocess_timeout_kills_entire_process_group; this test
    # only proves BaseCLIProvider.complete forwards its timeout argument
    # into the real runner it now always uses.
    from ai_orchestration.providers import cli as cli_module

    captured = {}

    def fake_run_cli_subprocess(args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return "ok"

    monkeypatch.setattr(cli_module, "_run_cli_subprocess", fake_run_cli_subprocess)
    provider = CodexProvider()
    provider.complete("hello")
    assert captured["timeout"] is not None
    assert captured["timeout"] > 0


# --- _run_cli_subprocess real production usage (finding #15) --------------


def test_claude_provider_complete_debug_routes_through_run_cli_subprocess(
    monkeypatch,
):
    # error: before the fix, debug=True still went through plain
    # subprocess.run, so _run_cli_subprocess (heartbeat/stream-JSON) had no
    # production caller. Claude's debug build_command actually requests
    # `--output-format stream-json` (the only provider that does), so its
    # debug path is the one that must observe stream-JSON extraction.
    import sys

    provider = ClaudeProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: [
            sys.executable,
            "-c",
            'print(\'{"type": "result", "result": "stream json text"}\')',
        ],
    )
    result = provider.complete("hello", debug=True)
    assert result == "stream json text"


def test_codex_provider_complete_debug_preserves_plain_text_output(monkeypatch):
    # error: Codex's debug build_command does not request stream-JSON output
    # (see test_codex_provider_build_command_debug), so
    # BaseCLIProvider.complete must not run stream-JSON extraction on its
    # ordinary debug text -- doing so drops every line that fails to parse
    # as JSON, silently returning "" instead of the real output.
    import sys

    provider = CodexProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: [
            sys.executable,
            "-c",
            "print('ordinary codex debug output')",
        ],
    )
    result = provider.complete("hello", debug=True)
    assert result == "ordinary codex debug output"


def test_agy_provider_complete_debug_preserves_plain_text_output(monkeypatch):
    # error: same class of bug as Codex -- agy's debug build_command does
    # not request stream-JSON output either.
    import sys

    provider = AgyProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: [
            sys.executable,
            "-c",
            "print('ordinary agy debug output')",
        ],
    )
    result = provider.complete("hello", debug=True)
    assert result == "ordinary agy debug output"


# --- stderr-fill deadlock guard (finding #16) -------------------------------


def test_run_cli_subprocess_does_not_deadlock_on_large_stderr():
    # Mutation-guarded: watching stdout only (no stderr selector
    # registration) deadlocks once the child fills the stderr pipe buffer
    # after closing stdout. A pipe buffer is typically 64KiB; write well
    # past that to stderr, then close stdout, to force the deadlock if the
    # stderr selector registration is removed.
    import sys

    from ai_orchestration.providers.cli import _run_cli_subprocess

    script = (
        "import sys\n"
        "sys.stderr.write('x' * 200000)\n"
        "sys.stderr.flush()\n"
        "sys.stdout.write('done')\n"
    )
    result = _run_cli_subprocess(
        [sys.executable, "-c", script], binary_name="python", timeout=10
    )
    assert result == "done"


# --- Process-group timeout kill (finding #21) -------------------------------


def test_run_cli_subprocess_timeout_kills_entire_process_group(tmp_path):
    # Mutation-guarded: killing only the immediate child (not the process
    # group) orphans a descendant that outlives the timeout. Spawn a parent
    # that forks a child which writes a marker file in a loop; after the
    # parent is killed on timeout, the descendant must not still be alive
    # writing to the marker file moments later.
    import sys
    import time

    from ai_orchestration.providers.base import CLIProviderError
    from ai_orchestration.providers.cli import _run_cli_subprocess

    marker = tmp_path / "marker.txt"
    child_script = tmp_path / "child.py"
    child_script.write_text(
        "import time\n"
        f"marker = {str(marker)!r}\n"
        "while True:\n"
        "    open(marker, 'a').write(str(time.time()))\n"
        "    time.sleep(0.05)\n"
    )
    parent_script = tmp_path / "parent.py"
    parent_script.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child_script)!r}])\n"
        "time.sleep(30)\n"
    )
    with pytest.raises(CLIProviderError, match="timeout"):
        _run_cli_subprocess(
            [sys.executable, str(parent_script)], binary_name="python", timeout=0.5
        )
    time.sleep(0.3)
    size_after_kill = marker.stat().st_size if marker.exists() else 0
    time.sleep(0.3)
    size_later = marker.stat().st_size if marker.exists() else 0
    assert size_after_kill == size_later, (
        "descendant process kept writing after the parent's timeout kill: "
        "the process group was not terminated"
    )


# --- Finding #3: complete()/complete_structured() must route through the
# --- process-group-safe runner regardless of `debug` --------------------


def _write_descendant_marker_scripts(tmp_path):
    """Shared fixture: a parent that forks a marker-writing descendant."""
    marker = tmp_path / "marker.txt"
    child_script = tmp_path / "child.py"
    child_script.write_text(
        "import time\n"
        f"marker = {str(marker)!r}\n"
        "while True:\n"
        "    open(marker, 'a').write(str(time.time()))\n"
        "    time.sleep(0.05)\n"
    )
    parent_script = tmp_path / "parent.py"
    parent_script.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child_script)!r}])\n"
        "time.sleep(30)\n"
    )
    return marker, parent_script


def _assert_descendant_stopped_writing(marker, sleep):
    sleep(0.3)
    size_after_kill = marker.stat().st_size if marker.exists() else 0
    sleep(0.3)
    size_later = marker.stat().st_size if marker.exists() else 0
    assert size_after_kill == size_later, (
        "descendant process kept writing after the timeout kill: "
        "the process group was not terminated"
    )


def test_base_cli_provider_complete_debug_false_kills_process_group_on_timeout(
    tmp_path, monkeypatch
):
    # error/acceptance: before the fix, `debug=False` (the production
    # default -- cli.py never sets debug=True on any complete_kwargs path)
    # went through plain `subprocess.run(..., start_new_session=True)`,
    # whose stdlib TimeoutExpired handler only kills the immediate PID, so
    # a forked descendant outlives the timeout. This drives the failure
    # through the real public entry point, `BaseCLIProvider.complete`, not
    # `_run_cli_subprocess` directly.
    import sys
    import time

    marker, parent_script = _write_descendant_marker_scripts(tmp_path)
    provider = CodexProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: [sys.executable, str(parent_script)],
    )
    with pytest.raises(CLIProviderError, match="timeout"):
        provider.complete("hello", debug=False, timeout=0.5)
    _assert_descendant_stopped_writing(marker, time.sleep)


def test_agy_provider_complete_structured_kills_process_group_on_timeout(
    tmp_path, monkeypatch
):
    # error/acceptance: before the fix, `AgyProvider.complete_structured`
    # (the brainstormer's default path) never routed through
    # `_run_cli_subprocess` at all -- it called plain `subprocess.run`
    # directly, with the same orphaned-descendant timeout gap. Drives the
    # failure through the real public entry point.
    import sys
    import time

    marker, parent_script = _write_descendant_marker_scripts(tmp_path)
    provider = AgyProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_structured_command",
        lambda prompt, schema_path: [sys.executable, str(parent_script)],
    )
    with pytest.raises(CLIProviderError, match="timeout"):
        provider.complete_structured("build a plan", schema=_Plan, timeout=0.5)
    _assert_descendant_stopped_writing(marker, time.sleep)


def test_base_cli_provider_complete_debug_false_still_terminates_on_timeout(
    monkeypatch,
):
    # acceptance: DEFAULT_CLI_TIMEOUT_SECONDS honored end-to-end for the
    # default (debug=False) call shape cli.py actually uses in production
    # (cli.py's complete_kwargs never sets debug=True).
    import sys

    provider = CodexProvider()
    monkeypatch.setattr(provider, "_binary_name", "python3")
    monkeypatch.setattr(
        provider,
        "build_command",
        lambda prompt, debug=False: [
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
        ],
    )
    with pytest.raises(CLIProviderError, match="timeout of 0.3s"):
        provider.complete("hello", timeout=0.3)


def test_run_cli_subprocess_timeout_handles_unterminated_output():
    import sys

    from ai_orchestration.providers.base import CLIProviderError
    from ai_orchestration.providers.cli import _run_cli_subprocess

    with pytest.raises(CLIProviderError, match="timeout"):
        _run_cli_subprocess(
            [
                sys.executable,
                "-c",
                "import sys, time; sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(5)",
            ],
            binary_name="python",
            timeout=0.2,
        )


def test_run_cli_subprocess_timeout_handles_closed_pipes_with_lingering_child():
    import sys
    import time

    from ai_orchestration.providers.base import CLIProviderError
    from ai_orchestration.providers.cli import _run_cli_subprocess

    start = time.monotonic()
    with pytest.raises(CLIProviderError, match="timeout"):
        _run_cli_subprocess(
            [
                sys.executable,
                "-c",
                "import os, time; os.close(1); os.close(2); time.sleep(5)",
            ],
            binary_name="python",
            timeout=0.2,
        )
    assert time.monotonic() - start < 0.75


def test_run_cli_subprocess_preserves_output_across_read_chunks():
    import sys

    from ai_orchestration.providers.cli import _run_cli_subprocess

    script = (
        "import os, time\n"
        "os.write(1, b'abc')\n"
        "time.sleep(0.05)\n"
        "os.write(1, b'def\\n')\n"
    )
    assert (
        _run_cli_subprocess(
            [sys.executable, "-c", script], binary_name="python", timeout=1
        )
        == "abcdef"
    )


def test_run_cli_subprocess_preserves_utf8_across_read_chunks():
    import sys

    from ai_orchestration.providers.cli import _run_cli_subprocess

    script = (
        "import os, time\n"
        "os.write(1, b'\\xed')\n"
        "time.sleep(0.1)\n"
        "os.write(1, b'\\x95\\x9c\\n')\n"
    )
    assert (
        _run_cli_subprocess(
            [sys.executable, "-c", script], binary_name="python", timeout=1
        )
        == "한"
    )
