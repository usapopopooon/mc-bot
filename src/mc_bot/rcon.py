from __future__ import annotations

import socket
import struct


class RconError(RuntimeError):
    pass


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._timeout = timeout

    def execute(self, command: str) -> str:
        with socket.create_connection((self._host, self._port), self._timeout) as connection:
            connection.settimeout(self._timeout)
            self._send(connection, 1, 3, self._password)
            request_id, _, _ = self._receive(connection)
            if request_id == -1:
                raise RconError("Minecraft RCON authentication failed")

            self._send(connection, 2, 2, command)
            response_id, _, payload = self._receive(connection)
            if response_id != 2:
                raise RconError("Minecraft RCON returned an unexpected response")
            return payload

    @staticmethod
    def _send(connection: socket.socket, request_id: int, packet_type: int, payload: str) -> None:
        body = struct.pack("<ii", request_id, packet_type) + payload.encode("utf-8") + b"\0\0"
        connection.sendall(struct.pack("<i", len(body)) + body)

    @staticmethod
    def _receive(connection: socket.socket) -> tuple[int, int, str]:
        length_data = _read_exact(connection, 4)
        (length,) = struct.unpack("<i", length_data)
        if length < 10 or length > 4_096_000:
            raise RconError("Minecraft RCON returned an invalid packet")
        body = _read_exact(connection, length)
        request_id, packet_type = struct.unpack("<ii", body[:8])
        payload = body[8:-2].decode("utf-8", errors="replace")
        return request_id, packet_type, payload


def _read_exact(connection: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = connection.recv(length - len(chunks))
        if not chunk:
            raise RconError("Minecraft RCON closed the connection")
        chunks.extend(chunk)
    return bytes(chunks)
