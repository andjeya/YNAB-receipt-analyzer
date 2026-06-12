"""Service for learned payee→category mapping.

Key rules:
- key = normalize_payee_key(payee_name) — imported from app.services.duplicates
- unique (budget_id, payee_key), last-write-wins
- single-category and split-template are mutually exclusive columns
- lookup only returns a category when it still exists in YNABCache (stale → no-op)
- upsert does NOT commit (caller must commit)
- upsert uses begin_nested() + IntegrityError re-fetch; NEVER calls full db.rollback()
  (a full rollback would discard the caller's pending transaction writes)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.enums import YNABCacheEntityType
from app.models import PayeeCategoryMemory, YNABCache
from app.services.duplicates import normalize_payee_key

logger = logging.getLogger(__name__)

_ITEM_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")


def normalize_item_text(text: Any) -> str | None:
    """Normalize a receipt line-item label for template matching.

    Lowercases, collapses whitespace, strips non-alphanumeric characters.
    Returns None if the result is empty.
    """
    raw = str(text or "").strip().lower()
    if not raw:
        return None
    canonical = _ITEM_NON_ALNUM_RE.sub(" ", raw)
    canonical = _SPACE_RE.sub(" ", canonical).strip()
    return canonical or None


def _category_exists(db: Any, budget_id: str, category_id: str) -> bool:
    """Return True if the category_id exists in the YNAB cache for this budget.

    Returns False for blank values.
    """
    if not category_id or not category_id.strip():
        return False
    row = db.scalar(
        select(YNABCache).where(
            YNABCache.budget_id == budget_id,
            YNABCache.entity_type == YNABCacheEntityType.CATEGORY.value,
            YNABCache.entity_id == category_id,
        )
    )
    return row is not None


def lookup_payee_memory(db: Any, budget_id: str, payee_name: Any) -> PayeeCategoryMemory | None:
    """Return the PayeeCategoryMemory row for this payee, or None.

    Returns None when:
    - payee_name normalizes to None
    - budget_id is blank
    - no memory row exists
    """
    key = normalize_payee_key(payee_name)
    if not key:
        return None
    if not budget_id or not budget_id.strip():
        return None

    return db.scalar(
        select(PayeeCategoryMemory).where(
            PayeeCategoryMemory.budget_id == budget_id,
            PayeeCategoryMemory.payee_key == key,
        )
    )


def apply_single_category_memory(
    payload: dict[str, Any],
    memory: PayeeCategoryMemory,
    *,
    allowed_category_ids: set[str],
    db: Any,
    budget_id: str,
) -> bool:
    """Apply a single-category memory to the payload. Returns True if applied.

    Only applies when:
    - memory.category_id is set (not a split memory row)
    - category still exists in cache (stale → no-op)
    - category_id is in allowed_category_ids

    When applied: sets payload["category_id"], clears payload["splits"],
    sets payload["category_source"] = "payee_memory".

    Memory OVERRIDES the model guess — it reflects the user's actual past synced
    behavior. A stale/deleted category is a no-op; the model guess stands.
    """
    if not memory.category_id:
        return False
    if memory.category_id not in allowed_category_ids:
        return False
    if not _category_exists(db, budget_id, memory.category_id):
        logger.debug(
            "Payee memory for payee_key=%s budget_id=%s points to stale category %s — ignoring",
            memory.payee_key,
            budget_id,
            memory.category_id,
        )
        return False

    payload["category_id"] = memory.category_id
    payload["splits"] = []
    payload["category_source"] = "payee_memory"
    return True


def apply_split_memory_to_workspace(
    payload: dict[str, Any],
    workspace: dict[str, Any],
    memory: PayeeCategoryMemory,
    *,
    allowed_category_ids: set[str],
    db: Any,
    budget_id: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Apply a split template from memory to the payload and workspace.

    Returns (payload, workspace, applied).

    Only applies when:
    - memory.template_json is set
    - ALL template lane category_ids are in allowed_category_ids AND exist in cache

    When applied: re-labels lanes from template, assigns items whose
    normalize_item_text matches item_categories to the matching lane, unmatched
    items go to the dominant_category lane, then routes amounts through
    recompute_payload_from_workspace (re-suggest mode, never hand-distributes).
    Sets category_source="payee_memory" on success.

    If any template category is not in allowed_category_ids → no-op (applied=False).
    """
    from app.services.allocation_workspace import recompute_payload_from_workspace

    template = memory.template_json
    if not isinstance(template, dict):
        return payload, workspace, False

    lanes_template = template.get("lanes", [])
    dominant_category_id = template.get("dominant_category_id")
    item_categories: dict[str, str] = template.get("item_categories", {})

    # Validate all template categories exist and are allowed.  A lane without
    # a category_id can never produce a syncable split — reject the whole
    # template rather than emit a blank-category lane.
    if any(not lane.get("category_id") for lane in lanes_template):
        return payload, workspace, False
    all_category_ids = {lane.get("category_id") for lane in lanes_template if lane.get("category_id")}
    if dominant_category_id:
        all_category_ids.add(dominant_category_id)
    for cat_id in all_category_ids:
        if cat_id not in allowed_category_ids:
            return payload, workspace, False
        if not _category_exists(db, budget_id, cat_id):
            logger.debug(
                "Payee split memory for payee_key=%s budget_id=%s references stale category %s — ignoring",
                memory.payee_key,
                budget_id,
                cat_id,
            )
            return payload, workspace, False

    # Build a new workspace from the template lanes.
    try:
        from receipt_shared.contracts import AllocationWorkspace as AW, AllocationLane, AllocationAssignment

        ws = AW.model_validate(workspace)

        # Build new lanes from template (maintaining split-N naming).
        new_lanes = []
        for index, lane_def in enumerate(lanes_template):
            new_lanes.append(
                AllocationLane(
                    lane_id=f"split-{index}",
                    category_id=lane_def.get("category_id"),
                    pinned_amount=None,
                )
            )
        from app.services.allocation_workspace import UNASSIGNED_LANE_ID
        new_lanes.append(AllocationLane(lane_id=UNASSIGNED_LANE_ID, category_id=None, pinned_amount=None))

        # Find the dominant lane index.
        dominant_lane_id: str | None = None
        for index, lane_def in enumerate(lanes_template):
            if lane_def.get("category_id") == dominant_category_id:
                dominant_lane_id = f"split-{index}"
                break
        if dominant_lane_id is None and len(lanes_template) > 0:
            dominant_lane_id = "split-0"

        # Reassign items: match by normalized label, unmatched → dominant lane.
        new_assignments = []
        for item in ws.items:
            normalized_label = normalize_item_text(item.label)
            matched_cat = item_categories.get(normalized_label) if normalized_label else None
            target_lane_id = UNASSIGNED_LANE_ID
            if matched_cat:
                for index, lane_def in enumerate(lanes_template):
                    if lane_def.get("category_id") == matched_cat:
                        target_lane_id = f"split-{index}"
                        break
            elif dominant_lane_id is not None:
                target_lane_id = dominant_lane_id
            new_assignments.append(AllocationAssignment(item_id=item.item_id, lane_id=target_lane_id))

        updated_ws = AW(
            version=ws.version,
            twin_version=ws.twin_version,
            generated_at=ws.generated_at,
            items=ws.items,
            lanes=new_lanes,
            assignments=new_assignments,
            warnings=ws.warnings,
        )
        workspace_dict = updated_ws.model_dump(mode="json")

        # Build the payload splits from the template lanes.
        new_splits = [
            {"category_id": lane_def.get("category_id") or "", "amount": 0.0, "memo": ""}
            for lane_def in lanes_template
        ]
        new_payload = dict(payload)
        new_payload["category_id"] = None
        new_payload["splits"] = new_splits

        # Route amounts through recompute (re-suggest mode owns milliunit-sum invariant).
        new_payload, workspace_dict, _warnings = recompute_payload_from_workspace(
            new_payload,
            workspace_dict,
            mode="re_suggest",
        )
        new_payload["category_source"] = "payee_memory"
        return new_payload, workspace_dict, True

    except Exception:
        logger.exception(
            "apply_split_memory_to_workspace failed for payee_key=%s — falling back",
            memory.payee_key,
        )
        return payload, workspace, False


