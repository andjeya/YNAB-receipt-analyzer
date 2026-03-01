#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "server" / "backend"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "server" / "shared"))

from app.config import get_settings
from app.db import SessionLocal
from app.jobs.tasks import _validate_ynab_payload
from app.services.ynab import get_cached_reference_data
from receipt_shared.contracts import GeminiReceiptExtraction, ReceiptTwinExtraction, UnifiedReceiptExtraction
from receipt_shared.gemini import (
    GeminiAnalyzer,
    build_analysis_prompt,
    build_twin_extraction_prompt,
    build_unified_prompt,
)
from receipt_shared.ynab_client import Category

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".pdf"}
DROPBOX_ATTR_SUFFIX = ":com.dropbox.attrs"
DEFAULT_REPORT_PATH = REPO_ROOT / "data" / "gemini_structured_test_report.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Gemini structured extraction across receipt samples and validate against contracts."
    )
    parser.add_argument("--receipt-glob", default="receipt_examples/*", help="Glob for receipt files.")
    parser.add_argument("--model", default=None, help="Override Gemini model from settings.")
    parser.add_argument("--limit", type=int, default=None, help="Max receipt files to process.")
    parser.add_argument("--stop-on-first-failure", action="store_true", help="Stop after first failed attempt.")
    parser.add_argument(
        "--report-path",
        default=str(DEFAULT_REPORT_PATH),
        help="JSON report output path.",
    )
    parser.add_argument(
        "--max-categories",
        type=int,
        default=0,
        help="Max categories in prompts/validation (0 = all).",
    )
    parser.add_argument(
        "--max-accounts",
        type=int,
        default=0,
        help="Max accounts in prompts/validation (0 = all).",
    )
    parser.add_argument(
        "--max-payees",
        type=int,
        default=0,
        help="Max payees in prompts (0 = all).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Override GeminiAnalyzer retries per request.",
    )
    return parser.parse_args()


def _slice_rows(rows: list[Any], limit: int) -> list[Any]:
    if limit <= 0:
        return rows
    return rows[:limit]


def _iter_receipt_files(receipt_glob: str) -> list[Path]:
    files: list[Path] = []
    for path in sorted(REPO_ROOT.glob(receipt_glob)):
        if not path.is_file():
            continue
        if path.name.endswith(DROPBOX_ATTR_SUFFIX):
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        files.append(path)
    return files


def _get_reference_context(
    *,
    max_categories: int,
    max_accounts: int,
    max_payees: int,
) -> tuple[list[Category], list[dict[str, Any]], list[str], set[str], set[str], str]:
    settings = get_settings()
    with SessionLocal() as db:
        reference_data = get_cached_reference_data(db, settings)

    categories = _slice_rows(list(reference_data["categories"]), max_categories)
    accounts = _slice_rows(list(reference_data["accounts"]), max_accounts)
    payees = _slice_rows(list(reference_data["payees"]), max_payees)

    if categories and accounts:
        prompt_categories = [
            Category(id=row.entity_id, name=row.name, group_name=row.group_name or "Uncategorized")
            for row in categories
        ]
        prompt_accounts = [row.raw_json for row in accounts]
        prompt_payees = [row.name for row in payees]
        allowed_category_ids = {row.entity_id for row in categories}
        allowed_account_ids = {row.entity_id for row in accounts}
        return (
            prompt_categories,
            prompt_accounts,
            prompt_payees,
            allowed_category_ids,
            allowed_account_ids,
            "ynab_cache",
        )

    fallback_categories = [Category(id="cat-1", name="General", group_name="Fallback")]
    fallback_accounts = [{"id": "acct-1", "name": "Fallback account"}]
    fallback_payees = []
    return (
        fallback_categories,
        fallback_accounts,
        fallback_payees,
        {"cat-1"},
        {"acct-1"},
        "synthetic_fallback",
    )


def _schema_contract_mismatch(schema_model: type[Any], parsed_json: dict[str, Any] | None) -> dict[str, Any]:
    schema = schema_model.model_json_schema()
    expected_properties = set(schema.get("properties", {}).keys())
    required_fields = set(schema.get("required", []))

    if not isinstance(parsed_json, dict):
        return {
            "missing_required_fields": sorted(required_fields),
            "unexpected_fields": [],
        }

    actual_fields = set(parsed_json.keys())
    missing_required_fields = sorted(required_fields - actual_fields)
    unexpected_fields = sorted(actual_fields - expected_properties)
    return {
        "missing_required_fields": missing_required_fields,
        "unexpected_fields": unexpected_fields,
    }


def _is_transport_payload_error(exc: Exception) -> bool:
    text = str(exc).lower()
    schema_error_markers = (
        "additional_properties",
        "additionalproperties",
        "generation_config.response_schema",
        "response_schema",
    )
    return any(marker in text for marker in schema_error_markers)


