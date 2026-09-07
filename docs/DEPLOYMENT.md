# Deployment Guide — English Learning API

Target: single Ubuntu 22.04/24.04 server (VPS or on-prem box). Stack:
Django 5.1 + DRF + Channels (ASGI, WebSocket support) served by Daphne,
PostgreSQL database, Nginx as reverse proxy/TLS terminator, systemd to
keep everything running.

Everything below assumes a fresh server and a non-root sudo user —
referred to as `deploy` below. Nothing runs as a separate system/service
account; the app lives in `deploy`'s home directory and runs as `deploy`.
Replace `deploy`, `english_learning_api`, and the domain placeholder with
your real values.

---

## 1. System packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip \
    postgresql postgresql-contrib \
    nginx git build-essential libpq-dev
```

Optional (only if you enable the Redis channel layer, see §6):

```bash
sudo apt install -y redis-server
sudo systemctl enable --now redis-server
```

Required only if you run the **real** pronunciation-assessment engine
(`PRONUNCIATION_BACKEND=azure`, see §12) — `ffmpeg` transcodes uploaded audio to
WAV PCM 16 kHz mono. The default `mock` engine needs neither `ffmpeg` nor Azure:

```bash
sudo apt install -y ffmpeg   # provides both ffmpeg and ffprobe
```

---

## 2. Database setup (PostgreSQL)

Install starts the `postgresql` service automatically. Make sure it's
enabled to survive reboots:

```bash
sudo systemctl enable --now postgresql
sudo systemctl status postgresql   # should show "active (running)"
```

Create the database and a dedicated app user (do **not** use the
`postgres` superuser role for the app):

```bash
sudo -u postgres psql <<'SQL'
CREATE USER english_learning_api WITH PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
CREATE DATABASE english_learning_api OWNER english_learning_api;
ALTER ROLE english_learning_api SET client_encoding TO 'utf8';
ALTER ROLE english_learning_api SET timezone TO 'UTC';
SQL
```

By default Postgres only listens on `localhost` (`postgresql.conf` →
`listen_addresses = 'localhost'`) and `pg_hba.conf` restricts connections
to local `peer`/`md5` auth — that's correct for this setup since Django
and Postgres run on the same box. Don't expose port 5432 externally
unless you have a specific reason to; if you do, restrict it with
`ufw`/security group rules to known IPs only.

Verify you can connect with the app credentials:

```bash
psql "postgresql://english_learning_api:CHANGE_ME_STRONG_PASSWORD@localhost:5432/english_learning_api" -c '\conninfo'
```

### Backups

At minimum, a daily dump via cron:

```bash
sudo -u postgres crontab -e
# add:
0 2 * * * pg_dump -U english_learning_api english_learning_api | gzip > /var/backups/english_learning_api_$(date +\%F).sql.gz
```

Prune old dumps on whatever retention policy fits (e.g. keep 14 days).

---

## 3. Clone the app

```bash
git clone <repo-url> ~/english_learning_api
cd ~/english_learning_api
```

Create the venv and install dependencies. `venv/` is already in
`.gitignore`, so keeping it inside the project dir is safe:

```bash
python3 -m venv ~/english_learning_api/venv
~/english_learning_api/venv/bin/pip install --upgrade pip
~/english_learning_api/venv/bin/pip install -r requirements.txt
```

---

## 4. Environment variables

The app reads config from a `.env` file in the project root (loaded via
`python-dotenv`, see `config/settings.py`). `.env.example` in the repo root is
the annotated master list — copy it and fill in the blanks:

```bash
cp .env.example .env
chmod 600 .env        # holds the DB password and the Django secret key
```

The four that a production deploy **must** set:

```dotenv
DATABASE_URL=postgresql://english_learning_api:CHANGE_ME_STRONG_PASSWORD@localhost:5432/english_learning_api
SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(64))">
DEBUG=false
ALLOWED_HOSTS=your-domain.example.com
CORS_ALLOWED_ORIGINS=https://app.example.com    # where the web client is served
```

### `DEBUG=false` is the production switch

Setting it turns on, in one move: the HTTPS redirect, HSTS (one year),
secure/HTTP-only session and CSRF cookies, `SECURE_PROXY_SSL_HEADER` for the
Nginx hop, and connection reuse to Postgres. Each is individually overridable
by env — see the "SECURITY OVERRIDES" block in `.env.example` — for the cases
where TLS terminates somewhere this app cannot see.

It also makes two misconfigurations fatal at boot rather than silent:

| Mistake | What happens |
|---|---|
| `SECRET_KEY` unset or still the dev value | `ImproperlyConfigured` on startup |
| `ALLOWED_HOSTS=*` | `ImproperlyConfigured` on startup |

`ALLOWED_HOSTS` defaults to localhost only. A box that forgets it does not
quietly accept every `Host` header; it returns `DisallowedHost` (400) on the
first request, which is loud and obvious rather than exploitable.

Rotating `SECRET_KEY` invalidates every issued JWT, every session, and every
signed media link (§12) — plan it as a logout-everyone event.

---

## 5. Migrations, static files, seed data

From the project root, using the venv's Python:

```bash
cd ~/english_learning_api
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser
```

Optional — load the demo dataset (wipes existing rows first, do not run
against real user data):

```bash
venv/bin/python manage.py seed_demo
```

`STATIC_ROOT` is set in `config/settings.py` (default
`<project>/staticfiles`, override with the `STATIC_ROOT` env var), so
`collectstatic` runs without extra configuration. WhiteNoise serves what it
writes directly from the app process — no Nginx alias required (§8).

Re-run `migrate` (and `collectstatic` if templates/static assets
changed) after every deploy.

### Verify the configuration before starting anything

```bash
venv/bin/python manage.py check --deploy
```

With the `.env` above this reports one remaining warning,
`security.W021` (HSTS preload), which is deliberate: preload submission is
close to irreversible, so it stays opt-in via `SECURE_HSTS_PRELOAD=true`.
Anything else in that output is a real finding — fix it before going live.

---

## 6. Channel layer (WebSockets)

`core/consumers.py` + `config/asgi.py` use Django Channels. With no
`REDIS_URL` set, `CHANNEL_LAYERS` falls back to `InMemoryChannelLayer`
(`config/settings.py`) — fine for a single Daphne process, but it means
WebSocket group messages don't cross process boundaries. If you plan to
run more than one Daphne worker (recommended for real traffic), install
Redis (§1) and set `REDIS_URL=redis://localhost:6379/0` in `.env`.

