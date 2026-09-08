import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_no_secrets.py"


def run(*files: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, files)],
        capture_output=True, text=True,
    )


def test_rejects_session_file(tmp_path):
    victim = tmp_path / "telegram.session"
    victim.write_bytes(b"SQLite format 3\x00")
    result = run(victim)
    assert result.returncode == 1
    assert "telegram.session" in result.stdout


def test_rejects_env_file(tmp_path):
    victim = tmp_path / ".env"
    victim.write_text("TG_API_HASH=deadbeef\n")
    assert run(victim).returncode == 1


def test_rejects_rar_by_magic_bytes_despite_harmless_name(tmp_path):
    victim = tmp_path / "backup.bin"
    victim.write_bytes(b"Rar!\x1a\x07\x01\x00rest")
    result = run(victim)
    assert result.returncode == 1
    assert "архив" in result.stdout


def test_rejects_zip_by_magic_bytes(tmp_path):
    victim = tmp_path / "files.dat"
    victim.write_bytes(b"PK\x03\x04rest")
    assert run(victim).returncode == 1


def test_allows_source_file(tmp_path):
    ok = tmp_path / "monitor.py"
    ok.write_text("print('hello')\n")
    assert run(ok).returncode == 0
