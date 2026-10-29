# Learning the agent routing stack

This guide is for an engineer who can read basic Python and run shell commands
but is new to agents, model gateways, and Docker networking.

Start with this guide to understand the system. Use the [runbook](README.md) for
setup and exact operating commands, and the [design](DESIGN.md) for scope and
acceptance criteria. Commands below assume you are in `local-routing/` unless
stated otherwise.

**Validation matters:** implementation is not the same as demonstrated success.
Native Linux AMD64 tests verified startup and provider-error propagation.
Successful Bedrock inference, Docker builds/runtime, and ARM64 execution remain
unverified in the recorded implementation environment. Consult the runbook's
validation section before treating any platform as supported in practice.

## 1. Understand the request path

```text
Your terminal
  POST http://127.0.0.1:8000/api/v1/query
       |
       v
Base agent container
  Python API + Foundry + Strands
  Turns a query into model messages
  Requests model alias "switchyard/mvp"
       |
       | POST http://agentgateway:8080/v1/chat/completions
       v
agentgateway container
  Matches the HTTP method/path and forwards the request
       |
       v
Relay container :4040
  Hosts the native Switchyard plugin
    alias → passthrough route → target → provider client
       |
       | HTTPS + Bedrock API key
       v
AWS Bedrock in us-east-1
  Runs NVIDIA Nemotron Nano 9B v2
       |
       +---- Response returns through the same components to the agent
```

There are **three local application containers**, not four. Switchyard runs as
a native plugin inside Relay. The model itself is hosted by AWS; Docker does
not download model weights or require a local GPU for this configuration.

An agent is the program managing the conversation and model interaction. A
model is the remote inference engine generating text. They are not synonymous.
This agent currently has no enabled tools or persistent memory.

## 2. What each component does

| Component | Responsibility here | What it does not do here |
| --- | --- | --- |
| Base agent | Accept queries, construct model interactions, return API responses | Select among multiple provider models |
| agentgateway | Match an HTTP route, proxy requests/streams, apply deadlines, log access | Choose the upstream model or hold the Bedrock key |
| Relay | Run the server, load/validate the plugin, provide its runtime and evidence output | Run model inference locally |
| Switchyard plugin | Resolve the alias to a target and execute the provider request | Classify prompts or choose a fallback in this MVP |
| Bedrock | Authenticate provider requests and serve the configured model | Operate the local agent or gateway |
| Docker Compose | Build/start services, connect their network, mount config and evidence | Prove that provider authentication or inference works |

### Base agent: application code and shared infrastructure

The repository is a **composition root**: it assembles reusable packages into
one service. Most agent infrastructure is in the locked `foundry-agent-*`
packages and Strands SDK, not copied into this repository.

Read these files in order:

1. [`config/agent.yaml`](config/agent.yaml): provider, endpoint, model alias,
   system prompt, and disabled optional capabilities.
2. [`server.py`](../strands_base_agent/server.py): creates the application and
   registers the agent factory/backend.
3. [`application/factory.py`](../strands_base_agent/application/factory.py):
   loads configuration and constructs the dependency container.
4. [`api/routes/query.py`](../strands_base_agent/api/routes/query.py):
   ordinary and streaming HTTP endpoints.
5. [`application/nims.py`](../strands_base_agent/application/nims.py):
   the narrowly scoped compatibility adapter described below.

**Dependency injection** means registering how a service is constructed, then
asking the container for it rather than manually constructing it everywhere.
This gives us a supported place to override one model provider without editing
the shared package.

The `nims` provider speaks an OpenAI-compatible Chat Completions protocol. In
this configuration it points to our gateway, not directly to NVIDIA or AWS.
An API format and the company actually serving the model are different things.
The agent does not receive the real provider credential.

### Why the NIMS compatibility adapter exists

The pinned Foundry factory supplies `httpx.Client`, a synchronous HTTP client.
The locked Strands OpenAI adapter expects `httpx.AsyncClient`. Without the
override, a query fails before reaching the gateway.

The override retains NIMS request formatting and the existing TLS context,
but creates a fresh async transport per model request. The SDK owns and closes
that transport. Reusing it on the next call would risk using a closed client
or connections associated with a different event loop.

`MODEL_PROVIDER_FACTORIES` copies the upstream provider registry and replaces
only `nims`. Other providers remain registered. Tests live in
[`test_nims_compatibility.py`](../tests/unit/test_nims_compatibility.py).
The adapter touches a protected SDK method, so recheck it on dependency
upgrades. Remove it only after an upstream fix is verified by tests and an
actual agent request.

