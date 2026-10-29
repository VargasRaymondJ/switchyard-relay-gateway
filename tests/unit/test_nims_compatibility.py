# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the locked NIMS transport incompatibility."""

import httpx
from openai import AsyncOpenAI

from foundry_strands_agent.factory import DEFAULT_MODEL_PROVIDER_FACTORIES

from strands_base_agent.application.nims import MODEL_PROVIDER_FACTORIES, create_nims_model


async def test_async_transport_is_fresh_and_owned_by_sdk():
    model = create_nims_model({"model": {"model_id": "switchyard/mvp", "nims_base_url": "http://localhost:8080/v1"}})
    previous = None
    for _ in range(2):
        args = model._resolve_client_args()
        transport = args["http_client"]
        assert isinstance(transport, httpx.AsyncClient)
        assert transport is not previous
        assert not transport.is_closed
        async with AsyncOpenAI(**args) as client:
            assert str(client.base_url) == "http://localhost:8080/v1/"
        assert transport.is_closed
        previous = transport


async def test_default_endpoint_and_other_providers_preserved():
    model = create_nims_model({"model": {"model_id": "example"}})
    args = model._resolve_client_args()
    assert args["base_url"] == "https://integrate.api.nvidia.com/v1"
    await args["http_client"].aclose()
    for name, factory in DEFAULT_MODEL_PROVIDER_FACTORIES.items():
        if name != "nims":
            assert MODEL_PROVIDER_FACTORIES[name] is factory
