FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib POSE_MODEL_PATH=/models/pose_landmarker_lite.task
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libegl1 libgles2 libglib2.0-0 libportaudio2 && rm -rf /var/lib/apt/lists/*
COPY integrations/pose/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY integrations/pose/ ./integrations/pose/
RUN python integrations/pose/download_model.py /models/pose_landmarker_lite.task && useradd --uid 10001 --create-home stride
USER stride
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2)"
CMD ["uvicorn", "integrations.pose.service:app", "--host", "0.0.0.0", "--port", "8001", "--no-proxy-headers"]
