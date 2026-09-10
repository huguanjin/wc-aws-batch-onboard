FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8787

WORKDIR /app

COPY server.py ./
COPY static ./static

RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app
USER app

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8787/api/health',timeout=3)"]

CMD ["python", "server.py"]
