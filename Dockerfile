# Build from repository root:
#   docker build -t nginxproxymanager-toolbox .
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.source=https://github.com/ludditious/NginxProxyManager-ToolBox

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY version.txt current-release.txt /app/
COPY npmtbx /app/npmtbx
COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt

COPY web /app/web
RUN chmod +x /app/web/docker-entrypoint.sh /app/web/cron/cron-tick.sh

ENV PYTHONPATH=/app:/app/web
ENV PORT=8080
ENV DATABASE_URL=sqlite:////data/npmtoolbox.db

WORKDIR /app/web
VOLUME ["/data"]
EXPOSE 8080

ENTRYPOINT ["/app/web/docker-entrypoint.sh"]
