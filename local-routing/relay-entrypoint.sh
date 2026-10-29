#!/bin/sh
set -eu
# Fail before serving if the secret or native plugin is absent/invalid.
: "${AWS_BEARER_TOKEN_BEDROCK:?Set the Bedrock API key in the ignored local .env}"
# Relay keeps desired plugin state beside the selected config. Do not write to
# the read-only configuration mount or persist this ephemeral runtime state.
mkdir -p "$HOME/runtime"
cp /etc/nemo-relay/config.toml /etc/nemo-relay/plugins.toml "$HOME/runtime/"
nemo-relay --config "$HOME/runtime/config.toml" plugins enable nvidia.switchyard
nemo-relay --config "$HOME/runtime/config.toml" plugins validate nvidia.switchyard
exec nemo-relay --config "$HOME/runtime/config.toml" --bind 0.0.0.0:4040
