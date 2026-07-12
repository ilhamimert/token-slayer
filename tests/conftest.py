"""Shared fixtures for all tests."""
from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """
    Minimal fake Python project with known structure:

        main.py           — imports app.config and app.utils
        app/__init__.py
        app/config.py     — defines Settings class
        app/utils.py      — defines helper() + unused_function()
        app/models.py     — defines User class, imports app.config
    """
    (tmp_path / "app").mkdir()

    (tmp_path / "main.py").write_text(
        "import os\n"
        "from app.config import settings\n"
        "from app.utils import helper\n"
        "\n"
        "def run():\n"
        "    helper()\n"
        "\n"
        "def startup():\n"
        "    pass\n",
        encoding="utf-8",
    )

    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")

    (tmp_path / "app" / "config.py").write_text(
        "import os\n"
        "\n"
        "class Settings:\n"
        "    debug: bool = False\n"
        "    db_url: str = 'sqlite:///test.db'\n"
        "\n"
        "settings = Settings()\n",
        encoding="utf-8",
    )

    (tmp_path / "app" / "utils.py").write_text(
        "def helper():\n"
        "    return 42\n"
        "\n"
        "def unused_function():\n"
        "    return None\n",
        encoding="utf-8",
    )

    (tmp_path / "app" / "models.py").write_text(
        "from app.config import settings\n"
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass\n"
        "class User:\n"
        "    id: int\n"
        "    name: str\n"
        "\n"
        "    def greet(self) -> str:\n"
        "        return f'Hello {self.name}'\n",
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture
def git_project(sample_project: Path) -> Path:
    """sample_project with a real git history (2 commits)."""
    try:
        from git import Repo
    except ImportError:
        pytest.skip("gitpython not available")

    repo = Repo.init(sample_project)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "t@test.com").release()

    repo.index.add([str(p.relative_to(sample_project)) for p in sample_project.rglob("*.py")])
    repo.index.commit("initial commit")

    # Second commit — touch utils.py and config.py
    (sample_project / "app" / "utils.py").write_text(
        "def helper():\n    return 99\n\ndef unused_function():\n    return None\n",
        encoding="utf-8",
    )
    (sample_project / "app" / "config.py").write_text(
        "import os\n\nclass Settings:\n    debug: bool = True\n\nsettings = Settings()\n",
        encoding="utf-8",
    )
    repo.index.add(["app/utils.py", "app/config.py"])
    repo.index.commit("fix: update config and utils")

    return sample_project


@pytest.fixture
def dirty_git_project(git_project: Path) -> Path:
    """git_project with an additional UNCOMMITTED edit to app/utils.py."""
    (git_project / "app" / "utils.py").write_text(
        "def helper():\n    return 99\n\ndef unused_function():\n    return None\n\n"
        "def extra():\n    return 1\n",
        encoding="utf-8",
    )
    return git_project
