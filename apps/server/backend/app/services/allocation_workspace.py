from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from receipt_shared.contracts import (
    AllocationAssignment,
    AllocationItem,
    AllocationLane,
    AllocationWorkspace,
)

MAIN_LANE_ID = "main"
UNASSIGNED_LANE_ID = "unassigned"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed != parsed:  # NaN guard
        return None
    return parsed


def _normalize_amount(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _lane_definitions_from_payload(payload: dict[str, Any]) -> list[AllocationLane]:
    splits = payload.get("splits", [])
    lanes: list[AllocationLane] = []
    if isinstance(splits, list) and len(splits) > 0:
        for index, split in enumerate(splits):
            if not isinstance(split, dict):
                continue
            lanes.append(
                AllocationLane(
                    lane_id=f"split-{index}",
                    category_id=str(split.get("category_id") or "") or None,
                    pinned_amount=_to_optional_float(split.get("amount")),
                )
            )
    else:
        lanes.append(
            AllocationLane(
                lane_id=MAIN_LANE_ID,
                category_id=str(payload.get("category_id") or "") or None,
                pinned_amount=_to_optional_float(payload.get("total_amount")),
            )
        )
    lanes.append(AllocationLane(lane_id=UNASSIGNED_LANE_ID, category_id=None, pinned_amount=None))
    return lanes


def _extract_items_from_twin_payload(twin_payload: dict[str, Any] | None) -> tuple[list[AllocationItem], list[str]]:
    if not isinstance(twin_payload, dict):
        return [], ["No receipt twin payload available for item allocation."]
    line_items = twin_payload.get("line_items")
    if not isinstance(line_items, list):
        return [], ["Receipt twin has no line items for item allocation."]

    items: list[AllocationItem] = []
    warnings: list[str] = []
    missing_amount_count = 0

    for row_index, raw_item in enumerate(line_items):
        if not isinstance(raw_item, dict):
            continue
        item_type = str(raw_item.get("item_type") or "product").strip().lower() or "product"
        if item_type in {"subtotal", "total"}:
            continue
        source_index = int(raw_item.get("index", row_index))
        raw_text = str(raw_item.get("raw_text") or "").strip()
        translated = str(raw_item.get("translated_text") or "").strip()
        label = raw_text or translated or f"Line {source_index + 1}"
        amount = _to_optional_float(raw_item.get("line_total"))
        if amount is None:
            missing_amount_count += 1
        items.append(
            AllocationItem(
                item_id=str(uuid4()),
                source_index=source_index,
                label=label,
                amount=amount,
                tax_code=str(raw_item.get("tax_code") or "") or None,
                item_type=item_type,
            )
        )

    if len(items) == 0:
        warnings.append("No allocatable line items were found.")
    if missing_amount_count > 0:
        warnings.append(f"{missing_amount_count} line item(s) have unknown amounts and were sent to unassigned.")
    return items, warnings


def _greedy_assign_items(
    items: list[AllocationItem],
    lanes: list[AllocationLane],
) -> list[AllocationAssignment]:
    lane_ids = [lane.lane_id for lane in lanes if lane.lane_id != UNASSIGNED_LANE_ID]
    if len(lane_ids) == 0:
        return [AllocationAssignment(item_id=item.item_id, lane_id=UNASSIGNED_LANE_ID) for item in items]
    if len(lane_ids) == 1:
        target_lane = lane_ids[0]
        assignments: list[AllocationAssignment] = []
        for item in items:
            lane_id = target_lane if item.amount is not None else UNASSIGNED_LANE_ID
            assignments.append(AllocationAssignment(item_id=item.item_id, lane_id=lane_id))
        return assignments

    targets = {
        lane.lane_id: abs(_to_decimal(lane.pinned_amount or 0))
        for lane in lanes
        if lane.lane_id != UNASSIGNED_LANE_ID
    }
    running = {lane_id: Decimal("0") for lane_id in targets}
    assignments: list[AllocationAssignment] = []

    known_items = [item for item in items if item.amount is not None]
    unknown_items = [item for item in items if item.amount is None]
    known_items.sort(key=lambda item: abs(_to_decimal(item.amount)), reverse=True)

    for item in known_items:
        item_amount = abs(_to_decimal(item.amount))
        best_lane = None
        best_score = None
        for lane_id in lane_ids:
            remaining = targets[lane_id] - running[lane_id]
            score = abs(remaining - item_amount)
            if best_score is None or score < best_score:
                best_lane = lane_id
                best_score = score
        assert best_lane is not None
        running[best_lane] += item_amount
        assignments.append(AllocationAssignment(item_id=item.item_id, lane_id=best_lane))

    for item in unknown_items:
        assignments.append(AllocationAssignment(item_id=item.item_id, lane_id=UNASSIGNED_LANE_ID))

    return assignments


def build_initial_allocation_workspace(
    payload: dict[str, Any],
    *,
    twin_payload: dict[str, Any] | None,
    twin_version: int,
) -> dict[str, Any]:
    lanes = _lane_definitions_from_payload(payload)
    items, warnings = _extract_items_from_twin_payload(twin_payload)
    assignments = _greedy_assign_items(items, lanes)
    workspace = AllocationWorkspace(
        version=1,
        twin_version=max(int(twin_version or 0), 0),
        generated_at=datetime.now(timezone.utc),
        items=items,
        lanes=lanes,
        assignments=assignments,
        warnings=warnings,
    )
    return workspace.model_dump(mode="json")


def reconcile_allocation_workspace(
    payload: dict[str, Any],
    workspace_payload: dict[str, Any] | None,
    *,
    twin_payload: dict[str, Any] | None,
    twin_version: int,
) -> dict[str, Any]:
    if not workspace_payload:
        return build_initial_allocation_workspace(payload, twin_payload=twin_payload, twin_version=twin_version)

    try:
        workspace = AllocationWorkspace.model_validate(workspace_payload)
    except ValidationError:
        return build_initial_allocation_workspace(payload, twin_payload=twin_payload, twin_version=twin_version)

    expected_lanes = _lane_definitions_from_payload(payload)
    expected_lane_map = {lane.lane_id: lane for lane in expected_lanes}
    existing_lane_map = {lane.lane_id: lane for lane in workspace.lanes}

    # FIX A: detect stale main-lane pin — the main lane's initial pin is the
    # payload total_amount (a default, not a user choice).  When the payload
    # total has since changed (e.g. twin correction) we must not preserve that
    # old value; doing so would resurrect a stale total via recompute.
    # Because AllocationLane has no is_user_pin flag we apply the safe rule:
    # if the existing main-lane pin differs from the current payload
    # total_amount, treat it as stale and clear it.
    payload_total = _normalize_amount(_to_decimal(payload.get("total_amount", 0)))

    merged_lanes: list[AllocationLane] = []
    for lane in expected_lanes:
        existing_lane = existing_lane_map.get(lane.lane_id)
        if existing_lane is None:
            merged_lanes.append(lane)
            continue

        preserved_pin = existing_lane.pinned_amount if lane.lane_id != UNASSIGNED_LANE_ID else None

        # Clear stale main-lane pin: if the pin was set equal to the old total
        # and the total has since changed, the pin is a defaulted value, not a
        # genuine user override, so drop it to avoid corrupting total_amount.
        if lane.lane_id == MAIN_LANE_ID and preserved_pin is not None:
            pin_decimal = _normalize_amount(_to_decimal(preserved_pin))
            if pin_decimal != payload_total:
                preserved_pin = None

        merged_lanes.append(
            AllocationLane(
                lane_id=lane.lane_id,
                category_id=lane.category_id,
                pinned_amount=preserved_pin,
            )
        )

    items = workspace.items
    item_ids = {item.item_id for item in items}
    assignments: list[AllocationAssignment] = []
    for assignment in workspace.assignments:
        if assignment.item_id not in item_ids:
            continue
        if assignment.lane_id not in expected_lane_map:
            assignments.append(AllocationAssignment(item_id=assignment.item_id, lane_id=UNASSIGNED_LANE_ID))
            continue
        assignments.append(assignment)

    assigned_item_ids = {assignment.item_id for assignment in assignments}
    for item in items:
        if item.item_id not in assigned_item_ids:
            assignments.append(AllocationAssignment(item_id=item.item_id, lane_id=UNASSIGNED_LANE_ID))

    warnings = list(workspace.warnings)
    normalized_twin_version = max(int(twin_version or 0), 0)
    if normalized_twin_version != workspace.twin_version:
        warnings.append("Receipt twin line items changed; allocation workspace may be stale.")

    normalized_workspace = AllocationWorkspace(
        version=max(int(workspace.version or 1), 1),
        twin_version=normalized_twin_version,
        generated_at=datetime.fromisoformat(workspace.generated_at.isoformat()) if isinstance(workspace.generated_at, datetime) else datetime.now(timezone.utc),
        items=items,
        lanes=merged_lanes,
        assignments=assignments,
        warnings=warnings,
    )
    return normalized_workspace.model_dump(mode="json")


def _largest_remainder_allocation(target_amount: Decimal, weights: list[Decimal]) -> list[Decimal]:
    if len(weights) == 0:
        return []
    target_cents = int((target_amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if target_cents <= 0:
        return [Decimal("0.00") for _ in weights]

    normalized_weights = [abs(weight) for weight in weights]
    weight_sum = sum(normalized_weights, Decimal("0"))
    if weight_sum <= 0:
        base = target_cents // len(weights)
        remainder = target_cents - base * len(weights)
        values = [base for _ in weights]
        for index in range(remainder):
            values[index] += 1
        return [Decimal(value) / Decimal("100") for value in values]

    raw_shares = [Decimal(target_cents) * weight / weight_sum for weight in normalized_weights]
    floor_shares = [int(share.to_integral_value(rounding=ROUND_FLOOR)) for share in raw_shares]
    remainder_cents = target_cents - sum(floor_shares)
    fractions = [(raw_shares[index] - Decimal(floor_shares[index]), index) for index in range(len(raw_shares))]
    fractions.sort(key=lambda row: row[0], reverse=True)
    for _, index in fractions[:remainder_cents]:
        floor_shares[index] += 1
    return [Decimal(value) / Decimal("100") for value in floor_shares]


def recompute_payload_from_workspace(
    payload: dict[str, Any],
    workspace_payload: dict[str, Any],
    *,
    mode: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    workspace = AllocationWorkspace.model_validate(workspace_payload)
    warnings = list(workspace.warnings)
    lane_ids = [lane.lane_id for lane in workspace.lanes if lane.lane_id != UNASSIGNED_LANE_ID]
    if len(lane_ids) == 0:
        warnings.append("No assignable lanes were found in workspace.")
        return payload, workspace.model_dump(mode="json"), warnings

    item_by_id = {item.item_id: item for item in workspace.items}
    lane_totals = {lane_id: Decimal("0") for lane_id in lane_ids}
    for assignment in workspace.assignments:
        if assignment.lane_id not in lane_totals:
            continue
        item = item_by_id.get(assignment.item_id)
        if item is None or item.amount is None:
            continue
        # FIX C: discount line items have negative amounts; they must *subtract*
        # from the lane's weight, not add to it.  Floor each weight at 0 so
        # a discount-heavy lane can't produce a negative weight; the all-zero
        # path in _largest_remainder_allocation already handles that case.
        lane_totals[assignment.lane_id] += _to_decimal(item.amount)

    # Floor each lane weight at 0 (a net-negative lane collapses to 0, which
    # triggers the equal-split fallback in _largest_remainder_allocation).
    lane_totals = {lid: max(v, Decimal("0")) for lid, v in lane_totals.items()}

    total_amount = _normalize_amount(_to_decimal(payload.get("total_amount", 0)))
    lane_values = [lane_totals.get(lane_id, Decimal("0")) for lane_id in lane_ids]

    lane_amounts: dict[str, Decimal] = {}
    lane_by_id = {lane.lane_id: lane for lane in workspace.lanes}
    if mode == "keep_manual_amounts":
        pinned_sum = Decimal("0")
        unpinned_ids: list[str] = []
        unpinned_weights: list[Decimal] = []
        for lane_id in lane_ids:
            lane = lane_by_id.get(lane_id)
            pinned = _to_optional_float(lane.pinned_amount if lane else None)
            if pinned is None:
                unpinned_ids.append(lane_id)
                unpinned_weights.append(lane_totals.get(lane_id, Decimal("0")))
                continue
            normalized = _normalize_amount(_to_decimal(pinned))
            lane_amounts[lane_id] = normalized
            pinned_sum += normalized

        remainder = total_amount - pinned_sum
        if remainder < Decimal("0"):
            warnings.append("Pinned split amounts exceed total amount; using pinned values as-is.")
            remainder = Decimal("0")
        # FIX B: when every lane is pinned and pins don't reach the total,
        # the unpinned list is empty so _largest_remainder_allocation returns []
        # and the shortfall silently vanishes.  Emit an explicit warning so the
        # UI can surface it; the shortfall is still unallocated (validation will
        # block sync on sum mismatch — no silent corruption).
        if len(unpinned_ids) == 0 and remainder > Decimal("0"):
            warnings.append(
                f"Pinned amounts sum to {pinned_sum} but total is {total_amount}; "
                f"difference {remainder} is unallocated."
            )
        distributed = _largest_remainder_allocation(remainder, unpinned_weights)
        for index, lane_id in enumerate(unpinned_ids):
            lane_amounts[lane_id] = distributed[index]
    else:
        distributed = _largest_remainder_allocation(total_amount, lane_values)
        for index, lane_id in enumerate(lane_ids):
            lane_amounts[lane_id] = distributed[index]

    next_payload = dict(payload)
    if any(lane_id.startswith("split-") for lane_id in lane_ids):
        existing_splits = payload.get("splits", [])
        split_memos: list[str] = []
        if isinstance(existing_splits, list):
            for split in existing_splits:
                if isinstance(split, dict):
                    split_memos.append(str(split.get("memo") or ""))
        next_splits: list[dict[str, Any]] = []
        for lane_id in sorted([lane_id for lane_id in lane_ids if lane_id.startswith("split-")], key=lambda item: int(item.split("-", 1)[1])):
            lane = lane_by_id.get(lane_id)
            split_index = int(lane_id.split("-", 1)[1])
            memo = split_memos[split_index] if split_index < len(split_memos) else ""
            next_splits.append(
                {
                    "category_id": str((lane.category_id if lane else "") or ""),
                    "amount": float(_normalize_amount(lane_amounts.get(lane_id, Decimal("0")))),
                    "memo": memo,
                }
            )
        next_payload["category_id"] = None
        next_payload["splits"] = next_splits
    else:
        # FIX A: total_amount is owned by the twin/validation, not the workspace.
        # Never overwrite it here; the main lane's computed amount is derived
        # from the payload total and is only used for display / split population.
        # Removing the write prevents a stale pinned main-lane amount from
        # resurrecting an old total after a twin correction.
        next_payload["splits"] = []
        main_lane = lane_by_id.get(MAIN_LANE_ID)
        if main_lane is not None:
            next_payload["category_id"] = main_lane.category_id or ""

    normalized_workspace = AllocationWorkspace(
        version=max(int(workspace.version or 1), 1),
        twin_version=max(int(workspace.twin_version or 0), 0),
        generated_at=datetime.now(timezone.utc),
        items=workspace.items,
        lanes=workspace.lanes,
        assignments=workspace.assignments,
        warnings=warnings,
    )
    return next_payload, normalized_workspace.model_dump(mode="json"), warnings