---

## 7. Application server (Daphne) via systemd

Daphne is already in `requirements.txt` and serves the ASGI app
(HTTP + WebSocket) defined in `config/asgi.py`. Create
`/etc/systemd/system/english-learning-api.service`. systemd doesn't
expand `~`, so use the real home path (`/home/deploy/...` below —
swap in your actual username):

```ini
[Unit]
Description=English Learning API (Daphne ASGI)
After=network.target postgresql.service

[Service]
User=deploy
Group=deploy
WorkingDirectory=/home/deploy/english_learning_api
EnvironmentFile=/home/deploy/english_learning_api/.env
ExecStart=/home/deploy/english_learning_api/venv/bin/daphne \
    -b 127.0.0.1 -p 8001 \
    --proxy-headers \
    config.asgi:application
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`EnvironmentFile` only picks up simple `KEY=VALUE` lines — it's a
belt-and-suspenders duplicate of the `.env` that `dotenv` already loads
inside the app; harmless to keep, but not strictly required if
`python-dotenv` is working. Keep the same 600 permissions concern in
mind if you do use it.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now english-learning-api
sudo systemctl status english-learning-api
sudo journalctl -u english-learning-api -f   # tail logs
```

Bind Daphne to `127.0.0.1` only — Nginx in front handles the public
interface and TLS. `--proxy-headers` makes Daphne read `X-Forwarded-For` and
`X-Forwarded-Proto` from Nginx, so logs record the real client IP rather than
`127.0.0.1` on every line.

One Daphne process is single-threaded for async work and fine for modest
traffic. To run more, put several units behind the Nginx upstream on different
ports **and set `REDIS_URL`** (§6) — otherwise WebSocket broadcasts only reach
clients that happen to share a process.

---

## 8. Nginx reverse proxy

`/etc/nginx/sites-available/english-learning-api`:

