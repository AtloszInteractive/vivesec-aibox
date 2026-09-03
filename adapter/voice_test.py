"""Unit tests for adapter/voice.py (no backend, no network).

Run: python adapter/voice_test.py
"""
import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice  # noqa: E402

PASS = [0]
FAIL = [0]


def check(name, cond):
    if cond:
        PASS[0] += 1
        print("  ok   %s" % name)
    else:
        FAIL[0] += 1
        print("  FAIL %s" % name)


class _Resp(io.BytesIO):
    def __init__(self, data, headers=None):
        io.BytesIO.__init__(self, data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(captured, data, headers=None):
    def opener(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["headers"] = dict(req.headers)
        return _Resp(data, headers)
    return opener


def test_lang():
    print("language normalisation")
    check("code", voice.normalize_lang("hu") == "hu")
    check("name", voice.normalize_lang("Hungarian") == "hu")
    check("locale", voice.normalize_lang("de-DE") == "de")
    check("unknown", voice.normalize_lang("klingon") is None)
    check("empty", voice.normalize_lang("") is None)


def test_speakable():
    print("speakable text")
    out = voice.speakable_text("**Revenue** was 14.7 M EUR [#1] and the plan [#12] was 15.1.")
    check("citations dropped", "[#" not in out)
    check("markdown dropped", "**" not in out)
    check("content kept", "14.7 M EUR" in out and "15.1" in out)
    check("bullets", voice.speakable_text("- first\n- second").startswith("first"))


def test_truncate():
    print("truncation")
    text = ("Sentence one. " * 40).strip()
    cut, truncated = voice._truncate_for_speech(text, 100)
    check("truncated flag", truncated is True)
    check("bounded", len(cut) <= 100)
    check("sentence boundary", cut.endswith("."))
    short, flag = voice._truncate_for_speech("Short.", 100)
    check("short untouched", short == "Short." and flag is False)


def test_unconfigured():
    print("unconfigured backends refuse cleanly")
    voice.STT_URL, voice.TTS_URL = "", ""
    check("stt unavailable", voice.stt_available() is False)
    check("tts unavailable", voice.tts_available() is False)
    check("status", voice.status()["stt"] is False and voice.status()["tts"] is False)
    try:
        voice.transcribe(b"x")
        check("stt raises", False)
    except voice.VoiceError:
        check("stt raises", True)
    try:
        voice.synthesize("hello")
        check("tts raises", False)
    except voice.VoiceError:
        check("tts raises", True)


def test_transcribe():
    print("transcribe")
    captured = {}
    original = urllib.request.urlopen
    voice.STT_URL = "http://127.0.0.1:8095/v1/audio/transcriptions"
    voice.STT_MODEL = "whisper-small"
    urllib.request.urlopen = _fake_urlopen(
        captured, json.dumps({"text": " Mennyi volt a bevetel? "}).encode("utf-8"))
    try:
        text = voice.transcribe(b"OPUSDATA", content_type="audio/webm;codecs=opus", lang="Hungarian")
    finally:
        urllib.request.urlopen = original
    check("text trimmed", text == "Mennyi volt a bevetel?")
    check("multipart", "multipart/form-data; boundary=" in captured["headers"].get("Content-type", ""))
    body = captured["body"]
    check("language sent", b'name="language"' in body and b"hu" in body)
    check("model sent", b"whisper-small" in body)
    check("audio in body", b"OPUSDATA" in body)
    check("filename", b'filename="speech.webm"' in body)


def test_transcribe_segments():
    print("transcribe (segments-only response)")
    original = urllib.request.urlopen
    voice.STT_URL = "http://127.0.0.1:8095/v1/audio/transcriptions"
    urllib.request.urlopen = _fake_urlopen(
        {}, json.dumps({"segments": [{"text": "one "}, {"text": "two"}]}).encode("utf-8"))
    try:
        text = voice.transcribe(b"AUDIO")
    finally:
        urllib.request.urlopen = original
    check("segments joined", text == "one  two".strip())


def test_synthesize():
    print("synthesize")
    captured = {}
    original = urllib.request.urlopen
    voice.TTS_URL = "http://127.0.0.1:8096/"
    voice.TTS_VOICES = "hu=hu_HU-anna-medium,en=en_US-lessac-medium"
    urllib.request.urlopen = _fake_urlopen(
        captured, b"RIFFWAVE", {"Content-Type": "audio/wav"})
    try:
        audio, mime, truncated = voice.synthesize("A bevetel **14.7** M EUR volt [#1].", lang="hu")
    finally:
        urllib.request.urlopen = original
    check("audio returned", audio == b"RIFFWAVE")
    check("mime", mime == "audio/wav")
    check("not truncated", truncated is False)
    check("voice selected", "voice=hu_HU-anna-medium" in captured["url"])
    check("markers stripped", b"[#1]" not in captured["body"])


def test_synthesize_errors():
    print("synthesize error paths")
    original = urllib.request.urlopen
    voice.TTS_URL = "http://127.0.0.1:8096/"
    urllib.request.urlopen = _fake_urlopen({}, b"", {"Content-Type": "audio/wav"})
    try:
        voice.synthesize("hello")
        check("empty audio raises", False)
    except voice.VoiceError:
        check("empty audio raises", True)
    finally:
        urllib.request.urlopen = original
    try:
        voice.synthesize("   [#1]  ")
        check("nothing to speak raises", False)
    except voice.VoiceError:
        check("nothing to speak raises", True)


def main():
    for fn in (test_lang, test_speakable, test_truncate, test_unconfigured,
               test_transcribe, test_transcribe_segments, test_synthesize,
               test_synthesize_errors):
        fn()
    print("\n%d passed / %d failed" % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
