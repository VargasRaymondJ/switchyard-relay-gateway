# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""Compatibility adapter for the locked Foundry NIMS/Strands OpenAI pair."""

from typing import Any

import httpx

from foundry_strands_agent.factory import DEFAULT_MODEL_PROVIDER_FACTORIES, NIMSModel
from foundry_strands_agent.tls import create_tls_context


class AsyncNIMSModel(NIMSModel):
    """Preserve NIMS formatting and TLS while using per-request async clients."""

    def _resolve_client_args(self) -> dict[str, Any]:
        # Strands closes AsyncOpenAI after each request. Never reuse a closed
        # transport across agent calls or event loops.
        args = super()._resolve_client_args()
        return {**args, "http_client": httpx.AsyncClient(verify=create_tls_context())}


def create_nims_model(config: dict[str, Any]) -> AsyncNIMSModel:
    """Construct the existing NIMS provider with its original configuration."""
    return AsyncNIMSModel(
        client_args={
            "base_url": config["model"].get("nims_base_url") or "https://integrate.api.nvidia.com/v1",
            "api_key": config["model"].get("nims_api_key") or "no-key",
        },
        model_id=config["model"]["model_id"],
    )


MODEL_PROVIDER_FACTORIES = {**DEFAULT_MODEL_PROVIDER_FACTORIES, "nims": create_nims_model}
