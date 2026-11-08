# Public mirror of the AggiePulse app (Render, Fly, Cloud Run...).
# Reads Databricks as a read-only service principal; see ../../README.md.
FROM node:22-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AGGIEPULSE_BACKEND=databricks
WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY fallback/ fallback/
COPY frontend/public/static/ frontend/public/static/
COPY --from=frontend /src/dist/ frontend/dist/
RUN useradd --create-home aggie
USER aggie
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*'"]
