import { useCallback, useEffect, useRef, useState } from "react";
import { synthesizeSpeech, transcribeSpeech } from "@/lib/api/rag.functions";

// Voice mode for the chat: record -> transcribe on the box -> the text goes
// through the normal ask pipeline; and read an answer back out loud.
//
// Everything stays on the appliance. The browser's own SpeechRecognition is
// deliberately NOT used: Chrome uploads the audio to Google, which is both a
// data leak and useless on a LAN-only box. Speech SYNTHESIS is different — the
// browser's voices are local — so it is kept as the fallback when the box has
// no TTS engine deployed.

const BCP47: Record<string, string> = {
  en: "en-US",
  hu: "hu-HU",
  da: "da-DK",
  de: "de-DE",
};

/** MediaRecorder support varies; pick the first container the browser admits. */
function pickMimeType(): string | undefined {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  if (typeof MediaRecorder === "undefined") return undefined;
  return candidates.find((t) => MediaRecorder.isTypeSupported(t));
}

export type MicState = "idle" | "recording" | "transcribing";

export type UseVoice = {
  /** The mic can physically be used here (permissions aside). */
  micSupported: boolean;
  /** Human-readable reason when it cannot — shown as the button tooltip. */
  micBlockedReason: string;
  micState: MicState;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  cancelRecording: () => void;
  speaking: boolean;
  speak: (text: string, lang?: string) => Promise<void>;
  stopSpeaking: () => void;
};

export function useVoice(opts: {
  /** Adapter capabilities from /api/v1/status (adapter/voice.py). */
  boxStt: boolean;
  boxTts: boolean;
  lang?: string;
  /** Called with the recognised text; empty results are not forwarded. */
  onTranscript: (text: string) => void;
  onError?: (message: string) => void;
}): UseVoice {
  const { boxStt, boxTts, lang, onTranscript, onError } = opts;

  const [micState, setMicState] = useState<MicState>("idle");
  const [speaking, setSpeaking] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const abortedRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);

  // getUserMedia only exists in a secure context: https, or http on localhost.
  // A box demo served over http://<lan-ip>:8080 therefore has NO microphone at
  // all — worth saying out loud rather than showing a button that never works.
  const secure = typeof window !== "undefined" ? window.isSecureContext : false;
  const hasMediaDevices =
    typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia);
  const micSupported = boxStt && hasMediaDevices && typeof MediaRecorder !== "undefined";

  const micBlockedReason = !boxStt
    ? "Speech recognition is not enabled on this AI Box."
    : !hasMediaDevices
      ? secure
        ? "This browser has no microphone API."
        : "The microphone needs a secure origin (https or localhost)."
      : typeof MediaRecorder === "undefined"
        ? "This browser cannot record audio."
        : "";

  const releaseStream = useCallback(() => {
    const rec = recorderRef.current;
    rec?.stream.getTracks().forEach((t) => t.stop());
    recorderRef.current = null;
  }, []);

  const startRecording = useCallback(async () => {
    if (!micSupported || micState !== "idle") return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      const mimeType = pickMimeType();
      const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];
      abortedRef.current = false;

      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        releaseStream();
        if (abortedRef.current) {
          setMicState("idle");
          return;
        }
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        chunksRef.current = [];
        if (blob.size < 1024) {
          setMicState("idle");
          onError?.("Nothing was recorded.");
          return;
        }
        setMicState("transcribing");
        void transcribeSpeech({ data: { audio: blob, lang } })
          .then((res) => {
            if (!res.ok) {
              onError?.(res.error ?? "Speech recognition failed.");
              return;
            }
            if (!res.text.trim()) {
              onError?.("Could not make out any speech.");
              return;
            }
            onTranscript(res.text.trim());
          })
          .finally(() => setMicState("idle"));
      };

      recorderRef.current = rec;
      rec.start();
      setMicState("recording");
    } catch (err) {
      setMicState("idle");
      onError?.(
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "Microphone permission was denied."
          : "Could not start recording.",
      );
    }
  }, [micSupported, micState, lang, onTranscript, onError, releaseStream]);

  const stopRecording = useCallback(() => {
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  }, []);

  const cancelRecording = useCallback(() => {
    abortedRef.current = true;
    stopRecording();
  }, [stopRecording]);

  const stopSpeaking = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
    if (typeof window !== "undefined" && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    setSpeaking(false);
  }, []);

  /** Browser voices are local, so this still works with no network at all. */
  const speakInBrowser = useCallback(
    (text: string, code?: string) => {
      if (typeof window === "undefined" || !window.speechSynthesis) {
        onError?.("No speech output is available.");
        setSpeaking(false);
        return;
      }
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = BCP47[(code ?? "en").toLowerCase()] ?? "en-US";
      utter.onend = () => setSpeaking(false);
      utter.onerror = () => setSpeaking(false);
      window.speechSynthesis.speak(utter);
    },
    [onError],
  );

  const speak = useCallback(
    async (text: string, code?: string) => {
      const body = text.trim();
      if (!body) return;
      stopSpeaking();
      setSpeaking(true);
      const spokenLang = code ?? lang;

      if (!boxTts) {
        speakInBrowser(body, spokenLang);
        return;
      }
      const res = await synthesizeSpeech({ data: { text: body, lang: spokenLang } });
      if (!res.ok || !res.audio) {
        // The box engine is the better voice, but never at the cost of silence.
        speakInBrowser(body, spokenLang);
        return;
      }
      const url = URL.createObjectURL(res.audio);
      objectUrlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => stopSpeaking();
      audio.onerror = () => {
        stopSpeaking();
        onError?.("Could not play the generated audio.");
      };
      try {
        await audio.play();
      } catch {
        stopSpeaking();
        onError?.("Audio playback was blocked by the browser.");
      }
    },
    [boxTts, lang, speakInBrowser, stopSpeaking, onError],
  );

  useEffect(
    () => () => {
      abortedRef.current = true;
      if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      releaseStream();
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
      if (typeof window !== "undefined" && window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
    },
    [releaseStream],
  );

  return {
    micSupported,
    micBlockedReason,
    micState,
    startRecording,
    stopRecording,
    cancelRecording,
    speaking,
    speak,
    stopSpeaking,
  };
}
