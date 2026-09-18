"""Offline evaluation CLI (app/ml/evaluate.py) — no network, no real LLM."""

from __future__ import annotations

import json

import pytest

from app.ml import evaluate as ev
from app.models.triage import TriageCategory
from tests.ml_helpers import train_model


@pytest.fixture
def model_path(tmp_path):
    train_model(tmp_path / "m.joblib")
    return tmp_path / "m.joblib"


def test_bundled_eval_dataset_loads_and_labels_are_valid():
    rows = ev.load_eval_dataset(ev.DEFAULT_DATASET_PATH)
    assert len(rows) >= 30
    for r in rows:
        assert TriageCategory(r.expected_label)
        assert r.kind in {"clear", "ambiguous", "conflict", "safety"}


def test_recording_llm_is_available_but_records_and_never_returns():
    llm = ev._RecordingLLM()
    assert llm.is_available is True
    with pytest.raises(Exception):
        llm.complete_json("s", "u")
    assert llm.invocations == 1


def test_evaluate_runs_offline_and_produces_stats(model_path):
    rows = ev.load_eval_dataset(ev.DEFAULT_DATASET_PATH)
    result, model_loaded = ev.evaluate(rows, model_path, threshold=0.75)

    assert model_loaded is True
    assert result.total == len(rows)
    d = result.to_dict()
    assert 0.0 <= d["accuracy"] <= 100.0
    assert 0.0 <= d["llm_avoidance_rate"] <= 100.0
    assert d["routing"]  # some method counts
    # deterministic-routed rows are never sent to the LLM
    assert all(not r.llm_invoked for r in result.rows if r.method == "deterministic")


def test_evaluate_reports_no_ml_safety_violations(model_path):
    rows = ev.load_eval_dataset(ev.DEFAULT_DATASET_PATH)
    for thr in (0.70, 0.75, 0.85):
        result, _ = ev.evaluate(rows, model_path, threshold=thr)
        assert result.safety_violations == [], f"ML overrode a safety row at threshold {thr}"


def test_evaluate_without_model_routes_everything_off_ml(tmp_path):
    rows = ev.load_eval_dataset(ev.DEFAULT_DATASET_PATH)
    result, model_loaded = ev.evaluate(rows, tmp_path / "missing.joblib", threshold=0.85)
    assert model_loaded is False
    assert result.ml_accepted == 0
    # every uncertain email would go to the LLM; confident ones stay deterministic
    assert result.by_method.get("ml", 0) == 0


def test_threshold_comparison_is_monotonic_in_ml_acceptance(model_path):
    rows = ev.load_eval_dataset(ev.DEFAULT_DATASET_PATH)
    table = ev.compare_thresholds(rows, model_path, [0.70, 0.80, 0.90])
    accepted = [row["ml_accepted"] for row in table]
    assert accepted == sorted(accepted, reverse=True)  # higher threshold -> fewer accepted
    avoidance = [row["llm_avoidance"] for row in table]
    assert avoidance == sorted(avoidance, reverse=True)


def test_cli_json_output(capsys, model_path):
    rc = ev.main(["--model", str(model_path), "--json", "--thresholds", "0.75,0.85"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "detailed" in payload and "threshold_table" in payload
    assert payload["model_loaded"] is True
    assert len(payload["threshold_table"]) == 2
    assert payload["detailed"]["ml_safety_violations"] == 0


def test_cli_text_output(capsys, model_path):
    rc = ev.main(["--model", str(model_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "HYBRID TRIAGE EVALUATION" in out
    assert "Threshold trade-off" in out
    assert "LLM avoided" in out
