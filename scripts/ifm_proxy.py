#!/usr/bin/env python3
"""Local proxy that makes OpenAI-compatible clients follow the IFM Horizon contract.

Hermes talks to http://127.0.0.1:${IFM_PROXY_PORT}/v1 as a plain
OpenAI-compatible provider. This proxy forwards each request to the IFM
gateway (IFM_BASE_URL, default https://api.ifm.ai/v1) and applies the same
rules dPanel's assistant applies (dPanel backend internal/ai/horizon):

- message content is text only; text content-part arrays are joined with
  newlines, any other part is refused with a 400
- "developer" messages are sent as "system"; legacy "function" messages are
  refused
- every assistant message carries reasoning_content and reasoning ("" when
  unknown)
- a tool message must reference a tool call of an earlier assistant message
- requests start at least 1.1 seconds apart
- 429 (honouring Retry-After), 500, 502, 503, 504 and 529 are retried up to
  three attempts, before any byte is sent back
- requests that set no sampling get dPanel's: temperature 0.2, top_p 0.95
  and at most IFM_DEFAULT_MAX_TOKENS (4096) completion tokens; without them
  the model sometimes reasons until the token limit and never answers
- an answer whose content carries the model's reasoning followed by
  </ifm|think> (the gateway does this for some requests with tools) is split:
  the reasoning goes to reasoning_content, only the answer stays in content,
  for both plain and streamed responses

The client's Authorization header is passed through, so the proxy never holds
the API key. It listens on 127.0.0.1 only and needs nothing beyond the Python
standard library.
"""

import http.client
import json
import os
import random
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

UPSTREAM = os.environ.get("IFM_BASE_URL", "https://api.ifm.ai/v1").rstrip("/")
PORT = int(os.environ.get("IFM_PROXY_PORT", "8650"))
REQUEST_INTERVAL = 1.1
MAX_ATTEMPTS = 3
MAX_BACKOFF = 60.0
MAX_BODY = 16 << 20
TIMEOUT = 180
RETRYABLE = {429, 500, 502, 503, 504, 529}
# Sampling dPanel's assistant sends (backend internal/ai/horizon/model.go).
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TOP_P = 0.95
DEFAULT_MAX_TOKENS = int(os.environ.get("IFM_DEFAULT_MAX_TOKENS", "4096"))
# Hop-by-hop and recomputed headers are not forwarded.
SKIP_HEADERS = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "accept-encoding"}

_upstream = urlsplit(UPSTREAM)
_pace_lock = threading.Lock()
_next_start = 0.0


THINK_CLOSE = "</ifm|think>"
THINK_OPEN = re.compile(r"^\s*<ifm\|think>")


def split_reasoning(content):
    """Return (reasoning, answer) when content holds reasoning ended by the
    closing think tag, else None."""
    index = content.find(THINK_CLOSE)
    if index < 0:
        return None
    reasoning = THINK_OPEN.sub("", content[:index]).strip()
    answer = content[index + len(THINK_CLOSE):].lstrip()
    return reasoning, answer


def fix_message(message):
    """Move leaked reasoning out of a non-streamed assistant message."""
    content = message.get("content")
    if not isinstance(content, str):
        return
    parts = split_reasoning(content)
    if parts is None:
        return
    reasoning, answer = parts
    earlier = message.get("reasoning_content") or message.get("reasoning") or ""
    combined = (earlier + "\n" + reasoning).strip() if earlier else reasoning
    message["content"] = answer
    message["reasoning_content"] = combined
    message["reasoning"] = combined


