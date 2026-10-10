from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MAX_UPLOAD_GB = 2.0


def _read_env_file(path: Path) -> dict[str, str]:
    """Read the small KEY=value subset used by this project's .env file."""
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _setting(name: str, file_values: dict[str, str], default: str = "") -> str:
    # Explicit process environment values take precedence over the .env file.
    if name in os.environ:
        return os.environ[name]
    return file_values.get(name, default)


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    session_string: str
    session_file: Path
    max_upload_bytes: int
    env_file: Path

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> "Config":
        env_path = Path(env_file).expanduser().resolve()
        file_values = _read_env_file(env_path)

        api_id_raw = _setting("TELEGRAM_API_ID", file_values)
        api_hash = _setting("TELEGRAM_API_HASH", file_values).strip()
        if (
            not api_id_raw.strip()
            or not api_hash
            or api_id_raw.strip().lower().startswith("replace_with_")
            or api_hash.lower().startswith("replace_with_")
        ):
            raise ValueError(
                f"Telegram API credentials are missing. Copy .env.example to {env_path} "
                "and set TELEGRAM_API_ID and TELEGRAM_API_HASH."
            )
        try:
            api_id = int(api_id_raw.strip())
        except ValueError as exc:
            raise ValueError("TELEGRAM_API_ID must be an integer.") from exc
        if api_id <= 0:
            raise ValueError("TELEGRAM_API_ID must be greater than zero.")

        session_string = _setting("TELEGRAM_SESSION", file_values).strip()
        session_file_raw = _setting("SESSION_FILE", file_values, ".telegram-bridge-session").strip()
        session_file = Path(session_file_raw or ".telegram-bridge-session").expanduser()
        if not session_file.is_absolute():
            session_file = (env_path.parent / session_file).resolve()

        max_upload_gb_raw = _setting("MAX_UPLOAD_GB", file_values, str(DEFAULT_MAX_UPLOAD_GB)).strip()
        try:
            max_upload_gb = float(max_upload_gb_raw)
        except ValueError as exc:
            raise ValueError("MAX_UPLOAD_GB must be a positive number, normally 2 or 4.") from exc
        if max_upload_gb <= 0:
            raise ValueError("MAX_UPLOAD_GB must be greater than zero.")

        return cls(
            api_id=api_id,
            api_hash=api_hash,
            session_string=session_string,
            session_file=session_file,
            max_upload_bytes=int(max_upload_gb * 1024**3),
            env_file=env_path,
        )
