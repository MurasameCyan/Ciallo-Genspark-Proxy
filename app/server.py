from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .accounts import AccountStore
from .build import build_info, check_update
from .config_store import ConfigStore, DATA_DIR, ROOT_DIR
from .gateway import Gateway, MODELS
from .registration import RegistrationService

WEB_DIR = ROOT_DIR / "web"


class LogBus:
    def __init__(self, limit: int = 500):
        self.limit = limit
        self.lines: deque[dict[str, Any]] = deque(maxlen=limit)
        self.clients: set[queue.Queue[Any]] = set()
        self.lock = threading.RLock()

    def emit(self, level: str, message: str) -> None:
        line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "level": level, "msg": str(message)}
        with self.lock:
            self.lines.append(line)
            clients = list(self.clients)
        print(f"[{line['ts']}] {level.upper():5} {line['msg']}", flush=True)
        for client in clients:
            try:
                client.put_nowait(line)
            except queue.Full:
                pass

    def stream(self) -> Iterator[str]:
        client: queue.Queue[Any] = queue.Queue(maxsize=100)
        with self.lock:
            client.put(list(self.lines))
            self.clients.add(client)
        try:
            while True:
                try:
                    item = client.get(timeout=15)
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            with self.lock:
                self.clients.discard(client)

class PanelAuth:
    def __init__(self) -> None:
        self.user = os.environ.get("PANEL_USER") or "admin"
        self.password, self.password_source = self._resolve_password()
        self.sessions: set[str] = set()
        self.lock = threading.RLock()

    @staticmethod
    def _read_password_file() -> str:
        """Sidecar credential for deployments that cannot set env (or need to rotate
        the password without recreating the container)."""
        configured = str(os.environ.get("PANEL_PASS_FILE") or "").strip()
        path = Path(configured) if configured else ROOT_DIR / "panel_password"
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _resolve_password(self) -> tuple[str, str]:
        """PANEL_PASS configures the container and therefore wins. A panel_password
        file is the fallback for deployments that mount a secret instead of setting
        env, so the env value is never silently overridden by a stray file."""
        from_env = os.environ.get("PANEL_PASS") or ""
        if from_env:
            return from_env, "env"
        from_file = self._read_password_file()
        if from_file:
            return from_file, "file"
        return "", "none"

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def login(self, user: str, password: str) -> str | None:
        if not self.enabled:
            token = secrets.token_urlsafe(24)
        elif user != self.user or not secrets.compare_digest(password, self.password):
            return None
        else:
            token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions.add(token)
        return token

    def valid(self, request: Request) -> bool:
        if not self.enabled:
            return True
        cookie = request.cookies.get("gs_session") or ""
        if cookie:
            with self.lock:
                if cookie in self.sessions:
                    return True
        header = request.headers.get("authorization") or ""
        if header.lower().startswith("basic "):
            try:
                raw = base64.b64decode(header.split(None, 1)[1]).decode("utf-8")
                user, password = raw.split(":", 1)
            except (ValueError, UnicodeError, IndexError):
                return False
            return user == self.user and secrets.compare_digest(password, self.password)
        return False

    def logout(self, request: Request) -> None:
        token = request.cookies.get("gs_session")
        if token:
            with self.lock:
                self.sessions.discard(token)


config_store = ConfigStore()
config = config_store.load()
accounts = AccountStore(config.get("accounts_file"))
gateway = Gateway(accounts)
logs = LogBus()
auth = PanelAuth()
registration = RegistrationService(config_store, accounts, logs.emit)
START = time.time()

app = FastAPI(title="Ciallo Genspark Proxy", docs_url=None, redoc_url=None)


def panel_required(request: Request) -> None:
    if not auth.valid(request):
        raise HTTPException(status_code=401, detail="需要登录控制台")


