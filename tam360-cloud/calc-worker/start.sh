#!/bin/sh
# Virtual display + window manager + VNC (local only) + noVNC websocket bridge + agent app + Caddy router.
set -e
Xvfb :99 -screen 0 1440x1000x24 -nolisten tcp &
sleep 1
fluxbox >/dev/null 2>&1 &
x11vnc -display :99 -forever -shared -nopw -localhost -rfbport 5900 -quiet &
websockify --web /usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900 >/dev/null 2>&1 &
python worker.py &
exec caddy run --config /app/Caddyfile --adapter caddyfile
