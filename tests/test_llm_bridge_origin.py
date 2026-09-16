"""Regression tests for the LLM bridge's local-only request guard.

The bridge performs no authentication, so refusing foreign ``Origin`` and
``Host`` headers is what stops an unrelated web page (or DNS rebinding)
from queueing viewer commands such as clear-scene.
"""

import importlib.util
import json
import pathlib
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

BRIDGE_PATH = pathlib.Path(__file__).resolve().parents[1] / "docker" / "llm-bridge" / "bridge_server.py"


@pytest.fixture(scope="module")
def bridge_url():
    spec = importlib.util.spec_from_file_location("bridge_server", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.BridgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _request(url, method="GET", headers=None):
    request = urllib.request.Request(url, method=method, headers=headers or {})
    if method == "POST":
        request.add_header("Content-Length", "0")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


@pytest.mark.parametrize("path", ["/api/scenarios/clear", "/api/simulations/run"])
def test_foreign_origin_cannot_queue_commands(bridge_url, path):
    status, payload = _request(bridge_url + path, "POST", {"Origin": "https://untrusted.example"})
    assert status == 403
    assert payload["ok"] is False
    # Nothing was queued.
    _, latest = _request(bridge_url + "/api/scenarios/clear/latest")
    assert latest["clear_scene"] is None


def test_foreign_host_is_rejected(bridge_url):
    status, _ = _request(bridge_url + "/api/health", headers={"Host": "attacker.example"})
    assert status == 403


@pytest.mark.parametrize("origin", ["http://localhost:8081", "http://127.0.0.1:3000"])
def test_local_origin_is_accepted(bridge_url, origin):
    status, payload = _request(bridge_url + "/api/health", headers={"Origin": origin})
    assert status == 200
    assert payload["ok"] is True
