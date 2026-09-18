"""In-process triage routing counters — development observability only.

Answers "what share of emails is handled by deterministic vs local ML vs LLM?"
without persistence and **without any email content**: only classification
method, confidences and routing reasons are recorded.

One process-wide singleton (:func:`get_classification_metrics`). The autouse
conftest fixture resets it between tests.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

_COUNTER_FIELDS = (
    "total",
    "deterministic",
    "ml",
    "llm",
    "llm_fallback_deterministic",
    "ml_attempted",
    "ml_accepted",
    "ml_rejected_low_confidence",
    "ml_rejected_conflict",
    "ml_rejected_unavailable",
    "ml_rejected_error",
    "deterministic_to_llm",
)

# ml_reject_reason -> the counter it feeds
_REJECT_BUCKET = {
    "low_confidence": "ml_rejected_low_confidence",
    "precedence_conflict": "ml_rejected_conflict",
    "signal_conflict": "ml_rejected_conflict",
    "invalid_label": "ml_rejected_conflict",
    "unavailable": "ml_rejected_unavailable",
    "disabled": "ml_rejected_unavailable",
    "predict_error": "ml_rejected_error",
}


@dataclass
class ClassificationMetrics:
    total: int = 0
    deterministic: int = 0
    ml: int = 0
    llm: int = 0
    llm_fallback_deterministic: int = 0
    ml_attempted: int = 0
    ml_accepted: int = 0
    ml_rejected_low_confidence: int = 0
    ml_rejected_conflict: int = 0
    ml_rejected_unavailable: int = 0
    ml_rejected_error: int = 0
    deterministic_to_llm: int = 0
    _ml_conf_sum: float = 0.0
    _ml_conf_n: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(self, routing: dict) -> None:
        """Fold one :meth:`TriageAgent.classify` routing outcome into the totals."""
        method = routing.get("method") or routing.get("classification_method")
        method = getattr(method, "value", method)
        reason = routing.get("ml_reject_reason")
        attempted = bool(routing.get("ml_attempted"))
        llm_invoked = bool(routing.get("llm_invoked"))
        conf = routing.get("ml_confidence")

        with self._lock:
            self.total += 1
            if method in ("deterministic", "ml", "llm", "llm_fallback_deterministic"):
                setattr(self, method, getattr(self, method) + 1)

            if attempted:
                self.ml_attempted += 1
                if conf is not None:
                    self._ml_conf_sum += float(conf)
                    self._ml_conf_n += 1

            if method == "ml":
                self.ml_accepted += 1
            elif reason in _REJECT_BUCKET:
                setattr(self, _REJECT_BUCKET[reason], getattr(self, _REJECT_BUCKET[reason]) + 1)

            if llm_invoked and not attempted:
                self.deterministic_to_llm += 1

    def snapshot(self) -> dict:
        with self._lock:
            avoided = self.deterministic + self.ml
            handled_by_llm = self.llm + self.llm_fallback_deterministic
            return {
                "total": self.total,
                "deterministic": self.deterministic,
                "ml": self.ml,
                "llm": self.llm,
                "llm_fallback_deterministic": self.llm_fallback_deterministic,
                "ml_attempted": self.ml_attempted,
                "ml_accepted": self.ml_accepted,
                "ml_rejected_low_confidence": self.ml_rejected_low_confidence,
                "ml_rejected_conflict": self.ml_rejected_conflict,
                "ml_rejected_unavailable": self.ml_rejected_unavailable,
                "ml_rejected_error": self.ml_rejected_error,
                "deterministic_to_llm": self.deterministic_to_llm,
                "ml_average_confidence": (
                    round(self._ml_conf_sum / self._ml_conf_n, 4) if self._ml_conf_n else None
                ),
                "llm_invocations": handled_by_llm,
                "llm_avoidance_rate": (
                    round(100.0 * avoided / self.total, 1) if self.total else 0.0
                ),
            }

    def reset(self) -> None:
        with self._lock:
            for name in _COUNTER_FIELDS:
                setattr(self, name, 0)
            self._ml_conf_sum = 0.0
            self._ml_conf_n = 0


_METRICS = ClassificationMetrics()


def get_classification_metrics() -> ClassificationMetrics:
    return _METRICS
