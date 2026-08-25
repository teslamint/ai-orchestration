"""Package metadata and console-script declaration (U1).

U1 only creates config.py, errors.py, and __init__.py; cli.py is U5's unit.
These tests assert the pyproject.toml contract that later units rely on,
not runtime CLI behavior.
"""

from pathlib import Path

import tomllib

import ai_orchestration

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_package_exposes_version():
    assert isinstance(ai_orchestration.__version__, str)
    assert ai_orchestration.__version__


def test_pyproject_declares_python_310_floor():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["requires-python"] == ">=3.10"


def test_pyproject_declares_console_script():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    scripts = data["project"]["scripts"]
    assert scripts["ai-orchestration"] == "ai_orchestration.cli:app"


def test_pyproject_declares_openai_dependency():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    deps = data["project"]["dependencies"]
    assert any(dep.startswith("openai") for dep in deps)


def test_ruff_target_version_is_py310():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert data["tool"]["ruff"]["target-version"] == "py310"


def test_package_discovery_declares_a_build_backend():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    build_backend = data.get("build-system", {}).get("build-backend", "")
    assert build_backend


def test_package_discovery_finds_src_ai_orchestration():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    tool_setuptools = data.get("tool", {}).get("setuptools", {})
    packages_find = tool_setuptools.get("packages", {}).get("find", {})
    assert packages_find.get("where") == ["src"]
