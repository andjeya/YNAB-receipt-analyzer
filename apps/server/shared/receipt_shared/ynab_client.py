from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

YNAB_BASE_URL = "https://api.ynab.com/v1"


class YNABConflictError(RuntimeError):
    """Raised when YNAB returns HTTP 409 (duplicate import_id on the same account).

    YNAB does NOT echo the existing transaction back — it just returns 409.
    Callers must resolve by listing transactions and matching import_id.
    """

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"YNAB API error {status_code}: {body}")
        self.status_code = status_code
        self.body = body


@dataclass
class Category:
    id: str
    name: str
    group_name: str


class YNABClient:
    def __init__(self, access_token: str):
        if not access_token:
            raise ValueError("YNAB access token is required")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.session.request(
            method,
            f"{YNAB_BASE_URL}{path}",
            json=payload,
            params=params,
            timeout=30,
        )
        if not response.ok:
            if response.status_code == 409:
                raise YNABConflictError(response.status_code, response.text)
            raise RuntimeError(f"YNAB API error {response.status_code}: {response.text}")
        return response.json().get("data", {})

    def list_budgets(self) -> list[dict[str, Any]]:
        return self._request("GET", "/budgets").get("budgets", [])

    def list_categories(self, budget_id: str) -> list[Category]:
        groups = self._request("GET", f"/budgets/{budget_id}/categories").get("category_groups", [])
        categories: list[Category] = []
        for group in groups:
            for category in group.get("categories", []):
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
        accounts = self._request("GET", f"/budgets/{budget_id}/accounts").get("accounts", [])
        return [account for account in accounts if not account.get("closed")]

    def list_payees(self, budget_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/budgets/{budget_id}/payees").get("payees", [])

    def list_transactions_since(self, budget_id: str, since_date: str) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            f"/budgets/{budget_id}/transactions",
            params={"since_date": since_date},
        ).get("transactions", [])

    def get_transaction(self, budget_id: str, transaction_id: str) -> dict[str, Any]:
        return self._request("GET", f"/budgets/{budget_id}/transactions/{transaction_id}").get("transaction", {})

    def create_transaction(self, budget_id: str, transaction: dict[str, Any]) -> dict[str, Any]:
        """Create a single transaction.

        Raises YNABConflictError (HTTP 409) when the import_id already exists on
        that account.  YNAB does NOT return an echo of the existing transaction —
        callers must resolve by calling list_transactions_since and matching import_id.
        """
        payload = {"transaction": transaction}
        data = self._request("POST", f"/budgets/{budget_id}/transactions", payload)
        return data.get("transaction") or {}

    def update_transaction(self, budget_id: str, transaction_id: str, transaction: dict[str, Any]) -> dict[str, Any]:
        payload = {"transaction": transaction}
        return self._request("PUT", f"/budgets/{budget_id}/transactions/{transaction_id}", payload).get("transaction", {})

    def delete_transaction(self, budget_id: str, transaction_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/budgets/{budget_id}/transactions/{transaction_id}").get("transaction", {})
