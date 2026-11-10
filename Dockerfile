FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data STATIC_DIR=/app/static
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home stride && mkdir /data && chown stride:stride /data
COPY --chown=stride:stride backend/app.py ./backend/app.py
COPY --chown=stride:stride backend/api_docs.html ./backend/api_docs.html
COPY --from=frontend --chown=stride:stride /build/dist ./static
USER stride
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
