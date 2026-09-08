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


def test_rejects_bzip2_by_magic_bytes(tmp_path):
    victim = tmp_path / "backup.bin"
    victim.write_bytes(b"BZh91AY&SY" + b"\x00" * 20)
    result = run(victim)
    assert result.returncode == 1
    assert "архив" in result.stdout


def test_rejects_xz_by_magic_bytes(tmp_path):
    victim = tmp_path / "backup.bin"
    victim.write_bytes(b"\xfd7zXZ\x00" + b"\x00" * 20)
    result = run(victim)
    assert result.returncode == 1


def test_rejects_zstd_by_magic_bytes(tmp_path):
    victim = tmp_path / "backup.bin"
    victim.write_bytes(b"\x28\xb5\x2f\xfd" + b"\x00" * 20)
    result = run(victim)
    assert result.returncode == 1


def test_rejects_uncompressed_tar_with_ustar_magic_at_offset_257(tmp_path):
    """Uncompressed tar has no leading magic bytes — the ustar signature sits
    at offset 257. Uses a harmless extension (.bin) so the filename check
    cannot be the thing pinning this — only the content check can."""
    victim = tmp_path / "backup.bin"
    header = bytearray(512)
    header[257:263] = b"ustar\x00"
    victim.write_bytes(bytes(header))
    result = run(victim)
    assert result.returncode == 1
    assert "архив" in result.stdout


def test_allows_empty_file(tmp_path):
    victim = tmp_path / "empty.bin"
    victim.write_bytes(b"")
    assert run(victim).returncode == 0


def test_allows_file_shorter_than_longest_signature(tmp_path):
    victim = tmp_path / "short.bin"
    victim.write_bytes(b"Ra")  # shorter than every archive magic we check
    assert run(victim).returncode == 0


def test_fails_closed_on_directory_argument(tmp_path):
    """A directory can't be opened for reading like a file (OSError). The
    hook must report it as a problem rather than silently treating it as
    not-an-archive."""
    victim = tmp_path / "somedir"
    victim.mkdir()
    result = run(victim)
    assert result.returncode == 1
    assert "somedir" in result.stdout
