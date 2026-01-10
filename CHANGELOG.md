# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- 6-stage AI orchestration workflow (Gemini → Codex → Claude)
- `--auto-select` option for non-interactive approach selection
- `--project-name` option for custom project naming
- `--auto-run`, `--auto-approve`, `--auto-fix` options for full automation
- `--skip-review` option to skip code review stage
- `--debug` and `--debug-log` options for debugging
- Project-based workspace organization (`workspace/<project_name>/`)
- GitHub Actions CI workflow
- Issue templates (bug report, feature request)
- Pull request template
- CONTRIBUTING.md guide

### Fixed
- Planner copying example file names literally instead of generating goal-specific plans
- Template placeholders and duplicates in approach selection
- `workspace/` prefix duplication in file paths
- ANSI escape codes breaking CLI help tests in CI

### Changed
- Python version requirement updated to 3.9+
