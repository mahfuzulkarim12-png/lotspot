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
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=frontend-build /app/frontend/dist frontend/dist

WORKDIR /app/backend
ENV LOTSPOT_HOST=0.0.0.0
ENV LOTSPOT_PORT=8080
EXPOSE 8080
CMD ["python", "app.py"]
