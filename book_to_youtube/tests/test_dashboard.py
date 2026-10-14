import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg requis")


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Tableau de bord sur un dossier de travail temporaire (AIP_DATA_DIR)."""
    monkeypatch.setenv("AIP_DATA_DIR", str(tmp_path))
    for name in ("input", "episodes", "audio", "images", "output"):
        (tmp_path / name).mkdir()
    # Recharge la configuration avec le nouveau dossier de travail.
    for module in [m for m in sys.modules if m in ("config", "dashboard")]:
        del sys.modules[module]
    import dashboard
    monkeypatch.setattr(dashboard, "LOG_DIR", tmp_path / "logs")
    dashboard.Handler.token = "secret"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", tmp_path, dashboard
    dashboard.RUNNER.cancel()
    httpd.shutdown()


def call(base, path, body=None, token="secret", raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(base + path, data=data, method="POST" if data else "GET",
                                     headers={"X-Token": token})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def wait_job(base, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, state = call(base, "/api/state")
        if state["job"] and state["job"]["status"] != "en_cours":
            return state
        time.sleep(0.5)
    pytest.fail("tâche trop longue")


def test_token_is_required(server):
    base, _, _ = server
    assert call(base, "/api/state", token="mauvais")[0] == 401
    with urllib.request.urlopen(base + "/") as page:  # la page elle-même est publique
        assert b"AI-pythoune" in page.read()


def test_import_build_review_and_media(server):
    base, data, _ = server
    body = " ".join(["Une phrase de test assez banale."] * 30)
    book = f"Chapitre 1\n\n{body}\n\nChapitre 2\n\n{body}\n".encode()
    assert call(base, "/api/books?name=mon%20livre.txt", raw=book) == (200, {"name": "mon_livre.txt",
                                                                         "size": len(book)})
    assert call(base, "/api/books?name=virus.exe", raw=b"x")[0] == 400

    status, job = call(base, "/api/build", {"book": "mon_livre.txt", "options": {
        "tts": "silent", "style": "slides", "resolution": "640x360", "max_episodes": "1"}})
    assert status == 200 and job["kind"] == "build"
    assert call(base, "/api/build", {"book": "mon_livre.txt", "options": {}})[0] == 409  # occupée
    state = wait_job(base)
    assert state["job"]["status"] == "termine", call(base, f"/api/log?id={job['id']}")[1]["text"]

    (series,) = state["series"]
    assert series["total"] == 2 and series["source"] == "mon_livre.txt"
    assert [e["status"] for e in series["episodes"]] == ["a_valider"]
    assert series["build_options"] == ["--tts", "silent", "--style", "slides",
                                       "--resolution", "640x360"]

    # Vidéo servie avec prise en charge des plages (lecture dans le navigateur).
    video_url = series["episodes"][0]["video_url"]
    request = urllib.request.Request(f"{base}{video_url}?token=secret",
                                     headers={"Range": "bytes=0-99"})
    with urllib.request.urlopen(request) as response:
        assert response.status == 206 and len(response.read()) == 100

    slug = series["slug"]
    assert call(base, "/api/edit", {"slug": slug, "episode": 1, "youtube_title": "Nouveau titre"})[0] == 200
    assert call(base, "/api/review", {"slug": slug, "episodes": [1], "action": "approve"}) == (
        200, {"changed": [1]})
    _, state = call(base, "/api/state")
    ep = state["series"][0]["episodes"][0]
    assert (ep["status"], ep["youtube_title"]) == ("valide", "Nouveau titre")

    # Génération de l'épisode 2 avec les réglages enregistrés.
    assert call(base, "/api/regenerate", {"slug": slug, "episode": 2})[0] == 200
    state = wait_job(base)
    assert [e["status"] for e in state["series"][0]["episodes"]] == ["valide", "a_valider"]


def test_paths_cannot_escape_data_folders(server):
    base, _, _ = server
    assert call(base, "/api/build", {"book": "../../config.py", "options": {}})[0] == 400
    assert call(base, "/api/review", {"slug": "../x", "episodes": [1], "action": "approve"})[0] == 400
    assert call(base, "/media/..%2F..%2Fconfig.py/x.mp4?token=secret")[0] == 404
