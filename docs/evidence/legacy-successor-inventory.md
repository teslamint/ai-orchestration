# Legacy test successor inventory

Baseline: 93 tests collected from the four legacy suites at `8ee3c4c`
(`uv run pytest --collect-only -q`): `tests/test_orchestrator_cli.py` (17),
`tests/test_orchestration_context.py` (34), `tests/test_llm_tools.py` (22),
`tests/test_api_tools.py` (20).

Each row names exactly one successor test. U2 implements the utility (10) and
context-model (34) rows now; the CLI-option rows (7) are U5's, and the
llm_tools/api_tools rows (42) are U3's per the approved unit ownership.

## tests/test_orchestrator_cli.py (17)

| Legacy test | Successor |
|---|---|
| `test_extract_json_list_from_embedded_text` | `tests/test_utils.py::test_extract_json_list_from_embedded_text` |
| `test_extract_json_list_no_json_returns_empty_list` | `tests/test_utils.py::test_extract_json_list_no_json_returns_empty_list` |
| `test_extract_code_content_from_fenced_block` | `tests/test_utils.py::test_extract_code_content_from_fenced_block` |
| `test_extract_code_content_no_fence_returns_stripped_text` | `tests/test_utils.py::test_extract_code_content_no_fence_returns_stripped_text` |
| `test_generate_diff_new_file` | `tests/test_utils.py::test_generate_diff_new_file` |
| `test_generate_diff_modified_file` | `tests/test_utils.py::test_generate_diff_modified_file` |
| `test_generate_diff_no_changes` | `tests/test_utils.py::test_generate_diff_no_changes` |
| `test_generate_project_name_from_goal` | `tests/test_utils.py::test_generate_project_name_from_goal` |
| `test_generate_project_name_non_english` | `tests/test_utils.py::test_generate_project_name_non_english` |
| `test_generate_project_name_length_limit` | `tests/test_utils.py::test_generate_project_name_length_limit` |
| `test_main_has_auto_select_option` | `tests/test_cli.py::test_help_lists_every_preserved_option` (U5) |
| `test_cli_auto_select_option_help` | `tests/test_cli.py::test_help_lists_every_preserved_option` (U5) |
| `test_main_has_project_name_option` | `tests/test_cli.py::test_help_lists_every_preserved_option` (U5) |
| `test_cli_project_name_option_help` | `tests/test_cli.py::test_help_lists_every_preserved_option` (U5) |
| `test_main_has_tool_options` | `tests/test_cli.py::test_help_lists_every_preserved_option` (U5) |
| `test_cli_tool_options_help` | `tests/test_cli.py::test_help_lists_every_preserved_option` (U5) |
| `test_orchestrator_config_has_tool_config` | `tests/test_config.py::test_default_table_covers_all_six_stages` (U1, already passing) |

## tests/test_orchestration_context.py (34)

Every test ports 1:1 by name into `tests/test_models.py`, importing from
`ai_orchestration.models.context` instead of `orchestration_context`. No enum
value, field default, or method behavior changes.

