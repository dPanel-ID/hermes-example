# Hermes Example

Small example project for running a custom Hermes gateway process. The gateway can connect Hermes to Telegram and can optionally expose an
OpenAI-compatible API server.

## Files

- `scripts/build.sh` installs Hermes into `.hermes/`.
- `scripts/start.sh` starts the gateway in the foreground.
- `config.yaml` is copied into `.hermes/config.yaml` before the gateway starts.
- `.hermes/api_server_key` is generated automatically when the API server needs a key.

## LLM Configuration

Set the default model and provider in `config.yaml`:

```yaml
model:
  default: gpt-4o-mini
  provider: openai-api
```

Set the provider API key in `scripts/start.sh`, systemd `Environment=...`, or a
secure environment file:

```bash
OPENAI_API_KEY=your_openai_api_key
```

Provider examples:

- OpenAI API: `provider: openai-api` and `OPENAI_API_KEY=...`
- Anthropic: `provider: anthropic` and `ANTHROPIC_API_KEY=...`
- OpenRouter: `provider: openrouter` and `OPENROUTER_API_KEY=...`

For `gpt-4o-mini`, keep this in `config.yaml`:

```yaml
agent:
  reasoning_effort: none
```

## Telegram Configuration

Create a bot with BotFather, then set:

```bash
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
GATEWAY_ALLOW_ALL_USERS=true
```

For production, prefer an allowlist instead of allowing everyone:

```bash
GATEWAY_ALLOW_ALL_USERS=false
TELEGRAM_ALLOWED_USERS=123456789,987654321
```

In Telegram groups, mention the bot directly, for example:

```text
@your_bot_username hello
```

If you want the bot to read all normal group messages, disable Telegram bot
privacy mode in BotFather.

## Gateway API Server

The API server is optional. Enable it only when you need an OpenAI-compatible
HTTP endpoint.

```bash
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_KEY=strong_random_secret
```

Use `127.0.0.1` for local-only access. Use `0.0.0.0` only behind a firewall or
trusted private network. If `API_SERVER_KEY` is not set, `scripts/start.sh`
generates one at `.hermes/api_server_key`.

When exposing the API server on the network, consider changing:

```yaml
terminal:
  backend: docker
```

This avoids running API-dispatched agent work directly as the host user.

## Running

Install Hermes:

```bash
./scripts/build.sh
```

Start the gateway:

```bash
./scripts/start.sh
```

For systemd, make sure the service uses this project as its working directory:

```ini
[Service]
WorkingDirectory=/Users/nedya.prakasa/Projects/hermes-example
ExecStart=/Users/nedya.prakasa/Projects/hermes-example/scripts/start.sh
Restart=on-failure
```

Do not commit real API keys or bot tokens. Keep secrets in systemd environment
files, `.hermes/.env`, or your deployment secret manager.
