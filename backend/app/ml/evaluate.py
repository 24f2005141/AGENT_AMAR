"""Offline evaluation of the hybrid triage pipeline: ``python -m app.ml.evaluate``.

Runs every email in a synthetic evaluation dataset through the **real**
:class:`~app.agents.triage_agent.TriageAgent` (deterministic -> local ML -> LLM)
and reports the routing distribution, accuracy and the ML-threshold trade-off.

Network-free: the LLM is a fake that only records whether it *would* have been
called; rows that reach it are scored on the deterministic result. Nothing here
calls Gemini / OpenAI / Anthropic / Ollama or Gmail.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.triage_agent import TriageAgent
from app.core.config import Settings, _BACKEND_DIR
from app.ml.email_classifier import EmailMLClassifier
from app.models.email import BodyFormat, NormalizedEmail, SenderInfo
from app.services.llm_service import LLMClient, LLMUnavailableError

DEFAULT_DATASET_PATH = _BACKEND_DIR / "data" / "eval" / "email_eval_dataset.jsonl"
DEFAULT_MODEL_PATH = Settings().ml_classifier_model_path_resolved
DEFAULT_THRESHOLDS = (0.70, 0.75, 0.80, 0.85, 0.90)

_TS = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)


class _RecordingLLM(LLMClient):
    """Available, but every call fails — records that the LLM *would* run."""

    provider = "recording-fake"

    def __init__(self) -> None:
        self.invocations = 0
        self.model = "recording-fake"

    @property
    def is_available(self) -> bool:
        return True

    def complete_json(self, system: str, user: str, *, max_tokens: int = 512) -> dict[str, Any]:
        self.invocations += 1
        raise LLMUnavailableError("evaluation: LLM calls are disabled")


@dataclass(frozen=True)
class EvalRecord:
    subject: str
    body: str
    sender: str
    expected_label: str
    kind: str = "clear"
    links: tuple[str, ...] = ()


def load_eval_dataset(path: str | Path) -> list[EvalRecord]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Evaluation dataset not found: {path}")
    rows: list[EvalRecord] = []
    for lineno, raw in enumerate(path.read_text("utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        rows.append(
            EvalRecord(
                subject=str(obj.get("subject", "") or ""),
                body=str(obj.get("body", "") or ""),
                sender=str(obj.get("sender", "") or ""),
                expected_label=str(obj["expected_label"]).strip(),
                kind=str(obj.get("kind", "clear")),
                links=tuple(obj.get("links", []) or ()),
            )
        )
    if not rows:
        raise ValueError(f"{path}: no usable rows")
    return rows


def _make_email(rec: EvalRecord, idx: int) -> NormalizedEmail:
    return NormalizedEmail(
        email_id=f"eval_{idx:04d}",
        thread_id=f"eval_thread_{idx:04d}",
        sender=SenderInfo(name=None, email=rec.sender),
        to=["student@example.com"],
        subject=rec.subject,
        body=rec.body,
        body_format=BodyFormat.TEXT,
        received_at=_TS,
        labels=["INBOX", "UNREAD"],
        is_unread=True,
        attachments=[],
        links=list(rec.links),
        has_links=bool(rec.links),
        body_parse_error=False,
        needs_human_review=False,
        source="gmail",
        ingested_at=_TS,
    )


@dataclass
class RowResult:
    expected: str
    predicted: str
    method: str
    kind: str
    deterministic_confidence: float
    ml_attempted: bool
    ml_confidence: float | None
    ml_reject_reason: str | None
    llm_invoked: bool

    @property
    def correct(self) -> bool:
        return self.expected == self.predicted


@dataclass
class EvalResult:
    threshold: float
    rows: list[RowResult] = field(default_factory=list)

    # -- derived ---------------------------------------------------
    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def correct(self) -> int:
        return sum(1 for r in self.rows if r.correct)

    @property
    def accuracy(self) -> float:
        return round(100.0 * self.correct / self.total, 1) if self.total else 0.0

    def _subset(self, pred):
        return [r for r in self.rows if pred(r)]

    def _acc(self, subset: list[RowResult]) -> float | None:
        if not subset:
            return None
        return round(100.0 * sum(1 for r in subset if r.correct) / len(subset), 1)

    @property
    def by_method(self) -> Counter:
        return Counter(r.method for r in self.rows)

    @property
    def llm_invocations(self) -> int:
        return sum(1 for r in self.rows if r.llm_invoked)

    @property
    def llm_avoidance_rate(self) -> float:
        if not self.total:
            return 0.0
        return round(100.0 * (self.total - self.llm_invocations) / self.total, 1)

    @property
    def ml_accepted(self) -> int:
        return sum(1 for r in self.rows if r.method == "ml")

    @property
    def ml_accuracy(self) -> float | None:
        return self._acc(self._subset(lambda r: r.method == "ml"))

    @property
    def deterministic_accuracy(self) -> float | None:
        return self._acc(self._subset(lambda r: r.method == "deterministic"))

    @property
    def safety_violations(self) -> list[RowResult]:
        """safety-kind rows where the *local ML* was accepted with a wrong label
        (i.e. the ML layer overrode a safety-critical email). Must be empty."""
        return [
            r for r in self.rows
            if r.kind == "safety" and r.method == "ml" and not r.correct
        ]

    @property
    def ml_attempted(self) -> int:
        return sum(1 for r in self.rows if r.ml_attempted)

    def to_dict(self) -> dict:
        by_kind = {}
        for kind in sorted({r.kind for r in self.rows}):
            subset = self._subset(lambda r, k=kind: r.kind == k)
            by_kind[kind] = {"n": len(subset), "accuracy": self._acc(subset)}
        reject = Counter(
            r.ml_reject_reason
            for r in self.rows
            if r.ml_reject_reason and r.ml_reject_reason != "deterministic_confident"
        )
        return {
            "threshold": self.threshold,
            "total": self.total,
            "correct": self.correct,
            "accuracy": self.accuracy,
            "routing": dict(self.by_method),
            "ml_attempted": self.ml_attempted,
            "ml_accepted": self.ml_accepted,
            "ml_accuracy_when_accepted": self.ml_accuracy,
            "deterministic_accuracy": self.deterministic_accuracy,
            "ml_reject_reasons": dict(reject),
            "llm_invocations": self.llm_invocations,
            "llm_avoidance_rate": self.llm_avoidance_rate,
            "by_kind": by_kind,
            "ml_safety_violations": len(self.safety_violations),
        }


def evaluate(
    dataset: list[EvalRecord],
    model_path: str | Path,
    threshold: float,
    *,
    triage_llm_threshold: float | None = None,
) -> tuple[EvalResult, bool]:
    """Run the whole dataset through a real TriageAgent. Returns (result, model_loaded)."""
    kwargs: dict[str, Any] = {"ml_classifier_enabled": True, "ml_classifier_threshold": threshold}
    if triage_llm_threshold is not None:
        kwargs["triage_llm_threshold"] = triage_llm_threshold
    settings = Settings(**kwargs)

    classifier = EmailMLClassifier(model_path, settings=settings)
    agent = TriageAgent(settings=settings, llm_client=_RecordingLLM(), ml_classifier=classifier)

    result = EvalResult(threshold=threshold)
    for idx, rec in enumerate(dataset):
        out = agent.classify(_make_email(rec, idx))
        routing = out.data["signals"].get("classification_routing", {})
        result.rows.append(
            RowResult(
                expected=rec.expected_label,
                predicted=out.data["category"],
                method=str(routing.get("method", out.data["signals"]["classification_method"])),
                kind=rec.kind,
                deterministic_confidence=float(routing.get("deterministic_confidence", 0.0)),
                ml_attempted=bool(routing.get("ml_attempted")),
                ml_confidence=routing.get("ml_confidence"),
                ml_reject_reason=routing.get("ml_reject_reason"),
                llm_invoked=bool(routing.get("llm_invoked")),
            )
        )
    return result, classifier.is_available()


def compare_thresholds(
    dataset: list[EvalRecord], model_path: str | Path, thresholds
) -> list[dict]:
    table: list[dict] = []
    for t in thresholds:
        res, _ = evaluate(dataset, model_path, t)
        table.append(
            {
                "threshold": t,
                "ml_accepted": res.ml_accepted,
                "ml_accuracy": res.ml_accuracy,
                "llm_calls": res.llm_invocations,
                "llm_avoidance": res.llm_avoidance_rate,
            }
        )
    return table


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt_pct(v: float | None) -> str:
    return "  n/a" if v is None else f"{v:5.1f}%"


def _render(result: EvalResult, model_loaded: bool, table: list[dict]) -> str:
    d = result.to_dict()
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("HYBRID TRIAGE EVALUATION")
    lines.append("=" * 60)
    if not model_loaded:
        lines.append("!! No usable ML model — every uncertain email routes to the LLM.")
    lines.append(f"Dataset rows      : {d['total']}")
    lines.append(f"ML threshold      : {result.threshold:.2f}")
    lines.append("")
    lines.append(f"Correct           : {d['correct']}/{d['total']}")
    lines.append(f"Accuracy          : {d['accuracy']:.1f}%")
    lines.append("")
    lines.append("Routing:")
    for method in ("deterministic", "ml", "llm", "llm_fallback_deterministic"):
        n = d["routing"].get(method, 0)
        share = (100.0 * n / d["total"]) if d["total"] else 0.0
        lines.append(f"  {method:<28} {n:>4}  ({share:4.1f}%)")
    lines.append("")
    lines.append(f"ML attempted            : {d['ml_attempted']}")
    lines.append(f"ML accepted             : {d['ml_accepted']}")
    lines.append(f"ML accuracy when accepted: {_fmt_pct(d['ml_accuracy_when_accepted'])}")
    lines.append(f"Deterministic accuracy  : {_fmt_pct(d['deterministic_accuracy'])}")
    if d["ml_reject_reasons"]:
        lines.append(f"ML reject reasons       : {d['ml_reject_reasons']}")
    lines.append("")
    lines.append(f"LLM invocations   : {d['llm_invocations']}/{d['total']}")
    lines.append(f"LLM avoided       : {d['llm_avoidance_rate']:.1f}%")
    lines.append("")
    lines.append("Accuracy by case kind:")
    for kind, info in d["by_kind"].items():
        lines.append(f"  {kind:<12} {info['n']:>3}   {_fmt_pct(info['accuracy'])}")
    lines.append("")
    lines.append(f"ML safety violations (ML accepted a wrong safety-critical label): "
                 f"{d['ml_safety_violations']}")
    lines.append("")
    lines.append("Threshold trade-off:")
    lines.append("  thr    ML accepted   ML acc    LLM calls   LLM avoidance")
    for row in table:
        lines.append(
            f"  {row['threshold']:.2f}   {row['ml_accepted']:>8}     "
            f"{_fmt_pct(row['ml_accuracy'])}   {row['llm_calls']:>7}     "
            f"{row['llm_avoidance']:5.1f}%"
        )
    lines.append("=" * 60)
    return "\n".join(lines)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m app.ml.evaluate",
        description="Offline evaluation of the deterministic -> ML -> LLM triage pipeline.",
    )
    p.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    p.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    p.add_argument(
        "--threshold",
        type=float,
        default=Settings().ml_classifier_threshold,
        help="ML threshold for the detailed run (default: ML_CLASSIFIER_THRESHOLD)",
    )
    p.add_argument(
        "--thresholds",
        type=str,
        default=",".join(str(t) for t in DEFAULT_THRESHOLDS),
        help="comma-separated thresholds for the trade-off table",
    )
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        dataset = load_eval_dataset(args.dataset)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    thresholds = [float(x) for x in args.thresholds.split(",") if x.strip()]
    result, model_loaded = evaluate(dataset, args.model, args.threshold)
    table = compare_thresholds(dataset, args.model, thresholds)

    if args.json:
        print(json.dumps(
            {"detailed": result.to_dict(), "model_loaded": model_loaded, "threshold_table": table},
            indent=2,
        ))
    else:
        print(_render(result, model_loaded, table))
        if not model_loaded:
            print("\nHint: train a model first — python -m app.ml.train --sample", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