| Legacy test | Successor |
|---|---|
| `test_review_item_type_values` | `tests/test_models.py::test_review_item_type_values` |
| `test_review_severity_values` | `tests/test_models.py::test_review_severity_values` |
| `test_code_review_item_creation` | `tests/test_models.py::test_code_review_item_creation` |
| `test_code_review_item_optional_fields` | `tests/test_models.py::test_code_review_item_optional_fields` |
| `test_code_review_result_creation` | `tests/test_models.py::test_code_review_result_creation` |
| `test_code_review_result_empty_items` | `tests/test_models.py::test_code_review_result_empty_items` |
| `test_orchestration_context_new_fields` | `tests/test_models.py::test_orchestration_context_new_fields` |
| `test_orchestration_context_set_new_fields` | `tests/test_models.py::test_orchestration_context_set_new_fields` |
| `test_task_creation` | `tests/test_models.py::test_task_creation` |
| `test_execution_log_creation` | `tests/test_models.py::test_execution_log_creation` |
| `test_review_decision_values` | `tests/test_models.py::test_review_decision_values` |
| `test_ralph_wiggum_feedback_creation` | `tests/test_models.py::test_ralph_wiggum_feedback_creation` |
| `test_ralph_wiggum_feedback_defaults` | `tests/test_models.py::test_ralph_wiggum_feedback_defaults` |
| `test_iteration_metadata_creation` | `tests/test_models.py::test_iteration_metadata_creation` |
| `test_iteration_metadata_defaults` | `tests/test_models.py::test_iteration_metadata_defaults` |
| `test_iteration_metadata_increment_attempt` | `tests/test_models.py::test_iteration_metadata_increment_attempt` |
| `test_iteration_metadata_add_note` | `tests/test_models.py::test_iteration_metadata_add_note` |
| `test_iteration_metadata_add_history_entry` | `tests/test_models.py::test_iteration_metadata_add_history_entry` |
| `test_orchestration_context_ralph_wiggum_defaults` | `tests/test_models.py::test_orchestration_context_ralph_wiggum_defaults` |
| `test_orchestration_context_ralph_wiggum_enabled` | `tests/test_models.py::test_orchestration_context_ralph_wiggum_enabled` |
| `test_submit_ralph_wiggum_feedback` | `tests/test_models.py::test_submit_ralph_wiggum_feedback` |
| `test_is_ralph_wiggum_accepted_by_decision` | `tests/test_models.py::test_is_ralph_wiggum_accepted_by_decision` |
| `test_is_ralph_wiggum_accepted_by_threshold` | `tests/test_models.py::test_is_ralph_wiggum_accepted_by_threshold` |
| `test_is_ralph_wiggum_accepted_false` | `tests/test_models.py::test_is_ralph_wiggum_accepted_false` |
| `test_is_ralph_wiggum_accepted_no_feedback` | `tests/test_models.py::test_is_ralph_wiggum_accepted_no_feedback` |
| `test_can_ralph_wiggum_retry` | `tests/test_models.py::test_can_ralph_wiggum_retry` |
| `test_prepare_ralph_wiggum_retry` | `tests/test_models.py::test_prepare_ralph_wiggum_retry` |
| `test_prepare_ralph_wiggum_retry_at_max` | `tests/test_models.py::test_prepare_ralph_wiggum_retry_at_max` |
| `test_check_promise_completion` | `tests/test_models.py::test_check_promise_completion` |
| `test_check_promise_completion_no_promise` | `tests/test_models.py::test_check_promise_completion_no_promise` |
| `test_save_iteration_snapshot` | `tests/test_models.py::test_save_iteration_snapshot` |
| `test_add_previous_output` | `tests/test_models.py::test_add_previous_output` |
| `test_get_self_reference_context` | `tests/test_models.py::test_get_self_reference_context` |
| `test_get_self_reference_context_empty` | `tests/test_models.py::test_get_self_reference_context_empty` |

## tests/test_llm_tools.py (22) — U3

`ToolType`/`LLMToolConfig`/`BaseLLMTool`/`LLMToolFactory` are superseded by
`ai_orchestration.config.StageConfig`/`resolve_stage_config` (U1, already
ported) and `ai_orchestration.providers.cli` (`AgyProvider`, `CodexProvider`,
`ClaudeProvider`) plus `providers.routing.resolve_provider_chain()` (U3). The
`GeminiTool` rows move to `AgyProvider` per the approved `ToolType.GEMINI` →
`ToolType.AGY` rename; the CLI subprocess prompt shape also changes for that
provider only (A12), so its successor test is not byte-identical to the
legacy assertion by design.

