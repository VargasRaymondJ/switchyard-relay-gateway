# Local routing MVP: setup, operation, and API testing

## Status and maintenance

**Planning / implementation pending.** This is the living runbook for the
three-service routing stack. No full-stack build or inference test has passed
yet. The root `compose.yaml` is the inherited base-agent setup, **not** this
routing experiment.

Update this file in each implementation PR when prerequisites, configuration,
commands, or verified behavior change. Replace planned steps with tested steps;
record the platform and results in the validation table below. Do not mark an
issue complete merely because its procedure is documented.

| Work | Tracking | Current status |
| --- | --- | --- |
| Design and acceptance contract | [#1](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/1) | Pending |
| Relay / plugin packaging | [#2](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/2) | Pending |
| Bedrock route and unknown-model rejection | [#3](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/3) | Pending |
| Gateway forwarding and logs | [#4](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/4) | Pending |
| Compose integration | [#5](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/5) | Pending |
| Success tests and evidence | [#6](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/6) | Pending |
| Failure and recovery tests | [#7](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/7) | Pending |
| Runbook and platform validation | [#8](https://github.com/VargasRaymondJ/switchyard-relay-gateway/issues/8) | Initial runbook only |

## What this project will run

```text
API client → base agent → agentgateway → Relay + Switchyard plugin → Bedrock
             :8000        :8080          :4040 (internal)           HTTPS
```

- Three application containers; Switchyard is loaded inside Relay.
- Agent-facing model alias: `switchyard/mvp`.
- Target: NVIDIA Nemotron Nano 9B v2, `nvidia.nemotron-nano-9b-v2`.
- AWS region: `us-east-1`.
- Passthrough routing: success means the target receives the request and its
  response returns to the agent. No classifier or second-model comparison.
- No Ollama, provider fallback, database, collector, tool integrations, or
  persistent sessions. Unknown model names must fail.
- Local HTTP only; host ports bind to loopback. This is not a production setup.

The [AWS model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-nvidia-nvidia-nemotron-nano-9b-v2.html)
documents Chat Completions support and regional availability. It currently
lists the model as Legacy with EOL on March 30, 2027. Recheck lifecycle and
account availability before implementation; never silently substitute a model.

## Prerequisites

- Git and access to this repository, release artifacts, package registries,
  container registries, and the Bedrock endpoint.
- Docker Engine or Docker Desktop with Docker Compose v2 and Linux containers.
- AMD64 or ARM64 host/runtime. Linux, macOS, and Windows support is intended,
  not yet verified. Minimum Docker resources and Compose version remain to be
  measured and pinned during implementation.
- A POSIX-compatible shell for the examples below (Linux/macOS shell, or WSL
  on Windows), plus `curl`; automated tests may add documented dependencies.
- Available local ports 8000 and 8080.
- Bedrock account authorization for the target model and a valid Bedrock API
  key. Inference incurs AWS charges. Credentials must never be pasted into
  issues, pull requests, chat, or logs.

Python is intended to run inside the agent container; a host Python install is
not required for the Compose workflow. Editing/testing the Python source outside
Docker follows the repository's separate contributor setup.

```bash
git clone https://github.com/VargasRaymondJ/switchyard-relay-gateway.git
cd switchyard-relay-gateway
docker version
docker compose version
```

## Environment setup — planned, not runnable yet

Issue #5 will add `local-routing/compose.yaml` and `.env.example`. Do not run
the following until those files exist and this section has been verified.

```bash
cd local-routing
cp .env.example .env
chmod 600 .env
# Edit .env locally using an editor; do not paste a key into shell commands.
```

The intended runtime secret is `AWS_BEARER_TOKEN_BEDROCK`. Compose must inject
it into **Relay only**, never the agent or agentgateway. The template will
contain placeholders only. Native AWS profile/SigV4 authentication is not the
planned authentication path.

Before entering a real secret, confirm the ignore rule is in place:

```bash
git check-ignore .env
```

If that command does not identify `.env` as ignored, stop and fix the ignore
configuration first. Generated responses and evidence must also be ignored.
Do not paste rendered Compose configuration into tickets: environment expansion
can expose credentials. Do not enable shell tracing around secret operations.

### Configuration contract

| Component | Intended configuration |
| --- | --- |
| Agent | `provider: nims`, `model_id: switchyard/mvp`, `nims_base_url: http://agentgateway:8080/v1`; nonsecret SDK placeholder only |
| Agent listener | `HOST=0.0.0.0`, `PORT=8000`, `STRANDS_TLS_MODE=platform`; publish `127.0.0.1:8000` |
| Agent config | Read-only `/app/config.yaml`; no memory, tools, external telemetry, or session persistence |
| agentgateway | Ordinary HTTP forwarding of `/v1/chat/completions` to `relay:4040`; publish `127.0.0.1:8080` |
| Relay | Direct gateway mode on `0.0.0.0:4040`, no host port; one active Switchyard plugin |
| Switchyard | `openai_chat` client, stable alias, configured NVIDIA target, server-owned credential |
| Bedrock | Candidate base URL `https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1`; live compatibility is an acceptance gate |

Relay 0.11.0 and plugin 0.3.2 are the initial packaging candidates. Match Linux
CPU/libc, verify committed artifact checksums, and pin container digests.
The agentgateway release/digest and inherited agent image digests remain TBD.
Do not assume YAML `streaming`, `temperature`, or `max_tokens` settings are
forwarded by the pinned NIMS factory; verify actual request behavior.

## Start and inspect services — planned

From `local-routing/`, once the Compose assets are implemented:

```bash
docker compose --env-file .env -f compose.yaml up --build -d
docker compose --env-file .env -f compose.yaml ps
curl --fail-with-body --silent --show-error --max-time 10 \
  http://127.0.0.1:8000/api/v1/health
```

Startup order is not readiness. Wait for service readiness and inspect Relay's
startup output for **active Switchyard plugin** before inference. A healthy
agent alone does not prove provider connectivity. Exact readiness/log commands
will be added with issues #2 and #5. Review logs locally before sharing them.

## Test through the base agent

These examples use the existing agent HTTP API contract. They have **not** been
run through the planned routing stack. Run them only after the stack is ready.
The client timeout below is a provisional test bound, not a configured server
or provider deadline.

### Ordinary response

```bash
curl --fail-with-body --silent --show-error --max-time 120 \
  -X POST http://127.0.0.1:8000/api/v1/query \
  -H 'Content-Type: application/json' \
  --data '{"query":"Reply with MVP_OK"}'
```

Expected: a successful JSON response with nonempty `content`. Exact model
wording is not an assertion. Do **not** include `session_id`: it can enable
session storage. An ordinary agent response does not prove that its internal
model call was non-streaming.

### Streaming response

```bash
curl --no-buffer --fail-with-body --silent --show-error --max-time 120 \
  -X POST http://127.0.0.1:8000/api/v1/query/stream \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  --data '{"query":"Reply with MVP_OK"}'
```

Expected: nonempty content in `token` and/or `result` events and a terminal
`done` event, with no `error` event or premature disconnect. HTTP 200 and a zero
curl exit status alone are insufficient: SSE errors can arrive inside a
successful HTTP response. Issue #6 will add automated assertions.

The [agent API guide](../docs/foundry/guides/http-api.md) defines response fields
and SSE events.

## Prove routing and test failures

For each test run, use a fresh ignored evidence directory, synthetic prompts,
and serial requests. Capture:

- Agent result and available correlation ID.
- Gateway access record with request time, path, status, and duration.
- `switchyard.routing.decision` naming the expected selected model and
  algorithm; inspect `served_model` when present, treating missing data as
  unknown rather than a match.
- Every observed attempt, without assuming automatic cross-service correlation
  or one upstream call per agent request.

Gracefully stop Relay before final ATOF assertions so buffered records flush.
Evidence may contain payload data; never commit it or authorization headers.
Evidence location, automated commands, and shutdown grace period remain pending.

The smoke suite must also send explicit `stream=false` and `stream=true` Chat
Completions requests through agentgateway; agent API tests alone do not prove
both upstream protocol paths.

Issue #7 will automate bounded tests for unavailable gateway, unavailable Relay,
missing/invalid provider credentials, unknown aliases/direct target names, and
interrupted streams. Restore configuration and services after each case and
finish with a successful agent query. Never destroy or print the real credential
to simulate an authentication failure. Report missing access as **BLOCKED**, not
PASS; distinguish mocked faults from live-provider evidence.

## Restart, shutdown, and cleanup — planned

Changing the target or routing TOML requires a Relay restart; do not assume hot
reload. From `local-routing/` after implementation:

```bash
docker compose --env-file .env -f compose.yaml restart relay
# Wait for plugin activation/readiness before making another request.
docker compose --env-file .env -f compose.yaml down
```

Preserve evidence until assertions and review are complete. Do not use `down -v`
or delete evidence indiscriminately. The runbook will document exact evidence
cleanup paths once storage layout is implemented. Protect `.env` after shutdown;
stopping containers does not revoke a Bedrock API key.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| Connection refused on port 8000 | Container status, listener binding, port conflict, readiness |
| Agent healthy but inference fails | Gateway forwarding, Relay plugin activation, provider access |
| Bedrock authentication/authorization error | Key validity and permissions, target availability; never print key |
| Unknown model succeeds | Fail-closed configuration is missing; acceptance fails |
| Stream returns HTTP 200 but no completion | SSE error events, disconnects, timeout, upstream failure |
| Routing evidence missing | Plugin active, alias matches, exporter enabled, writable directory, graceful flush |
| Native plugin load fails | CPU/libc mismatch, manifest, checksum, trust policy, Relay/plugin compatibility |

## Validation record

| Check | Platform | Result |
| --- | --- | --- |
| Agent API examples checked against repository documentation | Source review only | Contract reviewed; not executed |
| AMD64 build and plugin activation | TBD | Not run |
| ARM64 build and plugin activation | TBD | Not run |
| Full-path ordinary and streamed inference | TBD | Not run; requires secure Bedrock credential |
| Failure/recovery suite | TBD | Not run |

Record tested commits, artifact versions/digests, host OS/CPU, native versus
emulated execution, and remaining blockers here as implementation progresses.
