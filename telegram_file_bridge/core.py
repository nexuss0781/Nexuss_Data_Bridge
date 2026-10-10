from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OperationResult:
    action: str
    chat: str
    file_name: str
    size_bytes: int
    message_id: int | None
    local_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_filename(value: str | None, message_id: int) -> str:
    # Treat both Unix and Windows separators as path separators; Telegram filenames
    # are untrusted input and must never choose an arbitrary local path.
    raw = (value or "").replace("\\", "/").rsplit("/", 1)[-1]
    raw = "".join("_" if ord(char) < 32 or ord(char) == 127 else char for char in raw).strip()
    if raw in {"", ".", ".."}:
        return f"telegram-{message_id}.bin"
    return raw


def _message_file_info(message: Any) -> tuple[str, int | None, str]:
    media_file = getattr(message, "file", None)
    name = _safe_filename(getattr(media_file, "name", None), int(message.id))
    size = getattr(media_file, "size", None)
    caption = getattr(message, "message", None) or ""
    return name, size if isinstance(size, int) else None, caption


def _choose_target(directory: Path, name: str, message_id: int, overwrite: bool) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    if overwrite or not target.exists():
        return target

    base = Path(name)
    candidate = directory / f"{base.stem}-{message_id}{base.suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{base.stem}-{message_id}-{counter}{base.suffix}"
        counter += 1
    return candidate


async def download_message(
    client: Any,
    chat: str,
    message_id: int,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> OperationResult:
    message = await client.get_messages(chat, ids=message_id)
    if message is None:
        raise ValueError(f"No Telegram message found for {chat!r}, message {message_id}.")
    if not getattr(message, "media", None):
        raise ValueError(f"Telegram message {message_id} does not contain downloadable media.")

    name, expected_size, _ = _message_file_info(message)
    target = _choose_target(Path(output_dir).expanduser(), name, int(message.id), overwrite)
    try:
        result_path = await client.download_media(message, file=str(target))
        if not result_path:
            raise OSError("Telegram did not return a downloaded file path.")
        downloaded = Path(result_path).expanduser()
        if not downloaded.is_file():
            raise OSError(f"Telegram reported a download, but the file is missing: {downloaded}")
        actual_size = downloaded.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            raise OSError(
                f"Downloaded file size mismatch: expected {expected_size} bytes, got {actual_size}."
            )
    except Exception:
        # Do not leave a misleading partial file behind after a failed download.
        if target.exists():
            target.unlink()
        raise

    return OperationResult(
        action="download",
        chat=chat,
        file_name=downloaded.name,
        size_bytes=actual_size,
        message_id=int(message.id),
        local_path=str(downloaded.resolve()),
    )


async def upload_file(
    client: Any,
    chat: str,
    file_path: str | Path,
    *,
    caption: str | None = None,
    max_upload_bytes: int,
) -> OperationResult:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Upload file does not exist or is not a regular file: {path}")
    size = path.stat().st_size
    if size > max_upload_bytes:
        max_gb = max_upload_bytes / 1024**3
        actual_gb = size / 1024**3
        raise ValueError(
            f"File is {actual_gb:.2f} GiB, above the configured upload limit of {max_gb:g} GiB. "
            "Set MAX_UPLOAD_GB=4 only if the Telegram account and client support 4 GiB uploads."
        )

    message = await client.send_file(chat, str(path), caption=caption, force_document=True)
    if message is None:
        raise OSError("Telegram did not return a sent message.")
    message_id = getattr(message, "id", None)
    return OperationResult(
        action="upload",
        chat=chat,
        file_name=path.name,
        size_bytes=size,
        message_id=int(message_id) if message_id is not None else None,
    )


async def transfer_message(
    client: Any,
    source_chat: str,
    message_id: int,
    destination_chat: str,
    *,
    caption: str | None = None,
    max_upload_bytes: int,
) -> OperationResult:
    """Download one source message to a temporary file, then upload it to a chat."""
    with tempfile.TemporaryDirectory(prefix="telegram-file-bridge-") as temp_dir:
        downloaded = await download_message(client, source_chat, message_id, temp_dir)
        source_message = await client.get_messages(source_chat, ids=message_id)
        if caption is None:
            caption = getattr(source_message, "message", None) or None
        uploaded = await upload_file(
            client,
            destination_chat,
            downloaded.local_path or "",
            caption=caption,
            max_upload_bytes=max_upload_bytes,
        )
        return OperationResult(
            action="transfer",
            chat=destination_chat,
            file_name=uploaded.file_name,
            size_bytes=uploaded.size_bytes,
            message_id=uploaded.message_id,
        )
