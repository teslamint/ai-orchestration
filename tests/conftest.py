"""Shared test environment for trusted producer evidence."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _producer_attestation_key(monkeypatch):
    monkeypatch.setenv(
        "AI_ORCHESTRATION_PRODUCER_ATTESTATION_KEY",
        "cycle-one-producer-attestation-key-32-bytes",
    )
