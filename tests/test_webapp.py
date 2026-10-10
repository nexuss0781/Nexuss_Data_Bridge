from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from telethon.errors import SessionPasswordNeededError

from telegram_file_bridge.config import Config
from webapp import create_app


class FakeTelegramClient:
    def __init__(self, config: Config, store: dict[str, object], needs_password: bool = False):
        self.config = config
        self.store = store
        self.needs_password = needs_password
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return bool(self.store.get("authorized"))

    async def send_code_request(self, phone):
        self.store["phone"] = phone
        return SimpleNamespace(phone_code_hash="fake-code-hash")

    async def sign_in(self, *, phone=None, code=None, phone_code_hash=None, password=None):
        if code is not None:
            if self.needs_password:
                raise SessionPasswordNeededError(request=None)
            if code != "12345" or phone_code_hash != "fake-code-hash":
                raise ValueError("bad code")
        elif password != "correct-test-password":
            raise ValueError("bad password")
        self.store["authorized"] = True
        session_path = self.config.session_file
        if not str(session_path).endswith(".session"):
            session_path = Path(f"{session_path}.session")
        session_path.write_bytes(b"fake persistent session")

    async def get_me(self):
        return SimpleNamespace(username="bridge_test", first_name="Bridge", id=12345)


class WebSignInTests(unittest.TestCase):
    def _setup(self, needs_password: bool = False):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        config = Config(
            api_id=12345,
            api_hash="test-only",
            session_string="",
            session_file=root / "telegram-session",
            max_upload_bytes=2 * 1024**3,
            env_file=root / ".env",
        )
        store: dict[str, object] = {"authorized": False}

        def factory(cfg: Config):
            return FakeTelegramClient(cfg, store, needs_password=needs_password)

        app = create_app(config=config, client_factory=factory, access_code="test-access")
        return temp, config, store, app

    def _unlock(self, client: TestClient):
        response = client.post("/unlock", json={"access_code": "test-access"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.cookies.get("tgbridge_login"))

    def test_phone_code_login_saves_and_reopens_session(self):
        temp, config, store, app = self._setup()
        try:
            with TestClient(app) as client:
                self.assertIn("Telegram File Bridge", client.get("/").text)
                self.assertEqual(client.get("/status").status_code, 401)
                self._unlock(client)
                self.assertFalse(client.get("/status").json()["authorized"])
                sent = client.post("/send-code", json={"phone": "+251911111111"})
                self.assertEqual(sent.status_code, 200, sent.text)
                self.assertEqual(store["phone"], "+251911111111")
                signed_in = client.post("/verify-code", json={"code": "12345"})
                self.assertEqual(signed_in.status_code, 200, signed_in.text)
                self.assertTrue(signed_in.json()["session_persisted"])
                self.assertEqual(signed_in.json()["username"], "bridge_test")
                status = client.get("/status").json()
                self.assertTrue(status["authorized"])
                session_path = Path(f"{config.session_file}.session")
                self.assertTrue(session_path.is_file())
                self.assertEqual(session_path.stat().st_mode & 0o777, 0o600)
        finally:
            temp.cleanup()

    def test_two_step_password_completes_login_and_persists_session(self):
        temp, config, store, app = self._setup(needs_password=True)
        try:
            with TestClient(app) as client:
                self._unlock(client)
                self.assertEqual(client.post("/send-code", json={"phone": "+251911111111"}).status_code, 200)
                needs_2fa = client.post("/verify-code", json={"code": "12345"})
                self.assertEqual(needs_2fa.status_code, 200, needs_2fa.text)
                self.assertTrue(needs_2fa.json()["needs_2fa"])
                signed_in = client.post("/verify-password", json={"password": "correct-test-password"})
                self.assertEqual(signed_in.status_code, 200, signed_in.text)
                self.assertTrue(signed_in.json()["session_persisted"])
                self.assertTrue(store["authorized"])
                self.assertTrue(Path(f"{config.session_file}.session").is_file())
        finally:
            temp.cleanup()

    def test_requires_app_access_code_and_international_phone_format(self):
        temp, _config, _store, app = self._setup()
        try:
            with TestClient(app) as client:
                self.assertEqual(client.post("/unlock", json={"access_code": "wrong"}).status_code, 401)
                self._unlock(client)
                invalid = client.post("/send-code", json={"phone": "911111111"})
                self.assertEqual(invalid.status_code, 400)
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
