# Sandbox image. A virtual desktop the agent operates as a human would.
# Runs Chromium for browser tasks, xdotool/scrot for any-app tasks.
#
# Build:  docker build -t agent-sandbox -f sandbox/Dockerfile .
# Run:    docker run --rm -p 6080:6080 -p 8000:8000 agent-sandbox
#
# Ports:
#   6080  noVNC web UI (open http://localhost:6080 to watch)
#   8000  agent control API (POST /run accepts a Plan)

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    XDG_RUNTIME_DIR=/tmp/runtime-agent \
    LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System packages. Each one is here for a specific reason:
#   xvfb               virtual display (no GPU/screen needed)
#   openbox            minimal window manager
#   x11vnc             VNC server bridging Xvfb to the network
#   novnc, websockify  HTTP-based VNC viewer (the live-view UX)
#   xdotool, scrot     OS-level keyboard/mouse + screenshots (computer-use mode)
#   chromium-browser   browser
#   supervisor         process orchestration
#   ffmpeg             session recording for the audit trail
#   fonts-*            so screenshots don't render with empty boxes
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        openbox \
        x11vnc \
        novnc \
        websockify \
        xdotool \
        scrot \
        imagemagick \
        chromium-browser \
        supervisor \
        ffmpeg \
        python3 \
        python3-pip \
        python3-venv \
        ca-certificates \
        curl \
        wget \
        sudo \
        unzip \
        fonts-liberation \
        fonts-noto-color-emoji \
        fonts-noto-cjk \
        libnss3 \
        libxss1 \
        libasound2t64 \
        libatk-bridge2.0-0 \
        libgtk-3-0 \
        libgbm1 \
        netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Non-root user. Browsers shouldn't run as root.
RUN userdel -r ubuntu 2>/dev/null || true \
    && useradd -m -s /bin/bash -u 1000 agent \
    && mkdir -p $XDG_RUNTIME_DIR \
    && chown -R agent:agent $XDG_RUNTIME_DIR

# Python deps in a venv (Ubuntu 24.04 enforces PEP 668)
ENV VIRTUAL_ENV=/opt/agent-venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# COPY sandbox_agent/requirements.txt /opt/sandbox_agent/requirements.txt
# RUN pip install --no-cache-dir -r /opt/sandbox_agent/requirements.txt \
#     && playwright install chromium

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

COPY sandbox_agent/requirements.txt /opt/sandbox_agent/requirements.txt
RUN pip install --no-cache-dir -r /opt/sandbox_agent/requirements.txt \
    && playwright install chromium \
    && chmod -R a+rx /opt/playwright-browsers

# Agent code
COPY sandbox_agent/ /opt/sandbox_agent/

# Supervisor + entrypoint
COPY sandbox/supervisord.conf /etc/supervisor/conf.d/agent.conf
COPY sandbox/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN mkdir -p /home/agent/workspace && chown -R agent:agent /home/agent/workspace
WORKDIR /home/agent/workspace

EXPOSE 6080 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]