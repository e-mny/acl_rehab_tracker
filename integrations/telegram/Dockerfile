FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BOT_DATA_DIR=/bot-data
WORKDIR /app
COPY integrations/telegram/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home stride && mkdir /bot-data && chown stride:stride /bot-data
COPY --chown=stride:stride integrations/telegram/ ./integrations/telegram/
USER stride
CMD ["python", "-m", "integrations.telegram.bot"]
