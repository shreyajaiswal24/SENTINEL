"""Voice I/O for SENTINEL (Phase 8) — ElevenLabs cloud TTS + STT.

Three modes (utils.config.settings.VOICE_MODE):
  * "off"   -> no audio I/O at all. speak() is a no-op log; listen() returns "".
               HITL uses its headless default. Used for CI / graph tests.
  * "text"  -> speak() prints; listen() reads a line from stdin. Testable anywhere.
  * "voice" -> ElevenLabs TTS for speech out, ElevenLabs STT (Scribe) for speech in.

The ElevenLabs SDK and audio playback/recording are imported lazily so the rest
of SENTINEL runs without them installed. If "voice" is requested but the key or
SDK is missing, we degrade to "text" rather than crash.
"""
from __future__ import annotations

from utils.config import settings


def _eleven_client():
    """Lazily construct an ElevenLabs client; None if unavailable."""
    if not settings.elevenlabs_ready:
        return None
    try:
        from elevenlabs.client import ElevenLabs
    except ImportError:
        return None
    return ElevenLabs(api_key=settings.ELEVENLABS_API_KEY)


def _effective_mode() -> str:
    """Resolve the configured mode against what's actually available."""
    mode = settings.VOICE_MODE
    if mode == "voice" and _eleven_client() is None:
        # asked for voice but no key/SDK -> fall back to text so HITL still works
        return "text"
    return mode if mode in {"off", "text", "voice"} else "off"


def speak(text: str) -> None:
    """Speak `text` (queen -> human)."""
    mode = _effective_mode()
    if mode == "off":
        return
    if mode == "text":
        print(f"  [queen] {text}")
        return
    # mode == "voice"
    client = _eleven_client()
    audio = client.text_to_speech.convert(
        voice_id=settings.ELEVENLABS_VOICE_ID,
        model_id=settings.ELEVENLABS_TTS_MODEL,
        text=text,
    )
    try:
        from elevenlabs import play
        play(audio)
    except Exception:
        # no audio device (e.g. headless) — surface the line instead of failing
        print(f"  [queen|audio-unavailable] {text}")


def listen(prompt: str = "") -> str:
    """Capture a spoken/typed reply (human -> queen). Returns the transcript."""
    mode = _effective_mode()
    if mode == "off":
        return ""
    if mode == "text":
        return input(f"  [you] {prompt}").strip()
    # mode == "voice": record from mic, transcribe via ElevenLabs Scribe
    client = _eleven_client()
    audio_bytes = _record_microphone()
    if audio_bytes is None:
        return input(f"  [you|mic-unavailable] {prompt}").strip()
    result = client.speech_to_text.convert(
        model_id=settings.ELEVENLABS_STT_MODEL,
        file=audio_bytes,
    )
    return (getattr(result, "text", "") or "").strip()


def _record_microphone(seconds: float = 4.0):
    """Record a short clip from the default mic as WAV bytes; None if no device.
    Kept isolated so the import only happens in true voice mode."""
    try:
        import io
        import wave

        import sounddevice as sd  # type: ignore
    except ImportError:
        return None
    try:
        sample_rate = 16000
        frames = sd.rec(int(seconds * sample_rate), samplerate=sample_rate,
                        channels=1, dtype="int16")
        sd.wait()
    except Exception:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames.tobytes())
    buf.seek(0)
    return buf