def upsert_payee_memory(
    db: Any,
    budget_id: str,
    payee_name: Any,
    *,
    category_id: str | None = None,
    template: dict[str, Any] | None = None,
) -> PayeeCategoryMemory | None:
    """Create or update a payee→category memory. Does NOT commit.

    Returns None (no-op) when:
    - payee_name normalizes to None
    - budget_id is blank
    - both category_id and template are falsy

    Single-category and split-template are mutually exclusive (last-write-wins;
    setting one clears the other).

    Uses begin_nested() + IntegrityError re-fetch for concurrent-insert safety.
    NEVER calls full db.rollback() — a full rollback would discard the caller's
    pending transaction writes (e.g. gamification/corrections).
    """
    key = normalize_payee_key(payee_name)
    if not key:
        return None
    if not budget_id or not budget_id.strip():
        return None
    if not category_id and not template:
        return None

    # Single↔split exclusive: template wins when both given.
    if template:
        effective_category_id = None
        effective_template: dict[str, Any] | None = template
    else:
        effective_category_id = category_id
        effective_template = None

    existing = db.scalar(
        select(PayeeCategoryMemory).where(
            PayeeCategoryMemory.budget_id == budget_id,
            PayeeCategoryMemory.payee_key == key,
        )
    )
    if existing is not None:
        existing.category_id = effective_category_id
        existing.template_json = effective_template
        db.add(existing)
        return existing

    def _create() -> PayeeCategoryMemory:
        row = PayeeCategoryMemory(
            budget_id=budget_id,
            payee_key=key,
            category_id=effective_category_id,
            template_json=effective_template,
        )
        db.add(row)
        db.flush()
        return row

    try:
        with db.begin_nested():
            return _create()
    except IntegrityError:
        # Another writer raced us. The begin_nested() context manager has ALREADY
        # rolled back to its savepoint, leaving the outer transaction (and any
        # in-flight bookkeeping writes from the caller) intact. Do NOT call
        # db.rollback() here — a full-session rollback would discard the caller's
        # pending gamification/corrections writes. Just re-fetch and update.
        existing = db.scalar(
            select(PayeeCategoryMemory).where(
                PayeeCategoryMemory.budget_id == budget_id,
                PayeeCategoryMemory.payee_key == key,
            )
        )
        if existing is not None:
            existing.category_id = effective_category_id
            existing.template_json = effective_template
            db.add(existing)
            return existing
        return _create()