### agentgateway: HTTP routing, not model routing

[`config/agentgateway.yaml`](config/agentgateway.yaml) accepts exactly
`POST /v1/chat/completions` and forwards to `relay:4040`. Unrelated routes do
not match. It does not inspect the prompt to choose a model.

Its request/backend deadlines and response idle timeout are 65 seconds.
An idle timeout limits the gap between response activity; it is not necessarily
a whole-stream deadline. There is no explicit gateway retry policy.

Access logs tell you the path, status, destination and elapsed time. They help
answer “did the request cross this boundary?” without needing prompt logging.
The administrative listener is not published to the host.

### Relay: plugin host and evidence runtime

[`relay-entrypoint.sh`](relay-entrypoint.sh) checks for the required secret,
copies configuration into writable runtime state, enables and validates the
plugin, and starts Relay directly. The mounted source configuration remains
read-only. Runtime plugin state is ephemeral.

[`config/relay/plugins.toml`](config/relay/plugins.toml) names the plugin manifest
and Switchyard configuration. Activation is required: a plugin startup failure
must not quietly leave an apparently working routing service.

The plugin uses a narrowly scoped `integrity_only` trust exception. A native
plugin executes inside the host process, not in a separate security sandbox.
Checksums detect changed bytes relative to expected hashes; they do not
independently prove publisher trust when hashes and binaries come from the
same distribution channel.

Relay writes ATOF JSON-lines evidence into a named Docker volume. Each line is
a structured event; graceful shutdown allows buffered events to flush.
Full-payload export is disabled, but evidence must still be treated as sensitive.

### Switchyard: alias, route, target, provider client

Read [`config/routes.toml`](config/routes.toml) from the bottom upward:

```text
routes.mvp
  id = "switchyard/mvp"        ← public model alias requested by the agent
  type = "passthrough"         ← use the configured target, no classifier
  target = "primary"           ← reference to the target table

targets.primary
  id = "nvidia.nemotron-nano-9b-v2"  ← actual provider model identifier
  llm_client = "bedrock"       ← reference to provider-client configuration

llm_clients.bedrock
  format = "openai_chat"
  base_url = Bedrock endpoint in us-east-1
  api_key_env = name of the secret environment variable
```

The TOML table keys (`mvp`, `primary`, `bedrock`) are configuration references.
The two `id` values are externally meaningful model names. Confusing a table
key with a model identifier is an easy configuration error.

The stable alias lets you change the target without rewriting agent code.
Today “routing works” means the selected target receives the request and its
response returns to the agent. It does not mean the router has made an
intelligent choice among models.

Unknown aliases and direct provider model names do not match this route.
Relay's unmanaged upstreams are deliberately set to an unserved loopback
address, so they fail instead of reaching an unintended provider. This is
fail-closed forwarding, not authentication or an authorization policy.

### Bedrock: the remote inference boundary

Only Relay receives `AWS_BEARER_TOKEN_BEDROCK` from the ignored `.env` file.
The provider client uses HTTPS, a 60-second deadline and zero configured
provider retries. The surrounding agent SDK may still retry, so do not assume
one API query always equals one provider attempt.

A reachable endpoint does not prove your account can use a particular model
in a particular region. Check model availability, permissions, lifecycle and
pricing. The configured Nano model has a recorded Legacy/EOL risk in the
runbook; do not silently substitute a different model during testing.

## 3. Docker concepts you need here

An **image** contains software and runtime dependencies. A **container** is a
running instance of that image. A **bind mount** exposes a host file or directory;
a **named volume** is Docker-managed persistent storage.

[`compose.yaml`](compose.yaml) assembles the three services:

- Configuration files are read-only bind mounts, so you can review/edit them
  without baking credentials or environment-specific settings into images.
- Relay evidence uses a named volume and survives ordinary `down`.
- Service names resolve on the Compose network. From the agent,
  `agentgateway` names the gateway container; `127.0.0.1` would name the agent
  container itself.
- Published addresses `127.0.0.1:8000` and `127.0.0.1:8080` are for host access.
  Relay has no published host port.
- Health checks order startup, but cannot prove that inference succeeds.

The local Dockerfile packages Relay/plugin and gateway binaries. The inherited
root Dockerfile builds the Python agent. Release checksums and image digests
pin artifacts; distribution package installation is not snapshot-pinned, so
these builds are not promised to be bit-for-bit reproducible.

