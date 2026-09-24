"""Command-line interface for DocSem."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

from dotenv import load_dotenv

from .api import DocSem
from .config import DocSemConfig, ExtractorConfig, ProviderName
from .exceptions import DocSemError

load_dotenv()


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the DocSem CLI."""
    parser = argparse.ArgumentParser(
        prog="docsem",
        description="Extract and structure a document into DocumentIR.",
    )

    parser.add_argument(
        "input_path",
        type=Path,
        help="Path to the input document.",
    )

    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Optional path to save the resulting DocumentIR as JSON.",
    )

    return parser


def serialize_document_ir(document_ir: object) -> str:
    """Serialize a DocumentIR instance into formatted JSON."""
    if is_dataclass(document_ir):
        data = asdict(document_ir)
    elif hasattr(document_ir, "model_dump"):
        data = document_ir.model_dump()
    elif hasattr(document_ir, "dict"):
        data = document_ir.dict()
    else:
        raise TypeError(
            "DocumentIR must be a dataclass or provide model_dump()/dict()."
        )

    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def main() -> int:
    """Run the DocSem command-line interface."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.input_path.is_file():
        parser.error(
            f"Input file does not exist: {args.input_path}"
        )

    azure_endpoint = os.environ.get("AZURE_ENDPOINT")
    azure_api_key = os.environ.get("AZURE_API_KEY")

    if not azure_endpoint or not azure_api_key:
        parser.error(
            "Missing AZURE_ENDPOINT or AZURE_API_KEY. "
            "Set them in your environment or in a .env file."
        )

    try:
        config = DocSemConfig(
            extraction=ExtractorConfig(
                provider=ProviderName.AZURE,
                options={
                    "endpoint": azure_endpoint,
                    "api_key": azure_api_key,
                },
            ),
        )

        docsem = DocSem(config=config)
        document = docsem.process(args.input_path)

        print("Document Stats: \n")
        print(document.stats)

        print("Document Markdown : ")
        print(document.to_markdown())

        output_json = json.dumps(document.to_dict(), indent=2, ensure_ascii=False, default=str)

        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output_json, encoding="utf-8")
            print(f"DocumentIR saved to: {args.output}")
        else:
            print(output_json)

        return 0

    except DocSemError as exc:
        print(f"DocSem error: {exc}", file=sys.stderr)
        return 1

    except (OSError, TypeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())