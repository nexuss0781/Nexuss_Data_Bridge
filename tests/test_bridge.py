from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from telegram_file_bridge.config import Config
from telegram_file_bridge.core import download_message, transfer_message, upload_file


class FakeClient:
    def __init__(self, message_id: int, file_name: str, payload: bytes, caption: str = ""):
        self.payload = payload
        self.message = SimpleNamespace(
            id=message_id,
            media=object(),
            file=SimpleNamespace(name=file_name, size=len(payload)),
            message=caption,
        )
        self.sent: list[dict[str, object]] = []
        self.download_targets: list[str] = []

    async def get_messages(self, chat, ids):
        return self.message if ids == self.message.id else None

    async def download_media(self, message, file):
        self.download_targets.append(file)
        Path(file).write_bytes(self.payload)
        return file

    async def send_file(self, chat, file, caption=None, force_document=False):
        self.sent.append(
            {
                "chat": chat,
                "file_bytes": Path(file).read_bytes(),
                "caption": caption,
                "force_document": force_document,
                "path": file,
            }
        )
        return SimpleNamespace(id=9876)


class BridgeEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_sanitizes_name_and_preserves_bytes(self):
        payload = "የአማርኛ ዳታ-set".encode("utf-8")
        client = FakeClient(42, "../../የአማርኛ ዳታ.bin", payload)
        with tempfile.TemporaryDirectory() as temp_dir:
            result = await download_message(client, "@source", 42, temp_dir)
            path = Path(result.local_path)
            self.assertEqual(path.parent, Path(temp_dir).resolve())
            self.assertEqual(path.name, "የአማርኛ ዳታ.bin")
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(result.size_bytes, len(payload))

    async def test_upload_sends_exact_file_as_document(self):
        payload = b"archive-bytes\x00\xff"
        client = FakeClient(12, "source.bin", b"")
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "corpus.tar"
            file_path.write_bytes(payload)
            result = await upload_file(
                client,
                "@destination",
                file_path,
                caption="batch A",
                max_upload_bytes=1024,
            )
            self.assertEqual(client.sent[0]["chat"], "@destination")
            self.assertEqual(client.sent[0]["file_bytes"], payload)
            self.assertEqual(client.sent[0]["caption"], "batch A")
            self.assertTrue(client.sent[0]["force_document"])
            self.assertEqual(result.message_id, 9876)
            self.assertEqual(result.size_bytes, len(payload))

    async def test_transfer_downloads_then_uploads_and_cleans_temporary_file(self):
        payload = "Amharic corpus sample".encode("utf-8")
        client = FakeClient(51, "sample.txt", payload, caption="original caption")
        result = await transfer_message(
            client,
            "@source",
            51,
            "@destination",
            max_upload_bytes=1024,
        )
        self.assertEqual(client.sent[0]["chat"], "@destination")
        self.assertEqual(client.sent[0]["file_bytes"], payload)
        self.assertEqual(client.sent[0]["caption"], "original caption")
        self.assertFalse(Path(str(client.sent[0]["path"])).exists())
        self.assertEqual(result.action, "transfer")
        self.assertEqual(result.message_id, 9876)
        self.assertEqual(result.size_bytes, len(payload))

    async def test_upload_rejects_file_above_configured_limit_before_sending(self):
        client = FakeClient(12, "unused", b"")
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "large.bin"
            file_path.write_bytes(b"12345")
            with self.assertRaisesRegex(ValueError, "above the configured upload limit"):
                await upload_file(client, "@destination", file_path, max_upload_bytes=4)
        self.assertEqual(client.sent, [])

    async def test_transfer_rejects_over_limit_after_download_and_does_not_send(self):
        client = FakeClient(52, "large.bin", b"12345")
        with self.assertRaisesRegex(ValueError, "above the configured upload limit"):
            await transfer_message(
                client,
                "@source",
                52,
                "@destination",
                max_upload_bytes=4,
            )
        self.assertEqual(client.sent, [])


class ConfigTests(unittest.TestCase):
    def test_reads_credentials_and_resolves_session_path_relative_to_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "TELEGRAM_API_ID=123456\n"
                "TELEGRAM_API_HASH=abc123\n"
                "TELEGRAM_SESSION=sample-session\n"
                "SESSION_FILE=session-store\n"
                "MAX_UPLOAD_GB=4\n",
                encoding="utf-8",
            )
            config = Config.load(env_path)
            self.assertEqual(config.api_id, 123456)
            self.assertEqual(config.api_hash, "abc123")
            self.assertEqual(config.session_string, "sample-session")
            self.assertEqual(config.session_file, Path(temp_dir) / "session-store")
            self.assertEqual(config.max_upload_bytes, 4 * 1024**3)

    def test_missing_credentials_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "TELEGRAM_API_ID and TELEGRAM_API_HASH"):
                Config.load(Path(temp_dir) / "missing.env")


if __name__ == "__main__":
    unittest.main()
