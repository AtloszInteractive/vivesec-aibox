"""Minimal multi-voice Piper HTTP server (stdlib only, no flask).

Piper's own http_server needs flask and serves a single voice; the AI Box UI
answers in four languages, so the voice has to be selectable per request. This
speaks exactly the contract adapter/voice.py expects:

    POST /            body = the utterance (text/plain)   -> audio/wav
    GET  /?text=...                                       -> audio/wav
    ?voice=<name>     picks a loaded voice, else the default
    GET  /health                                          -> {"ok":true,...}

Voices are loaded lazily and cached: the first request for a language pays the
model load, the rest do not.
"""
import io
import json
import os
import sys
import threading
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from piper import PiperVoice

VOICE_DIR = os.environ.get("PIPER_VOICE_DIR", "/voices")
DEFAULT_VOICE = os.environ.get("PIPER_DEFAULT_VOICE", "en_US-lessac-medium")
HOST = os.environ.get("PIPER_HOST", "0.0.0.0")
PORT = int(os.environ.get("PIPER_PORT", "5000"))
MAX_CHARS = int(os.environ.get("PIPER_MAX_CHARS", "4000"))

_voices = {}
_lock = threading.Lock()


def available():
    return sorted(f[:-5] for f in os.listdir(VOICE_DIR) if f.endswith(".onnx"))


def load(name):
    with _lock:
        if name in _voices:
            return _voices[name]
        path = os.path.join(VOICE_DIR, name + ".onnx")
        if not os.path.isfile(path):
            raise FileNotFoundError(name)
        sys.stderr.write("[piper] loading %s\n" % name)
        _voices[name] = PiperVoice.load(path)
        return _voices[name]


def synth(voice, text):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        if hasattr(voice, "synthesize_wav"):
            voice.synthesize_wav(text, wf)
        else:  # older API: a stream of raw chunks, header written from the first
            configured = False
            for chunk in voice.synthesize(text):
                if not configured:
                    wf.setnchannels(getattr(chunk, "sample_channels", 1))
                    wf.setsampwidth(getattr(chunk, "sample_width", 2))
                    wf.setframerate(getattr(chunk, "sample_rate", 22050))
                    configured = True
                wf.writeframes(chunk.audio_int16_bytes)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "ViVeSecPiper/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[piper] %s\n" % (fmt % args))

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _speak(self, text, voice_name):
        text = (text or "").strip()[:MAX_CHARS]
        if not text:
            self._json(400, {"ok": False, "error": "no text"})
            return
        try:
            voice = load(voice_name or DEFAULT_VOICE)
        except FileNotFoundError:
            try:
                voice = load(DEFAULT_VOICE)
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
                return
        try:
            audio = synth(voice, text)
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(e)})
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        if path.rstrip("/") in ("", "/health"):
            if path.rstrip("/") == "/health" or not params.get("text"):
                self._json(200, {"ok": True, "voices": available(),
                                 "default": DEFAULT_VOICE,
                                 "loaded": sorted(_voices)})
                return
        self._speak((params.get("text") or [""])[0], (params.get("voice") or [""])[0])

    def do_POST(self):
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = (self.headers.get("Content-Type") or "").lower()
        text, voice_name = "", (params.get("voice") or [""])[0]
        if "application/json" in ctype:
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
                text = payload.get("text") or ""
                voice_name = payload.get("voice") or voice_name
            except Exception:  # noqa: BLE001
                self._json(400, {"ok": False, "error": "invalid json"})
                return
        else:
            text = raw.decode("utf-8", "replace")
        self._speak(text, voice_name)


def main():
    voices = available()
    sys.stderr.write("[piper] voices: %s (default %s)\n" % (", ".join(voices), DEFAULT_VOICE))
    if not voices:
        sys.stderr.write("[piper] FATAL: no .onnx voice in %s\n" % VOICE_DIR)
        return 1
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
