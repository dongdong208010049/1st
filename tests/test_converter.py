import converter


def test_find_soffice_prefers_env_override(tmp_path, monkeypatch):
    fake_soffice = tmp_path / "soffice.exe"
    fake_soffice.write_text("not a real binary")
    monkeypatch.setenv("SOFFICE_PATH", str(fake_soffice))

    assert converter._find_soffice() == str(fake_soffice)


def test_find_soffice_ignores_missing_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SOFFICE_PATH", str(tmp_path / "does-not-exist.exe"))
    monkeypatch.setattr(converter.shutil, "which", lambda name: "/usr/bin/soffice")

    assert converter._find_soffice() == "/usr/bin/soffice"
