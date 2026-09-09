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


# ── Скрипт обязан быть ПОДКЛЮЧЁН к pre-commit ─────────────────────────
#
# Всё выше запускает `scripts/check_no_secrets.py` напрямую — 13 кейсов,
# fail-closed проверен. Но вырезание хука из `.pre-commit-config.yaml`
# (замена `entry` на `/bin/true`) проходило зелёным на всём наборе, то есть
# полностью протестированный скрипт мог никогда не вызываться.
#
# Это не абстрактная опасность: ветка существует потому, что в исходном
# репозитории был закоммичен архив с `.env`, двумя файлами сессии Telethon,
# логами и резюме прошлого автора. Скрипт — единственный механизм, который
# не даёт этому повториться.

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
HOOK_ID = "check-no-secrets"


def _hooks() -> list[dict]:
    """Хуки из `.pre-commit-config.yaml`.

    YAML разбирается настоящим парсером, если он есть (PyYAML приходит с
    самим `pre-commit`, объявленным в dev-зависимостях), — иначе разбор
    вырождается в поиск строки `entry:`. Пропускать проверку нельзя: она
    закрывает подключение защиты, а не удобство.
    """
    text = PRE_COMMIT_CONFIG.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:                                   # pragma: no cover
        return [
            {"id": HOOK_ID if HOOK_ID in text else "?",
             "entry": line.split("entry:", 1)[1].strip()}
            for line in text.splitlines() if "entry:" in line
        ]
    config = yaml.safe_load(text)
    return [hook for repo in config.get("repos", []) for hook in repo.get("hooks", [])]


def test_the_pre_commit_config_exists():
    assert PRE_COMMIT_CONFIG.exists(), (
        ".pre-commit-config.yaml нет: скрипт защиты от коммита секретов работает, "
        "но его никто не вызывает"
    )


def test_the_secrets_hook_is_wired_to_the_script_that_is_tested_here():
    hooks = _hooks()
    assert hooks, f"в {PRE_COMMIT_CONFIG.name} не нашлось ни одного хука"

    ours = [hook for hook in hooks if hook.get("id") == HOOK_ID]
    assert len(ours) == 1, (
        f"хук {HOOK_ID!r} в {PRE_COMMIT_CONFIG.name} не найден (найдены: "
        f"{[hook.get('id') for hook in hooks]}) — защита от коммита секретов отключена"
    )
    entry = str(ours[0].get("entry", ""))
    assert SCRIPT.name in entry, (
        f"хук {HOOK_ID!r} вызывает {entry!r}, а не {SCRIPT.name} — все тесты выше "
        "проверяют скрипт, который больше не запускается"
    )
    # `entry` — путь от корня репозитория, и он должен указывать на файл,
    # который в репозитории и правда есть: опечатка тут даёт хук, падающий
    # на каждом коммите, или (для `pre-commit` с `language: script`) хук,
    # который тихо ничего не делает.
    target = (REPO_ROOT / entry.split()[0]).resolve()
    assert target == SCRIPT.resolve(), f"entry указывает мимо скрипта: {target}"
    assert target.exists()


def test_the_hook_script_is_executable():
    """`language: script` в pre-commit означает «запусти файл как есть».
    Без бита исполнения хук падает с `Permission denied` — и пользователь,
    у которого он не заработал, скорее его отключит, чем починит."""
    assert SCRIPT.stat().st_mode & 0o111, (
        f"{SCRIPT.name} не исполняем: хук с language: script не запустится"
    )
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!"), (
        "у скрипта нет shebang — под `language: script` его нечем запустить"
    )


def test_the_hook_runs_on_commit():
    ours = [hook for hook in _hooks() if hook.get("id") == HOOK_ID][0]
    stages = ours.get("stages")
    # `stages` может отсутствовать — по умолчанию pre-commit гоняет хук на
    # всех стадиях, включая нужную. А вот перечисленные стадии БЕЗ
    # pre-commit означают, что на коммите он не запускается вовсе.
    if stages is not None:
        assert "pre-commit" in stages or "commit" in stages, (
            f"хук объявлен только на стадиях {stages} — на коммите он не сработает"
        )
