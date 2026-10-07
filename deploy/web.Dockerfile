FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
COPY release_identity.json /app/release_identity.json
RUN npm run build
FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgles2 libegl1 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY . /app
COPY --from=frontend /app/frontend/dist /app/frontend/dist
CMD ["sh", "-c", "python -m alembic -c alembic.ini upgrade head && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*"]
