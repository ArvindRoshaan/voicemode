"""Tests for the opt-in manual-stop (ESC-to-stop-and-transcribe) feature.

When manual stop is enabled (manual_stop or manual_stop_with_silence_detection),
pressing ESC during an in-chat converse recording should transcribe the audio
captured so far (via a module-level shared holder that survives the
non-cancellable executor thread) instead of discarding it.

The recovery is SYNCHRONOUS (run on a dedicated thread), because the converse
cancel handler runs inside a coroutine asyncio is actively cancelling, where any
await re-raises CancelledError immediately.

The DEFAULT behavior (manual stop off) must be unchanged: ESC -> "Cancelled by
user." (the VM-1026/#337 guard lives in test_converse_cancellation.py).
"""

import asyncio
import sys
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Mock webrtcvad before importing voice_mode modules (matches other tests)
sys.modules.setdefault('webrtcvad', MagicMock())

import voice_mode.tools.converse as cv
from voice_mode.tools.converse import (
    _manual_stop_transcribe_sync,
    _run_manual_stop_transcribe,
    _start_keypress_watcher,
    _manual_stop_recording,
    _manual_stop_lock,
)


class TestKeypressWatcher:
    """CLI surface: /dev/tty Enter/Space watcher feeds the stop event."""

    def test_no_tty_returns_none(self):
        """If /dev/tty can't be opened, return None so caller falls back."""
        ev = threading.Event()
        with patch("voice_mode.tools.converse.os.open", side_effect=OSError("no tty")):
            cleanup = _start_keypress_watcher(ev)
        assert cleanup is None
        assert not ev.is_set()

    def test_not_a_real_tty_returns_none(self):
        """If the opened fd isn't a real tty (tcgetattr fails), fall back + close fd."""
        import termios
        with patch("voice_mode.tools.converse.os.open", return_value=4242), \
             patch("voice_mode.tools.converse.os.close") as mock_close, \
             patch("termios.tcgetattr", side_effect=termios.error("not a tty")):
            cleanup = _start_keypress_watcher(threading.Event())
        assert cleanup is None
        mock_close.assert_called_once_with(4242)

    @pytest.mark.skipif(not sys.platform.startswith(("linux", "darwin")),
                        reason="termios/tty keypress is POSIX-only")
    def test_enter_sets_stop_event(self, monkeypatch):
        """An Enter byte from the tty fd sets the stop event; other keys don't."""
        fake_fd = 99
        reads = [b"x", b"\r"]  # an ignored key, then Enter

        def fake_select(rlist, wlist, xlist, timeout):
            return ([fake_fd], [], []) if reads else ([], [], [])

        def fake_read(fd, n):
            return reads.pop(0) if reads else b""

        monkeypatch.setattr(cv.os, "open", lambda *a, **k: fake_fd)
        monkeypatch.setattr(cv.os, "close", lambda fd: None)
        monkeypatch.setattr(cv.os, "read", fake_read)
        monkeypatch.setattr("termios.tcgetattr", lambda fd: [])
        monkeypatch.setattr("termios.tcsetattr", lambda *a, **k: None)
        monkeypatch.setattr("tty.setcbreak", lambda fd: None)
        monkeypatch.setattr("select.select", fake_select)

        ev = threading.Event()
        cleanup = _start_keypress_watcher(ev)
        assert cleanup is not None
        try:
            assert ev.wait(timeout=2.0), "stop event not set after Enter"
        finally:
            cleanup()


def _reset_holder():
    with _manual_stop_lock:
        _manual_stop_recording["chunks"] = None
        _manual_stop_recording["stop_event"] = None
        _manual_stop_recording["active"] = False


@pytest.fixture(autouse=True)
def clean_holder():
    _reset_holder()
    yield
    _reset_holder()


@pytest.fixture(autouse=True)
def temp_transcript_file(tmp_path, monkeypatch):
    """Redirect the manual-stop transcript file to a temp path so tests never
    write to the user's real ~/.voicemode/last_esc_transcript.txt. The recovery
    function does `from voice_mode.config import MANUAL_STOP_TRANSCRIPT_FILE` at
    call time, so patching the config attribute is sufficient."""
    import voice_mode.config as config
    monkeypatch.setattr(config, "MANUAL_STOP_TRANSCRIPT_FILE",
                        tmp_path / "last_esc_transcript.txt")
    yield


def _populate(chunks):
    ev = threading.Event()
    with _manual_stop_lock:
        _manual_stop_recording["chunks"] = chunks
        _manual_stop_recording["stop_event"] = ev
        _manual_stop_recording["active"] = True
    return ev


