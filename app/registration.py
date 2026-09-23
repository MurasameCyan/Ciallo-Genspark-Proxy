from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .accounts import AccountStore
from .config_store import ConfigStore, DATA_DIR, ROOT_DIR
from .mail_service import CloudflareMailClient


@dataclass
class RegistrationJob:
    job_id: str
    seq: int
    email: str = ""
    proxy: str = ""
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    error: str = ""
    code: str = ""
    mailbox_token: str = ""
    send_code_requested: bool = False
    code_started: bool = False
    stop_requested: bool = False
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    profile_dir: Path | None = field(default=None, repr=False)
    out_dir: Path | None = field(default=None, repr=False)
    cmd_file: Path | None = field(default=None, repr=False)
    cookie_file: Path | None = field(default=None, repr=False)
    log_text: str = field(default="", repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "seq": self.seq,
            "email": self.email,
            "proxy": self.proxy,
            "state": self.state,
            "created_at": self.created_at,
            "error": self.error,
            "code_started": self.code_started,
        }


class RegistrationService:
    def __init__(
        self,
        config: ConfigStore,
        accounts: AccountStore,
        log: Callable[[str, str], None],
        root_dir: Path | str = ROOT_DIR,
        data_dir: Path | str = DATA_DIR,
    ):
        self.config = config
        self.accounts = accounts
        self.log = log
        self.root_dir = Path(root_dir)
        self.data_dir = Path(data_dir)
        self.jobs_dir = self.data_dir / "register"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._job: RegistrationJob | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if not self._job:
                return {"running": False, "job_id": "", "state": "idle"}
            value = self._job.snapshot()
            value["running"] = self._job.state not in {"succeeded", "failed", "stopped"}
            return value

    def start(self, seq: int | None = None, email: str = "", proxy: str = "") -> dict[str, Any]:
        with self._lock:
            if self._job and self._job.state not in {"succeeded", "failed", "stopped"}:
                raise RuntimeError("已有注册任务在运行")
            chosen_seq = int(seq) if seq is not None else self.accounts.next_seq()
            job_id = uuid.uuid4().hex[:12]
            job_root = self.jobs_dir / job_id
            job = RegistrationJob(
                job_id=job_id,
                seq=chosen_seq,
                email=str(email or "").strip(),
                proxy=str(proxy or "").strip(),
                profile_dir=job_root / "profile",
                out_dir=job_root / "out",
                cmd_file=job_root / "commands.txt",
                cookie_file=job_root / f"gs_cookies{chosen_seq}.json",
            )
            job_root.mkdir(parents=True, exist_ok=True)
            job.profile_dir.mkdir(parents=True, exist_ok=True)
            job.out_dir.mkdir(parents=True, exist_ok=True)
            self._job = job
            thread = threading.Thread(target=self._run, args=(job,), name=f"register-{job_id}", daemon=True)
            job.thread = thread
            thread.start()
            return job.snapshot()

    def command(self, command: str, value: str = "") -> dict[str, Any]:
        with self._lock:
            job = self._job
            if not job or job.state in {"succeeded", "failed", "stopped"}:
                raise RuntimeError("当前没有运行中的注册任务")
            name = str(command or "").strip().lower()
            value = str(value or "").strip()
            if name in {"captcha", "code", "type"} and not value:
                raise ValueError(f"命令 {name} 需要 value")
            if name == "captcha":
                line = f"captcha={value}"
            elif name == "code":
                job.code = value.replace("-", "").replace(" ", "").upper()
                line = ""
            elif name == "type":
                line = f"type={value}"
            elif name in {"sendcode", "password", "create", "extract", "quit"}:
                line = name
            elif name == "click":
                line = f"click={value}"
            else:
                raise ValueError("不支持的注册命令")
            if name == "sendcode":
                job.send_code_requested = True
            if line:
                self._write_command(job, line)
            self.log("info", f"[register] command={name}")
            return {"ok": True, "command": name}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if not job:
                return {"ok": True, "state": "idle"}
            job.stop_requested = True
            job.state = "stopped"
            if job.cmd_file:
                self._write_command(job, "quit")
            proc = job.process
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
            self.log("warn", f"[register] stopped job={job.job_id}")
            return {"ok": True, "state": job.state}

    def _run(self, job: RegistrationJob) -> None:
        try:
            job.state = "creating_mailbox" if not job.email else "starting_browser"
            if not job.email:
                client = CloudflareMailClient(self.config.load(), log=lambda text: self.log("info", text))
                mailbox = client.create_temp_email()
                job.email = mailbox["address"]
                job.mailbox_token = mailbox["jwt"]
                self.log("ok", f"[mail] created address={job.email} domain={mailbox.get('domain', '')}")
            else:
                self.log("warn", "[mail] 使用手动邮箱，验证码需要通过 UI 的 code 命令提交")
            self._launch_driver(job)
            self._drive_flow(job)
        except Exception as exc:
            job.state = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            self.log("error", f"[register] {job.error}")
            self._terminate(job)

    def _launch_driver(self, job: RegistrationJob) -> None:
        assert job.profile_dir and job.out_dir and job.cmd_file
        job.profile_dir.mkdir(parents=True, exist_ok=True)
        job.out_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update(
            {
                "GS_BASE_DIR": str(self.root_dir),
                "GS_PROFILE": str(job.profile_dir),
                "GS_CMDFILE": str(job.cmd_file),
                "GS_OUT": str(job.out_dir),
                "GS_LOG": str(job.out_dir / "driver.log"),
                "GS_EMAIL": job.email,
                "GS_PROXY": job.proxy,
                "PYTHONUNBUFFERED": "1",
            }
        )
        driver = self.root_dir / "gs_reg_driver.py"
        if not driver.is_file():
            raise FileNotFoundError(f"缺少注册浏览器驱动: {driver}")
        job.process = subprocess.Popen(
            [sys.executable, str(driver), "open"],
            cwd=str(self.root_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.log("info", f"[register] browser started seq={job.seq} email={job.email}")
        threading.Thread(target=self._read_process_output, args=(job,), daemon=True).start()

    def _read_process_output(self, job: RegistrationJob) -> None:
        proc = job.process
        if not proc or not proc.stdout:
            return
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            with self._lock:
                job.log_text = (job.log_text + line + "\n")[-12000:]
                if "fill:#email] ok" in line:
                    job.state = "waiting_for_captcha"
                elif "after_sendcode" in line:
                    job.state = "waiting_code"
                elif "state:after_create]" in line and "genspark.ai" in line:
                    job.state = "exporting_cookie"
            self.log("info", f"[browser] {line}")
        with self._lock:
            if job.state not in {"succeeded", "failed", "stopped", "exporting_cookie"} and not job.stop_requested:
                job.state = "browser_exited"

    def _drive_flow(self, job: RegistrationJob) -> None:
        deadline = time.time() + 1800
        client = CloudflareMailClient(self.config.load(), log=lambda text: self.log("info", text)) if job.mailbox_token else None
        while time.time() < deadline:
            if job.stop_requested or job.state == "stopped":
                self._terminate(job)
                return
            if job.process and job.process.poll() is not None and job.state not in {"exporting_cookie", "succeeded"}:
                raise RuntimeError("浏览器进程提前退出")
            if job.send_code_requested and not job.code_started:
                job.code_started = True
                job.state = "sending_code"
                time.sleep(8)
                if client and job.mailbox_token:
                    job.state = "waiting_code"
                    code = client.wait_for_code(job.mailbox_token, job.email, timeout=int(self.config.load().get("mail_timeout", 240)))
                    if not code:
                        raise RuntimeError("邮箱验证码轮询超时")
                    job.code = code
                else:
                    job.state = "waiting_code_manual"
                    while not job.code and time.time() < deadline and not job.stop_requested:
                        time.sleep(0.5)
                    if not job.code:
                        raise RuntimeError("未提交验证码")
                self._write_command(job, f"type=#emailVerificationCode|{job.code}")
                time.sleep(2)
                self._write_command(job, "click=Verify code")
                time.sleep(28)
                self._write_command(job, "password")
                time.sleep(4)
                self._write_command(job, "create")
                job.state = "creating_account"
            if "state:after_create]" in job.log_text and "genspark.ai" in job.log_text:
                self._export_cookie(job)
                return
            time.sleep(0.5)
        raise TimeoutError("注册任务超过 30 分钟")

    def _export_cookie(self, job: RegistrationJob) -> None:
        if job.state == "succeeded":
            return
        assert job.out_dir and job.cookie_file
        self._write_command(job, "quit")
        time.sleep(8)
        env = os.environ.copy()
        env.update(
            {
                "GS_PROFILE": str(job.profile_dir),
                "GS_EMAIL": job.email,
                "GS_ACCOUNT": str(job.seq),
                "GS_COOKIE_FILE": str(job.cookie_file),
                "GS_PROXY": job.proxy,
            }
        )
        template = self.root_dir / "gs_export_template.py"
        if not template.is_file():
            raise FileNotFoundError(f"缺少 Cookie 导出脚本: {template}")
        result = subprocess.run(
            [sys.executable, str(template)],
            cwd=str(self.root_dir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        for line in (result.stdout + result.stderr).splitlines()[-8:]:
            self.log("info", f"[export] {line}")
        if result.returncode != 0 or not job.cookie_file.is_file():
            raise RuntimeError("Cookie 导出失败")
        self.accounts.upsert(
            {
                "seq": job.seq,
                "email": job.email,
                "cookie_file": str(job.cookie_file),
                "proxy": job.proxy,
                "status": "active",
                "note": f"{time.strftime('%Y-%m-%d')} UI 注册",
            }
        )
        job.state = "succeeded"
        self.log("ok", f"[register] succeeded seq={job.seq} email={job.email}")

    def _write_command(self, job: RegistrationJob, line: str) -> None:
        if not job.cmd_file:
            return
        tmp = job.cmd_file.with_suffix(".tmp")
        tmp.write_text(line.rstrip("\n") + "\n", encoding="utf-8")
        os.replace(tmp, job.cmd_file)

    def _terminate(self, job: RegistrationJob) -> None:
        proc = job.process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
