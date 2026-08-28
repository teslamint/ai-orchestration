"""Transport-adjacent authorization gate tests."""

import hashlib
import json
import socket
import threading
from pathlib import Path

import pytest

from ai_orchestration.cli import _authorization_gate_from_environment
from ai_orchestration.errors import GateError
from ai_orchestration.providers.call_control import (
    AuthorizationGate,
    NoOpAuthorizationGate,
    OutboundPayload,
    SocketAuthorizationGate,
)
from ai_orchestration.providers.cli import CodexProvider
from ai_orchestration.providers.http import HttpProvider


def _socket_path(tmp_path: Path) -> Path:
    name = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    return Path("/tmp") / f"call-control-{name}.sock"


def _authorization_server(socket_path: Path, reply):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)
    observed = {}

    def serve():
        try:
            connection, _ = server.accept()
            with connection:
                request = b""
                while not request.endswith(b"\n"):
                    request += connection.recv(4096)
                packet = json.loads(request)
                observed.update(packet)
                response = reply(packet)
                if response is not None:
                    connection.sendall(json.dumps(response).encode() + b"\n")
        finally:
            server.close()
            socket_path.unlink(missing_ok=True)

    thread = threading.Thread(target=serve)
    thread.start()
    return observed, thread


def _approval(packet, *, authorized=True):
    return {
        "version": "authorization-response/v1",
        "request_id": packet["request_id"],
        "payload_sha256": packet["payload_sha256"],
        "authorized": authorized,
    }


def test_outbound_payload_preserves_exact_http_json_bytes():
    encoded = b'{"model":"test","messages":[{"content":"secret"}]}'

    payload = OutboundPayload.for_http_json(encoded)

    assert payload.transport == "http_json"
    assert payload.render() == encoded
    assert payload.sha256 == hashlib.sha256(encoded).hexdigest()


def test_outbound_payload_represents_exact_cli_argv():
    payload = OutboundPayload.for_cli_argv(["codex", "exec", "hello world"])

    assert payload.transport == "cli_argv"
    assert payload.argv == ("codex", "exec", "hello world")
    assert json.loads(payload.render()) == ["codex", "exec", "hello world"]


def test_no_op_gate_satisfies_protocol_and_persists_nothing(tmp_path):
    gate = NoOpAuthorizationGate()
    payload = OutboundPayload.for_http_json({"prompt": "content"})

    assert isinstance(gate, AuthorizationGate)
    gate.authorize(payload)

    assert list(tmp_path.iterdir()) == []


def test_socket_gate_sends_only_content_free_packet_and_continues(tmp_path):
    socket_path = _socket_path(tmp_path)
    payload_path = tmp_path / "payload.json"
    payload = OutboundPayload.for_http_json(b'{"prompt":"exact secret bytes"}')
    observed, thread = _authorization_server(socket_path, _approval)

    SocketAuthorizationGate(
        socket_path=socket_path,
        payload_path=payload_path,
        timeout=1.0,
    ).authorize(payload)
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert set(observed) == {
        "version",
        "request_id",
        "transport",
        "payload_path",
        "payload_sha256",
    }
    assert "exact secret bytes" not in json.dumps(observed)
    assert observed["payload_sha256"] == payload.sha256
    assert not payload_path.exists()


def test_socket_gate_payload_file_is_private_and_exact_while_waiting(tmp_path):
    socket_path = _socket_path(tmp_path)
    payload_path = tmp_path / "payload.json"
    payload = OutboundPayload.for_cli_argv(["claude", "--print", "secret prompt"])
    captured = {}

    def approve(packet):
        path = Path(packet["payload_path"])
        captured["mode"] = path.stat().st_mode & 0o777
        captured["bytes"] = path.read_bytes()
        return _approval(packet)

    _, thread = _authorization_server(socket_path, approve)
    SocketAuthorizationGate(socket_path, payload_path, timeout=1.0).authorize(payload)
    thread.join(timeout=1)

    assert captured == {"mode": 0o600, "bytes": payload.render()}
    assert not payload_path.exists()


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda response: response.update(payload_sha256="0" * 64), "digest"),
        (lambda response: response.update(request_id="stale"), "current request"),
        (lambda response: response.update(authorized=False), "rejected"),
    ],
)
def test_socket_gate_rejects_before_transport_and_removes_payload(
    tmp_path, mutate, message
):
    socket_path = _socket_path(tmp_path)
    payload_path = tmp_path / "payload.json"
    transport_called = False

    def reply(packet):
        response = _approval(packet)
        mutate(response)
        return response

    _, thread = _authorization_server(socket_path, reply)
    gate = SocketAuthorizationGate(socket_path, payload_path, timeout=1.0)

    with pytest.raises(GateError, match=message):
        gate.authorize(OutboundPayload.for_http_json({"prompt": "secret"}))
        transport_called = True
    thread.join(timeout=1)

    assert transport_called is False
    assert not payload_path.exists()


