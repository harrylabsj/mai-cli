FROM python:3.13-slim

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -e '.[api]' \
  && chmod +x /app/scripts/mai.py /app/scripts/mai_agent.py /app/scripts/mai_api.py /app/scripts/verify.sh \
  && mkdir -p /data

VOLUME ["/data"]

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2).read()"

CMD ["python3", "scripts/mai_api.py", "--db", "/data/mai-cli.sqlite", "--host", "0.0.0.0", "--port", "8765"]
