#!/bin/bash
# Container entrypoint. Sets up the per-run state and hands off to supervisord.

set -euo pipefail

# Make sure the runtime dir exists with right permissions even if mounted
mkdir -p "${XDG_RUNTIME_DIR:-/tmp/runtime-agent}"
chmod 700 "${XDG_RUNTIME_DIR:-/tmp/runtime-agent}"
chown -R agent:agent "${XDG_RUNTIME_DIR:-/tmp/runtime-agent}"

# Wipe any stale supervisord state from previous restarts
rm -f /var/run/supervisord.pid /tmp/.X99-lock

# Optional: pre-populate credentials from env vars into the agent home so
# Playwright/cookies/etc can use them without us writing files at runtime.
if [[ -n "${AGENT_BOOTSTRAP_ENV:-}" ]]; then
    echo "$AGENT_BOOTSTRAP_ENV" > /home/agent/.bootstrap.env
    chown agent:agent /home/agent/.bootstrap.env
fi

# Hand off PID 1 to supervisord
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/agent.conf