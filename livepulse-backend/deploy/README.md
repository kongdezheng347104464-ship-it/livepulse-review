# Deploy Environments

| Environment | DB | Redis | MinIO | Purpose |
|------------|------|-------|-------|---------|
| **dev** | AIMS shared | AIMS shared | Local (9002/9003) | Daily development |
| **staging** | Isolated container | Isolated container | Isolated | Manual testing |
| **prod** | Isolated container | Isolated container | Isolated | Production |

## Dev (`deploy/dev/`)

Start local dev:
```bash
# 1. MinIO (single container)
docker compose up -d

# 2. Backend
cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8004

# 3. Frontend (frontend_v2 — 当前主推,端口 3002)
cd frontend_v2 && npm run dev
```

Requires AIMS `postgres_dev` and `redis_dev` containers running.

## Staging (`deploy/staging/`)

Start fully isolated staging:
```bash
cp deploy/staging/.env.template .env.staging
# edit .env.staging with real credentials
docker compose -f docker-compose.standalone.yml --env-file .env.staging up -d
```

## Production (`deploy/prod/`)

```bash
cp deploy/prod/.env.template .env.prod
# ALL secrets must come from vault — NEVER hardcode
docker compose -f docker-compose.standalone.yml --env-file .env.prod up -d
```

## CI/CD

GitHub Actions (`./github/workflows/`):
- `backend-ci.yml` — ruff + pytest + alembic migration check
- `frontend-ci.yml` — npm ci + build
