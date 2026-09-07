import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated_repo(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[2] / "scripts" / "dev.py"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(source, scripts / "dev.py")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, timeout=10)
    return tmp_path


@pytest.mark.parametrize(
    "name,content",
    [
        (".env", "NOT_A_SECRET=example"),
        (".env.production", "NOT_A_SECRET=example"),
        ("credential.pem", "synthetic fixture"),
        ("settings.txt", "sk-" + "x" * 24),
        ("settings.txt", "CUSTOM_API_KEY=" + "synthetic-test-value"),
    ],
)
def test_secret_guard_rejects_staged_credentials(
    isolated_repo: Path, name: str, content: str
) -> None:
    (isolated_repo / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "--", name], cwd=isolated_repo, check=True, timeout=10)
    # A clean working tree copy must not conceal a secret already in the index.
    (isolated_repo / name).write_text("clean working copy", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(isolated_repo / "scripts/dev.py"), "secrets"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert name in result.stderr
    assert content not in result.stdout + result.stderr


def test_secret_guard_allows_reviewed_example(isolated_repo: Path) -> None:
    source = Path(__file__).resolve().parents[2] / ".env.example"
    shutil.copyfile(source, isolated_repo / ".env.example")
    subprocess.run(["git", "add", ".env.example"], cwd=isolated_repo, check=True, timeout=10)
    result = subprocess.run(
        [sys.executable, str(isolated_repo / "scripts/dev.py"), "secrets"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