def main() -> int:
    args = parse_args()
    started = time.time()
    settings = get_settings()
    model_name = args.model or settings.gemini_model

    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not configured.", file=sys.stderr)
        return 2

    files = _iter_receipt_files(args.receipt_glob)
    if args.limit is not None:
        files = files[: args.limit]
    if not files:
        print(f"No matching files for glob: {args.receipt_glob}", file=sys.stderr)
        return 2

    (
        prompt_categories,
        prompt_accounts,
        prompt_payees,
        allowed_category_ids,
        allowed_account_ids,
        reference_source,
    ) = _get_reference_context(
        max_categories=args.max_categories,
        max_accounts=args.max_accounts,
        max_payees=args.max_payees,
    )

    max_retries = args.max_retries if args.max_retries is not None else settings.gemini_max_retries
    analyzer = GeminiAnalyzer(settings.gemini_api_key, model_name, max_retries)

    attempt_specs = [
        {
            "name": "unified",
            "schema": UnifiedReceiptExtraction,
            "prompt": build_unified_prompt(settings.gemini_prompt, prompt_categories, prompt_accounts, prompt_payees),
            "ynab_validate": True,
        },
        {
            "name": "fallback_ynab",
            "schema": GeminiReceiptExtraction,
            "prompt": build_analysis_prompt(settings.gemini_prompt, prompt_categories, prompt_accounts, prompt_payees),
            "ynab_validate": True,
        },
        {
            "name": "fallback_twin",
            "schema": ReceiptTwinExtraction,
            "prompt": build_twin_extraction_prompt(settings.gemini_prompt),
            "ynab_validate": False,
        },
    ]

    receipts_report: list[dict[str, Any]] = []
    mismatch_counter: Counter[str] = Counter()
    transport_errors = 0
    schema_failures = 0
    ynab_validation_failures = 0
    total_attempts = 0

    for file_path in files:
        file_report: dict[str, Any] = {
            "file": str(file_path.relative_to(REPO_ROOT)),
            "attempts": [],
        }
        mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

        for spec in attempt_specs:
            total_attempts += 1
            attempt_report: dict[str, Any] = {
                "attempt_kind": spec["name"],
                "schema_model": spec["schema"].__name__,
            }

            try:
                analysis = analyzer.analyze_file(
                    file_path=file_path,
                    prompt_text=spec["prompt"],
                    mime_type=mime_type,
                    response_schema=spec["schema"],
                )
                attempt_report.update(
                    {
                        "transport_ok": True,
                        "schema_valid": analysis.schema_valid,
                        "schema_errors": analysis.schema_errors,
                        "parse_source": analysis.parse_source,
                        "structured_output_available": analysis.structured_output_available,
                        "duration_ms": analysis.duration_ms,
                        "raw_output": analysis.raw_output,
                        "parsed_json": analysis.parsed_json,
                    }
                )
            except Exception as exc:
                message = str(exc)
                attempt_report.update(
                    {
                        "transport_ok": False,
                        "transport_error_type": type(exc).__name__,
                        "transport_error_message": message,
                        "transport_error_payload_schema_related": _is_transport_payload_error(exc),
                    }
                )
                transport_errors += 1
                mismatch_counter[f"{spec['name']}:transport:{type(exc).__name__}"] += 1
                file_report["attempts"].append(attempt_report)
                if args.stop_on_first_failure:
                    receipts_report.append(file_report)
                    break
                continue

            contract_mismatch = _schema_contract_mismatch(spec["schema"], analysis.parsed_json)
            attempt_report["contract_mismatch"] = contract_mismatch

            if not analysis.schema_valid:
                schema_failures += 1
                for err in analysis.schema_errors:
                    mismatch_counter[f"{spec['name']}:schema:{err}"] += 1

            ynab_validation: dict[str, Any] | None = None
            if spec["ynab_validate"] and analysis.schema_valid and isinstance(analysis.parsed_json, dict):
                normalized_payload, valid, errors = _validate_ynab_payload(
                    analysis.parsed_json,
                    default_account_id=settings.ynab_default_account_id,
                    allowed_category_ids=allowed_category_ids,
                    allowed_account_ids=allowed_account_ids,
                )
                ynab_validation = {
                    "valid": valid,
                    "errors": errors,
                    "normalized_payload": normalized_payload,
                }
                if not valid:
                    ynab_validation_failures += 1
                    for err in errors:
                        mismatch_counter[f"{spec['name']}:ynab:{err}"] += 1
            attempt_report["ynab_validation"] = ynab_validation

            file_report["attempts"].append(attempt_report)
            failed = (
                not attempt_report.get("transport_ok", False)
                or not attempt_report.get("schema_valid", False)
                or (ynab_validation is not None and not ynab_validation["valid"])
            )
            if failed and args.stop_on_first_failure:
                break

        receipts_report.append(file_report)
        if args.stop_on_first_failure:
            stop = any(
                (not attempt.get("transport_ok", False))
                or (attempt.get("schema_valid") is False)
                or (
                    isinstance(attempt.get("ynab_validation"), dict)
                    and attempt["ynab_validation"].get("valid") is False
                )
                for attempt in file_report["attempts"]
            )
            if stop:
                break

    top_mismatches = [{"issue": key, "count": count} for key, count in mismatch_counter.most_common(25)]
    report = {
        "generated_at_epoch_s": int(time.time()),
        "duration_s": round(time.time() - started, 3),
        "model": model_name,
        "receipt_glob": args.receipt_glob,
        "max_retries": max_retries,
        "receipt_count": len(receipts_report),
        "reference_source": reference_source,
        "reference_counts": {
            "categories": len(prompt_categories),
            "accounts": len(prompt_accounts),
            "payees": len(prompt_payees),
        },
        "totals": {
            "attempts": total_attempts,
            "transport_errors": transport_errors,
            "schema_failures": schema_failures,
            "ynab_validation_failures": ynab_validation_failures,
        },
        "top_mismatches": top_mismatches,
        "receipts": receipts_report,
    }

    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Report written to {report_path}")
    print(
        "attempts={attempts} transport_errors={transport} schema_failures={schema} ynab_validation_failures={ynab}".format(
            attempts=total_attempts,
            transport=transport_errors,
            schema=schema_failures,
            ynab=ynab_validation_failures,
        )
    )

    if transport_errors or schema_failures or ynab_validation_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