def build_template_from_validation(
    validation_payload: dict[str, Any],
    workspace: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a split template dict from a validation payload + workspace, or None for single-category.

    Returns None when the payload is single-category (no splits).

    Template shape:
    {
        "version": 1,
        "lanes": [{"category_id": "..."}],
        "dominant_category_id": "...",
        "item_categories": {"<normalized item text>": "cat-id"},
    }

    dominant_category_id = the category_id of the lane with the largest summed
    item amounts (by absolute value). Falls back to the first lane if no items.
    """
    splits = validation_payload.get("splits")
    if not isinstance(splits, list) or len(splits) == 0:
        return None

    lanes = [{"category_id": str(split.get("category_id") or "") or None} for split in splits]

    # Build item_categories from workspace assignments.
    item_categories: dict[str, str] = {}
    lane_weights: dict[int, float] = {i: 0.0 for i in range(len(lanes))}

    if isinstance(workspace, dict):
        try:
            from receipt_shared.contracts import AllocationWorkspace as AW
            ws = AW.model_validate(workspace)

            item_by_id = {item.item_id: item for item in ws.items}
            for assignment in ws.assignments:
                if not assignment.lane_id.startswith("split-"):
                    continue
                try:
                    lane_index = int(assignment.lane_id.split("-", 1)[1])
                except (ValueError, IndexError):
                    continue
                item = item_by_id.get(assignment.item_id)
                if item is None:
                    continue
                if item.label and lane_index < len(lanes):
                    normalized = normalize_item_text(item.label)
                    if normalized and lanes[lane_index].get("category_id"):
                        item_categories[normalized] = lanes[lane_index]["category_id"]
                if item.amount is not None and lane_index < len(lane_weights):
                    lane_weights[lane_index] += abs(float(item.amount))
        except Exception:
            logger.exception("build_template_from_validation: workspace parse failed")

    # Dominant lane = largest weight; fallback to first lane.
    dominant_index = max(lane_weights, key=lambda i: lane_weights[i]) if lane_weights else 0
    dominant_category_id = lanes[dominant_index].get("category_id") if dominant_index < len(lanes) else None
    if dominant_category_id is None and lanes:
        dominant_category_id = lanes[0].get("category_id")

    return {
        "version": 1,
        "lanes": lanes,
        "dominant_category_id": dominant_category_id,
        "item_categories": item_categories,
    }
