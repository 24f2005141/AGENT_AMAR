"""Controlled feedback-driven retraining — ``python -m app.ml.retrain``.

    python -m app.ml.retrain run [--sample] [--no-feedback] [--threshold T]
    python -m app.ml.retrain promote --yes

``run`` builds a CANDIDATE model from (base training data + validated user
feedback), evaluates it on the holdout dataset, and writes it under
``data/models/candidate/`` **without touching the production model**. It prints a
candidate-vs-production comparison.

``promote`` copies the candidate over the production model — an explicit,
deliberate step (``--yes`` required). Nothing in the app or the test suite calls
either automatically.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import _BACKEND_DIR
from app.db.session import db_session
from app.ml.evaluate import evaluate, load_eval_dataset
from app.ml.feedback_dataset import (
    collect_feedback,
    to_training_records,
    write_feedback_export,
)
from app.ml.training import (
    DEFAULT_DATA_PATH,
    DEFAULT_MODEL_PATH,
    SAMPLE_DATA_PATH,
    TrainingDataError,
    load_training_data,
    train_and_save,
)

CANDIDATE_DIR = _BACKEND_DIR / "data" / "models" / "candidate"
CANDIDATE_MODEL_PATH = CANDIDATE_DIR / "email_classifier.joblib"
CANDIDATE_REPORT_PATH = CANDIDATE_DIR / "retrain_report.json"
FEEDBACK_EXPORT_PATH = CANDIDATE_DIR / "feedback_export.jsonl"
DEFAULT_EVAL_PATH = _BACKEND_DIR / "data" / "eval" / "email_eval_dataset.jsonl"


def _base_records(sample: bool, data_path: Path | None):
    if data_path is not None:
        return load_training_data(data_path), str(data_path)
    if sample:
        return load_training_data(SAMPLE_DATA_PATH), str(SAMPLE_DATA_PATH)
    if DEFAULT_DATA_PATH.is_file():
        return load_training_data(DEFAULT_DATA_PATH), str(DEFAULT_DATA_PATH)
    return load_training_data(SAMPLE_DATA_PATH), f"{SAMPLE_DATA_PATH} (fallback)"


def run(
    *,
    sample: bool = False,
    include_feedback: bool = True,
    threshold: float = 0.85,
    eval_path: Path = DEFAULT_EVAL_PATH,
    data_path: Path | None = None,
    candidate_model_path: Path | None = None,
) -> dict:
    """Train + evaluate a candidate. Returns the report dict. Does NOT promote."""
    candidate_model = Path(candidate_model_path) if candidate_model_path else CANDIDATE_MODEL_PATH
    candidate_dir = candidate_model.parent
    report_path = candidate_dir / "retrain_report.json"
    export_path = candidate_dir / "feedback_export.jsonl"

    base, base_src = _base_records(sample, data_path)
    base_dicts = [
        {"subject": r.subject, "body": r.body, "sender": r.sender, "label": r.label}
        for r in base
    ]

    feedback_records: list[dict] = []
    n_feedback_total = 0
    if include_feedback:
        with db_session() as session:
            examples = collect_feedback(session)
        n_feedback_total = len(examples)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        write_feedback_export(export_path, examples)
        feedback_records = to_training_records(examples)

    merged = base_dicts + feedback_records

    with tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as tmp:
        for rec in merged:
            tmp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        merged_path = tmp.name

    try:
        metadata = train_and_save(merged_path, candidate_model)
    finally:
        Path(merged_path).unlink(missing_ok=True)

    cand_acc = prod_acc = None
    eval_rows = 0
    if Path(eval_path).is_file():
        dataset = load_eval_dataset(eval_path)
        eval_rows = len(dataset)
        cand_res, cand_loaded = evaluate(dataset, candidate_model, threshold)
        cand_acc = cand_res.accuracy if cand_loaded else None
        if DEFAULT_MODEL_PATH.is_file():
            prod_res, prod_loaded = evaluate(dataset, DEFAULT_MODEL_PATH, threshold)
            prod_acc = prod_res.accuracy if prod_loaded else None

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_dataset": base_src,
        "base_samples": len(base_dicts),
        "feedback_examples_collected": n_feedback_total,
        "feedback_training_records_used": len(feedback_records),
        "merged_samples": len(merged),
        "candidate_model": str(candidate_model),
        "candidate_metadata": metadata,
        "eval_dataset": str(eval_path) if eval_rows else None,
        "eval_rows": eval_rows,
        "threshold": threshold,
        "candidate_holdout_accuracy": cand_acc,
        "production_holdout_accuracy": prod_acc,
        "delta_vs_production": (
            round(cand_acc - prod_acc, 2)
            if (cand_acc is not None and prod_acc is not None)
            else None
        ),
        "production_model_untouched": True,
        "promotion": "manual only — python -m app.ml.retrain promote --yes",
    }
    report_path.write_text(json.dumps(report, indent=2), "utf-8")
    return report


def promote(
    *,
    yes: bool = False,
    candidate_model_path: Path | None = None,
    production_model_path: Path | None = None,
) -> dict:
    """Copy the candidate over the production model. Deliberate; ``yes`` required."""
    candidate = Path(candidate_model_path) if candidate_model_path else CANDIDATE_MODEL_PATH
    production = Path(production_model_path) if production_model_path else DEFAULT_MODEL_PATH

    if not yes:
        raise PromotionRefused(
            f"Refusing to promote without --yes. Review the report next to {candidate} first."
        )
    if not candidate.is_file():
        raise PromotionRefused(f"No candidate model at {candidate}. Run a retrain first.")

    from app.ml.email_classifier import EmailMLClassifier

    probe = EmailMLClassifier(candidate)
    if not probe.is_available():
        raise PromotionRefused(f"Candidate does not load ({probe.load_error}). Not promoting.")

    production.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, production)
    cand_meta = candidate.with_name(candidate.stem + "_metadata.json")
    if cand_meta.is_file():
        shutil.copy2(cand_meta, production.with_name(production.stem + "_metadata.json"))
    return {"promoted": str(production), "from": str(candidate)}


class PromotionRefused(RuntimeError):
    """Raised when :func:`promote` will not overwrite the production model."""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _render(report: dict) -> str:
    lines = ["=" * 60, "FEEDBACK-DRIVEN CANDIDATE RETRAIN", "=" * 60]
    lines.append(f"Base dataset            : {report['base_dataset']}")
    lines.append(f"Base samples            : {report['base_samples']}")
    lines.append(f"Feedback examples        : {report['feedback_examples_collected']}")
    lines.append(f"Feedback records used     : {report['feedback_training_records_used']}")
    lines.append(f"Merged training samples   : {report['merged_samples']}")
    lines.append("")
    lines.append(f"Candidate model          : {report['candidate_model']}")
    if report["eval_rows"]:
        lines.append(f"Holdout rows              : {report['eval_rows']}")
        lines.append(f"Candidate accuracy        : {report['candidate_holdout_accuracy']}")
        lines.append(f"Production accuracy        : {report['production_holdout_accuracy']}")
        lines.append(f"Delta vs production       : {report['delta_vs_production']}")
    lines.append("")
    lines.append("Production model: UNTOUCHED.")
    lines.append("Promote deliberately: python -m app.ml.retrain promote --yes")
    lines.append("=" * 60)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ml.retrain")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="train + evaluate a candidate (no promotion)")
    run_p.add_argument("--sample", action="store_true", help="use the bundled starter dataset")
    run_p.add_argument("--no-feedback", action="store_true", help="ignore user feedback")
    run_p.add_argument("--threshold", type=float, default=0.85)
    run_p.add_argument("--eval", type=Path, default=DEFAULT_EVAL_PATH)

    prom_p = sub.add_parser("promote", help="copy the candidate over production (deliberate)")
    prom_p.add_argument("--yes", action="store_true", help="required confirmation")

    args = parser.parse_args(argv)

    if args.cmd == "run":
        try:
            report = run(
                sample=args.sample,
                include_feedback=not args.no_feedback,
                threshold=args.threshold,
                eval_path=args.eval,
            )
        except TrainingDataError as exc:
            print(f"training data error: {exc}", file=sys.stderr)
            return 2
        print(_render(report))
        print(f"\nReport: {CANDIDATE_REPORT_PATH}")
        return 0

    try:
        result = promote(yes=args.yes)
    except PromotionRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Promoted {result['from']} -> {result['promoted']}")
    print("Restart the backend (or call app.ml.email_classifier.reset_cache()) to load it.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
