"""
_loopback.py — a raw TCP server on 127.0.0.1, for tests of bounded reads.

A real server, not a mock: what hermes bounds is time spent in real socket
calls, and only a real socket can drip one byte at a time. Each test hands in
a `script(conn, stop)` that plays one misbehaving server on the accepted
connection. The server runs on daemon threads and stops in close(); a
script should check `stop` between bytes so a test that ends early doesn't
leave it dripping.

Not a test module (no test_ prefix), so discover never runs it.
"""

import socket
import threading
import time
from collections.abc import Callable

Script = Callable[[socket.socket, threading.Event], None]


class Loopback:
    """One listening socket on 127.0.0.1:<free port>, one thread per
    connection. Use as a context manager."""

    def __init__(self, script: Script) -> None:
        self._script = script
        self.stop = threading.Event()
        self.requests: list[bytes] = []         # each request's head, as read
        self._srv = socket.create_server(("127.0.0.1", 0))
        self._srv.settimeout(0.1)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def url(self, path: str = "/", scheme: str = "http") -> str:
        return f"{scheme}://127.0.0.1:{self.port}{path}"

    def _serve(self) -> None:
        while not self.stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:                     # the accept timeout, or close()
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(10)
        with conn:
            try:
                self._script(conn, self.stop)
            except OSError:                     # the client hung up: expected
                pass

    def close(self) -> None:
        self.stop.set()
        self._srv.close()
        self._thread.join(2)

    def __enter__(self) -> "Loopback":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_head(conn: socket.socket, sink: "list[bytes] | None" = None) -> bytes:
    """Read one request's head (up to the blank line)."""
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    if sink is not None:
        sink.append(data)
    return data


def drip(conn: socket.socket, stop: threading.Event, data: bytes,
         interval: float) -> None:
    """Send `data` one byte at a time, `interval` seconds apart."""
    for i in range(len(data)):
        if stop.is_set():
            return
        conn.sendall(data[i:i + 1])
        time.sleep(interval)
