# Single-origin build: FastAPI serves the compiled React frontend from one process
# (mirrors README "Run it (production mode, single origin)").

FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

# Apply the base-image CVEs that Debian has actually published a fix for.
# The rest of the trixie findings have no upstream fix yet — each one is
# triaged in deploy/security/trivy-triage.md and gated by deploy/tests.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends liblzma5 \
 && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements.txt
# The pip shipped in the base image carries 5 known CVEs; upgrade before use.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=frontend-build /app/frontend/dist frontend/dist

WORKDIR /app/backend
ENV LOTSPOT_HOST=0.0.0.0
ENV LOTSPOT_PORT=8080
EXPOSE 8080
CMD ["python", "app.py"]