```nginx
upstream english_learning_api {
    server 127.0.0.1:8001;
}

server {
    listen 80;
    server_name your-domain.example.com;

    # Uploaded audio, streamed by Nginx only after Django has authorised the
    # request (see §12). `internal` means it is unreachable from outside —
    # the only way in is Django's X-Accel-Redirect header.
    location /protected-media/ {
        internal;
        alias /home/deploy/english_learning_api/media/;
    }

    # WebSocket + HTTP both go through Daphne; Channels needs the
    # Upgrade/Connection headers forwarded.
    location / {
        proxy_pass http://english_learning_api;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/english-learning-api /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Static files

There is deliberately no `/static/` block above. WhiteNoise
(`config/settings.py` middleware) serves `STATIC_ROOT` from the app process
with compression and cache headers, which avoids the usual permission trap:
home directories are `0750`, so Nginx running as `www-data` cannot traverse
into `/home/deploy/...` and every `/static/` URL 404s until someone loosens
permissions on a home directory.

If you would rather Nginx serve them, add the alias back **and** grant the
traversal it needs — but there is no performance reason to at this scale:

```nginx
location /static/ { alias /home/deploy/english_learning_api/staticfiles/; }
```

```bash
sudo chmod o+x /home/deploy   # only needed for the alias approach
```

The `/protected-media/` block, by contrast, **is** required if you enable
`MEDIA_X_ACCEL_REDIRECT=true` (§12). Its `alias` must point at `MEDIA_ROOT`
and must keep `internal;`, which is what stops anyone requesting it directly.

### Timeouts

Nginx closes an upstream response after 60s by default. Requests that call a
slow AI provider can exceed that — see §13.

### TLS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.example.com
```

Certbot rewrites the Nginx config to add the `listen 443 ssl` block and
sets up auto-renewal via a systemd timer — verify with
`sudo systemctl list-timers | grep certbot`.

---

