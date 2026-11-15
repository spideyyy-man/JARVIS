"""Stand-in for local model servers, used only by the end-to-end test (no real models needed).
Speaks the same protocols JARVIS expects from real ones:
  POST /v1/audio/transcriptions  (Whisper-style STT)      POST /v1/audio/speech  (TTS)
  POST /sdapi/v1/txt2img         (Automatic1111 image generation)
Run:  python frontend/e2e/stub_local_models.py 9911"""
import base64
import io
import json
import math
import struct
import sys
import wave
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = []


def wav(seconds=0.4, freq=440, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(int(seconds * rate))))
    return buf.getvalue()


def png(size=64):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    rows = b"".join(b"\x00" + bytes([(x * 4) % 256, 180, (y * 4) % 256]) * 1 for y in range(size) for x in [0]) if False else b"".join(
        b"\x00" + b"".join(bytes([x * 255 // size, 200 - y * 120 // size, 255 - x * 100 // size]) for x in range(size)) for y in range(size))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code); self.send_header("content-type", ctype); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        self._send(200, json.dumps(LOG).encode())

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        if self.path.endswith("/audio/transcriptions"):
            LOG.append({"path": self.path, "bytes": len(body), "multipart": b"filename=" in body})
            self._send(200, json.dumps({"text": "Jarvis, how is my computer performing?"}).encode())
        elif self.path.endswith("/audio/speech"):
            LOG.append({"path": self.path, "input": json.loads(body).get("input", "")[:80]})
            self._send(200, wav(), "audio/wav")
        elif self.path.endswith("/sdapi/v1/txt2img"):
            LOG.append({"path": self.path, "prompt": json.loads(body).get("prompt")})
            self._send(200, json.dumps({"images": [base64.b64encode(png()).decode()]}).encode())
        else:
            self._send(404, b"{}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--mic-wav":  # write the fake-microphone input: a tone, then silence
        open(sys.argv[2], "wb").write(wav(1.2, 300, 48000)[:44 + 2 * 57600] + b"\x00" * (2 * 48000 * 3))
        data = bytearray(open(sys.argv[2], "rb").read())
        struct.pack_into("<I", data, 4, len(data) - 8); struct.pack_into("<I", data, 40, len(data) - 44)
        open(sys.argv[2], "wb").write(data)
    else:
        ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 9911), Handler).serve_forever()
