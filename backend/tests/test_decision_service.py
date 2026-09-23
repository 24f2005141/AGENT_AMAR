"""Shared Jev decision bundle and one-call orchestration tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.action_agent import ActionAgent
from app.agents.amar_orchestrator import AMAROrchestrator
from app.agents.deadline_agent import DeadlineAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.core.config import Settings
from app.models.action import ActionType
from app.services.decision_service import LangChainJevDecisionClient, LayaDecisionClient
from tests.triage_helpers import make_email


class _FakeHttp:
    def __init__(self) -> None:
        self.calls = 0

    def post(self, url, *, json):
        self.calls += 1
        answers = {}
        for name in json["questions"]:
            if name == "category":
                answers[name] = _choice("OTHER", 0.94)
            elif name == "importance":
                answers[name] = _choice("MEDIUM", 0.92)
            elif name == "priority_adjustment":
                answers[name] = _choice("0", 0.97)
            elif name == "human_review":
                answers[name] = _noul(0.08)
            elif name.startswith("action__"):
                answers[name] = _noul(0.04)
            elif name.startswith("deadline__"):
                answers[name] = _choice("DEADLINE", 0.93)
        return _FakeResponse(
            {
                "answers": answers,
                "model": "jev-test",
                "id": "req_test",
                "usage": {"input_tokens": 123, "output_tokens": 17},
            }
        )


class _FakeResponse:
    def __init__(self, payload) -> None:
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _choice(value: str, confidence: float):
    return SimpleNamespace(
        type="choice",
        choice=value,
        confidence=confidence,
        probabilities={value: confidence},
    )


def _noul(probability: float):
    return SimpleNamespace(type="noul", noul=probability)


def _settings() -> Settings:
    return Settings(
        decision_provider="jev",
        typesafe_api_key="test-key",
        jev_choice_confidence_threshold=0.85,
        jev_noul_decision_threshold=0.85,
        llm_provider="none",
        ml_classifier_enabled=False,
    )


def _local_outputs(settings: Settings, email):
    triage_agent = TriageAgent(settings=settings, ml_classifier=None)
    action_agent = ActionAgent(settings=settings)
    deadline_agent = DeadlineAgent(settings=settings)
    priority_agent = PriorityAgent(settings=settings)
    triage = triage_agent.classify(
        email, allow_remote=False, record_metrics=False
    )
    action = action_agent.detect(email, triage, allow_remote=False)
    deadline = deadline_agent.analyze(
        email, triage, action, allow_remote=False
    )
    priority = priority_agent.score(
        email, triage, action, deadline, allow_remote=False
    )
    return triage, action, deadline, priority


def test_shared_bundle_batches_all_questions_and_caches():
    settings = _settings()
    email = make_email(subject="Question", body="Could you look at this and let me know?")
    triage, action, deadline, priority = _local_outputs(settings, email)
    client = LangChainJevDecisionClient(settings)
    fake = _FakeHttp()
    client._http = fake

    first = client.evaluate(
        email,
        triage=triage,
        action=action,
        deadline=deadline,
        priority=priority,
    )
    second = client.evaluate(
        email,
        triage=triage,
        action=action,
        deadline=deadline,
        priority=priority,
    )

    assert fake.calls == 1
    assert first.category.value == "OTHER"
    assert set(first.actions) == {item.value for item in ActionType}
    assert first.input_tokens == 123
    assert second.cache_hit is True


def test_orchestrator_uses_one_shared_jev_call_for_uncertain_email():
    settings = _settings()
    client = LangChainJevDecisionClient(settings)
    fake = _FakeHttp()
    client._http = fake
    orchestrator = AMAROrchestrator(
        TriageAgent(settings=settings, ml_classifier=None),
        ActionAgent(settings=settings),
        DeadlineAgent(settings=settings),
        PriorityAgent(settings=settings),
        settings=settings,
        decision_client=client,
    )

    output = orchestrator.process(
        make_email(subject="Question", body="Could you look at this and let me know?")
    )

    assert fake.calls == 1
    traces = output.data["agent_trace"]
    assert any(
        trace["agent"] == "Jev Decision Layer" and trace["method"] == "jev"
        for trace in traces
    )
    triage_trace = next(trace for trace in traces if trace["agent"] == "Triage Agent")
    assert triage_trace["method"] == "jev"


def test_laya_uses_same_bundle_locally_and_caches():
    settings = _settings().model_copy(update={"decision_provider": "laya"})
    email = make_email(subject="Question", body="Could you look at this and let me know?")
    triage, action, deadline, priority = _local_outputs(settings, email)
    client = LayaDecisionClient(settings)

    class FakeAgent:
        calls = 0

        def predict(self, state, questions):
            self.calls += 1
            fake = _FakeHttp()
            return fake.post("local", json={"questions": questions}).json()

    agent = FakeAgent()
    client._agent = agent
    first = client.evaluate(
        email, triage=triage, action=action, deadline=deadline, priority=priority
    )
    second = client.evaluate(
        email, triage=triage, action=action, deadline=deadline, priority=priority
    )

    assert agent.calls == 1
    assert first.category.value == "OTHER"
    assert first.model.endswith("/typed-decisions")
    assert second.cache_hit is True
