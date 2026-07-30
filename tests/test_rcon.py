import struct

import pytest

from mc_bot.rcon import RconClient, RconError


class FakeSocket:
    def __init__(self, responses: bytes) -> None:
        self.responses = bytearray(responses)
        self.sent = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, length: int) -> bytes:
        result = bytes(self.responses[:length])
        del self.responses[:length]
        return result


def test_authenticates_and_executes_command(monkeypatch) -> None:
    connection = FakeSocket(_packet(1, 2, "") + _packet(2, 0, "Added Steve to the whitelist"))
    monkeypatch.setattr(
        "mc_bot.rcon.socket.create_connection",
        lambda *_args: connection,
    )

    response = RconClient("minecraft", 25575, "secret").execute("whitelist add Steve")

    assert response == "Added Steve to the whitelist"
    assert b"secret\0\0" in connection.sent
    assert b"whitelist add Steve\0\0" in connection.sent


def test_rejects_failed_authentication(monkeypatch) -> None:
    connection = FakeSocket(_packet(-1, 2, ""))
    monkeypatch.setattr(
        "mc_bot.rcon.socket.create_connection",
        lambda *_args: connection,
    )

    with pytest.raises(RconError, match="authentication failed"):
        RconClient("minecraft", 25575, "wrong").execute("list")


def _packet(request_id: int, packet_type: int, payload: str) -> bytes:
    body = struct.pack("<ii", request_id, packet_type) + payload.encode() + b"\0\0"
    return struct.pack("<i", len(body)) + body
