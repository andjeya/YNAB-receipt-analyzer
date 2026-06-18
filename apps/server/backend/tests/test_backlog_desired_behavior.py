"""Backlog: DESIRED behavior not yet implemented (plan B-03..B-06).

Skipped stubs documenting decided / accounting-safe behavior that has no
implementation surface yet (currency, multi-tender, cash-back, mixed
purchase/return). Each needs extraction/schema work + product design. Unskip and
fill in assertions when the feature lands. Keeping them in-tree makes the gaps
visible without breaking the suite.

B-01 (deleted-txn flag-not-recreate) and B-02 (time-less near-match warning) have
been IMPLEMENTED — see test_resync_deleted_txn.py and test_duplicate_timeless_bypass.py.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="Backlog B-03 (CUR-01/02): non-USD currency detection/blocking not implemented")
def test_b03_non_usd_receipt_blocks_sync() -> None:
    """Desired: a receipt whose currency differs from the YNAB budget currency must
    block sync (clear reason) until an explicit converted/confirmed amount exists.
    No silent single-currency sync. Requires a currency field + gate that do not
    exist yet."""


@pytest.mark.skip(reason="Backlog B-04 (CUR-03): multi-tender detection not implemented")
def test_b04_multi_tender_receipt_requires_review() -> None:
    """Desired: a receipt paid across multiple tenders (e.g. gift card + credit
    card) must either record only the amount charged to the mapped account or be
    forced to manual review — never a silent single-account sync of the full total."""


@pytest.mark.skip(reason="Backlog B-05 (DISC-07): cash-back detection not implemented")
def test_b05_cash_back_not_silently_booked_as_spend() -> None:
    """Desired: register cash-back must not be silently booked as spending. Block /
    manual review until an explicit split/transfer model exists."""


@pytest.mark.skip(reason="Backlog B-06 (RET-08/09): mixed purchase+return handling not implemented")
def test_b06_mixed_purchase_return_receipt() -> None:
    """Desired: a receipt mixing purchases and returns is modeled as a purchase
    whose returned items reduce the net total (credits), or forced to manual review
    if it cannot reconcile. A net-zero receipt blocks sync (total must be > 0)."""
