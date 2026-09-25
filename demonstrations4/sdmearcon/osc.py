"""Minimal OSC over UDP, standard library only.

python-osc would do this too, but it is one more thing to install on a machine
where the only hard requirement is already numpy and scipy. OSC 1.0 messages are
about eighty lines, so they are written out here instead.

Only what this project sends and receives is implemented: int32, float32,
float64 and string arguments. Bundles are not needed, since sclang sends plain
messages.
"""

from __future__ import annotations

import socket
import struct
import threading


def _pad(b: bytes) -> bytes:
    return b + b"\0" * ((4 - len(b) % 4) % 4)


def _ostr(s: str) -> bytes:
    return _pad(s.encode("utf-8") + b"\0")


def encode(address: str, *args) -> bytes:
    tags, body = ",", b""
    for a in args:
        if isinstance(a, bool):
            a = int(a)
        if isinstance(a, int):
            tags += "i"
            body += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            body += struct.pack(">f", a)
        elif isinstance(a, str):
            tags += "s"
            body += _ostr(a)
        else:
            raise TypeError(f"cannot encode {type(a).__name__} as an OSC argument")
    return _ostr(address) + _ostr(tags) + body


def _read_str(buf: bytes, i: int):
    end = buf.index(b"\0", i)
    s = buf[i:end].decode("utf-8", "replace")
    return s, i + (((end - i) // 4) + 1) * 4


def decode(buf: bytes):
    """Return (address, [args]). Unknown type tags end the parse rather than raise."""
    address, i = _read_str(buf, 0)
    if i >= len(buf):
        return address, []
    tags, i = _read_str(buf, i)
    args = []
    for t in tags[1:]:
        if t == "i":
            args.append(struct.unpack(">i", buf[i:i + 4])[0]); i += 4
        elif t == "f":
            args.append(struct.unpack(">f", buf[i:i + 4])[0]); i += 4
        elif t == "d":
            args.append(struct.unpack(">d", buf[i:i + 8])[0]); i += 8
        elif t == "s":
            s, i = _read_str(buf, i); args.append(s)
        elif t in "TF":
            args.append(t == "T")
        else:
            break
    return address, args


class OscClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 57120):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, address: str, *args) -> None:
        self.sock.sendto(encode(address, *args), self.addr)

    def close(self) -> None:
        self.sock.close()


class OscServer(threading.Thread):
    """Receives OSC on a UDP port and dispatches by address to callbacks."""

    daemon = True

    def __init__(self, port: int = 57121, host: str = "127.0.0.1"):
        super().__init__(name="osc-server")
        self.handlers: dict[str, callable] = {}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.settimeout(0.5)
        self._stop = threading.Event()

    def on(self, address: str, fn) -> None:
        self.handlers[address] = fn

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                address, args = decode(data)
            except Exception:
                continue
            fn = self.handlers.get(address)
            if fn is not None:
                try:
                    fn(*args)
                except Exception as exc:            # never let a handler kill the loop
                    print(f"[osc] handler for {address} raised: {exc}")

    def stop(self) -> None:
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass
