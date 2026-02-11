# Receipt → Gemini 3 Flash → YNAB

A standalone Python tool to:

1. Read a receipt PDF.
2. Pull available YNAB categories from your selected budget.
3. Ask Gemini (`gemini-3-flash-preview`) to classify receipt line items into YNAB categories.
4. Create a YNAB transaction, including split-category subtransactions when needed.

This project is isolated in `receipt_ynab_tool/` and does not modify other repository code.

## Features

- Direct PDF multimodal analysis via `google-genai` file upload.
- Gemini analysis via `google-genai`.
- YNAB API integration for:
  - listing budgets (`GET /budgets`),
  - listing categories (`GET /budgets/{budget_id}/categories`),
  - creating transactions (`POST /budgets/{budget_id}/transactions`).
- Dry-run mode to preview payload before posting.

## Requirements

- Python 3.10+
- Environment variables:
  - `GEMINI_API_KEY`
  - `YNAB_ACCESS_TOKEN`

Install dependencies:

```bash
pip install -r requirements.txt
```

## Quick start

```bash
cd receipt_ynab_tool
cp .env.example .env
# edit .env with your keys (or export env vars directly)
python main.py list-budgets
python main.py list-categories --budget-id <budget_id>
python main.py list-accounts --budget-id <budget_id>
python main.py process-receipt \
  --budget-id <budget_id> \
  --account-id <account_id> \
  --pdf ./example_receipt.pdf \
  --dry-run
```

When you're satisfied with the dry-run output, remove `--dry-run` to create the transaction in YNAB.

## Local Backup (Raw JSON)

Create a raw snapshot from `GET /budgets/{budget_id}` before making changes:

```bash
cd receipt_ynab_tool
set -a; source .env; set +a
./backup_ynab_budget.sh <budget_id>
```

Optional output directory:

```bash
./backup_ynab_budget.sh <budget_id> ./backups
```

## CLI commands

### `list-budgets`
Lists accessible YNAB budgets.

### `list-categories --budget-id ...`
Lists category groups and categories for a budget.

### `list-accounts --budget-id ...`
Lists accounts (including account IDs) for a budget.

### `process-receipt ...`
Pipeline command:

1. Uploads the PDF to Gemini.
2. Downloads categories for the budget.
3. Sends PDF + category context + user prompt to Gemini.
4. Parses model JSON output.
5. Creates YNAB transaction with optional splits.

`--prompt` is optional. If omitted, the tool uses a default categorization instruction.

## Model output contract

Gemini is asked to return strict JSON:

```json
{
  "payee_name": "Store Name",
  "transaction_date": "2026-01-15",
  "memo": "short description",
  "total_amount": 54.22,
  "splits": [
    {
      "category_id": "ynab-category-id",
      "category_name": "Groceries",
      "amount": 34.22,
      "memo": "food items"
    },
    {
      "category_id": "ynab-category-id-2",
      "category_name": "Household",
      "amount": 20.00,
      "memo": "cleaning supplies"
    }
  ]
}
```

Amounts are in dollars in model output; tool converts to YNAB milliunits.

## YNAB API references

The implementation follows official YNAB API documentation:

- API overview: https://api.ynab.com/
- OpenAPI/Swagger JSON: https://api.ynab.com/v1/openapi.json
- Endpoints used:
  - `GET /budgets`
  - `GET /budgets/{budget_id}/categories`
  - `GET /budgets/{budget_id}/accounts`
  - `POST /budgets/{budget_id}/transactions`

## Notes

- YNAB `amount` values are integer milliunits (e.g., `$12.34` => `12340`; outflow sent as negative).
- If Gemini does not provide valid JSON, the command fails fast and shows raw output for troubleshooting.
- You can tune prompt behavior with `--prompt` and model via `--model`.
