"""Central configuration. Loads .env once and exposes typed settings.

Keeps secrets out of the code and gives every phase one place to read the Groq
model + key. Import `settings` anywhere; it's a module-level singleton.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root if present (no error if it's missing).
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    # Diagnose LLM provider preference. OpenAI is tried first; Groq is the
    # fallback if OpenAI is unavailable or errors at call time. Override the
    # order via LLM_PROVIDER ("openai" | "groq") to force a single provider.
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai").lower()

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_TEMPERATURE: float = float(os.getenv("OPENAI_TEMPERATURE", "0.1"))

    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    # A current, widely-available Groq model. Override via .env if you prefer another.
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    GROQ_TEMPERATURE: float = float(os.getenv("GROQ_TEMPERATURE", "0.1"))

    # Voice HITL mode (Phase 6/8):
    #   "off"   -> no I/O; HITL falls back to the headless default (CI / graph tests)
    #   "text"  -> prompts printed, decision read from stdin (testable anywhere)
    #   "voice" -> ElevenLabs TTS (speech out) + STT/Scribe (speech in)
    VOICE_MODE: str = os.getenv("VOICE_MODE", "off").lower()
    # ElevenLabs (cloud) voice. Key provided via .env, like Groq.
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
    # Default voice = "Sarah" (a stock ElevenLabs voice present on most accounts).
    # Override in .env with any voice_id from your account (client.voices.get_all()).
    ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
    ELEVENLABS_TTS_MODEL: str = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_turbo_v2_5")
    ELEVENLABS_STT_MODEL: str = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v1")

    @property
    def openai_ready(self) -> bool:
        return bool(self.OPENAI_API_KEY)

    @property
    def groq_ready(self) -> bool:
        return bool(self.GROQ_API_KEY)

    @property
    def llm_ready(self) -> bool:
        return self.openai_ready or self.groq_ready

    @property
    def elevenlabs_ready(self) -> bool:
        return bool(self.ELEVENLABS_API_KEY)


settings = Settings()