**Common mistake:** running Compose from the repository root selects a different
configuration. Use the runbook's explicit `-f compose.yaml` commands from
`local-routing/`.

## 4. Ordinary responses versus streaming

`POST /api/v1/query` returns one JSON response containing the agent's answer.
The agent may still use a streaming model call internally. An ordinary API
response therefore does not prove the upstream non-streaming path works.

`POST /api/v1/query/stream` uses **Server-Sent Events (SSE)**: text event records
separated by blank lines. The agent protocol uses events such as `token`,
`result`, `done`, and `error`. The upstream Chat Completions stream instead
uses JSON deltas in `data:` records and a terminal `[DONE]`.

These are different protocols. Tests must validate the appropriate one:

- Ordinary response: successful HTTP status and nonempty content.
- Agent stream: content, terminal `done`, and no error event.
- Gateway model stream: content, `[DONE]`, and no error payload.

An SSE connection can open with HTTP 200 and fail later. The native
invalid-credential test demonstrated exactly that: the agent stream emitted
an error event. Neither HTTP 200 nor a clean curl exit proves model success.

## 5. Learn by tracing one request

First follow the [runbook](README.md) to start the stack. Use synthetic prompts:
live calls incur charges and logs may contain sensitive material.

1. Check `/api/v1/health`. This proves the API is responsive, not that Bedrock
   is authenticated.
2. Send the ordinary API example from the runbook. Check status and content.
3. Inspect gateway access logs. Confirm the Chat Completions path and status.
4. Run the explicit smoke suite after stopping the regular stack. It creates
   a fresh Compose project, isolates evidence, and exercises both gateway
   stream modes plus both agent endpoints.
5. Inspect `results.json`, per-case response/access files and flushed ATOF.
   A routing decision should identify the expected algorithm and selected model.

Missing `served_model` metadata means unknown, not a match. A decision record
alone does not prove a successful response. Conversely, text returned by an
agent without the expected routing evidence does not establish the intended
path. Successful acceptance needs both behavior and evidence.

The tests execute serially and flush preceding records before success cases.
Otherwise a previous successful request could accidentally satisfy the next
test. Keep all attempts visible; retries complicate event counts.

### Debug the earliest failing boundary

| Observation | Investigate first |
| --- | --- |
| Agent health fails | Container startup, port binding, agent configuration |
| Query fails and no gateway request appears | Agent model construction, SDK/transport compatibility, network address |
| Gateway request appears but forwarding fails | Backend hostname, Relay readiness, timeout settings |
| Managed alias returns provider error | Plugin execution, provider endpoint, permissions/credential/model availability |
| HTTP 200 stream contains error | Actual SSE events; do not treat status alone as success |
| Response succeeds but decision evidence is absent | Alias, plugin activation, exporter permissions, flush and test isolation |

Do not print a rendered Compose configuration containing expanded secrets.
Review logs locally before sharing them. Never put a key in an issue or a test
fixture.

## 6. How to extend the project

Make one change at a time, keep a known-good baseline, and add tests for both
success and failure. The examples below describe development work; they do not
claim additional configurations have already been validated.

### Change the prompt or agent behavior

Edit `config/agent.yaml` for the system prompt and descriptive fields, then
restart the agent. For Python behavior, use the existing application/routes
and composition-root extension points, not edits inside `.venv`.

Do not assume every generic model option in YAML reaches the provider: the
current NIMS factory forwards a limited set of fields. Inspect construction
and outgoing behavior before relying on temperature, token limits or streaming
flags.

### Change the target model behind the existing alias

1. Confirm the replacement model supports the required API format in the chosen
   region and account, including streaming.
2. Change `targets.primary.id` in `config/routes.toml`. Change the client endpoint
   only if the provider/region requires it.
3. Update the expected `MODEL` in `scripts/smoke.py` and the documentation.
   Keep `switchyard/mvp` stable if callers should not change.
4. Restart Relay, check activation, then run ordinary, streaming and failure
   acceptance tests with fresh evidence.

A configuration edit is not a hot-reload contract. Changing the target also
changes expected evidence, cost and possibly the response protocol.

### Add a second explicit alias

After checking support in the pinned Switchyard schema, add a target and another
passthrough route. This illustrative addition references the existing client:

```toml
[targets.experimental]
id = "REPLACE_WITH_A_SUPPORTED_MODEL_ID"
llm_client = "bedrock"

[routes.experimental]
id = "switchyard/experimental"
type = "passthrough"
target = "experimental"
```

