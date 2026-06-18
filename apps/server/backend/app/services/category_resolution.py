"""Category/split candidate generation for the Quick-review flow.

Turns the model's primary pick plus its low-confidence `candidate_arrangements`
into up to three COMPLETE, sum-to-total arrangements the user can one-tap accept.
Every arrangement is materialized to an exact milliunit sum and re-validated with
the same `validate_payload` the money path uses, so a candidate can never reach
/sync with splits that don't add up.

Grounding: `payee_category_hint_text` surfaces the user's learned payee→category
habits (PayeeCategoryMemory) so the model's guesses reflect past choices. This is
the deterministic Tier-0 signal; the deeper budget-pattern reasoning pass is
backlog.
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PayeeCategoryMemory
from app.services.validation import validate_payload
from app.services.allocation_workspace import build_initial_allocation_workspace
from receipt_shared.money import dollars_to_milliunits, milliunits_to_dollars

MAX_CANDIDATES = 3


def payee_category_hint_text(
    db: Session,
    budget_id: str,
    category_names: dict[str, str],
    *,
    limit: int = 80,
) -> str:
    """A compact table of learned payee→category habits for the extraction prompt.

    Returns "" when nothing is learned yet (so the prompt is unchanged). Built from
    PayeeCategoryMemory — populated on every successful sync — so the model can
    ground its guesses in the user's past choices ("you've used Groceries here").
    """
    if not budget_id:
        return ""
    rows = db.scalars(
        select(PayeeCategoryMemory)
        .where(PayeeCategoryMemory.budget_id == budget_id)
        .order_by(PayeeCategoryMemory.updated_at.desc())
        .limit(limit)
    ).all()
    lines: list[str] = []
    for row in rows:
        if row.category_id and row.category_id in category_names:
            lines.append(f"- {row.payee_key} → {category_names[row.category_id]}")
        elif row.template_json:
            lines.append(f"- {row.payee_key} → (a learned split)")
    return "\n".join(lines)


def _distribute_milliunits(total_amount: Any, weights: list[float]) -> list[float]:
    """Split `total_amount` across `weights` so the parts sum EXACTLY to the total
    in milliunits (largest-remainder). Equal weights when all are zero/blank."""
    n = len(weights)
    if n == 0:
        return []
    total_mu = dollars_to_milliunits(total_amount or 0, outflow=False)
    clean = [
        w if (isinstance(w, (int, float)) and math.isfinite(w) and w > 0) else 0.0
        for w in weights
    ]
    wsum = sum(clean)
    if wsum <= 0:
        clean = [1.0] * n
        wsum = float(n)
    raw = [total_mu * (w / wsum) for w in clean]
    floors = [int(math.floor(x)) for x in raw]
    remainder = total_mu - sum(floors)
    # Hand the leftover milliunits to the largest fractional parts.
    order = sorted(range(n), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in range(max(remainder, 0)):
        floors[order[i % n]] += 1
    return [milliunits_to_dollars(mu) for mu in floors]


def materialize_arrangement(
    base_payload: dict[str, Any],
    arrangement: dict[str, Any],
    *,
    allowed_category_ids: set[str],
) -> dict[str, Any] | None:
    """Build a complete payload (money fields from base_payload, category/splits from
    the arrangement). Returns None if the arrangement references unknown categories or
    is structurally empty. Split amounts are re-distributed to an exact milliunit sum."""
    if not base_payload:
        return None
    cat = arrangement.get("category_id")
    splits = arrangement.get("splits") or []
    payload = dict(base_payload)
    payload.pop("category_source", None)  # an AI guess, not learned memory

    if isinstance(splits, list) and len(splits) >= 2:
        cats = [str(s.get("category_id") or "") for s in splits]
        if any((not c) or c not in allowed_category_ids for c in cats):
            return None
        weights = [s.get("amount") or 0.0 for s in splits]
        amounts = _distribute_milliunits(payload.get("total_amount"), weights)
        payload["category_id"] = None
        payload["splits"] = [
            {"category_id": cats[i], "amount": amounts[i], "memo": str(splits[i].get("memo") or "")}
            for i in range(len(cats))
        ]
        return payload

    if cat and cat in allowed_category_ids:
        payload["category_id"] = cat
        payload["splits"] = []
        return payload

    return None


def _arrangement_signature(payload: dict[str, Any]) -> tuple:
    """Dedup key over category/splits only (ignores money fields + memo text)."""
    splits = payload.get("splits") or []
    if splits:
        return ("split", tuple(sorted(
            (str(s.get("category_id") or ""), dollars_to_milliunits(s.get("amount") or 0, outflow=False))
            for s in splits
        )))
    return ("single", str(payload.get("category_id") or ""))


def _label_for(payload: dict[str, Any], category_names: dict[str, str]) -> str:
    splits = payload.get("splits") or []
    if splits:
        names = [category_names.get(str(s.get("category_id") or ""), "Category") for s in splits]
        # De-dupe consecutive identical names for a tidy label.
        seen: list[str] = []
        for name in names:
            if name not in seen:
                seen.append(name)
        return " + ".join(seen)
    return category_names.get(str(payload.get("category_id") or ""), "Category")


def _primary_confidence(ambiguity_flags: list[dict[str, Any]]) -> float:
    """A display hint for the pre-selected #1 card: lower when the model flagged
    more category ambiguity. Floored so it never reads as near-zero."""
    if not ambiguity_flags:
        return 0.9
    try:
        worst = max(float(f.get("confidence") or 0.0) for f in ambiguity_flags)
    except (TypeError, ValueError):
        worst = 0.0
    return round(max(0.5, 1.0 - worst), 2)


def materialize_proposals(
    base_payload: dict[str, Any],
    raw_proposals: list[dict[str, Any]],
    *,
    twin_payload: dict[str, Any] | None,
    twin_version: int,
    category_names: dict[str, str],
    allowed_category_ids: set[str],
    allowed_account_ids: set[str],
    provenance: str = "user_instruction",
    limit: int = MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    """Materialize a list of raw arrangements (e.g. type-to-organize proposals) into
    stored candidate dicts, in order. Each is validated to an exact sum-to-total
    payload; invalid/duplicate ones are dropped. No primary is prepended."""
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for raw in raw_proposals or []:
        if len(out) >= limit:
            break
        if not isinstance(raw, dict):
            continue
        mat = materialize_arrangement(base_payload, raw, allowed_category_ids=allowed_category_ids)
        if mat is None:
            continue
        _, is_valid, _ = validate_payload(
            mat, allowed_category_ids=allowed_category_ids, allowed_account_ids=allowed_account_ids
        )
        if not is_valid:
            continue
        key = _arrangement_signature(mat)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "label": str(raw.get("label") or "") or _label_for(mat, category_names),
            "rationale": str(raw.get("rationale") or ""),
            "confidence": float(raw.get("confidence") or 0.0),
            "category_id": mat.get("category_id"),
            "splits": mat.get("splits") or [],
            "allocation_workspace": build_initial_allocation_workspace(
                mat, twin_payload=twin_payload, twin_version=twin_version
            ),
            "provenance": provenance,
        })
    return out


def build_candidate_arrangements(
    base_payload: dict[str, Any],
    gemini_candidates: list[dict[str, Any]],
    *,
    ambiguity_flags: list[dict[str, Any]] | None,
    twin_payload: dict[str, Any] | None,
    twin_version: int,
    category_names: dict[str, str],
    allowed_category_ids: set[str],
    allowed_account_ids: set[str],
) -> list[dict[str, Any]]:
    """Assemble up to MAX_CANDIDATES stored arrangements: the primary pick first
    (pre-selected #1), then the model's distinct ranked alternatives. Each is
    materialized to an exact sum and validated; invalid/duplicate ones are dropped."""
    arrangements: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def _add(raw: dict[str, Any], *, rationale: str, confidence: float, provenance: str, label: str = "") -> None:
        if len(arrangements) >= MAX_CANDIDATES:
            return
        mat = materialize_arrangement(base_payload, raw, allowed_category_ids=allowed_category_ids)
        if mat is None:
            return
        _, is_valid, _ = validate_payload(
            mat,
            allowed_category_ids=allowed_category_ids,
            allowed_account_ids=allowed_account_ids,
        )
        if not is_valid:
            return
        key = _arrangement_signature(mat)
        if key in seen:
            return
        seen.add(key)
        arrangements.append({
            "label": label or _label_for(mat, category_names),
            "rationale": rationale,
            "confidence": confidence,
            "category_id": mat.get("category_id"),
            "splits": mat.get("splits") or [],
            "allocation_workspace": build_initial_allocation_workspace(
                mat, twin_payload=twin_payload, twin_version=twin_version
            ),
            "provenance": provenance,
        })

    # #1 — the primary pick (what extraction chose); always first + pre-selected.
    _add(
        {"category_id": base_payload.get("category_id"), "splits": base_payload.get("splits") or []},
        rationale="Snappy's first pick",
        confidence=_primary_confidence(ambiguity_flags or []),
        provenance="model_primary",
    )

    # Alternatives from the model, most confident first.
    for cand in sorted(gemini_candidates or [], key=lambda c: c.get("confidence") or 0.0, reverse=True):
        _add(
            cand,
            rationale=str(cand.get("rationale") or ""),
            confidence=float(cand.get("confidence") or 0.0),
            provenance="model_topk",
            label=str(cand.get("label") or ""),
        )

    return arrangements
