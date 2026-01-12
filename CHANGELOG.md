# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Devcontainer with security sandbox (network firewall)
  - Python 3.13 + uv + Claude CLI + zsh pre-installed
  - iptables-based firewall allowing only required domains
  - API keys passed via `remoteEnv` from host environment
- API direct call support (no CLI dependencies required)
  - `gemini_api` - Google AI API (Gemini 2.0 Flash)
  - `openai_api` - OpenAI API (GPT-4o)
  - `anthropic_api` - Anthropic API (Claude Sonnet 4)
  - Streaming output with Rich Live display
- Configurable LLM provider support
  - `--brainstormer`, `--reviewer`, `--planner`, `--executor`, `--code-reviewer`, `--fixer` CLI options
  - `--tool-config` option for JSON configuration file
- Ralph Wiggum feedback loop for automated iterative review/fix cycles
  - `--enable-ralph-wiggum` to activate the feedback loop
  - `--ralph-wiggum-threshold` for acceptance threshold (0.0-1.0)
  - `--ralph-wiggum-max-iterations` for maximum review attempts
  - `--completion-promise` for task completion signal
  - Self-reference context for tracking iteration history
- Optional API dependencies in `pyproject.toml` (`uv sync --extra api`)
- Ruff linter and formatter configuration
- Pre-commit hooks for automated code quality checks
- Lint and format check steps in CI workflow

### Changed
- Stage functions now support both CLI-based and API-based tools
- LLMToolFactory extended with `is_api_tool()` and `create_api_tool()` methods

## [0.3.0-preview] - 2025-01-10

### Added
- GitHub Actions CI workflow
- Issue templates (bug report, feature request)
- Pull request template
- CONTRIBUTING.md guide
- MIT LICENSE
- SECURITY.md policy
- CHANGELOG.md

### Fixed
- Planner copying example file names literally instead of generating goal-specific plans
- Template placeholders and duplicates in approach selection
- ANSI escape codes breaking CLI help tests in CI

## [0.2.0-preview] - 2025-01-10

### Added
- `--auto-select` option for non-interactive approach selection
- `--project-name` option for custom project naming
- Project-based workspace organization (`workspace/<project_name>/`)

### Fixed
- `workspace/` prefix duplication in file paths

### Changed
- Python version requirement updated to 3.9+

## [0.1.0-preview] - 2025-01-10

### Added
- 6-stage AI orchestration workflow (Gemini → Codex → Claude)
- `--auto-run`, `--auto-approve`, `--auto-fix` options for full automation
- `--skip-review` option to skip code review stage
- `--debug` and `--debug-log` options for debugging
- Pydantic models for orchestration context
- Agent prompts for each stage
