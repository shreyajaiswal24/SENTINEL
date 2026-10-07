"""Voice layer (Phase 8): ElevenLabs cloud TTS + STT, with text/off fallbacks."""
from voice.hitl_voice import request_approval
from voice.speech import listen, speak

__all__ = ["speak", "listen", "request_approval"]
