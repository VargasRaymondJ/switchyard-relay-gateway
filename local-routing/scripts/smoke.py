"""Live Docker acceptance suite. Requires Python 3.11+, Docker, and Bedrock access."""

from __future__ import annotations

import argparse
import datetime
import http.client
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODEL = "nvidia.nemotron-nano-9b-v2"
ALIAS = "switchyard/mvp"
DEADLINE = 240


def assert_response(body: bytes, *, stream: bool, agent: bool) -> None:
    """Validate semantic completion, not merely HTTP success."""
    if not stream:
        data = json.loads(body)
        content = data.get("content") if agent else data["choices"][0]["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise AssertionError("Response has no nonempty text content")
        return
    text = body.decode("utf-8").replace("\r\n", "\n")
    content = ""
    finished = False
    for block in text.split("\n\n"):
        event = ""
        payload = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                payload.append(line[5:].strip())
        if not payload:
            continue
        raw = "\n".join(payload)
        if raw == "[DONE]":
            if not agent:
                finished = True
            continue
        item = json.loads(raw)
        if event == "error" or item.get("error") or item.get("type") == "error":
            raise AssertionError("Stream contains an error event")
        if isinstance(item.get("payload"), dict) and item["payload"].get("type") == "error":
            raise AssertionError("Stream contains a nested error event")
        if agent:
            if event in {"token", "result"}:
                content += item.get("content") or ""
            finished |= event == "done"
        else:
            for choice in item.get("choices", []):
                content += choice.get("delta", {}).get("content") or ""
    if not content.strip() or not finished:
        raise AssertionError("Stream lacks content or terminal completion")


def request(url: str, payload: dict | None = None, *, timeout: float = DEADLINE) -> tuple[int, bytes]:
    """Read a local API response under one deadline, including streamed bodies."""
    target = urllib.parse.urlsplit(url)
    if target.scheme != "http" or target.hostname != "127.0.0.1":
        raise ValueError("Acceptance probes must use the local loopback HTTP APIs")
    deadline = time.monotonic() + timeout
    data = json.dumps(payload).encode() if payload is not None else None
    connection = http.client.HTTPConnection(target.hostname, target.port, timeout=timeout)
    try:
        connection.connect()
        transport = connection.sock
        assert transport is not None

        def remaining() -> float:
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError("Overall response deadline exceeded")
            return value

        transport.settimeout(remaining())
        connection.request(
            "POST" if data is not None else "GET",
            target.path,
            body=data,
            headers={"Content-Type": "application/json"},
        )
        transport.settimeout(remaining())
        response = connection.getresponse()
        chunks = []
        while not response.isclosed():
            transport.settimeout(remaining())
            chunk = response.read1(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return response.status, b"".join(chunks)
    finally:
        connection.close()


class Suite:
    def __init__(self, *, failures: bool, build: bool):
        self.failures = failures
        self.build = build
        self.project = "routing-test-" + uuid.uuid4().hex[:10]
        self.evidence = ROOT / "evidence" / self.project
        self.evidence.mkdir(parents=True, mode=0o700)
        self.results: list[dict] = []
        self.command = [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--env-file",
            str(ROOT / ".env"),
            "-f",
            str(ROOT / "compose.yaml"),
        ]

    def compose(self, *args: str, env: dict | None = None, capture: bool = False) -> bytes:
        # Never echo compose stderr/config: it can include environment values.
        result = subprocess.run(  # noqa: S603
            [*self.command, *args],
            env=env,
            capture_output=True,
            timeout=900 if self.build else 180,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"Compose {args[0]} failed (exit {result.returncode}); inspect locally")
        return result.stdout if capture else b""

    def ready(self) -> None:
        end = time.monotonic() + 90
        while time.monotonic() < end:
            try:
                code, body = request("http://127.0.0.1:8000/api/v1/health", timeout=3)
                if code == 200 and body:
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(1)
        raise RuntimeError("Agent readiness deadline exceeded")

    def record(self, name: str, status: str, detail: str = "") -> None:
        self.results.append({"case": name, "status": status, "detail": detail})
        sys.stdout.write(f"{name}: {status}\n")
        (self.evidence / "results.json").write_text(json.dumps(self.results, indent=2) + "\n")

    def snapshot(self, name: str, since: str) -> list[dict]:
        """Stop and flush Relay, copy its log, and extract new records by offset."""
        self.compose("stop", "relay")
        destination = self.evidence / f"{name}.jsonl"
        self.compose("cp", "relay:/evidence/events.jsonl", str(destination))
        lines = destination.read_text().splitlines()
        offset = getattr(self, "offset", 0)
        new = [json.loads(line) for line in lines[offset:] if line.strip()]
        self.offset = len(lines)
        destination.write_text("".join(json.dumps(item) + "\n" for item in new))
        gateway_log = self.compose("logs", "--no-color", "--since", since, "agentgateway", capture=True)
        (self.evidence / f"{name}-gateway.log").write_bytes(gateway_log)
        self.compose("start", "relay")
        self.compose("up", "-d", "--wait", "--wait-timeout", "90")
        if b"/v1/chat/completions" not in gateway_log:
            raise AssertionError("Missing gateway access record")
        return new

    def success(self, name: str, *, agent: bool, stream: bool) -> None:
        # Flush preceding failures/retries too, not just preceding successes.
        self.compose("stop", "relay")
        baseline = self.evidence / f"{name}-baseline.jsonl"
        self.compose("cp", "relay:/evidence/events.jsonl", str(baseline))
        self.offset = len(baseline.read_text().splitlines())
        self.compose("up", "-d", "--wait", "--wait-timeout", "90")
        since = datetime.datetime.now(datetime.UTC).isoformat()
        started = time.monotonic()
        if agent:
            url = "http://127.0.0.1:8000/api/v1/query" + ("/stream" if stream else "")
            payload = {"query": "Reply briefly with MVP_OK"}
        else:
            url = "http://127.0.0.1:8080/v1/chat/completions"
            payload = {
                "model": ALIAS,
                "stream": stream,
                "max_tokens": 128,
                "messages": [{"role": "user", "content": "Reply briefly with MVP_OK"}],
            }
        code, body = request(url, payload)
        (self.evidence / f"{name}-response.txt").write_bytes(body)
        if code != 200:
            raise AssertionError(f"{name}: HTTP {code}")
        assert_response(body, stream=stream, agent=agent)
        events = self.snapshot(name, since)
        # ATOF envelope contains a mark whose data carries routing metadata.
        decisions = []
        for event in events:
            if "switchyard.routing.decision" in json.dumps(event):
                decisions.extend(find_decisions(event))
        if not any(d.get("selected_model") == MODEL and d.get("algorithm") == "passthrough" for d in decisions):
            raise AssertionError("Missing expected Switchyard routing decision")
        self.record(name, "PASS", f"{time.monotonic() - started:.2f}s; decisions={decisions}")

    def expect_failure(self, name: str, *, model: str | None = None) -> None:
        started = time.monotonic()
        if model:
            url = "http://127.0.0.1:8080/v1/chat/completions"
            payload = {"model": model, "messages": [{"role": "user", "content": "Hello"}]}
        else:
            url = "http://127.0.0.1:8000/api/v1/query"
            payload = {"query": "Reply briefly with MVP_OK"}
        try:
            code, body = request(url, payload)
        except (OSError, urllib.error.URLError):
            self.record(name, "PASS", "Transport failure within bounded client deadline")
            return
        (self.evidence / f"{name}-response.txt").write_bytes(body)
        if code < 400:
            raise AssertionError(f"{name}: unexpectedly succeeded with HTTP {code}")
        self.record(name, "PASS", f"HTTP {code}; {time.monotonic() - started:.2f}s")

    def interrupted_stream(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=75)
        try:
            connection.request(
                "POST",
                "/v1/chat/completions",
                json.dumps(
                    {
                        "model": ALIAS,
                        "stream": True,
                        "max_tokens": 2048,
                        "messages": [{"role": "user", "content": "Count from 1 to 1000, one number per line."}],
                    }
                ),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise AssertionError("Stream failed before interruption")
            received = b""
            while b"data:" not in received:
                line = response.readline()
                if not line:
                    self.record("interrupted-stream", "BLOCKED", "No stream event before termination")
                    return
                received += line
            self.compose("kill", "-s", "SIGKILL", "relay")
            try:
                received += response.read()
            except (OSError, http.client.HTTPException):
                pass
            (self.evidence / "interrupted-stream-response.txt").write_bytes(received)
            try:
                assert_response(received, stream=True, agent=False)
            except (AssertionError, ValueError):
                self.record("interrupted-stream", "PASS", "No successful terminal completion")
            else:
                self.record("interrupted-stream", "BLOCKED", "Provider completed before interruption")
        finally:
            connection.close()
            self.compose("up", "-d", "--wait", "--wait-timeout", "90")

    def run(self) -> int:
        try:
            options = ["up", "-d", "--wait", "--wait-timeout", "120"]
            if self.build:
                options.append("--build")
            self.compose(*options)
            self.ready()
            for agent in (False, True):
                for stream in (False, True):
                    self.success(
                        f"{'agent' if agent else 'gateway'}-{'stream' if stream else 'ordinary'}",
                        agent=agent,
                        stream=stream,
                    )
            if self.failures:
                for service in ("agentgateway", "relay"):
                    try:
                        self.compose("stop", service)
                        self.expect_failure(f"{service}-unavailable")
                    finally:
                        self.compose("up", "-d", "--wait", "--wait-timeout", "90")
                        self.ready()
                for model in ("unknown-route", MODEL):
                    self.expect_failure("reject-" + model, model=model)
                try:
                    invalid = dict(os.environ, AWS_BEARER_TOKEN_BEDROCK="invalid-test-credential")
                    self.compose("up", "-d", "--no-deps", "--force-recreate", "relay", env=invalid)
                    self.compose("up", "-d", "--wait", "--wait-timeout", "90", env=invalid)
                    self.expect_failure("invalid-provider-credential")
                    missing = dict(os.environ, AWS_BEARER_TOKEN_BEDROCK="")
                    try:
                        self.compose("config", "--quiet", env=missing)
                    except RuntimeError:
                        self.record("missing-provider-credential", "PASS", "Compose refused missing secret")
                    else:
                        raise AssertionError("Missing credential accepted")
                finally:
                    self.compose("up", "-d", "--force-recreate", "--no-deps", "relay")
                    self.compose("up", "-d", "--wait", "--wait-timeout", "90")
                self.interrupted_stream()
                self.success("recovery", agent=True, stream=False)
        except Exception as exc:  # Report type only; provider errors can contain sensitive data.
            self.record("suite", "FAIL", type(exc).__name__)
            return 1
        finally:
            try:
                self.compose("stop")
                self.compose("cp", "relay:/evidence/events.jsonl", str(self.evidence / "all-events.jsonl"))
                # Do not remove evidence volume; cleanup is an explicit operator action.
                self.compose("down")
            except Exception:
                self.record("cleanup", "FAIL", f"Inspect Docker project {self.project}")
        if any(r["status"] == "FAIL" for r in self.results):
            return 1
        return 2 if any(r["status"] == "BLOCKED" for r in self.results) else 0


def find_decisions(value: object) -> list[dict]:
    if isinstance(value, dict):
        found = [value] if "selected_model" in value and "algorithm" in value else []
        return found + [item for child in value.values() for item in find_decisions(child)]
    if isinstance(value, list):
        return [item for child in value for item in find_decisions(child)]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failures", action="store_true", help="Also run disruptive failure/recovery tests")
    parser.add_argument("--build", action="store_true", help="Build images before tests")
    parser.add_argument("--confirm-live", action="store_true", help="Acknowledge real Bedrock calls and charges")
    args = parser.parse_args()
    if not args.confirm_live or not (ROOT / ".env").exists():
        sys.stderr.write("BLOCKED: requires --confirm-live and a securely populated local-routing/.env\n")
        return 2
    # Tests own ports 8000/8080. Stop the regular stack before running.
    return Suite(failures=args.failures, build=args.build).run()


if __name__ == "__main__":
    raise SystemExit(main())
