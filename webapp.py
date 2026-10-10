from __future__ import annotations

import asyncio
import hmac
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from telegram_file_bridge.cli import _create_client, _restrict_session_permissions
from telegram_file_bridge.config import Config

PROJECT_DIR = Path(__file__).resolve().parent
COOKIE_NAME = "tgbridge_login"
COOKIE_MAX_AGE = 8 * 60 * 60
PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")

PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Telegram File Bridge — Sign in</title>
  <style>
    :root { color-scheme: light; font: 16px/1.5 system-ui, sans-serif; color: #172033; background: #f3f6fb; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; }
    main { width: min(100%, 520px); background: white; border: 1px solid #dce3ee; border-radius: 16px; padding: 28px; box-shadow: 0 16px 40px #1d355710; }
    h1 { font-size: 1.45rem; margin: 0 0 6px; }
    p { color: #58657b; margin: 0 0 18px; }
    label { display: block; font-weight: 650; margin: 16px 0 6px; }
    input { width: 100%; padding: 11px 12px; border: 1px solid #cbd5e1; border-radius: 9px; font: inherit; }
    button { margin-top: 14px; padding: 11px 15px; width: 100%; color: white; background: #2563eb; border: 0; border-radius: 9px; font: inherit; font-weight: 700; cursor: pointer; }
    button:disabled { opacity: .6; cursor: wait; }
    .note { background: #f1f5f9; border-radius: 9px; padding: 12px; font-size: .9rem; }
    #status { min-height: 1.5em; margin-top: 14px; color: #334155; overflow-wrap: anywhere; }
    .success { color: #087443 !important; }
    .error { color: #b42318 !important; }
    [hidden] { display: none !important; }
  </style>
</head>
<body>
<main>
  <h1>Telegram File Bridge</h1>
  <p>Sign in once to save a local Telegram session for the bridge.</p>
  <section id="gate">
    <p class="note"><strong>First:</strong> enter the <strong>web app access code</strong> supplied with this page. It is not your Telegram login code. Unlocking reveals the phone-number step; Telegram will send a separate login code afterward.</p>
    <label for="access">Web app access code</label>
    <input id="access" type="password" autocomplete="current-password" required>
    <button id="unlock">Unlock and continue to phone number</button>
  </section>
  <section id="login" hidden>
    <div class="note">Your phone number and Telegram login code are sent to Telegram for sign-in. The authorized session is stored in this computer's private session file and survives app restarts.</div>
    <form id="phone-form">
      <label for="phone">Telegram phone number</label>
      <input id="phone" type="tel" placeholder="+2519…" autocomplete="tel" required>
      <button type="submit">Send login code</button>
    </form>
    <form id="code-form" hidden>
      <label for="code">Login code</label>
      <input id="code" inputmode="numeric" autocomplete="one-time-code" required>
      <button type="submit">Verify code</button>
    </form>
    <form id="password-form" hidden>
      <label for="password">Telegram 2-step verification password</label>
      <input id="password" type="password" autocomplete="current-password" required>
      <button type="submit">Complete sign-in</button>
    </form>
  </section>
  <div id="status" role="status" aria-live="polite"></div>
</main>
<script>
const statusBox = document.getElementById('status');
const gate = document.getElementById('gate');
const login = document.getElementById('login');
const phoneForm = document.getElementById('phone-form');
const codeForm = document.getElementById('code-form');
const passwordForm = document.getElementById('password-form');
function showStatus(text, kind='') { statusBox.textContent = text; statusBox.className = kind; }
async function callApi(path, payload={}) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload), credentials:'same-origin'});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Request failed.');
  return data;
}
function showAuthorized(data) {
  gate.hidden = true; login.hidden = true;
  showStatus(`Signed in${data.username ? ' as @' + data.username : ''}. Persistent session verified at ${data.session_file}.`, 'success');
}
async function refreshStatus() {
  const response = await fetch('/status', {credentials:'same-origin'});
  if (response.status === 401) { gate.hidden = false; login.hidden = true; return; }
  const data = await response.json();
  if (data.authorized) showAuthorized(data);
  else { gate.hidden = true; login.hidden = false; showStatus('Enter the Telegram number with country code to request a sign-in code.'); }
}
document.getElementById('unlock').addEventListener('click', async () => {
  try { await callApi('/unlock', {access_code:document.getElementById('access').value}); await refreshStatus(); }
  catch (error) { showStatus(error.message, 'error'); }
});
phoneForm.addEventListener('submit', async event => {
  event.preventDefault();
  try { const result = await callApi('/send-code', {phone:document.getElementById('phone').value.trim()});
    codeForm.hidden = false; showStatus(result.message); document.getElementById('code').focus(); }
  catch (error) { showStatus(error.message, 'error'); }
});
codeForm.addEventListener('submit', async event => {
  event.preventDefault();
  try { const result = await callApi('/verify-code', {code:document.getElementById('code').value.trim()});
    if (result.needs_2fa) { passwordForm.hidden = false; showStatus('Code accepted. Enter the Telegram 2-step verification password.'); document.getElementById('password').focus(); }
    else showAuthorized(result); }
  catch (error) { showStatus(error.message, 'error'); }
});
passwordForm.addEventListener('submit', async event => {
  event.preventDefault();
  try { const result = await callApi('/verify-password', {password:document.getElementById('password').value}); showAuthorized(result); }
  catch (error) { showStatus(error.message, 'error'); }
});
refreshStatus();
</script>
</body>
</html>"""


def _user_label(user: Any) -> str:
    username = getattr(user, "username", None)
    return username or getattr(user, "first_name", None) or "Telegram account"


def create_app(
    *,
    config: Config | None = None,
    client_factory: Callable[[Config], Any] | None = None,
    access_code: str | None = None,
) -> FastAPI:
    """Build the small sign-in app; injectable dependencies keep the flow testable."""
    factory = client_factory or _create_client
    expected_access_code = access_code or os.environ.get("TG_BRIDGE_ACCESS_CODE") or secrets.token_urlsafe(18)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = config or Config.load(os.environ.get("BRIDGE_ENV_FILE", str(PROJECT_DIR / ".env")))
        if cfg.session_string:
            raise RuntimeError(
                "The sign-in page requires a persistent SESSION_FILE; clear TELEGRAM_SESSION in .env first."
            )
        client = factory(cfg)
        app.state.config = cfg
        app.state.client = client
        app.state.pending_phone = None
        app.state.phone_code_hash = None
        app.state.last_send_time = 0.0
        app.state.authorized = False
        app.state.username = None
        app.state.session_persisted = False
        await client.connect()
        _restrict_session_permissions(cfg)
        if await client.is_user_authorized():
            user = await client.get_me()
            app.state.authorized = True
            app.state.username = _user_label(user)
            app.state.session_persisted = True
            await client.disconnect()
            app.state.client = None
        try:
            yield
        finally:
            active = app.state.client
            if active is not None and active.is_connected():
                await active.disconnect()

    app = FastAPI(title="Telegram File Bridge Sign-in", lifespan=lifespan)
    app.state.access_code = expected_access_code
    app.state.unlocked_sessions = set()
    app.state.login_lock = asyncio.Lock()

    def require_unlocked(request: Request) -> None:
        token = request.cookies.get(COOKIE_NAME, "")
        if not token or token not in app.state.unlocked_sessions:
            raise HTTPException(status_code=401, detail="Unlock the app first.")

    async def finish_sign_in() -> dict[str, Any]:
        cfg: Config = app.state.config
        client = app.state.client
        if client is None:
            raise HTTPException(status_code=409, detail="This Telegram session is already signed in.")

        user = await client.get_me()
        await client.disconnect()
        _restrict_session_permissions(cfg)

        # Reopen from the same on-disk SQLite session to prove auth survives a client restart.
        verifier = factory(cfg)
        try:
            await verifier.connect()
            if not await verifier.is_user_authorized():
                raise RuntimeError("Telegram sign-in did not persist in the local session database.")
            user = await verifier.get_me()
        except Exception:
            if verifier.is_connected():
                await verifier.disconnect()
            app.state.client = None
            raise
        await verifier.disconnect()
        _restrict_session_permissions(cfg)

        app.state.client = None
        app.state.authorized = True
        app.state.username = _user_label(user)
        app.state.session_persisted = True
        app.state.pending_phone = None
        app.state.phone_code_hash = None
        return {
            "authorized": True,
            "username": app.state.username,
            "session_persisted": True,
            "session_file": Path(f"{cfg.session_file}.session" if not str(cfg.session_file).endswith(".session") else cfg.session_file).name,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(PAGE, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/unlock")
    async def unlock(request: Request) -> JSONResponse:
        try:
            data = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid request.") from exc
        candidate = str(data.get("access_code", ""))
        if not hmac.compare_digest(candidate, expected_access_code):
            raise HTTPException(status_code=401, detail="Access code is incorrect.")
        token = secrets.token_urlsafe(32)
        app.state.unlocked_sessions.add(token)
        secure_cookie = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
        response = JSONResponse({"unlocked": True})
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=secure_cookie,
            samesite="strict",
            path="/",
        )
        return response

    @app.get("/status")
    async def status(request: Request) -> dict[str, Any]:
        require_unlocked(request)
        cfg: Config = app.state.config
        session_path = cfg.session_file if str(cfg.session_file).endswith(".session") else Path(f"{cfg.session_file}.session")
        return {
            "authorized": app.state.authorized,
            "username": app.state.username,
            "session_persisted": app.state.session_persisted,
            "session_file": session_path.name,
        }

    @app.post("/send-code")
    async def send_code(request: Request) -> dict[str, str]:
        require_unlocked(request)
        if app.state.authorized:
            raise HTTPException(status_code=409, detail="This Telegram account is already signed in.")
        try:
            data = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid request.") from exc
        phone = str(data.get("phone", "")).strip()
        if not PHONE_PATTERN.fullmatch(phone):
            raise HTTPException(status_code=400, detail="Enter the phone number with + and country code, e.g. +2519… .")
        async with app.state.login_lock:
            remaining = 30.0 - (time.monotonic() - app.state.last_send_time)
            if remaining > 0:
                raise HTTPException(status_code=429, detail=f"Please wait {int(remaining) + 1} seconds before requesting another code.")
            try:
                sent = await app.state.client.send_code_request(phone)
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Telegram could not send a code. Check the number and try again.") from exc
            app.state.last_send_time = time.monotonic()
            app.state.pending_phone = phone
            app.state.phone_code_hash = sent.phone_code_hash
        return {"message": "Telegram sent a login code. Enter it here to continue."}

    @app.post("/verify-code")
    async def verify_code(request: Request) -> dict[str, Any]:
        require_unlocked(request)
        if app.state.authorized:
            raise HTTPException(status_code=409, detail="This Telegram account is already signed in.")
        if not app.state.pending_phone or not app.state.phone_code_hash:
            raise HTTPException(status_code=409, detail="Request a login code first.")
        try:
            data = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid request.") from exc
        code = str(data.get("code", "")).strip()
        if not code or len(code) > 32:
            raise HTTPException(status_code=400, detail="Enter the login code Telegram sent you.")
        async with app.state.login_lock:
            try:
                await app.state.client.sign_in(
                    phone=app.state.pending_phone,
                    code=code,
                    phone_code_hash=app.state.phone_code_hash,
                )
            except Exception as exc:
                from telethon.errors import SessionPasswordNeededError
                if isinstance(exc, SessionPasswordNeededError):
                    return {"needs_2fa": True}
                from telethon.errors import PhoneCodeExpiredError, PhoneCodeInvalidError
                if isinstance(exc, PhoneCodeInvalidError):
                    raise HTTPException(status_code=400, detail="That code is incorrect. Check it and try again.") from exc
                if isinstance(exc, PhoneCodeExpiredError):
                    raise HTTPException(status_code=400, detail="That code expired. Request a new code.") from exc
                raise HTTPException(status_code=400, detail="Telegram sign-in failed. Request a new code and try again.") from exc
            return await finish_sign_in()

    @app.post("/verify-password")
    async def verify_password(request: Request) -> dict[str, Any]:
        require_unlocked(request)
        if app.state.authorized:
            raise HTTPException(status_code=409, detail="This Telegram account is already signed in.")
        if not app.state.pending_phone:
            raise HTTPException(status_code=409, detail="Request a login code first.")
        try:
            data = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid request.") from exc
        password = str(data.get("password", ""))
        if not password:
            raise HTTPException(status_code=400, detail="Enter the Telegram 2-step verification password.")
        async with app.state.login_lock:
            try:
                await app.state.client.sign_in(password=password)
            except Exception as exc:
                from telethon.errors import PasswordHashInvalidError
                if isinstance(exc, PasswordHashInvalidError):
                    raise HTTPException(status_code=400, detail="That 2-step verification password was not accepted.") from exc
                raise HTTPException(status_code=400, detail="Telegram sign-in failed. Check the password and try again.") from exc
            return await finish_sign_in()

    return app


app = create_app()


def main() -> None:
    import uvicorn

    access_code = app.state.access_code
    port = int(os.environ.get("PORT", "8765"))
    print(f"Telegram File Bridge sign-in page: listening on port {port}", flush=True)
    print(f"One-time app access code: {access_code}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning", access_log=False, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