The placeholder is not a usable model. Existing callers keep their old route.
Add tests asserting each alias selects its own target and unknown aliases still
fail. To make the base agent use the new alias, change its `model_id` explicitly.
Two aliases are still deterministic routing, not automatic model selection.

### Add a provider or intelligent selection

A new provider needs a supported client format, endpoint, secret-injection
path, target configuration, timeout policy and isolated tests. Do not add its
real credential to the agent or gateway.

For classifiers, fallback, cost-aware or other routing policies, consult the
schema and documentation for the **pinned plugin release** first. Do not assume
features on upstream main are present in the packaged binary. Define selection
criteria, failure semantics, latency/cost budgets and expected evidence before
implementation. Preserve the passthrough route as a baseline.

Never retry a partially delivered stream as though nothing happened: callers
could receive duplicate or inconsistent output. Automatic fallback also changes
the current acceptance contract that only the named target should serve a call.

### Add tools or memory

Tools let an agent call functions; memory persists state between requests.
Both are intentionally disabled here. Use the existing
[tool guide](../docs/foundry/guides/adding-tools.md) and repository extension
patterns rather than building a second agent framework.

Before enabling a tool, define its allowed actions, credential scope, input
validation, timeouts and approval requirements. Test it without real destructive
effects. Before adding memory, define storage, session isolation, retention and
deletion. Keep routing tests session-free so old conversations do not affect
their results.

### Upgrade Relay, Switchyard, gateway or dependencies

Treat a native plugin and its host as a compatibility pair. Check release notes,
manifest compatibility, CPU architecture, libc requirements and configuration
schema. Update expected hashes from trusted release artifacts, never disable
checksum verification to get a build passing.

Validate config and real plugin activation, then ordinary/streaming/failure
behavior on each intended architecture. Recheck license/attribution files.
For Python changes, update the lockfile deliberately, rerun the async-client
regression tests, and retest the full agent path.

### Move beyond trusted local development

Publishing on `0.0.0.0` is not a production-readiness change by itself. Before
remote access, design authentication, authorization, TLS, secret rotation,
network controls, quotas/rate limits, retention, monitoring and deployment
rollback. Reassess the native-plugin trust exception and update the security
checklist. This MVP is not a production security template.

## 7. Testing and review checklist

From the repository root, run the required quality gate:

```bash
just check
python3 -m unittest discover -s local-routing/scripts -p 'test_*.py'
```

`just check` covers lint, formatting, type checking and application tests.
The second command covers smoke-helper assertions and local HTTP deadline
behavior. Neither proves a live provider call works.

Use the runbook's Docker smoke commands for real inference and the disruptive
failure suite. Those require a key, incur charges, and take ownership of the
test ports. Never run destructive fault tests against someone else's shared
stack.

Before calling a change complete:

- Explain which component owns the change and why.
- Verify both protocols if model-facing behavior changed.
- Assert the intended model and fresh evidence, not only returned text.
- Demonstrate failures and recovery; record blocked cases honestly.
- Check secrets/evidence are excluded from Git and build contexts.
- Update the runbook, design and relevant security findings.

## 8. Suggested learning exercises

1. Draw the request path from memory and label which process has the provider
   key. Compare it with the diagram above.
2. Change only the system prompt, restart the agent, and explain why the
   routing decision should remain unchanged.
3. Run the unknown-model failure case and explain why “no unexpected success”
   is a useful safety property but not an authorization mechanism.
4. Compare ordinary agent output with SSE output. Identify content, termination
   and error signals without using HTTP status as the only criterion.
5. Propose a second alias with its tests and rollback plan before implementing it.

You understand the MVP when you can identify the earliest failed boundary,
explain why a result counts as success, and predict which files and tests must
change for a new behavior.

## References

- [NVIDIA NeMo Relay](https://github.com/NVIDIA/NeMo-Relay)
- [Relay plugins and release bundles](https://github.com/NVIDIA/NeMo-Relay-Plugins)
- [NVIDIA NeMo Switchyard](https://github.com/NVIDIA-NeMo/Switchyard)
- [agentgateway](https://github.com/agentgateway/agentgateway)
- [Project runbook](README.md) and [design contract](DESIGN.md)
- [Repository extension guidance](../AGENTS.md)

Use the release versions recorded in the runbook when reading upstream docs;
the latest documentation can describe behavior our pinned artifacts do not have.
