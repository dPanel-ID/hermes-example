#!/bin/bash

export CURRENT_DIR=$(pwd)
export HERMES_HOME="${CURRENT_DIR}/.hermes"
export VENV_HERMES="${HERMES_HOME}/hermes-agent/venv/bin/hermes"

echo "Installing Hermes Agent to ${HERMES_HOME}..."

# always copy the config.yaml to the hermes home directory
cp -rf config.yaml "${HERMES_HOME}/config.yaml"

# INJECT the enablement configurations directly into your fresh copy
"$VENV_HERMES" config set platforms.telegram.enabled true

# Expose the API server layout globally 
export API_SERVER_ENABLED=false
export API_SERVER_HOST=0.0.0.0 # Allows connections outside localhost
if [ "${API_SERVER_ENABLED}" = "true" ] && [ -z "${API_SERVER_KEY:-}" ]; then
  API_SERVER_KEY_FILE="${HERMES_HOME}/api_server_key"
  if [ ! -s "$API_SERVER_KEY_FILE" ]; then
    umask 077
    openssl rand -hex 32 > "$API_SERVER_KEY_FILE"
  fi
  export API_SERVER_KEY="$(cat "$API_SERVER_KEY_FILE")"
fi

# Telegram bot configuration
export GATEWAY_ALLOW_ALL_USERS=true
export TELEGRAM_BOT_TOKEN=1234567890:ABCDEF1234567890abcdef1234567890ab
# export TELEGRAM_ALLOWED_USERS=
# Telegram bot configuration

# OpenAI API key configuration
export OPENAI_API_KEY=sk-project-1-0-0-

# Anthropic API key configuration
export ANTHROPIC_API_KEY=sk-anthropic-1-0-0-

exec "$VENV_HERMES" gateway run --replace --accept-hooks
