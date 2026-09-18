"""CLI entrypoint: ``python -m app.ml.train``.

Trains the local email classifier from a labelled JSONL dataset and writes the
model + metadata under ``backend/data/models/``. Never invoked automatically.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.ml.training import (
    DEFAULT_DATA_PATH,
    DEFAULT_MODEL_PATH,
    SAMPLE_DATA_PATH,
    TrainingDataError,
    train_and_save,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.ml.train",
        description="Train the local email pre-classifier (TF-IDF + LogisticRegression).",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help=f"labelled JSONL dataset (default: {DEFAULT_DATA_PATH})",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="use the bundled starter dataset (development / testing only)",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=DEFAULT_MODEL_PATH,
        help=f"output model path (default: {DEFAULT_MODEL_PATH})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.sample:
        data_path = SAMPLE_DATA_PATH
    elif args.data is not None:
        data_path = args.data
    elif DEFAULT_DATA_PATH.is_file():
        data_path = DEFAULT_DATA_PATH
    else:
        print(
            f"No dataset given and {DEFAULT_DATA_PATH} does not exist.\n"
            f"  • create it (one JSON object per line: "
            f'{{"subject": "...", "body": "...", "sender": "...", "label": "EXISTING_CATEGORY"}}), or\n'
            f"  • run with --sample to use the bundled starter dataset.",
            file=sys.stderr,
        )
        return 2

    print(f"Training from {data_path}")
    try:
        metadata = train_and_save(data_path, args.model)
    except TrainingDataError as exc:
        print(f"Training data error: {exc}", file=sys.stderr)
        return 2

    meta_out = Path(args.model).with_name(Path(args.model).stem + "_metadata.json")
    print(f"Saved model    -> {args.model}")
    print(f"Saved metadata -> {meta_out}")
    print(f"  samples : {metadata['num_samples']}")
    print(f"  labels  : {', '.join(metadata['labels'])}")
    if metadata.get("metrics"):
        print(f"  metrics : {metadata['metrics']}")
    for warning in metadata.get("warnings", []):
        print(f"  WARNING : {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