def test_socket_gate_times_out_before_transport_and_removes_payload(tmp_path):
    socket_path = _socket_path(tmp_path)
    payload_path = tmp_path / "payload.json"
    release_server = threading.Event()

    def no_reply(_packet):
        release_server.wait(timeout=1)
        return None

    _, thread = _authorization_server(socket_path, no_reply)
    gate = SocketAuthorizationGate(socket_path, payload_path, timeout=0.05)

    with pytest.raises(GateError, match="timed out"):
        gate.authorize(OutboundPayload.for_cli_argv(["agy", "-p", "secret"]))

    release_server.set()
    thread.join(timeout=1)
    assert not payload_path.exists()


def test_socket_gate_rejects_existing_payload_path_without_overwrite(tmp_path):
    socket_path = _socket_path(tmp_path)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text("existing", encoding="utf-8")

    gate = SocketAuthorizationGate(socket_path, payload_path, timeout=0.1)

    with pytest.raises(GateError, match="already exists"):
        gate.authorize(OutboundPayload.for_http_json({"prompt": "secret"}))
    assert payload_path.read_text(encoding="utf-8") == "existing"


def test_socket_gate_cancellation_removes_payload_and_preserves_interrupt(
    tmp_path, monkeypatch
):
    gate = SocketAuthorizationGate(
        socket_path=tmp_path / "unused.sock",
        payload_path=tmp_path / "payload.json",
        timeout=1.0,
    )

    def interrupt(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(SocketAuthorizationGate, "_request_authorization", interrupt)
    with pytest.raises(KeyboardInterrupt):
        gate.authorize(OutboundPayload.for_http_json({"prompt": "secret"}))
    assert not gate.payload_path.exists()


def test_cli_gate_rejects_before_subprocess(monkeypatch):
    subprocess_called = False

    class RejectGate:
        def authorize(self, payload):
            assert payload.transport == "cli_argv"
            raise GateError("denied")

    def fake_subprocess(*_args, **_kwargs):
        nonlocal subprocess_called
        subprocess_called = True
        return "unexpected"

    monkeypatch.setattr(
        "ai_orchestration.providers.cli._run_cli_subprocess", fake_subprocess
    )
    provider = CodexProvider(authorization_gate=RejectGate())

    with pytest.raises(GateError, match="denied"):
        provider.complete("secret")
    assert subprocess_called is False


def test_http_gate_rejects_before_sdk_request():
    sdk_called = False

    class RejectGate:
        def authorize(self, payload):
            assert payload.transport == "http_json"
            assert json.loads(payload.render())["model"] == "gpt-test"
            raise GateError("denied")

    provider = HttpProvider(
        model="gpt-test",
        base_url="http://127.0.0.1:1/v1",
        api_key="test",
        authorization_gate=RejectGate(),
    )

    class Completions:
        def create(self, **_kwargs):
            nonlocal sdk_called
            sdk_called = True

    provider._client.chat.completions = Completions()
    with pytest.raises(GateError, match="denied"):
        provider.complete("secret")
    assert sdk_called is False


def test_cli_environment_builds_eval_socket_gate(tmp_path):
    gate = _authorization_gate_from_environment(
        {
            "AI_ORCHESTRATION_AUTH_SOCKET": str(tmp_path / "gate.sock"),
            "AI_ORCHESTRATION_AUTH_PAYLOAD": str(tmp_path / "payload.json"),
            "AI_ORCHESTRATION_AUTH_TIMEOUT": "5",
        }
    )

    assert isinstance(gate, SocketAuthorizationGate)
    assert gate.timeout == 5