| Legacy test | Successor |
|---|---|
| `TestToolType::test_tool_type_values` | `tests/test_providers.py::test_tool_type_values` |
| `TestToolType::test_tool_type_from_string` | `tests/test_providers.py::test_tool_type_from_string` |
| `TestToolType::test_tool_type_invalid` | `tests/test_providers.py::test_tool_type_invalid` |
| `TestStageRole::test_stage_role_values` | `tests/test_config.py::test_default_table_covers_all_six_stages` (U1, already passing; `StageRole` superseded by `STAGE_NAMES`) |
| `TestLLMToolConfig::test_default_config` | `tests/test_config.py::test_resolve_stage_config_falls_back_to_built_in_default` (U1, already passing) |
| `TestLLMToolConfig::test_custom_config` | `tests/test_config.py::test_stage_config_from_full_object` (U1, already passing) |
| `TestLLMToolConfig::test_get_tool_for_stage` | `tests/test_config.py::test_resolve_stage_config_cli_flag_overrides_config_and_default` (U1, already passing) |
| `TestGeminiTool::test_build_command` | `tests/test_providers.py::test_agy_provider_build_command` |
| `TestGeminiTool::test_build_command_with_debug` | `tests/test_providers.py::test_agy_provider_build_command_debug` |
| `TestCodexTool::test_build_command` | `tests/test_providers.py::test_codex_provider_build_command` |
| `TestCodexTool::test_build_command_with_debug` | `tests/test_providers.py::test_codex_provider_build_command_debug` |
| `TestClaudeTool::test_build_command` | `tests/test_providers.py::test_claude_provider_build_command` |
| `TestClaudeTool::test_build_command_with_debug` | `tests/test_providers.py::test_claude_provider_build_command_debug` |
| `TestLLMToolFactory::test_create_gemini` | `tests/test_providers.py::test_create_agy_provider` |
| `TestLLMToolFactory::test_create_codex` | `tests/test_providers.py::test_create_codex_provider` |
| `TestLLMToolFactory::test_create_claude` | `tests/test_providers.py::test_create_claude_provider` |
| `TestLLMToolFactory::test_get_tool_for_stage` | `tests/test_routing.py::test_resolve_provider_chain_proxy_model_returns_http_provider` |
| `TestLoadToolConfig::test_load_default` | `tests/test_config.py::test_resolve_stage_config_falls_back_to_built_in_default` (U1, already passing) |
| `TestLoadToolConfig::test_load_with_cli_options` | `tests/test_config.py::test_resolve_stage_config_cli_flag_overrides_config_and_default` (U1, already passing) |
| `TestLoadToolConfig::test_load_from_file` | `tests/test_config.py::test_resolve_stage_config_file_overrides_built_in_default` (U1, already passing) |
| `TestLoadToolConfig::test_cli_options_override_file` | `tests/test_config.py::test_resolve_stage_config_cli_flag_overrides_config_and_default` (U1, already passing) |
| `TestValidateToolConfig::test_validate_returns_warnings_list` | `tests/test_providers.py::test_validate_tool_config_returns_warnings_list` |

## tests/test_api_tools.py (20) — U3

`APIResponse`/`OpenAITool`/`AnthropicTool`/`GoogleAITool` port unchanged into
`ai_orchestration.providers.legacy_api` per U3's Interfaces (compatibility
adapters, `ToolType.GEMINI_API`/`OPENAI_API`/`ANTHROPIC_API` unchanged).

