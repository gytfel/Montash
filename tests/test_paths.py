import os
import tempfile

import montash.paths as paths


def test_data_dir_next_to_program_or_profile(tmp_path, monkeypatch):
    program = tmp_path / "program"
    program.mkdir()
    monkeypatch.setattr(paths, "app_dir", lambda: program)
    paths.data_dir.cache_clear()
    try:
        assert paths.data_dir() == program / "data"
        assert paths.downloads_dir() == program / "downloads"

        # Рядом с программой писать нельзя (там файл, а не папка) — берём профиль.
        blocked = tmp_path / "blocked"
        blocked.write_text("x")
        monkeypatch.setattr(paths, "app_dir", lambda: blocked)
        monkeypatch.setattr(paths.Path, "home", lambda: tmp_path / "home")
        paths.data_dir.cache_clear()
        assert paths.data_dir() == tmp_path / "home" / ".montash"
        assert paths.downloads_dir() == tmp_path / "home" / ".montash" / "downloads"
    finally:
        paths.data_dir.cache_clear()


def test_setup_moves_temp_and_legacy_cache(tmp_path, monkeypatch):
    program = tmp_path / "program"
    program.mkdir()
    legacy = tmp_path / "old_cache"
    legacy.mkdir()
    (legacy / "film.npz").write_bytes(b"data")
    monkeypatch.setattr(paths, "app_dir", lambda: program)
    monkeypatch.setattr(paths, "_LEGACY_CACHE", legacy)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    for var in ("TMP", "TEMP", "TMPDIR", "HF_HOME"):
        monkeypatch.setenv(var, "unchanged")
    monkeypatch.delenv("HF_HOME")
    stale = program / "data" / "tmp" / "old"
    stale.mkdir(parents=True)
    os.utime(stale, (0, 0))
    paths.data_dir.cache_clear()
    try:
        paths.setup()
        assert tempfile.gettempdir() == str(program / "data" / "tmp")
        assert os.environ["TEMP"] == str(program / "data" / "tmp")
        assert os.environ["HF_HOME"] == str(program / "data" / "models")
        assert (program / "data" / "cache" / "film.npz").read_bytes() == b"data"
        assert not legacy.exists() and not stale.exists()
    finally:
        paths.data_dir.cache_clear()
