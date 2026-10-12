# Production deployment — A3 target

This directory ships the **production-shape** of LivePulse: full stack in
containers, TLS at the edge, secrets in a dedicated env file, no source
mounted live. It is designed so that the same `docker compose -f ...` command
works on:

1. **Your laptop** (with self-signed TLS) — for validation before pushing
2. **The阿里云 ECS host** (with Let's Encrypt) — actual production

---

## Local validation (no domain, no ECS)

```bash
cd deploy/prod

# 1. Secrets — copy template and fill local values
cp .env.production.example .env.production
$EDITOR .env.production
# At minimum, change every CHANGEME_* to a non-empty value. Aliyun keys
# can stay placeholder if you don't plan to actually trigger ASR/OCR
# during validation — pipeline will fail at those steps but the rest
# of the stack still proves out.

# 2. TLS — self-signed for localhost
./nginx/gen-self-signed.sh

# 3. Boot
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build

# 4. Wait for healthy (~30s for backend, ~90s for worker first time)
docker compose -f docker-compose.prod.yml --env-file .env.production ps

# 5. Smoke test
curl -ksf https://localhost/api/v1/health | jq
curl -ksf https://localhost/ -o /dev/null -w "frontend %{http_code}\n"

# Logs
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f backend
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f worker

# Teardown
docker compose -f docker-compose.prod.yml --env-file .env.production down

# Full teardown (including volumes — destroys all data)
docker compose -f docker-compose.prod.yml --env-file .env.production down -v
```

**Browser will warn** about the self-signed cert on `https://localhost`. Add
exception once and move on. The ECS deployment uses real Let's Encrypt.

---

## ECS deploy (after domain + ECS are ready)

### Prerequisites on ECS host (one-time)

```bash
# Ubuntu 22.04 LTS, root or sudo
apt-get update
apt-get install -y docker.io docker-compose-plugin certbot

# Open firewall for HTTP + HTTPS + SSH
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

### Push code

```bash
# From your laptop
DOMAIN=your-domain.com  # set this once
ECS=user@$DOMAIN
rsync -avz --exclude .venv --exclude node_modules --exclude _data \
    /Users/.../project/ $ECS:/opt/livepulse/
ssh $ECS
cd /opt/livepulse/deploy/prod
```

### TLS on ECS (Let's Encrypt)

```bash
# One-time: obtain initial cert via standalone challenge.
# nginx must NOT be running yet — certbot binds :80.
certbot certonly --standalone -d your-domain.com

# Wire the issued cert into our nginx folder
mkdir -p nginx/certs
ln -sf /etc/letsencrypt/live/your-domain.com/fullchain.pem nginx/certs/fullchain.pem
ln -sf /etc/letsencrypt/live/your-domain.com/privkey.pem nginx/certs/privkey.pem

# Auto-renewal: certbot installs a systemd timer by default; verify
systemctl list-timers | grep certbot

# To reload nginx after auto-renewal, add a deploy hook:
echo '#!/bin/sh
docker compose -f /opt/livepulse/deploy/prod/docker-compose.prod.yml \
  --env-file /opt/livepulse/deploy/prod/.env.production \
  exec nginx nginx -s reload' > /etc/letsencrypt/renewal-hooks/deploy/livepulse-reload.sh
chmod +x /etc/letsencrypt/renewal-hooks/deploy/livepulse-reload.sh
```

### Boot

```bash
cd /opt/livepulse/deploy/prod

# Secrets — create from template (never check this file into git)
cp .env.production.example .env.production
$EDITOR .env.production
# Fill EVERY CHANGEME. Generate strong values:
#   openssl rand -hex 32   → JWT_SECRET, API_KEY
#   pwgen 24 1             → POSTGRES_PASSWORD, MINIO_ROOT_PASSWORD, ADMIN_PASSWORD
# Reuse existing 阿里云 RAM keys (livestream-analyzer-kdz).

# First boot — builds backend + frontend images
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build

# Migrate DB (idempotent — safe to re-run on each deploy)
docker compose -f docker-compose.prod.yml --env-file .env.production \
    exec backend alembic upgrade head

# Verify
curl -fsS https://your-domain.com/api/v1/health | jq
```

### Systemd auto-start on host reboot

```bash
cat > /etc/systemd/system/livepulse.service <<'EOF'
[Unit]
Description=LivePulse production stack
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/livepulse/deploy/prod
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml --env-file .env.production up -d
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml --env-file .env.production down

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable livepulse.service
```

---

## Operations

### Update code (zero-downtime-ish)

```bash
# On laptop: rsync new code
rsync -avz --exclude .venv --exclude node_modules /Users/.../project/ $ECS:/opt/livepulse/

# On ECS
cd /opt/livepulse/deploy/prod

# Rebuild + restart only changed services (postgres/redis/minio stay up)
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build backend worker frontend

# Apply migrations (if any)
docker compose -f docker-compose.prod.yml --env-file .env.production \
    exec backend alembic upgrade head
```

The 30-60 second backend restart causes a brief 502 from nginx — acceptable
for M1 internal beta. M2 should add gunicorn rolling reload via SIGHUP.

### Backup

```bash
# DB snapshot (run on ECS — pg_dump inside the postgres container)
docker compose -f docker-compose.prod.yml --env-file .env.production \
    exec postgres pg_dump -U $POSTGRES_USER -Fc $POSTGRES_DB > backup-$(date +%Y%m%d).pgdump

# MinIO backup (when staying on MinIO; OSS migration removes this need)
docker compose -f docker-compose.prod.yml --env-file .env.production \
    exec minio mc mirror /data /backup
```

For M1 a daily cron + scp to laptop is sufficient. M2 should move to阿里云 OSS
+ object-storage-native snapshot.

### Logs

```bash
# Real-time
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f backend worker

# Last N lines per service
docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail 200 backend
```

For long-term storage, ship to Sentry (errors) + 阿里云 SLS (access logs) once
volume warrants it.

---

## Architecture diagram (text version)

```
                   ┌─────────┐
  https://...      │  user   │
        │          └────┬────┘
        ▼               │
  ┌───────────┐         │
  │ nginx :443│ ← TLS terminator + reverse proxy
  └─┬───────┬─┘
    │       │
   /api    /
    │       │
    ▼       ▼
  ┌─────┐ ┌──────────┐
  │ be  │ │ frontend │ ← static SPA (inner nginx for SPA fallback)
  │×4   │ └──────────┘
  └──┬──┘
     │
     ├── postgres (volume: pgdata)
     ├── redis    (volume: redisdata, AOF)
     └── minio    (volume: miniodata)

  ┌──────────┐
  │ worker   │ ← celery, reads from redis queue, writes to postgres
  │ ×2 procs │
  └──────────┘
```

---

## Known gaps (M2 backlog)

1. **No gunicorn rolling reload** — backend restart on deploy = brief 502
2. **MinIO not 阿里云 OSS** — migration planned post-M1
3. **No CDN** — frontend assets served straight from nginx
4. **No log shipping** — `docker logs` only, no central aggregation
5. **No backup automation** — manual `pg_dump + scp` until M2
6. **No staging environment** — pushes go straight to prod (low-traffic OK
   for M1, but plan a staging tier before M2 public launch)

These are all known and accepted for the internal beta scope. Revisit in
the roadmap before any public launch.

---

## Rollback

If a deploy bricks prod:

```bash
# 1. Quick: roll back backend / worker / frontend to previous image
ssh $ECS
cd /opt/livepulse
git log --oneline -5      # find last good commit
git checkout <SHA>
cd deploy/prod
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build backend worker frontend

# 2. DB rollback (if migration is at fault)
docker compose -f docker-compose.prod.yml --env-file .env.production \
    exec backend alembic downgrade -1

# 3. Restore from last backup (worst case)
docker compose -f docker-compose.prod.yml --env-file .env.production \
    exec -T postgres pg_restore -U $POSTGRES_USER -d $POSTGRES_DB --clean < backup-YYYYMMDD.pgdump
```