| Legacy test | Successor |
|---|---|
| `TestAPIResponse::test_api_response_creation` | `tests/test_legacy_api.py::test_api_response_creation` |
| `TestOpenAITool::test_model_name` | `tests/test_legacy_api.py::TestOpenAITool::test_openai_tool_model_name` |
| `TestOpenAITool::test_is_available_with_env` | `tests/test_legacy_api.py::TestOpenAITool::test_openai_tool_is_available_with_env` |
| `TestOpenAITool::test_is_available_without_env` | `tests/test_legacy_api.py::TestOpenAITool::test_openai_tool_is_available_without_env` |
| `TestAnthropicTool::test_model_name` | `tests/test_legacy_api.py::TestAnthropicTool::test_anthropic_tool_model_name` |
| `TestAnthropicTool::test_is_available_with_env` | `tests/test_legacy_api.py::TestAnthropicTool::test_anthropic_tool_is_available_with_env` |
| `TestAnthropicTool::test_is_available_without_env` | `tests/test_legacy_api.py::TestAnthropicTool::test_anthropic_tool_is_available_without_env` |
| `TestGoogleAITool::test_model_name` | `tests/test_legacy_api.py::TestGoogleAITool::test_google_tool_model_name` |
| `TestGoogleAITool::test_is_available_with_env` | `tests/test_legacy_api.py::TestGoogleAITool::test_google_tool_is_available_with_env` |
| `TestGoogleAITool::test_is_available_without_env` | `tests/test_legacy_api.py::TestGoogleAITool::test_google_tool_is_available_without_env` |
| `TestLLMToolFactoryAPITools::test_is_api_tool_gemini_api` | `tests/test_legacy_api.py::TestLLMToolFactoryAPITools::test_is_api_tool_gemini_api` |
| `TestLLMToolFactoryAPITools::test_is_api_tool_openai_api` | `tests/test_legacy_api.py::TestLLMToolFactoryAPITools::test_is_api_tool_openai_api` |
| `TestLLMToolFactoryAPITools::test_is_api_tool_anthropic_api` | `tests/test_legacy_api.py::TestLLMToolFactoryAPITools::test_is_api_tool_anthropic_api` |
| `TestLLMToolFactoryAPITools::test_is_api_tool_cli_tools` | `tests/test_legacy_api.py::TestLLMToolFactoryAPITools::test_is_api_tool_cli_tools` |
| `TestLLMToolFactoryAPITools::test_create_api_tool_openai` | `tests/test_legacy_api.py::TestLLMToolFactoryAPITools::test_create_api_tool_openai` |
| `TestLLMToolFactoryAPITools::test_create_api_tool_anthropic` | `tests/test_legacy_api.py::TestLLMToolFactoryAPITools::test_create_api_tool_anthropic` |
| `TestLLMToolFactoryAPITools::test_create_api_tool_gemini` | `tests/test_legacy_api.py::TestLLMToolFactoryAPITools::test_create_api_tool_gemini` |
| `TestLLMToolFactoryAPITools::test_create_api_tool_invalid` | `tests/test_legacy_api.py::TestLLMToolFactoryAPITools::test_create_api_tool_invalid` |
| `TestToolTypeAPIValues::test_api_tool_type_values` | `tests/test_legacy_api.py::TestToolTypeAPIValues::test_api_tool_type_values` |
| `TestToolTypeAPIValues::test_api_tool_type_from_string` | `tests/test_legacy_api.py::TestToolTypeAPIValues::test_api_tool_type_from_string` |

## Totals

- 93 legacy test names, 93 successor rows, each named exactly once.
- U2 (this unit) implements 44: 10 in `tests/test_utils.py`, 34 in
  `tests/test_models.py`.
- U1 (already committed) incidentally already covers 9 successor slots via
  its own config contract tests: 1 from `tests/test_orchestrator_cli.py`
  (`test_orchestrator_config_has_tool_config`) and 8 from
  `tests/test_llm_tools.py` (`test_stage_role_values`, `test_default_config`,
  `test_custom_config`, `test_get_tool_for_stage`, `test_load_default`,
  `test_load_with_cli_options`, `test_load_from_file`,
  `test_cli_options_override_file`).
- U3 implements 34: 14 remaining rows from `tests/test_llm_tools.py` plus all
  20 rows from `tests/test_api_tools.py`, in `tests/test_providers.py`,
  `tests/test_routing.py`, and `tests/test_legacy_api.py`.
- U5 implements 6 in `tests/test_cli.py`.
- 44 + 9 + 34 + 6 = 93.