class StreamFixer:
    """Hold back streamed content until it is clear whether it starts with
    leaked reasoning: once the closing think tag arrives the held text is sent
    as reasoning and the rest as content; at the end of the answer without a
    tag, the held text is sent as content unchanged."""

    def __init__(self):
        self.held = ""
        self.passing = False
        self.template = None

    def _event(self, delta, finish_reason=None):
        event = {key: self.template[key] for key in ("id", "object", "created", "model") if key in self.template}
        event["choices"] = [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
        return b"data: " + json.dumps(event).encode() + b"\n\n"

    def _flush(self):
        out = []
        if self.held:
            parts = split_reasoning(self.held)
            if parts is None:
                out.append(self._event({"content": self.held}))
            else:
                if parts[0]:
                    out.append(self._event({"reasoning_content": parts[0], "reasoning": parts[0]}))
                if parts[1]:
                    out.append(self._event({"content": parts[1]}))
        self.held = ""
        self.passing = True
        return out

    def feed(self, line):
        """Take one SSE line (without newline); return the bytes to send."""
        if not line.startswith(b"data:"):
            return [line + b"\n"]
        payload = line[5:].strip()
        if payload == b"[DONE]":
            out = [] if self.passing or self.template is None else self._flush()
            return out + [line + b"\n"]
        try:
            event = json.loads(payload)
        except ValueError:
            return [line + b"\n"]
        choices = event.get("choices") or []
        if self.passing or not choices:
            return [line + b"\n"]
        self.template = event
        choice = choices[0]
        delta = choice.get("delta") or {}
        if not self.held and (delta.get("reasoning_content") or delta.get("reasoning")):
            # The gateway separates reasoning in this answer: nothing to hold back.
            self.passing = True
            return [line + b"\n"]
        content = delta.pop("content", None)
        out = []
        if content:
            self.held += content
            if THINK_CLOSE in self.held:
                out.extend(self._flush())
        if choice.get("finish_reason") is not None or delta.get("tool_calls"):
            # The answer is complete, or a tool call follows: release what is held.
            if not self.passing:
                out.extend(self._flush())
        if delta or choice.get("finish_reason") is not None:
            out.append(b"data: " + json.dumps(event).encode() + b"\n")
        return out


class ContractError(Exception):
    """A request the IFM contract does not allow."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def text_content(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str):
                kind = part.get("type", "unknown") if isinstance(part, dict) else "unknown"
                raise ContractError("unsupported_content_part", f"Horizon supports text content only; received a {kind} content part.")
            parts.append(part["text"])
        return "\n".join(parts)
    raise ContractError("unsupported_content_part", "Horizon supports text content only.")


def normalize_messages(messages):
    seen_tool_calls = set()
    for message in messages:
        if not isinstance(message, dict):
            raise ContractError("unsupported_role", "Every message must be an object.")
        role = message.get("role")
        if role == "developer":
            message["role"] = role = "system"
        if role in ("system", "user"):
            message["content"] = text_content(message.get("content"))
        elif role == "assistant":
            message["content"] = text_content(message.get("content"))
            for call in message.get("tool_calls") or []:
                call_id = (call or {}).get("id") or ""
                name = ((call or {}).get("function") or {}).get("name") or ""
                if not call_id.strip() or not name.strip():
                    raise ContractError("invalid_tool_call", "Tool calls require a non-empty id and function name.")
                seen_tool_calls.add(call_id)
            reasoning = message.get("reasoning_content")
            if not isinstance(reasoning, str):
                reasoning = message.get("reasoning") if isinstance(message.get("reasoning"), str) else ""
            message["reasoning_content"] = reasoning
            message["reasoning"] = reasoning
        elif role == "tool":
            call_id = message.get("tool_call_id") or ""
            if not call_id.strip() or call_id not in seen_tool_calls:
                raise ContractError("unknown_tool_call_reference", "Tool messages must reference a preceding assistant tool call.")
            message["content"] = text_content(message.get("content"))
        elif role == "function":
            raise ContractError("unsupported_role", "Legacy function messages are not supported. Use assistant tool calls and tool messages instead.")
        else:
            raise ContractError("unsupported_role", f"Horizon does not support the {role} message role.")
    return messages


def normalize_body(body):
    try:
        payload = json.loads(body)
    except ValueError:
        raise ContractError("unsupported_role", "Request body is not a JSON object.")
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise ContractError("unsupported_role", "Horizon Chat Completions requests require an array of messages.")
    normalize_messages(payload["messages"])
    payload.setdefault("temperature", DEFAULT_TEMPERATURE)
    payload.setdefault("top_p", DEFAULT_TOP_P)
    if "max_tokens" not in payload and "max_completion_tokens" not in payload:
        payload["max_tokens"] = DEFAULT_MAX_TOKENS
    return json.dumps(payload).encode()


def wait_for_slot():
    """Reserve the next start slot, then sleep outside the lock."""
    global _next_start
    with _pace_lock:
        now = time.monotonic()
        start = max(now, _next_start)
        _next_start = start + REQUEST_INTERVAL
    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def retry_delay(status, headers, attempt):
    retry_after = headers.get("Retry-After")
    if status == 429 and retry_after:
        try:
            return min(float(retry_after), MAX_BACKOFF)
        except ValueError:
            pass
    base = 2.0 if status == 529 else 1.0
    delay = base * (2 ** (attempt - 1))
    return min(delay + random.uniform(0, delay / 4), MAX_BACKOFF)


def open_upstream(method, path, body, headers):
    connection_class = http.client.HTTPSConnection if _upstream.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(_upstream.netloc, timeout=TIMEOUT)
    connection.request(method, _upstream.path + path, body=body, headers=headers)
    return connection, connection.getresponse()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        # A client closing an idle keep-alive connection is normal, not an error.
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def log_message(self, fmt, *args):
        # Method, path and status only; never headers or bodies.
        sys.stderr.write("ifm-proxy %s\n" % (fmt % args))

    def send_json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def forward(self, method):
        if not self.path.startswith("/v1"):
            self.send_json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})
            return
        path = self.path[len("/v1"):]
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.send_json(413, {"error": {"message": "request body too large", "type": "invalid_request_error"}})
            return
        body = self.rfile.read(length) if length else None
        if method == "POST" and path.rstrip("/").endswith("/chat/completions") and body:
            try:
                body = normalize_body(body)
            except ContractError as err:
                sys.stderr.write(f"ifm-proxy refused request: {err.code}: {err.message}\n")
                self.send_json(400, {"error": {"message": err.message, "type": "invalid_request_error", "code": err.code}})
                return
        headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP_HEADERS}
        if body is not None:
            headers["Content-Length"] = str(len(body))

        for attempt in range(1, MAX_ATTEMPTS + 1):
            wait_for_slot()
            try:
                connection, response = open_upstream(method, path, body, headers)
            except OSError as err:
                if attempt == MAX_ATTEMPTS:
                    self.send_json(502, {"error": {"message": f"IFM gateway unreachable: {type(err).__name__}", "type": "api_error"}})
                    return
                time.sleep(retry_delay(502, {}, attempt))
                continue
            if response.status in RETRYABLE and attempt < MAX_ATTEMPTS:
                response.read()
                connection.close()
                time.sleep(retry_delay(response.status, response.headers, attempt))
                continue
            self.relay(response, completions=method == "POST" and path.rstrip("/").endswith("/chat/completions"))
            connection.close()
            return

    def write_chunk(self, data):
        if data:
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

    def relay(self, response, completions=False):
        """Send the gateway's answer back; chat completions are checked for
        reasoning leaked into content, everything else streams through."""
        content_type = response.getheader("Content-Type") or ""
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in SKIP_HEADERS:
                self.send_header(key, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        if completions and response.status == 200 and "application/json" in content_type:
            data = response.read()
            try:
                payload = json.loads(data)
                for choice in payload.get("choices") or []:
                    if isinstance(choice.get("message"), dict):
                        fix_message(choice["message"])
                data = json.dumps(payload).encode()
            except ValueError:
                pass
            self.write_chunk(data)
        elif completions and response.status == 200 and "text/event-stream" in content_type:
            fixer = StreamFixer()
            while True:
                line = response.readline()
                if not line:
                    break
                for out in fixer.feed(line.rstrip(b"\r\n")):
                    self.write_chunk(out)
        else:
            while True:
                chunk = response.read1(65536) if hasattr(response, "read1") else response.read(65536)
                if not chunk:
                    break
                self.write_chunk(chunk)
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def do_GET(self):
        self.forward("GET")

    def do_POST(self):
        self.forward("POST")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    sys.stderr.write(f"ifm-proxy listening on 127.0.0.1:{PORT}, forwarding to {UPSTREAM}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
