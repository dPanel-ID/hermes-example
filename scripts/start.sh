#!/bin/bash

export CURRENT_DIR=$(pwd)
export HERMES_HOME="${CURRENT_DIR}/.hermes"

# The Hermes installer has used two layouts: a venv in hermes-agent/venv
# (older) and a launcher in hermes-agent/.hermes/bin (current).
VENV_HERMES=""
for candidate in \
  "${HERMES_HOME}/hermes-agent/.hermes/bin/hermes" \
  "${HERMES_HOME}/hermes-agent/venv/bin/hermes"; do
  if [ -x "${candidate}" ]; then
    VENV_HERMES="${candidate}"
    break
  fi
done
if [ -z "${VENV_HERMES}" ]; then
  echo "Hermes is not installed in ${HERMES_HOME}; run ./scripts/build.sh first" >&2
  exit 1
fi
export VENV_HERMES

echo "Installing Hermes Agent to ${HERMES_HOME}..."

# Install config.yaml into the Hermes home, keeping what Hermes saved there at
# runtime (the home channel set with /sethome). Prefer the system Python,
# which has PyYAML on Debian and Ubuntu; merge_config.py copies the file as
# is when PyYAML is missing.
mkdir -p "${HERMES_HOME}"
CONFIG_PYTHON="$(command -v /usr/bin/python3 || command -v python3)"
"${CONFIG_PYTHON}" "${CURRENT_DIR}/scripts/merge_config.py" config.yaml "${HERMES_HOME}/config.yaml"

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

# Telegram home channel: where Hermes delivers cron results and cross-platform
# messages. Without one, Hermes asks for /sethome on the first message. With
# exactly one allowed user, that user's private chat is the home channel.
if [ -z "${TELEGRAM_HOME_CHANNEL:-}" ] && [ -n "${TELEGRAM_ALLOWED_USERS:-}" ] && [[ "${TELEGRAM_ALLOWED_USERS}" =~ ^[[:space:]]*[0-9]+[[:space:]]*$ ]]; then
  export TELEGRAM_HOME_CHANNEL="${TELEGRAM_ALLOWED_USERS//[[:space:]]/}"
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
