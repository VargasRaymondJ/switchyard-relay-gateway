# Local routing MVP runbook

## Status

Implementation assets are present. **Live Bedrock inference and Docker builds
are not yet verified.** Do not interpret native-process tests as Docker or ARM64
validation. Update this document in every implementation PR/commit that changes
setup, operation, or evidence. [Design](DESIGN.md).

**Compatibility fix:** the pinned Foundry NIMS factory supplies `httpx.Client`,
while the locked Strands OpenAI adapter requires `httpx.AsyncClient`. With the
owner's approval, a composition-root provider override supplies a fresh async
transport per request while preserving NIMS formatting and TLS configuration.
No shared package or dependency-lock changes are made.

```text
API client → base agent :8000 → agentgateway :8080
           → Relay + Switchyard :4040 (internal) → Bedrock HTTPS
```

The alias `switchyard/mvp` targets NVIDIA Nemotron Nano 9B v2
(`nvidia.nemotron-nano-9b-v2`) in `us-east-1`. Routing is passthrough, not a
classifier. No Ollama, provider fallback, tools, persistent sessions, database,
or telemetry collector. Unknown models fail against an unserved Relay loopback
upstream. This is a trusted-local-development setup, not production.

The [AWS model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-nvidia-nvidia-nemotron-nano-9b-v2.html)
lists this model as Legacy, EOL March 30, 2027. Recheck access/lifecycle rather
than silently changing the target.

## Requirements

- Git, Docker Engine/Desktop with Linux containers and Compose v2 supporting
  `up --wait` (configuration validated with Compose 2.40.3).
- AMD64 or ARM64 runtime. Linux/macOS/Windows hosts are intended; see validation
  limitations below. Minimum resource requirements have not been measured.
- Network access to GitHub releases, Debian/package/container registries, and
  the selected Bedrock endpoint.
- Ports 8000 and 8080 free. Only loopback host addresses are published.
- AWS account access to the target and a valid Bedrock API key; calls cost money.
- POSIX shell and curl for examples (WSL on Windows). Python 3.11+ is needed
  only for the host smoke-test script; the agent's Python runs in its container.

## Environment setup

```bash
git clone https://github.com/VargasRaymondJ/switchyard-relay-gateway.git
cd switchyard-relay-gateway/local-routing
docker version
docker compose version
cp .env.example .env
chmod 600 .env
git check-ignore .env
```

Edit `.env` locally to supply `AWS_BEARER_TOKEN_BEDROCK`. Never paste the value
into chat, tickets, shell commands, Git, or build arguments. It is passed to
Relay only. Compose rejects an empty value. No AWS profile or SigV4 adapter is
used. Avoid printing rendered Compose config: secret expansion can expose it.
The local Docker build context explicitly excludes secrets and evidence.

## Build and start

All Compose commands in this document run from `local-routing/`, not the root.
The inherited root Compose file is **not** the routing stack.

```bash
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up --build -d --wait --wait-timeout 120
docker compose --env-file .env -f compose.yaml ps
curl --fail-with-body --silent --show-error --max-time 10 \
  http://127.0.0.1:8000/api/v1/health
```

Relay startup enables and validates the plugin in ephemeral writable state,
then starts the gateway. A native plugin activation failure prevents startup.
Readiness waits for Relay, then gateway, then agent; it does not prove Bedrock
authentication. Inspect logs locally:

```bash
docker compose --env-file .env -f compose.yaml logs --tail 50 relay agentgateway agent
```

Do not share logs without inspecting/redacting them. Relay evidence can contain
request/response data even with full payload export disabled.

Release versions: Relay 0.11.0, Switchyard plugin 0.3.2, agentgateway 1.7.0.
`checksums.sha256` contains expected release hashes for AMD64 and ARM64.
Base images are digest-pinned; distribution packages are not snapshot-pinned,
so this is not a bit-for-bit reproducibility guarantee.

## Call the base agent API

Use synthetic prompts and omit `session_id` to avoid implicit session storage.

### Ordinary response

```bash
curl --fail-with-body --silent --show-error --max-time 240 \
  -X POST http://127.0.0.1:8000/api/v1/query \
  -H 'Content-Type: application/json' \
  --data '{"query":"Reply with MVP_OK"}'
```

Require a successful JSON response with nonempty `content`; exact wording is
not an assertion. The ordinary agent API can use a streaming model call
internally, so it does not establish upstream non-streaming coverage.

### Streaming response

```bash
curl --no-buffer --fail-with-body --silent --show-error --max-time 240 \
  -X POST http://127.0.0.1:8000/api/v1/query/stream \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  --data '{"query":"Reply with MVP_OK"}'
```

Require nonempty content in `token`/`result`, terminal `done`, no error event,
and no premature disconnect. HTTP 200 or curl exit zero alone is insufficient.
See the [agent API contract](../docs/foundry/guides/http-api.md).

## Automated tests

