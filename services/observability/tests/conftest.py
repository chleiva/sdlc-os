from __future__ import annotations

import uuid

import pytest
from run_registry import RegistryService

from observability import EventSink, InMemoryAlertSink, MockSessionBrokerBackend, ObservabilityClient


@pytest.fixture
def tenant_id() -> str:
    return f"tenant-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def registry(tmp_path):
    svc = RegistryService(str(tmp_path / "registry.db"))
    yield svc
    svc.close()


@pytest.fixture
def sink(tmp_path):
    s = EventSink(path=tmp_path / "off_node_store.jsonl")
    yield s
    s.close()


@pytest.fixture
def alert_sink() -> InMemoryAlertSink:
    return InMemoryAlertSink()


@pytest.fixture
def session_backend() -> MockSessionBrokerBackend:
    return MockSessionBrokerBackend()


@pytest.fixture
def observability_client(sink, alert_sink, session_backend) -> ObservabilityClient:
    return ObservabilityClient(
        sink=sink,
        service_name="test-service",
        alert_sink=alert_sink,
        session_broker_backend=session_backend,
    )
