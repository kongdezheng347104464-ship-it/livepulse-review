# dev environment — shares AIMS PostgreSQL/Redis, runs MinIO + backend + frontend locally
#
# Startup:
#   docker compose up -d                    # starts MinIO only
#   ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8004 &
#   cd ../frontend && npm run dev &

# Requirements:
# - AIMS postgres_dev container running on port 5432
# - AIMS redis_dev container running on port 6379
# - .venv with all Python dependencies installed
# - Node.js for frontend

echo "Starting MinIO..."
docker compose up -d

echo "Waiting for MinIO..."
sleep 3

echo "Starting backend (FastAPI :8004)..."
cd backend
../.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8004 &
BACKEND_PID=$!

echo "Starting frontend (React :3002)..."
cd frontend_v2
npm run dev &
FRONTEND_PID=$!

echo ""
echo "--- Dev environment ready ---"
echo "Frontend: http://localhost:3001"
echo "Backend:  http://localhost:8004"
echo "MinIO:    http://localhost:9003 (console)"
echo ""
echo "Stop with: kill $BACKEND_PID $FRONTEND_PID"
