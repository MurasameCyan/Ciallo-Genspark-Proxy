# Ciallo Genspark Proxy

OpenAI-compatible Genspark web-session bridge with a local control plane, account rotation, browser registration orchestration (manual or 2captcha-assisted), and Cloudflare Temp Email integration.

> Unofficial software. Use only with accounts and infrastructure you own, and follow the upstream service terms and applicable law. CAPTCHA solving is delegated to the 2captcha service you configure; no access control is bypassed.

## Features

- OpenAI-compatible `GET /v1/models` and `POST /v1/chat/completions`, including SSE streaming.
- Account pool with per-account cookie files, optional per-account proxy, round-robin selection, cooldowns, and health counters.
- Chinese responsive dashboard modeled after the Ciallo Zen Proxy console: overview, accounts, registration controls, mailbox configuration, and SSE logs.
- Cloudflare Temp Email API: admin/public auth modes, configurable create path, mailbox JWT polling, Genspark `XXX-XXX` and six-digit code extraction.
- Mail domain pool: `mail_domain` fallback plus `mail_domains` / `MAIL_DOMAINS`, round-robin or random selection, hot configuration persistence.
- CAPTCHA automation: optional 2captcha solver (`TWOCAPTCHA_KEY`) with automatic retry on rejected images, driven end-to-end by the orchestrator; manual takeover remains available.
- Docker image published for `linux/amd64` and `linux/arm64` by GitHub Actions.

## Quick start with GHCR

```bash
cp .env.example .env
# Set PANEL_PASS and the Cloudflare Temp Email Worker values in .env.
docker compose pull
docker compose up -d
```

Open `http://127.0.0.1:8899/`. If `PANEL_PASS` is set, use `PANEL_USER` (default `admin`) and that password.

The host `./data` directory stores `accounts.json`, configuration, cookies, browser profiles, and registration logs. Treat it as sensitive. Do not publish it or expose the dashboard without a strong password and a reverse proxy/firewall.

## Cloudflare Temp Email

Deploy a compatible Worker/API such as `dreamhunter2333/cloudflare_temp_email` first. Configure the Worker root URL, not a Pages frontend URL:

```dotenv
MAIL_API_BASE=https://your-worker.example
MAIL_ADMIN_AUTH=your-admin-password
MAIL_AUTH_MODE=x-admin-auth
MAIL_DOMAIN=example.com
MAIL_DOMAINS=example.com,example.net
MAIL_DOMAIN_MODE=round_robin
```

The default admin create request is `POST /admin/new_address` with `x-admin-auth`. Set `MAIL_AUTH_MODE=none` to use the public `POST /api/new_address` route; this changes the default path automatically. `MAIL_CREATE_PATH` overrides the path. The dashboard can save these values without rebuilding the image.

The domain pool is parsed from commas or newlines, strips `@`, normalizes case, filters invalid DNS labels, and de-duplicates while preserving order. With `round_robin`, successive registrations use the next configured domain; `random` uses a random pool member.

## Account pool

Copy the template and put exported cookie JSON files under `data/`:

```bash
cp accounts.example.json data/accounts.json
```

Example entry:

```json
{
  "seq": 1,
  "email": "you@example.com",
  "cookie_file": "/data/cookies1.json",
  "proxy": "",
  "status": "active"
}
```

For manual login/export outside the container:

```bash
python -m pip install -r requirements.txt
python gs_login.py
```

The API process never launches a browser for chat requests. It only loads exported cookies and forwards HTTP requests.

## Registration flow

Use the dashboard **注册编排** card:

1. Start a task. If the email is blank, the service creates a Cloudflare mailbox from the configured pool.
2. A persistent browser opens the Genspark signup form and fills the mailbox.
3. CAPTCHA handling: with `TWOCAPTCHA_KEY` set and 自动解验证码 enabled, the orchestrator sends the driver `autocap`, which reads the challenge image, submits it to 2captcha, fills the answer, and retries up to five times with a refreshed image. Without a key, solve it yourself and submit the value in the dashboard.
4. The mailbox is polled directly and the latest matching code is submitted, then the generated password is filled and the account created.
5. Cookies are exported with `gs_export.py`; the pool entry records email, password, `cogen_id`, and cookie file. The generated password is read back from the driver log, so account recovery works without the browser.

Per-job commands available from the dashboard: `autocap`, `capimg`, `caprefresh`, `state`, `pages`, `sendcode`, `password`, `create`, `extract`, plus valued `captcha`, `code`, `click`, `type`, `goto`, and `fill` (`fill` takes `selector<TAB>text`).

```dotenv
REGISTER_AUTO_SOLVE=1
TWOCAPTCHA_KEY=your-2captcha-key
TWOCAPTCHA_PROXY=http://user:pass@proxy:port   # optional solver egress
```

A manually supplied email can be used when the temporary mailbox backend is unavailable; then submit the received code through the dashboard. Stop a task before deleting its data directory.

## API authentication

Set `API_KEY` to protect `/v1/*` with either `Authorization: Bearer <key>` or `x-api-key: <key>`. Dashboard routes use the cookie session from `PANEL_USER` / `PANEL_PASS` and are not the same credential as `API_KEY`.

## Local development

```bash
python -m venv .venv
.venv/Scripts/activate       # Windows
# source .venv/bin/activate  # Linux/macOS
python -m pip install -r requirements.txt
python -m pytest -q
python main.py
```

The base bridge scripts are retained for direct use, while `main.py` is the container/control-plane entrypoint.

## Docker build and tags

GitHub Actions builds and pushes:

- `ghcr.io/<owner>/ciallo-genspark-proxy:latest` for the configured default branch;
- `sha-<short>` for each build;
- `vX.Y.Z` and `X.Y` for version tags.

The workflow uses Buildx + QEMU and publishes a manifest for `linux/amd64,linux/arm64`. For a local build:

```bash
docker build --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) -t ciallo-genspark-proxy .
docker compose up -d
```

## License

MIT. See [LICENSE](LICENSE) and [DISCLAIMER.md](DISCLAIMER.md).
