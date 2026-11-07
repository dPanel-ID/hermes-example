#!/bin/bash

export CURRENT_DIR=$(pwd)
export HERMES_HOME="${CURRENT_DIR}/.hermes"
export VENV_HERMES="${HERMES_HOME}/hermes-agent/venv/bin/hermes"

echo "Installing Hermes Agent to ${HERMES_HOME}..."

# always copy the config.yaml to the hermes home directory
cp -rf config.yaml "${HERMES_HOME}/config.yaml"

# Expose the API server layout globally
# export API_SERVER_ENABLED=false
# export API_SERVER_HOST=0.0.0.0 # Allows connections outside localhost
export API_SERVER_PORT=8642
if [ "${API_SERVER_ENABLED}" = "true" ] && [ -z "${API_SERVER_KEY:-}" ]; then
  API_SERVER_KEY_FILE="${HERMES_HOME}/api_server_key"
  if [ ! -s "$API_SERVER_KEY_FILE" ]; then
    umask 077
    openssl rand -hex 32 >"$API_SERVER_KEY_FILE"
  fi
  export API_SERVER_KEY="$(cat "$API_SERVER_KEY_FILE")"
fi

# Telegram bot configuration
# export GATEWAY_ALLOW_ALL_USERS=true
# export TELEGRAM_BOT_TOKEN=1234567890:ABCDEF1234567890abcdef1234567890ab
# export TELEGRAM_ALLOWED_USERS=
# Telegram bot configuration

# IFM API key configuration (set IFM_API_KEY in the environment; K2THINK_API_KEY
# still works for older setups)
export IFM_API_KEY="${IFM_API_KEY:-${K2THINK_API_KEY:-}}"
if [ -z "${IFM_API_KEY}" ]; then
  echo "IFM_API_KEY is not set" >&2
  exit 1
fi

# OpenAI API key configuration
# export OPENAI_API_KEY=sk-project-1-0-0-

# Anthropic API key configuration
# export ANTHROPIC_API_KEY=sk-anthropic-1-0-0-

# The IFM contract proxy runs beside the gateway and stops with it.
export IFM_PROXY_PORT="${IFM_PROXY_PORT:-8650}"
python3 "${CURRENT_DIR}/scripts/ifm_proxy.py" &
IFM_PROXY_PID=$!
trap 'kill "${IFM_PROXY_PID}" 2>/dev/null' EXIT INT TERM
for _ in $(seq 1 50); do
  if (exec 3<>"/dev/tcp/127.0.0.1/${IFM_PROXY_PORT}") 2>/dev/null; then
    break
  fi
  sleep 0.1
done

"$VENV_HERMES" gateway run --replace --accept-hooks
