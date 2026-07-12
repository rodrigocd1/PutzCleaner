"""Cache local de transcricoes para iteracao rapida."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .transcriber import TranscriptionResult, WordToken

_CACHE_VERSION = 1
_MAX_CACHE_FILES = 20


@dataclass(frozen=True)
class CacheKey:
    digest: str

    @classmethod
    def build(
        cls,
        *,
        input_path: Path,
        model_requested: str,
        timeline_duration: float,
        cache_version: int = _CACHE_VERSION,
    ) -> "CacheKey":
        stat = input_path.stat()
        payload = {
            "cache_version": cache_version,
            "input_path": str(input_path.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "model_requested": model_requested,
            "timeline_duration": round(float(timeline_duration), 6),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        return cls(digest=digest)


class TranscriptionCache:
    def __init__(self, cache_dir: Path, max_files: int = _MAX_CACHE_FILES) -> None:
        self._cache_dir = Path(cache_dir)
        self._max_files = max_files

    def load(self, key: CacheKey) -> TranscriptionResult | None:
        path = self._path_for(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            words = tuple(WordToken(**item) for item in payload["words"])
            result = TranscriptionResult(
                words=words,
                audio_duration=float(payload["audio_duration"]),
                language=str(payload["language"]),
                language_probability=float(payload["language_probability"]),
                model_requested=str(payload["model_requested"]),
                model_resolved=str(payload["model_resolved"]),
                device_requested=str(payload["device_requested"]),
                device_used=str(payload["device_used"]),
                compute_type=str(payload["compute_type"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            try:
                path.unlink()
            except OSError:
                pass
            return None
        return result

    def save(self, key: CacheKey, result: TranscriptionResult) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(key)
        payload = asdict(result)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        self._prune()

    def _path_for(self, key: CacheKey) -> Path:
        return self._cache_dir / f"{key.digest}.json"

    def _prune(self) -> None:
        files = sorted(
            self._cache_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for stale in files[self._max_files :]:
            try:
                stale.unlink()
            except OSError:
                pass
