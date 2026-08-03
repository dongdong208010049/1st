import io

import pytest

import app as app_module


@pytest.fixture
def client():
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as client:
        yield client


def test_index_page_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "HWP".encode() in resp.data


def test_convert_rejects_missing_file(client):
    resp = client.post("/convert", data={}, content_type="multipart/form-data")
    assert resp.status_code == 302


def test_convert_rejects_bad_extension(client):
    data = {"file": (io.BytesIO(b"hello"), "test.txt")}
    resp = client.post("/convert", data=data, content_type="multipart/form-data")
    assert resp.status_code == 302


def test_convert_success(client, monkeypatch):
    monkeypatch.setattr(app_module, "convert_to_pdf", lambda src, workdir: b"%PDF-1.4 fake")
    data = {"file": (io.BytesIO(b"fake hwp content"), "sample.hwp")}
    resp = client.post("/convert", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data == b"%PDF-1.4 fake"


def test_convert_failure_shows_flash(client, monkeypatch):
    def raise_error(src, workdir):
        raise app_module.ConversionError("LibreOffice가 설치되어 있지 않습니다.")

    monkeypatch.setattr(app_module, "convert_to_pdf", raise_error)
    data = {"file": (io.BytesIO(b"fake hwp content"), "sample.hwp")}
    resp = client.post("/convert", data=data, content_type="multipart/form-data")
    assert resp.status_code == 302