def api_key_required(request: Request) -> None:
    expected = os.environ.get("API_KEY") or ""
    if not expected:
        return
    raw = request.headers.get("x-api-key") or ""
    if not raw:
        authorization = request.headers.get("authorization") or ""
        if authorization.lower().startswith("bearer "):
            raw = authorization[7:].strip()
    if not raw or not secrets.compare_digest(raw, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")


def registration_error(exc: Exception) -> HTTPException:
    """Turn orchestrator errors into client-visible responses instead of 500s."""
    status = 409 if isinstance(exc, RuntimeError) else 400
    return HTTPException(status_code=status, detail=str(exc) or exc.__class__.__name__)


@app.on_event("startup")
async def startup() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logs.emit("ok", f"服务启动 build={os.environ.get('GIT_COMMIT') or 'dev'} accounts={len(gateway.accounts)}")
    if auth.enabled:
        logs.emit("info", f"控制台鉴权已开启 user={auth.user} 来源={auth.password_source}")
    else:
        logs.emit("warn", "PANEL_PASS 与 panel_password 都未设置，控制台未启用登录保护")


@app.get("/health")
def health() -> dict[str, Any]:
    state = gateway.health()
    return {
        "ok": True,
        "uptime_s": round(time.time() - START, 1),
        "accounts": len(state["accounts"]),
        "ready": sum(1 for item in state["accounts"] if item["ready"]),
    }


@app.get("/v1/models")
def models(request: Request) -> dict[str, Any]:
    api_key_required(request)
    return {"object": "list", "data": [{"id": model, "object": "model", "owned_by": "genspark-web"} for model in MODELS]}


@app.post("/v1/chat/completions")
async def chat(request: Request) -> Response:
    api_key_required(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    if payload.get("stream"):
        return StreamingResponse(gateway.stream(payload), media_type="text/event-stream")
    result, status = await asyncio.to_thread(gateway.complete, payload)
    return JSONResponse(result, status_code=status)


@app.post("/api/login")
async def login(request: Request) -> Response:
    # The dashboard form also posts natively (no JavaScript in the loop), so the
    # button still works when login.js is blocked, stale, or never loaded. JSON
    # callers keep the original JSON reply.
    content_type = request.headers.get("content-type") or ""
    if content_type.startswith("application/x-www-form-urlencoded"):
        # Hand-parsed: Starlette's request.form() hard-requires python-multipart
        # even for urlencoded bodies, and this endpoint only needs two fields.
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        user = (form.get("user") or [""])[0]
        password = (form.get("pass") or [""])[0]
        is_form = True
    else:
        body = await request.json()
        user, password = str(body.get("user") or ""), str(body.get("pass") or "")
        is_form = False
    token = auth.login(user, password)
    if not token:
        if is_form:
            return RedirectResponse("/login.html?error=1", status_code=303)
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    response: Response = RedirectResponse("/", status_code=303) if is_form else JSONResponse({"ok": True, "user": auth.user})
    response.set_cookie("gs_session", token, httponly=True, samesite="lax", secure=os.environ.get("COOKIE_SECURE") == "1", max_age=86400)
    logs.emit("ok", f"[auth] {auth.user} 登录")
    return response


@app.post("/api/logout")
def logout(request: Request) -> JSONResponse:
    panel_required(request)
    auth.logout(request)
    response = JSONResponse({"ok": True})
    response.delete_cookie("gs_session")
    return response


@app.get("/api/status")
def api_status(request: Request) -> dict[str, Any]:
    panel_required(request)
    gateway.reload()
    status = gateway.health()
    return {
        "uptime_s": round(time.time() - START, 1),
        "account_count": len(status["accounts"]),
        "ready_count": sum(1 for item in status["accounts"] if item["ready"]),
        "registration": registration.snapshot(),
        # build / buildUrl / repoUrl / trackRef: the header hash chip. They ride
        # along with the polled status instead of costing another request.
        **build_info(),
    }


@app.get("/api/accounts")
def api_accounts(request: Request) -> dict[str, Any]:
    panel_required(request)
    gateway.reload()
    return {"accounts": accounts.public_records(gateway.accounts)}


@app.get("/api/config")
def api_config(request: Request) -> dict[str, Any]:
    panel_required(request)
    return config_store.public()


@app.post("/api/config")
async def update_config(request: Request) -> dict[str, Any]:
    panel_required(request)
    body = await request.json()
    saved = config_store.save(body)
    logs.emit("ok", "[config] 配置已保存")
    return config_store.public()


@app.post("/api/check-update")
def api_check_update(request: Request) -> dict[str, Any]:
    panel_required(request)
    result = check_update()
    logs.emit(
        "warn" if result["error"] else "ok",
        f"[update] 检查更新失败: {result['error']}"
        if result["error"]
        else (
            f"[update] 有新版本 {result['latest']}(当前 {result['current']}),docker compose pull 后重启"
            if result["hasUpdate"]
            else f"[update] 已是最新 {result['current']}"
        ),
    )
    return result


@app.get("/api/logs")
def api_logs(request: Request) -> StreamingResponse:
    panel_required(request)
    return StreamingResponse(
        logs.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/register/start")
async def register_start(request: Request) -> dict[str, Any]:
    panel_required(request)
    body = await request.json()
    raw_seq = body.get("seq")
    try:
        seq = int(raw_seq) if raw_seq not in (None, "") else None
        raw_auto = body.get("auto_solve")
        auto_solve = None if raw_auto is None else bool(raw_auto)
        result = registration.start(seq=seq, email=str(body.get("email") or ""), proxy=str(body.get("proxy") or ""), auto_solve=auto_solve)
    except (RuntimeError, ValueError, TypeError) as exc:
        raise registration_error(exc) from exc
    logs.emit("info", f"[register] queued job={result['job_id']} seq={result['seq']} auto_solve={result.get('auto_solve')}")
    return result


@app.post("/api/register/command")
async def register_command(request: Request) -> dict[str, Any]:
    panel_required(request)
    body = await request.json()
    try:
        return registration.command(str(body.get("command") or ""), str(body.get("value") or ""))
    except (RuntimeError, ValueError) as exc:
        raise registration_error(exc) from exc


@app.post("/api/register/stop")
def register_stop(request: Request) -> dict[str, Any]:
    panel_required(request)
    return registration.stop()


@app.get("/")
def index(request: Request) -> FileResponse:
    if auth.enabled and not auth.valid(request):
        return FileResponse(WEB_DIR / "login.html")
    return FileResponse(WEB_DIR / "index.html")


@app.get("/login.html")
def login_page() -> FileResponse:
    return FileResponse(WEB_DIR / "login.html")


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
