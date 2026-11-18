FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GARMIN_DATA_DIR=/garmin-data
WORKDIR /app
COPY integrations/garmin/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home stride && mkdir /garmin-data && chown stride:stride /garmin-data
COPY --chown=stride:stride integrations/garmin/ ./integrations/garmin/
USER stride
CMD ["python", "-m", "integrations.garmin.worker"]
