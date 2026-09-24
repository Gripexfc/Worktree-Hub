"""Native Windows launcher for Worktree Hub."""

import os
import secrets
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

local_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
os.environ.setdefault("WORKTREE_HARBOR_DATA_DIR", str(local_data / "Worktree Hub"))

import webview
from worktree_harbor import Harbor, make_handler, read_config


def main() -> None:
    harbor = Harbor(read_config())
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(harbor, token))
    thread = threading.Thread(target=server.serve_forever, name="worktree-hub-server", daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        webview.create_window("Worktree Hub", url=url, width=1320, height=840, min_size=(900, 620))
        webview.start(gui="edgechromium", debug=False, private_mode=True)
    finally:
        harbor.close()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
