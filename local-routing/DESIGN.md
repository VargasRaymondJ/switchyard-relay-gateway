# Local routing MVP design

The request path is base agent → agentgateway → Relay with its native Switchyard
plugin → Bedrock in us-east-1. Three application services run on one Compose
network. Only the agent and gateway publish loopback host ports. The agent
requests `switchyard/mvp`; Switchyard resolves it to
`nvidia.nemotron-nano-9b-v2` using passthrough routing.

Success means that target receives the request and returns content to the
agent. Intelligent selection, alternate providers, tools, persistent sessions,
databases, and telemetry collectors are out of scope. Unknown model names
reach a deliberately unserved loopback Relay upstream rather than a provider.
This is fail-closed forwarding, not model-name validation or an authorization
boundary.

Agent YAML points to the gateway; only Relay receives the Bedrock API key.
A narrowly scoped, owner-approved composition-root NIMS override creates a
fresh async HTTP transport per request, fixing the pinned Foundry/Strands
client incompatibility while preserving NIMS formatting and TLS configuration.
Other providers and shared packages are unchanged. The inherited Dockerfile
changes only to pin existing image references. The lockfile remains unchanged.

Relay 0.11.0 loads Switchyard plugin 0.3.2, built from Switchyard commit
`45e424d050addfbbe3bed8a61f55c440e7689eda`. agentgateway is v1.7.0.
Release checksums are committed in `checksums.sha256`. The publisher and
checksums share a distribution channel: integrity verification is not an
independent authenticity guarantee. Only this plugin has an integrity-only
trust exception. Native plugins run inside Relay.

The Debian image and inherited Python/uv images are digest-pinned. Debian
package installation still uses the distribution repositories; builds are
artifact-pinned, not guaranteed bit-for-bit reproducible.

GNU/Linux AMD64 and ARM64 artifact mappings are supplied. Docker Engine and
Docker Desktop on compatible hosts are intended deployment environments.
Native process validation on one architecture is not proof of Docker builds
or cross-platform behavior. See the runbook's validation record.

The provider client has no retries and a 60-second whole-call deadline.
agentgateway has 65-second header/backend and body-idle deadlines, no explicit
retry policy. The unchanged agent SDK may retry requests; tests allow a
240-second client deadline and retain all observed attempts.

Successful tests need content, proper protocol termination, gateway access
records, and an expected Switchyard decision. Missing served-model metadata
remains unknown. Fresh Compose projects/volumes and serial cases prevent stale
evidence from passing a new run. Negative tests require failure and subsequent
recovery. Incomplete or raced interruption tests report BLOCKED, not PASS.

No production security or compliance claim is made. Local HTTP, no user
authentication, and potentially sensitive Relay logs require synthetic prompts
and trusted local use. See `security/README.md` for the local exception.
