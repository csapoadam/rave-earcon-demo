"""The tuning panel. Serves a local page and relays it to SuperCollider.

    python3 -m sdmearcon.app

Standard library plus numpy and scipy. No web framework and no websockets: the
page polls a JSON endpoint a few times a second, which is ample for sliders and
avoids two more dependencies on a machine where the only thing that must work is
the audio.
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .presets import PresetError
from .session import Session

HERE = Path(__file__).resolve().parent
UI = HERE / "ui.html"
PORT = 8731

SESSION: Session | None = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):            # keep the console for our own output
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/state":
            self._send(200, json.dumps(SESSION.snapshot()))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            self._send(400, json.dumps({"error": "bad json"}))
            return
        action = payload.get("action", "set")

        if action == "set":
            # Slider movement. Updates the family shown, sends nothing to audio.
            SESSION.apply(payload)
        elif action == "commit":
            # Slider released. THIS is what bakes the traverse into the family
            # and re-measures, because a new family cannot be auditioned until it
            # has been measured.
            SESSION.apply(payload)
            SESSION.commit()
        elif action == "reset":
            SESSION.reset()
        elif action == "move":
            # Reorder only. No sound changes, so nothing is re-measured.
            SESSION.move_gradation(int(payload.get("index", 0)),
                                   int(payload.get("delta", 0)))
        elif action == "setz":
            # Latents typed in directly for one earcon.
            try:
                SESSION.set_row(int(payload.get("index", 0)),
                                payload.get("values", []))
            except (ValueError, TypeError) as exc:
                SESSION.last_error = str(exc)
        elif action == "master":
            SESSION.set_master(float(payload.get("value", 0.0)))
        elif action == "play":
            SESSION.play(int(payload.get("index", 0)))
        elif action == "sweep":
            SESSION.sweep()
        elif action == "panic":
            SESSION.panic()
        elif action == "ping":
            SESSION.ping()
        elif action == "export":
            try:
                SESSION.export_preset(payload.get("name", ""))
            except PresetError as exc:
                SESSION.last_error = str(exc)
        elif action == "import":
            try:
                SESSION.import_preset(payload.get("name", ""))
            except PresetError as exc:
                SESSION.last_error = str(exc)
        else:
            self._send(400, json.dumps({"error": f"unknown action {action}"}))
            return
        self._send(200, json.dumps(SESSION.snapshot()))


def main() -> int:
    global SESSION
    SESSION = Session()
    SESSION.ping()
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print()
    print("  SDM earcon tuning panel")
    print(f"  {url}")
    print("  OSC out 57120 (sclang), in 57121")
    print("  SuperCollider must already be running with sdm.scd BLOCK 1 done.")
    print("  Ctrl+C to stop.")
    print()
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopping")
    finally:
        SESSION.panic()
        SESSION.close()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
