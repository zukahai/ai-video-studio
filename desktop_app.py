from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
import webview


BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _is_server_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/auth/status", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _pick_port(preferred: int = 7860) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> None:
    preferred_port = 7860
    owned_server: uvicorn.Server | None = None
    server_thread: threading.Thread | None = None

    if _is_server_alive(preferred_port):
        port = preferred_port
    else:
        port = _pick_port(preferred_port)
        config = uvicorn.Config(
            "server:app",
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
        owned_server = uvicorn.Server(config)
        server_thread = threading.Thread(target=owned_server.run, daemon=True)
        server_thread.start()

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if _is_server_alive(port):
                break
            time.sleep(0.2)

    webview.create_window(
        title="HaiAI Studio - Video, Image & AI Director",
        url=f"http://127.0.0.1:{port}/",
        width=1440,
        height=900,
        min_size=(1024, 700),
        background_color="#1e1e1e",
        text_select=True,
    )
    try:
        webview.start(private_mode=False)
    finally:
        if owned_server is not None:
            owned_server.should_exit = True
        if server_thread is not None and server_thread.is_alive():
            server_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