Run the inexpensive response-contract tests without Docker or credentials:

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
```

For live tests, first stop the regular stack to free ports. The suite creates
a fresh Compose project and evidence volume, runs cases serially, and explicitly
requires acknowledgement of live calls and charges:

```bash
docker compose --env-file .env -f compose.yaml down
python3 scripts/smoke.py --confirm-live --build
# Includes disruptive failures and recovery:
python3 scripts/smoke.py --confirm-live --failures
```

The suite tests both agent API paths and explicit streaming/non-streaming
Chat Completions through the gateway. It checks content, terminal events,
gateway access records, and expected `switchyard.routing.decision` metadata.
Missing `served_model` is unknown, not proof of a match.

Failure tests cover stopped gateway/Relay, unknown aliases/direct model names,
invalid credentials, missing-secret configuration, stream interruption, and
recovery. They never overwrite the saved real credential. A stream that finishes
before interruption reports BLOCKED rather than PASS. Results exit codes:
0 PASS, 1 FAIL, 2 BLOCKED. Inspect `evidence/routing-test-*/results.json`.

The provider timeout is 60 seconds with zero provider retries. Gateway header
and idle deadlines are 65 seconds. The unchanged agent SDK can retry; the
client test deadline is 240 seconds. Test commands also have process deadlines.
No claim of a one-to-one agent/provider request count is made.

The suite gracefully stops Relay to flush ATOF and copies new records after each
success case. It preserves all final events and the evidence volume. Evidence
directories are ignored and private; use synthetic data only. A provider access
failure must be investigated before reporting the MVP passed.

## Configuration and restart

- `config/agent.yaml`: NIMS provider, gateway URL, alias, no tools/memory.
- `config/agentgateway.yaml`: exact POST route, deadlines, normal access logging.
- `config/relay/`: runtime and plugin configuration, required activation.
- `config/routes.toml`: alias, target, provider endpoint, secret variable name.
- `compose.yaml`: three services, one network, Relay-only provider secret.

The agent Dockerfile's image pins changed and the composition root registers
the NIMS async-client compatibility override. The lockfile is preserved.
The NIMS factory does not pass all generic
temperature/token/streaming options through; do not assume YAML alone changes
those request fields.

```bash
docker compose --env-file .env -f compose.yaml restart relay
docker compose --env-file .env -f compose.yaml up -d --wait --wait-timeout 120
docker compose --env-file .env -f compose.yaml down
```

Restart Relay after editing TOML; there is no assumed hot reload. `down` retains
the evidence volume. After reviewing evidence, explicitly remove only the
appropriate project volume with `docker volume rm <exact-volume-name>`. Never
use broad volume pruning. Stopping services does not revoke a provider key.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Port 8000 unavailable | Port conflict, container logs, listener, health checks |
| Healthy agent but failed query | Gateway route, plugin activation, provider access |
| Plugin startup failure | Matching CPU/libc, checksum, manifest, trust policy |
| Unknown model succeeds | Fail-closed configuration regressed; do not accept |
| HTTP 200 stream without done | Error events, provider interruption, deadlines |
| No routing decision | Alias match, activation, exporter write access, graceful flush |
| Smoke suite Compose failure | Inspect Docker locally; script suppresses potentially sensitive stderr |

## Validation and remaining work

Validated in the development sandbox on native Linux AMD64:

- Release checksums for downloaded AMD64 artifacts.
- Pinned gateway config validation, Relay/plugin startup and health.
- Gateway forwarding, access records, unknown-model failure.
- Compose 2.40.3 configuration validation (no Docker daemon).
- Agent configuration parsing; nine smoke-helper tests including HTTP deadlines.
- Required `just check`: lint, formatting, security lint, type checking and
  150 tests passed, with 88.76% coverage. Nine additional smoke-helper tests pass.
- Both base-agent endpoints now reach the gateway/Relay with an invalid
  credential. Ordinary calls return HTTP 500 and streaming calls emit an error
  event. Gateway logs confirm HTTP 401 from the provider path, not a successful
  inference. The previous NIMS transport type failure is resolved.
- A direct gateway request for the managed alias using an intentionally invalid
  credential reaches the Bedrock path and returns HTTP 401. Flushed ATOF contains
  Switchyard error/metric records. This proves plugin execution on failure only,
  not a successful provider response or correct account/model authorization.

**Not verified:** Docker image builds/execution, ARM64 execution, Windows/macOS,
valid Bedrock ordinary/streamed inference, live success/evidence and failure
suite, or resource sizing. No successful provider response is claimed.

Three inherited Markdown code-block formatting errors were corrected with the
owner's approval so the required formatting gate passes.

Tracking: [#1 design](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/1),
[#2 packaging](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/2),
[#3 provider](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/3),
[#4 gateway](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/4),
[#5 Compose](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/5),
[#6 success](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/6),
[#7 failures](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/7),
[#8 runbook](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/8).
Do not close runtime-validation issues until their remaining gates pass.
