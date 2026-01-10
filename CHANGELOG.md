# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- SECURITY.md policy
- CHANGELOG.md

## [0.1.0-preview.3] - 2025-01-10

### Added
- GitHub Actions CI workflow
- Issue templates (bug report, feature request)
- Pull request template
- CONTRIBUTING.md guide
- MIT LICENSE

### Fixed
- Planner copying example file names literally instead of generating goal-specific plans
- Template placeholders and duplicates in approach selection
- ANSI escape codes breaking CLI help tests in CI

## [0.1.0-preview.2] - 2025-01-10

### Added
- `--auto-select` option for non-interactive approach selection
- `--project-name` option for custom project naming
- Project-based workspace organization (`workspace/<project_name>/`)

### Fixed
- `workspace/` prefix duplication in file paths

### Changed
- Python version requirement updated to 3.9+

## [0.1.0-preview.1] - 2025-01-10

### Added
- 6-stage AI orchestration workflow (Gemini → Codex → Claude)
- `--auto-run`, `--auto-approve`, `--auto-fix` options for full automation
- `--skip-review` option to skip code review stage
- `--debug` and `--debug-log` options for debugging
- Pydantic models for orchestration context
- Agent prompts for each stage
