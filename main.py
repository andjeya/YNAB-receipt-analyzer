#!/usr/bin/env python3
import argparse
import json
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types


YNAB_BASE_URL = "https://api.ynab.com/v1"
DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_PROMPT = "Categorize receipt line items into the most appropriate YNAB categories."


@dataclass
class Category:
    id: str
    name: str
    group_name: str


class YNABClient:
    def __init__(self, access_token: str):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{YNAB_BASE_URL}{path}"
        response = self.session.request(method, url, json=payload, timeout=30)
        if not response.ok:
            raise RuntimeError(f"YNAB API error {response.status_code}: {response.text}")
        return response.json()["data"]

    def list_budgets(self) -> list[dict[str, Any]]:
        return self._request("GET", "/budgets")["budgets"]

    def list_categories(self, budget_id: str) -> list[Category]:
        groups = self._request("GET", f"/budgets/{budget_id}/categories")["category_groups"]
        categories: list[Category] = []
        for group in groups:
            for category in group["categories"]:
                if category.get("hidden"):
                    continue
                categories.append(
                    Category(
                        id=category["id"],
                        name=category["name"],
                        group_name=group["name"],
                    )
                )
        return categories

    def list_accounts(self, budget_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/budgets/{budget_id}/accounts")["accounts"]

    def create_transaction(
        self,
        budget_id: str,
        account_id: str,
        payee_name: str,
        transaction_date: str,
        memo: str,
        total_amount_milliunits: int,
        splits: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "transaction": {
                "account_id": account_id,
                "date": transaction_date,
                "amount": total_amount_milliunits,
                "payee_name": payee_name,
                "memo": memo,
                "subtransactions": splits,
            }
        }
        return self._request("POST", f"/budgets/{budget_id}/transactions", payload)["transaction"]


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini response was not valid JSON. Raw response:\n{text}") from exc


def dollars_to_milliunits(amount: float | int | str, outflow: bool = True) -> int:
    dec = Decimal(str(amount)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    milliunits = int((dec * 1000).to_integral_value(rounding=ROUND_HALF_UP))
    if outflow and milliunits > 0:
        return -milliunits
    return milliunits


def build_analysis_prompt(user_prompt: str, categories: list[Category]) -> str:
    category_lines = "\n".join(
        f"- id={c.id} | group={c.group_name} | name={c.name}" for c in categories
    )

    return f"""
You are analyzing a purchase receipt PDF and mapping line items to YNAB categories.

User instruction: {user_prompt}

Return STRICT JSON ONLY. No markdown. No prose.

Schema:
{{
  "payee_name": "string",
  "transaction_date": "YYYY-MM-DD",
  "memo": "string",
  "total_amount": number,
  "splits": [
    {{
      "category_id": "string",
      "category_name": "string",
      "amount": number,
      "memo": "string"
    }}
  ]
}}

Rules:
1. Use category_id values ONLY from the category list below.
2. Ensure splits sum to total_amount.
3. Keep memo text concise.
4. If date is unclear, use today's date.

Available YNAB categories:
{category_lines}

Input receipt is provided as an attached PDF file in this request.
""".strip()


def analyze_with_gemini(api_key: str, model: str, prompt_text: str, pdf_path: Path) -> dict[str, Any]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file, got: {pdf_path}")

    client = genai.Client(api_key=api_key)
    uploaded_pdf = client.files.upload(file=str(pdf_path))
    response = client.models.generate_content(
        model=model,
        contents=[
            prompt_text,
            types.Part.from_uri(
                file_uri=uploaded_pdf.uri,
                mime_type=uploaded_pdf.mime_type or "application/pdf",
            ),
        ],
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
            response_mime_type="application/json",
        ),
    )
    if not response.text:
        raise RuntimeError("Gemini returned an empty response.")
    return parse_json_response(response.text)


def build_subtransactions(splits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subtransactions: list[dict[str, Any]] = []
    for split in splits:
        subtransactions.append(
            {
                "amount": dollars_to_milliunits(split["amount"], outflow=True),
                "category_id": split["category_id"],
                "memo": split.get("memo", ""),
            }
        )
    return subtransactions


def cmd_list_budgets(client: YNABClient) -> None:
    budgets = client.list_budgets()
    for b in budgets:
        print(f"{b['id']}\t{b['name']}")


def cmd_list_categories(client: YNABClient, budget_id: str) -> None:
    categories = client.list_categories(budget_id)
    for c in categories:
        print(f"{c.id}\t{c.group_name}\t{c.name}")


def cmd_list_accounts(client: YNABClient, budget_id: str) -> None:
    accounts = client.list_accounts(budget_id)
    for a in accounts:
        print(f"{a['id']}\t{a.get('name', '')}")


def cmd_process_receipt(args: argparse.Namespace, ynab_client: YNABClient) -> None:
    categories = ynab_client.list_categories(args.budget_id)
    pdf_path = Path(args.pdf)
    prompt_text = build_analysis_prompt(args.prompt, categories)

    analysis = analyze_with_gemini(args.gemini_api_key, args.model, prompt_text, pdf_path)
    splits = analysis.get("splits", [])
    if not splits:
        raise ValueError("Gemini returned no splits; cannot create YNAB transaction.")

    subtransactions = build_subtransactions(splits)
    total_amount = analysis.get("total_amount")
    total_milliunits = dollars_to_milliunits(total_amount, outflow=True)

    transaction_date = analysis.get("transaction_date") or date.today().isoformat()
    payload_preview = {
        "account_id": args.account_id,
        "date": transaction_date,
        "amount": total_milliunits,
        "payee_name": analysis.get("payee_name", "Receipt Import"),
        "memo": analysis.get("memo", "Imported from receipt via Gemini"),
        "subtransactions": subtransactions,
    }

    print("Proposed transaction payload:")
    print(json.dumps(payload_preview, indent=2))

    if args.dry_run:
        print("Dry run enabled. No transaction created.")
        return

    created = ynab_client.create_transaction(
        budget_id=args.budget_id,
        account_id=args.account_id,
        payee_name=payload_preview["payee_name"],
        transaction_date=payload_preview["date"],
        memo=payload_preview["memo"],
        total_amount_milliunits=payload_preview["amount"],
        splits=payload_preview["subtransactions"],
    )
    print(f"Created transaction: {created['id']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Receipt to YNAB categorization tool using Gemini.")
    parser.add_argument("--ynab-access-token", default=os.environ.get("YNAB_ACCESS_TOKEN"))
    parser.add_argument("--gemini-api-key", default=os.environ.get("GEMINI_API_KEY"))

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-budgets")

    categories_parser = subparsers.add_parser("list-categories")
    categories_parser.add_argument("--budget-id", required=True)

    accounts_parser = subparsers.add_parser("list-accounts")
    accounts_parser.add_argument("--budget-id", required=True)

    process_parser = subparsers.add_parser("process-receipt")
    process_parser.add_argument("--budget-id", required=True)
    process_parser.add_argument("--account-id", required=True)
    process_parser.add_argument("--pdf", required=True)
    process_parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    process_parser.add_argument("--model", default=DEFAULT_MODEL)
    process_parser.add_argument("--dry-run", action="store_true")

    return parser


def main() -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    if not args.ynab_access_token:
        raise ValueError("YNAB access token missing. Set YNAB_ACCESS_TOKEN or pass --ynab-access-token.")

    if args.command == "process-receipt" and not args.gemini_api_key:
        raise ValueError("Gemini API key missing. Set GEMINI_API_KEY or pass --gemini-api-key.")

    ynab_client = YNABClient(args.ynab_access_token)

    if args.command == "list-budgets":
        cmd_list_budgets(ynab_client)
    elif args.command == "list-categories":
        cmd_list_categories(ynab_client, args.budget_id)
    elif args.command == "list-accounts":
        cmd_list_accounts(ynab_client, args.budget_id)
    elif args.command == "process-receipt":
        cmd_process_receipt(args, ynab_client)


if __name__ == "__main__":
    main()