class TestManualStopTranscribeSync:
    def test_no_active_recording_returns_none(self):
        """With no in-progress manual-stop recording, the helper returns None."""
        assert _manual_stop_transcribe_sync(transport="local") is None

    def test_empty_chunks_returns_none(self):
        """ESC pressed before any audio captured -> None (caller falls back)."""
        _populate([])  # nothing captured yet
        assert _manual_stop_transcribe_sync(transport="local") is None

    def test_captured_audio_is_transcribed(self):
        """Captured chunks are concatenated and transcribed; text is returned."""
        chunks = [np.zeros(1200, dtype=np.int16), np.ones(1200, dtype=np.int16)]
        ev = _populate(chunks)

        async def fake_stt(audio_data, *args, **kwargs):
            assert len(audio_data) == 2400  # both chunks concatenated
            return {"text": "hello world", "provider": "whisper"}

        with patch.object(cv, "speech_to_text", new=fake_stt):
            result = _manual_stop_transcribe_sync(transport="local")

        assert result == "hello world"
        assert ev.is_set()  # recording thread was signalled to stop
        # Holder cleared after consumption.
        with _manual_stop_lock:
            assert _manual_stop_recording["active"] is False
            assert _manual_stop_recording["chunks"] is None

    def test_stt_error_returns_none(self):
        """If STT returns an error dict, the helper returns None (graceful)."""
        _populate([np.ones(1200, dtype=np.int16)])

        async def fake_stt(audio_data, *args, **kwargs):
            return {"error": "stt failed"}

        with patch.object(cv, "speech_to_text", new=fake_stt):
            assert _manual_stop_transcribe_sync(transport="local") is None


class TestRunManualStopTranscribe:
    def test_wrapper_is_fire_and_forget(self):
        """The wrapper returns immediately (None) and writes the transcript via the
        background worker — it must not block on STT."""
        import time as _time
        _populate([np.ones(2400, dtype=np.int16)])

        done = threading.Event()

        async def fake_stt(audio_data, *args, **kwargs):
            done.set()
            return {"text": "threaded ok", "provider": "whisper"}

        with patch.object(cv, "speech_to_text", new=fake_stt):
            result = _run_manual_stop_transcribe(transport="local")
            # Fire-and-forget: returns immediately with no text.
            assert result is None
            # The background worker still runs and completes.
            assert done.wait(timeout=2.0), "background worker did not run"


class TestManualStopDefaultUnchanged:
    @pytest.mark.asyncio
    async def test_manual_stop_off_returns_cancelled_message(self):
        """With manual stop off (default), cancellation returns 'Cancelled by user.'."""
        from voice_mode.tools.converse import converse

        with patch("voice_mode.core.text_to_speech") as mock_tts:
            mock_tts.side_effect = asyncio.CancelledError()
            with patch("voice_mode.config.TTS_BASE_URLS", ["https://api.openai.com/v1"]):
                with patch("voice_mode.config.OPENAI_API_KEY", "test-api-key"):
                    result = await getattr(converse, 'fn', converse)(
                        message="Test message",
                        wait_for_response=False,
                        manual_stop=False,
                    )

        assert isinstance(result, str)
        assert "cancel" in result.lower()

    @pytest.mark.asyncio
    async def test_manual_stop_triggers_recovery_path(self):
        """manual_stop=true must route ESC cancellation through the recovery path."""
        from voice_mode.tools import converse as cv

        called = {"hit": False}

        def fake_recover(transport):
            called["hit"] = True
            return None  # nothing captured -> falls back to "Cancelled by user."

        with patch("voice_mode.core.text_to_speech") as mock_tts, \
             patch.object(cv, "_run_manual_stop_transcribe", new=fake_recover):
            mock_tts.side_effect = asyncio.CancelledError()
            with patch("voice_mode.config.TTS_BASE_URLS", ["https://api.openai.com/v1"]):
                with patch("voice_mode.config.OPENAI_API_KEY", "test-api-key"):
                    result = await getattr(cv.converse, 'fn', cv.converse)(
                        message="Test message",
                        wait_for_response=False,
                        manual_stop=True,
                    )

        assert called["hit"], "manual_stop=true should route through the recovery path"
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_manual_stop_with_silence_detection_triggers_recovery_path(self):
        """manual_stop_with_silence_detection=true must also enable ESC recovery."""
        from voice_mode.tools import converse as cv

        called = {"hit": False}

        def fake_recover(transport):
            called["hit"] = True
            return None

        with patch("voice_mode.core.text_to_speech") as mock_tts, \
             patch.object(cv, "_run_manual_stop_transcribe", new=fake_recover):
            mock_tts.side_effect = asyncio.CancelledError()
            with patch("voice_mode.config.TTS_BASE_URLS", ["https://api.openai.com/v1"]):
                with patch("voice_mode.config.OPENAI_API_KEY", "test-api-key"):
                    await getattr(cv.converse, 'fn', cv.converse)(
                        message="Test message",
                        wait_for_response=False,
                        manual_stop_with_silence_detection=True,
                    )

        assert called["hit"], "manual_stop_with_silence_detection=true should enable recovery"
