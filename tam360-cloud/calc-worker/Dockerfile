# TAM 360 cloud Calculator agent: Chromium on a virtual display + live view (noVNC) + agent web app.
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 DISPLAY=:99 DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify fluxbox curl ca-certificates \
 && curl -fsSL -o /tmp/caddy.tgz https://github.com/caddyserver/caddy/releases/download/v2.8.4/caddy_2.8.4_linux_amd64.tar.gz \
 && tar -xzf /tmp/caddy.tgz -C /usr/local/bin caddy && rm /tmp/caddy.tgz \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker.py calculator_agent.py legacy_share.py Caddyfile start.sh ./
RUN chmod +x start.sh && useradd -m agent && mkdir -p /data && chown agent /data
USER agent
EXPOSE 8000
CMD ["./start.sh"]
