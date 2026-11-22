"""Обмен командой остановки между программой и локальным киоском."""

import socket
import threading
from pathlib import Path
from socketserver import BaseRequestHandler, UnixStreamServer


CONTROL_SOCKET = Path(__file__).resolve().parents[1] / "data" / ".kiosk-control" / "stop.sock"


class LocalStop:
    def __init__(self, stop_event: threading.Event):
        self.stop_event = stop_event
        self.server = None
        self.thread = None

    def start(self):
        owner = self

        class StopHandler(BaseRequestHandler):
            def handle(self):
                owner.stop_event.set()
                self.request.sendall(b"1")

        CONTROL_SOCKET.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        CONTROL_SOCKET.unlink(missing_ok=True)
        self.server = UnixStreamServer(str(CONTROL_SOCKET), StopHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            CONTROL_SOCKET.unlink(missing_ok=True)


def request_stop():
    """Попросить программу завершиться через сокет пользователя УМКИ."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(CONTROL_SOCKET))
        if client.recv(1) != b"1":
            raise OSError("УМКА не подтвердила остановку")
