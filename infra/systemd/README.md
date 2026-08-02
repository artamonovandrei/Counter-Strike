# Running without Docker

Use this if you would rather manage the process directly, or if Docker isn't available on
the host. The layout assumes the repository is checked out at `/srv/webstrike`.

## 1. User and code

```bash
sudo useradd --system --home /srv/webstrike --shell /usr/sbin/nologin webstrike
sudo git clone <your-repo-url> /srv/webstrike
cd /srv/webstrike
sudo cp .env.example .env    # edit DOMAIN, ACME_EMAIL, CORS_ORIGINS
sudo chmod 600 .env
```

## 2. Backend

```bash
cd /srv/webstrike/backend
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo .venv/bin/python -m app.scripts.gen_map
sudo .venv/bin/python -m app.scripts.gen_nav alley
sudo chown -R webstrike:webstrike /srv/webstrike
```

Note the unit file binds uvicorn to `127.0.0.1`, not `0.0.0.0` — the only thing that
should reach it is the local reverse proxy.

## 3. Client

```bash
cd /srv/webstrike/frontend
npm ci
npm run build          # outputs frontend/dist
```

## 4. Caddy

```bash
sudo apt-get install -y caddy
sudo cp /srv/webstrike/infra/Caddyfile /etc/caddy/Caddyfile
```

Then edit `/etc/caddy/Caddyfile`:

- replace `{$DOMAIN::80}` with your hostname (e.g. `webstrike.example.com`)
- replace `root * /srv/www` with `root * /srv/webstrike/frontend/dist`
- replace both `reverse_proxy backend:8000` with `reverse_proxy 127.0.0.1:8000`

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

## 5. Start

```bash
sudo cp /srv/webstrike/infra/systemd/webstrike-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now webstrike-backend
sudo systemctl status webstrike-backend
journalctl -u webstrike-backend -f
```

## 6. Verify

```bash
curl -s https://your-domain/api/health
curl -s https://your-domain/api/version
```

## Updating

```bash
cd /srv/webstrike
sudo -u webstrike git pull --ff-only
cd frontend && sudo -u webstrike npm ci && sudo -u webstrike npm run build
sudo systemctl restart webstrike-backend
```

Restarting drops every connected player — there is no state migration, and a match in
progress is lost. Deploy between rounds if that matters to you.

## Notes

- `ProtectSystem=strict` plus `ReadOnlyPaths=/srv/webstrike` means the service cannot
  write anywhere. That is correct: the game keeps no state on disk. If you add match
  logging to SQLite, you will need a `ReadWritePaths=` entry for that file.
- Don't raise `--workers`. A room lives in one process's memory, so a second worker
  creates rooms the first cannot see. Scale with Redis and separate units instead.