## 9. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'   # 80 + 443
sudo ufw enable
```

Postgres (5432), Redis (6379), and Daphne (8001) should NOT be exposed
— they're only reachable from `localhost`, which the above rules
already reflect (nothing opens those ports externally).

---

## 10. Deploy checklist for updates

```bash
cd ~/english_learning_api
git pull
venv/bin/pip install -r requirements.txt
venv/bin/python manage.py check --deploy     # config sanity, before anything changes
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart english-learning-api
curl -fsS http://127.0.0.1:8001/health/      # non-zero exit = roll back
```

`migrate` is not automatically reversible. Take a dump first
(`pg_dump` as in §2) on any release that adds or alters columns.

## 11. Health check

```bash
curl -s http://127.0.0.1:8001/health/     # {"status":"ok","database":"ok","db_latency_ms":0.9}
curl -s http://127.0.0.1:8001/            # {"status":"ok","service":"english-learning-api"}
curl -s https://your-domain.example.com/api/admin/health/   # admin-only; adds entity counts
```

Point the load balancer and the uptime monitor at **`GET /health/`**. It is
anonymous, runs `SELECT 1`, and returns **503** when the database is
unreachable, so a probe failure means something a restart might fix. It is also
listed in `SECURE_REDIRECT_EXEMPT`, so it answers over plain HTTP on the
private interface instead of 301-ing to HTTPS.

`GET /` is a static string. It proves the process is listening and nothing
more — a database outage still returns `ok` there, which is why it is the
wrong probe to alert on.

---

## 12. Media uploads & pronunciation practice

The pronunciation-practice feature (`docs/research/04-pronunciation-practice.md`)
introduces the platform's first real file uploads
(`PronunciationAttempt.audio_file`).

**Storage.** `config/settings.py` sets `MEDIA_ROOT` (default `<project>/media`,
overridable via the `MEDIA_ROOT` env var) and `MEDIA_URL=/media/`. Ensure the
directory exists and is writable by the `deploy` user:

```bash
mkdir -p ~/english_learning_api/media && chmod 750 ~/english_learning_api/media
```

Put `MEDIA_ROOT` somewhere that survives a release, e.g.
`MEDIA_ROOT=/var/lib/english-learning-api/media`. If it sits inside the
checkout and you ever deploy by replacing that directory, every recording
uploaded so far goes with it.

**Serving.** Do **not** add a public `alias` for `/media/`. These files are
student voice recordings, and mock-test uploads keep the browser's filename
(`answer.webm`), so a public alias is both a privacy problem and guessable.

`core/media.py` serves them instead, in every environment including
`DEBUG=false`. A stored file is handed to clients as
`/media/<path>?t=<signed token>`: the token carries the path, is signed with
`SECRET_KEY`, and expires after `MEDIA_URL_TTL` (default 7 days). That works in
a plain `<audio src>` element — which cannot send an `Authorization` header —
while a leaked link stops working. `ProtectedMediaView` accepts a valid token
or an authenticated session (so Django admin links stay clickable) and refuses
everything else with 403. It is the same shape as the S3 presigned URLs this is
meant to become, so moving `STORAGES["default"]` to `django-storages` later
does not change any client.

By default Django streams the bytes. With Nginx in front, set:

```dotenv
MEDIA_X_ACCEL_REDIRECT=true
MEDIA_X_ACCEL_PREFIX=/protected-media/
```

Django then authorises the request and returns an empty response carrying
`X-Accel-Redirect`; Nginx streams the file from the `internal` location in §8
and the worker is released immediately. This matters on ASGI: a long download
otherwise occupies a worker for its whole duration.

**Client note.** `SubmissionSerializer` exposes the signed link as `audio_url`,
alongside the raw stored `audio_recording_url`. Clients should render
`audio_url`; the raw field is kept because it is what the upload flow writes.
`POST /api/media/audio` returns both: `url` (signed, for playback) and `path`
(unsigned, the value to persist — a stored signature would expire).

**Assessment engine.** `PRONUNCIATION_BACKEND` (env, default `mock`) selects the
engine, exactly like `AI_BACKEND`:

```dotenv
# Optional pronunciation engine (defaults to "mock" — deterministic, offline)
# PRONUNCIATION_BACKEND=azure
# AZURE_SPEECH_KEY=<key>
# AZURE_SPEECH_REGION=<region, e.g. southeastasia>
```

`mock` runs fully offline with deterministic scores and needs no `ffmpeg`.
`azure` requires `ffmpeg` on PATH (§1) plus the Azure env keys above; the backend
fails loudly (`ImproperlyConfigured`) if they are missing, so a misconfigured
deploy never silently mis-scores.

**Retention.** Attempt audio accumulates under `MEDIA_ROOT`. Decide a retention
policy (e.g. prune files older than N days) — left open in the research doc.

---

## 13. AI providers in production

Every AI switch defaults to a deterministic offline mock, so the platform runs
with no provider key at all. Turning one on (`AI_BACKEND`, `AI_ASSIST_BACKEND`,
`PRONUNCIATION_BACKEND` — see `.env.example`) makes a **synchronous** outbound
call inside the request, which has two consequences worth planning for.

**Latency vs. proxy timeouts.** Text tasks route through `core/ai/llm.py` via
`AI_GRADING_MODEL` / `AI_ASSIST_MODEL`, written `provider[:model]`:

| Provider | Typical first-token latency | Suitable for |
|---|---|---|
| `gemini:gemini-3.6-flash` | seconds | anything a user waits on |
| `nvidia:moonshotai/kimi-k3` | 2-3 min (NIM queue) | batch / background only |
| `nvidia:deepseek-ai/deepseek-v4-pro-0813` | 2-3 min (NIM queue) | batch / background only |

Nginx gives up on an upstream after 60s by default, so an NVIDIA-routed request
returns 504 to the browser while the Daphne worker stays blocked for minutes.
If you route to NVIDIA deliberately, raise the proxy timeout to match
`NVIDIA_TIMEOUT` **and** add workers, or move grading to a background job
first:

```nginx
proxy_read_timeout 300s;
proxy_send_timeout 300s;
```

Audio tasks (speaking grading, pronunciation, transcription) always go to
Gemini regardless of routing — the NVIDIA-hosted models are text-only.

**Failure handling.** `AI_FALLBACK_PROVIDER` retries once on a second provider
when the first fails, and every response records the `engine` that actually
answered. Provider outages surface as **502** (`AI service unavailable`), a
missing key or bad input as **400** — neither takes the rest of the API down.

**Cost.** These are per-request paid calls with no quota ceiling in the app.
Keep the throttles in `core/views.py` in mind and watch the provider dashboard
after enabling anything.

---

## 14. Pre-flight checklist

Run through this once before the first public request:

- [ ] `.env` exists, `chmod 600`, and `DEBUG=false`
- [ ] `SECRET_KEY` is unique to this environment (not the dev value, not staging's)
- [ ] `ALLOWED_HOSTS` lists the real hostnames; `CORS_ALLOWED_ORIGINS` lists the web client
- [ ] `manage.py check --deploy` is clean apart from `security.W021`
- [ ] `migrate` and `collectstatic` have run; `staticfiles/` is populated
- [ ] TLS works and `https://.../admin/` loads its CSS (proves static + CSRF origins)
- [ ] `curl -f https://.../health/` returns 200 and the monitor is pointed at it
- [ ] `MEDIA_ROOT` is outside the release directory and is writable by the app user
- [ ] A recording plays back for a student, and the same URL 403s once you strip `?t=`
- [ ] `pg_dump` backup cron is installed and one restore has been rehearsed
- [ ] `REDIS_URL` is set if more than one worker process runs
- [ ] `journalctl -u english-learning-api` shows the app's own log lines
