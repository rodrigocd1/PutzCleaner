from __future__ import annotations

from pathlib import Path

from putz.transcription_cache import CacheKey, TranscriptionCache
from transcriber import TranscriptionResult, WordToken


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        words=(
            WordToken("né", "né", 1.0, 1.2, 0.8, 0, -0.1, 0.1),
            WordToken("hum", "hum", 2.0, 2.1, 0.7, 0, -0.1, 0.1),
        ),
        audio_duration=10.0,
        language="pt",
        language_probability=0.98,
        model_requested="small",
        model_resolved="small",
        device_requested="auto",
        device_used="cpu",
        compute_type="int8",
    )


def test_cache_key_changes_when_model_changes(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"123")

    key_a = CacheKey.build(
        input_path=video,
        model_requested="small",
        timeline_duration=10.0,
    )
    key_b = CacheKey.build(
        input_path=video,
        model_requested="medium",
        timeline_duration=10.0,
    )

    assert key_a != key_b


def test_transcription_cache_roundtrip(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"123")
    cache = TranscriptionCache(tmp_path / "cache")
    key = CacheKey.build(
        input_path=video,
        model_requested="small",
        timeline_duration=10.0,
    )

    cache.save(key, _result())
    loaded = cache.load(key)

    assert loaded == _result()
