import { AllocationAssignment, AllocationItem, AllocationLane, AllocationWorkspace, ReceiptTwin, ValidationPayloadInput } from "@/lib/types";

export const MAIN_LANE_ID = "main";
export const UNASSIGNED_LANE_ID = "unassigned";

function normalizeAmount(value: number): number {
  return Math.round(value * 100) / 100;
}

function laneDefinitionsFromDraft(draft: ValidationPayloadInput): AllocationLane[] {
  const lanes: AllocationLane[] = [];
  if (draft.splits.length > 0) {
    draft.splits.forEach((split, index) => {
      lanes.push({
        lane_id: `split-${index}`,
        category_id: split.category_id || null,
        pinned_amount: null,
      });
    });
  } else {
    lanes.push({
      lane_id: MAIN_LANE_ID,
      category_id: draft.category_id || null,
      pinned_amount: null,
    });
  }
  lanes.push({
    lane_id: UNASSIGNED_LANE_ID,
    category_id: null,
    pinned_amount: null,
  });
  return lanes;
}

function inferAllocatableItems(twin: ReceiptTwin | null): AllocationItem[] {
  if (!twin) return [];
  const rows = Array.isArray(twin.payload.line_items) ? twin.payload.line_items : [];
  return rows
    .filter((item) => {
      const kind = String(item.item_type || "").toLowerCase();
      return kind !== "subtotal" && kind !== "total";
    })
    .map((item, index) => {
      const sourceIndex = Number.isFinite(item.index) ? item.index : index;
      const translated = item.translated_text?.trim() || null;
      const raw = item.raw_text?.trim() || null;
      // Label uses translated-first for accessibility names and search; raw is stored separately.
      const label = translated || raw || `Line ${sourceIndex + 1}`;
      const amount = typeof item.line_total === "number" && Number.isFinite(item.line_total) ? normalizeAmount(Math.abs(item.line_total)) : null;
      return {
        item_id: `item-${sourceIndex}-${index}`,
        source_index: sourceIndex,
        label,
        translated_text: translated,
        raw_text: raw,
        amount,
        tax_code: item.tax_code ?? null,
        item_type: item.item_type ?? "product",
      };
    });
}

function defaultAssignments(items: AllocationItem[], lanes: AllocationLane[]): AllocationAssignment[] {
  const firstLane = lanes.find((lane) => lane.lane_id !== UNASSIGNED_LANE_ID)?.lane_id ?? UNASSIGNED_LANE_ID;
  return items.map((item) => ({
    item_id: item.item_id,
    lane_id: item.amount == null ? UNASSIGNED_LANE_ID : firstLane,
  }));
}

function isWorkspaceLike(value: unknown): value is AllocationWorkspace {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  return Array.isArray(row.items) && Array.isArray(row.lanes) && Array.isArray(row.assignments);
}

export function buildFallbackWorkspace(draft: ValidationPayloadInput, twin: ReceiptTwin | null): AllocationWorkspace {
  const lanes = laneDefinitionsFromDraft(draft);
  const items = inferAllocatableItems(twin);
  const assignments = defaultAssignments(items, lanes);
  return {
    version: 1,
    twin_version: twin?.version ?? 0,
    generated_at: new Date().toISOString(),
    items,
    lanes,
    assignments,
    warnings: [],
  };
}

export function reconcileWorkspaceToDraft(
  workspace: AllocationWorkspace | null,
  draft: ValidationPayloadInput,
  twin: ReceiptTwin | null,
): AllocationWorkspace {
  const fallback = buildFallbackWorkspace(draft, twin);
  if (!workspace) return fallback;

  const expectedLanes = laneDefinitionsFromDraft(draft);
  const expectedLaneIds = new Set(expectedLanes.map((lane) => lane.lane_id));
  const existingLaneMap = new Map(workspace.lanes.map((lane) => [lane.lane_id, lane]));

  const lanes = expectedLanes.map((lane) => {
    const existing = existingLaneMap.get(lane.lane_id);
    if (!existing) return lane;
    return {
      lane_id: lane.lane_id,
      category_id: lane.category_id,
      pinned_amount: lane.lane_id === UNASSIGNED_LANE_ID ? null : existing.pinned_amount ?? null,
    };
  });

  const itemIds = new Set(workspace.items.map((item) => item.item_id));
  const assignments = workspace.assignments
    .filter((assignment) => itemIds.has(assignment.item_id))
    .map((assignment) => ({
      item_id: assignment.item_id,
      lane_id: expectedLaneIds.has(assignment.lane_id) ? assignment.lane_id : UNASSIGNED_LANE_ID,
    }));

  const assignedIds = new Set(assignments.map((assignment) => assignment.item_id));
  workspace.items.forEach((item) => {
    if (!assignedIds.has(item.item_id)) {
      assignments.push({ item_id: item.item_id, lane_id: UNASSIGNED_LANE_ID });
    }
  });

  const warnings = [...(workspace.warnings ?? [])];
  if ((workspace.twin_version ?? 0) !== (twin?.version ?? 0)) {
    warnings.push("Line items changed in Receipt details. Refreshing allocation is recommended.");
  }

  return {
    version: workspace.version ?? 1,
    twin_version: twin?.version ?? 0,
    generated_at: workspace.generated_at ?? new Date().toISOString(),
    items: workspace.items,
    lanes,
    assignments,
    warnings,
  };
}

export function workspaceFromApi(
  value: unknown,
  draft: ValidationPayloadInput,
  twin: ReceiptTwin | null,
): AllocationWorkspace {
  if (!isWorkspaceLike(value)) {
    return buildFallbackWorkspace(draft, twin);
  }
  return reconcileWorkspaceToDraft(value, draft, twin);
}

export function moveWorkspaceItems(
  workspace: AllocationWorkspace,
  itemIds: string[],
  laneId: string,
): AllocationWorkspace {
  const idSet = new Set(itemIds);
  const assignments = workspace.assignments.map((assignment) => {
    if (!idSet.has(assignment.item_id)) return assignment;
    return { ...assignment, lane_id: laneId };
  });
  return {
    ...workspace,
    assignments,
    generated_at: new Date().toISOString(),
  };
}

export function setWorkspaceLanePinnedAmount(
  workspace: AllocationWorkspace,
  laneId: string,
  amount: number | null,
): AllocationWorkspace {
  return {
    ...workspace,
    lanes: workspace.lanes.map((lane) =>
      lane.lane_id === laneId
        ? {
            ...lane,
            pinned_amount: amount == null ? null : normalizeAmount(amount),
          }
        : lane,
    ),
    generated_at: new Date().toISOString(),
  };
}

export function clearWorkspacePins(workspace: AllocationWorkspace): AllocationWorkspace {
  return {
    ...workspace,
    lanes: workspace.lanes.map((lane) => ({
      ...lane,
      pinned_amount: null,
    })),
    generated_at: new Date().toISOString(),
  };
}
