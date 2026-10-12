FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY knowledge ./knowledge
COPY samples/anon ./samples/anon
COPY fonts ./fonts
RUN useradd -m appuser
USER appuser
# Cloud Run は PORT を渡す
CMD ["sh", "-c", "exec uvicorn app.main:app_factory --factory --host 0.0.0.0 --port ${PORT:-8080}"]
