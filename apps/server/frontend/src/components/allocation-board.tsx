"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Announcements,
  DndContext,
  DragEndEvent,
  DragOverlay,
  DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical } from "lucide-react";

import { AllocationItem, AllocationLane, AllocationWorkspace } from "@/lib/types";
import { setWorkspaceLanePinnedAmount } from "@/lib/allocation-workspace";
import { isSelectionFullyInLane } from "@/lib/allocation-board-helpers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type CategoryOption = {
  entity_id: string;
  name: string;
  group_name: string | null;
};

function laneLabel(laneId: string, categoryId: string | null, categories: CategoryOption[]): string {
  if (laneId === "unassigned") return "Unassigned";
  if (laneId === "main") return "Main Transaction";
  if (laneId.startsWith("split-")) {
    const splitIndex = Number(laneId.split("-", 2)[1] ?? "0");
    const category = categories.find((row) => row.entity_id === categoryId);
    if (!category) return `Split ${splitIndex + 1}`;
    const prefix = category.group_name ? `${category.group_name} / ` : "";
    return `Split ${splitIndex + 1}: ${prefix}${category.name}`;
  }
  return laneId;
}

const UNDO_TTL_MS = 6000;

function laneDollarTotal(
  laneId: string,
  items: AllocationItem[],
  assignments: AllocationWorkspace["assignments"],
): number | null {
  const laneItemIds = new Set(assignments.filter((a) => a.lane_id === laneId).map((a) => a.item_id));
  const itemMap = new Map(items.map((item) => [item.item_id, item]));
  let total = 0;
  let hasAmount = false;
  for (const itemId of laneItemIds) {
    const item = itemMap.get(itemId);
    if (item && item.amount != null) {
      total += item.amount;
      hasAmount = true;
    }
  }
  return hasAmount ? Math.round(total * 100) / 100 : null;
}

function DraggableAllocationItem({
  item,
  selected,
  onToggle,
  laneColorClass,
}: {
  item: AllocationItem;
  selected: boolean;
  onToggle: (itemId: string) => void;
  laneColorClass?: string;
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: item.item_id,
    data: { item },
  });
  const style = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <button
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      type="button"
      aria-pressed={selected}
      data-testid={`alloc-item-${item.source_index}`}
      className={`w-full rounded-xl border border-l-2 px-2 py-1.5 text-left text-xs transition ${laneColorClass ?? "border-l-transparent"} ${
        selected ? "border-sky-500 bg-sky-100 text-sky-900" : "border-ink/15 bg-white text-ink hover:bg-sand/50"
      }`}
      onClick={() => onToggle(item.item_id)}
    >
      <div className="flex items-center gap-1.5">
        <GripVertical className="h-3.5 w-3.5 shrink-0 text-ink/30 cursor-grab" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-medium truncate">
            {item.translated_text || item.label || `Item ${item.source_index + 1}`}
          </p>
          {item.raw_text && item.raw_text !== item.translated_text ? (
            <p className="text-[10px] text-ink/50 font-mono uppercase truncate">{item.raw_text}</p>
          ) : null}
          <p className="text-[11px] text-ink/70">
            {item.amount == null ? "Unknown amount" : `$${Math.abs(item.amount).toFixed(2)}`}
          </p>
        </div>
      </div>
    </button>
  );
}

function PinBadge({ pinnedAmount, onUnpin }: { pinnedAmount: number; onUnpin: () => void }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-sky-400 bg-sky-100 px-2 py-0.5 text-[10px] font-semibold text-sky-800"
      aria-label={`Pinned at $${pinnedAmount.toFixed(2)}`}
    >
      {String.fromCodePoint(0x1f4cc)} {`$${pinnedAmount.toFixed(2)}`}
      <button
        type="button"
        aria-label="Remove pin"
        className="ml-0.5 rounded-full p-0.5 hover:bg-sky-200"
        onClick={onUnpin}
      >
        {"×"}
      </button>
    </span>
  );
}

function getLaneColorClass(laneId: string): string {
  if (laneId === "unassigned") return "border-l-slate-300";
  if (laneId === "main") return "border-l-sky-400";
  const splitIndex = Number(laneId.split("-")[1] ?? 0);
  const colors = ["border-l-violet-400", "border-l-amber-400", "border-l-emerald-400", "border-l-rose-400"];
  return colors[splitIndex % colors.length] ?? "border-l-sky-400";
}

function LaneColumn({
  lane,
  title,
  dollarTotal,
  items,
  selectedItemIds,
  assignments,
  onToggleItem,
  onUnpin,
  onMoveItems,
}: {
  lane: AllocationLane;
  title: string;
  dollarTotal: number | null;
  items: AllocationItem[];
  selectedItemIds: Set<string>;
  assignments: { item_id: string; lane_id: string }[];
  onToggleItem: (itemId: string) => void;
  onUnpin: (laneId: string) => void;
  onMoveItems: (itemIds: string[], laneId: string) => void;
}) {
  const { isOver, setNodeRef } = useDroppable({ id: lane.lane_id });
  const colorClass = getLaneColorClass(lane.lane_id);
  const selectionAlreadyHere = isSelectionFullyInLane(selectedItemIds, lane.lane_id, assignments);

  return (
    <div
      ref={setNodeRef}
      role="group"
      aria-label={title}
      data-testid={`lane-${lane.lane_id}`}
      className={`rounded-2xl border p-2 ${isOver ? "border-sky-500 bg-sky-50" : "border-ink/15 bg-sand/40"}`}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink/70">{title}</p>
          {lane.pinned_amount != null ? (
            <PinBadge pinnedAmount={lane.pinned_amount} onUnpin={() => onUnpin(lane.lane_id)} />
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {selectedItemIds.size > 0 && !selectionAlreadyHere ? (
            <button
              type="button"
              className="text-[10px] font-semibold text-sky-700 hover:underline focus-visible:ring-1 focus-visible:ring-sky-400 rounded px-1"
              onClick={() => onMoveItems(Array.from(selectedItemIds), lane.lane_id)}
              title={`Move ${selectedItemIds.size} selected item(s) here`}
            >
              ← Move here
            </button>
          ) : null}
          {dollarTotal != null ? (
            <p className="text-[11px] font-semibold text-ink/60">${dollarTotal.toFixed(2)}</p>
          ) : null}
          <p className="text-[11px] text-ink/60">{items.length}</p>
        </div>
      </div>
      <div className="space-y-1">
        {items.length === 0 ? (
          <p className="rounded-lg border border-dashed border-ink/20 px-2 py-2 text-[11px] text-ink/50">
            Drop items here
          </p>
        ) : null}
        {items.map((item) => (
          <DraggableAllocationItem
            key={item.item_id}
            item={item}
            selected={selectedItemIds.has(item.item_id)}
            onToggle={onToggleItem}
            laneColorClass={colorClass}
          />
        ))}
      </div>
    </div>
  );
}

export function AllocationBoard({
  workspace,
  categories,
  selectedItemIds,
  onToggleItem,
  onClearSelection,
  onMoveItems,
  onRecomputeDiscard,
  onRecomputeKeep,
  isRecomputing,
  warnings,
  onWorkspaceChange,
  onRefreshFromTwin,
}: {
  workspace: AllocationWorkspace;
  categories: CategoryOption[];
  selectedItemIds: Set<string>;
  onToggleItem: (itemId: string) => void;
  onClearSelection: () => void;
  onMoveItems: (itemIds: string[], laneId: string) => void;
  onRecomputeDiscard: () => void;
  onRecomputeKeep: () => void;
  isRecomputing: boolean;
  warnings: string[];
  onWorkspaceChange?: (next: AllocationWorkspace) => void;
  onRefreshFromTwin?: () => void;
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 100, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const [activeDragItemIds, setActiveDragItemIds] = useState<string[]>([]);

  const previousWorkspaceRef = useRef<AllocationWorkspace | null>(null);
  const [undoAvailable, setUndoAvailable] = useState(false);
  const undoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const offerUndo = useCallback((snapshot: AllocationWorkspace) => {
    previousWorkspaceRef.current = snapshot;
    setUndoAvailable(true);
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
    undoTimerRef.current = setTimeout(() => {
      setUndoAvailable(false);
      previousWorkspaceRef.current = null;
    }, UNDO_TTL_MS);
  }, []);

  const handleUndo = useCallback(() => {
    if (!previousWorkspaceRef.current || !onWorkspaceChange) return;
    onWorkspaceChange(previousWorkspaceRef.current);
    setUndoAvailable(false);
    if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
    previousWorkspaceRef.current = null;
  }, [onWorkspaceChange]);

  useEffect(() => {
    return () => {
      if (undoTimerRef.current) clearTimeout(undoTimerRef.current);
    };
  }, []);

  const handleUnpinLane = (laneId: string) => {
    if (!onWorkspaceChange) return;
    onWorkspaceChange(setWorkspaceLanePinnedAmount(workspace, laneId, null));
  };

  const itemsByLane = useMemo(() => {
    const itemMap = new Map(workspace.items.map((item) => [item.item_id, item]));
    const laneMap = new Map<string, AllocationItem[]>();
    workspace.lanes.forEach((lane) => laneMap.set(lane.lane_id, []));
    workspace.assignments.forEach((assignment) => {
      const laneRows = laneMap.get(assignment.lane_id);
      const item = itemMap.get(assignment.item_id);
      if (!laneRows || !item) return;
      laneRows.push(item);
    });
    workspace.lanes.forEach((lane) => {
      laneMap.get(lane.lane_id)?.sort((a, b) => a.source_index - b.source_index);
    });
    return laneMap;
  }, [workspace]);

  const activeItems = useMemo(() => {
    if (activeDragItemIds.length === 0) return [];
    const itemMap = new Map(workspace.items.map((item) => [item.item_id, item]));
    return activeDragItemIds
      .map((itemId) => itemMap.get(itemId))
      .filter((item): item is AllocationItem => !!item);
  }, [activeDragItemIds, workspace.items]);

  const onDragStart = (event: DragStartEvent) => {
    const activeId = String(event.active.id);
    if (selectedItemIds.has(activeId) && selectedItemIds.size > 0) {
      setActiveDragItemIds(Array.from(selectedItemIds));
      return;
    }
    setActiveDragItemIds([activeId]);
  };

  const onDragEnd = (event: DragEndEvent) => {
    const laneId = event.over ? String(event.over.id) : "";
    if (laneId && activeDragItemIds.length > 0) {
      const dragSet = new Set(activeDragItemIds);
      const isNoop = isSelectionFullyInLane(dragSet, laneId, workspace.assignments);
      if (!isNoop) {
        offerUndo(workspace);
        onMoveItems(activeDragItemIds, laneId);
      }
    }
    setActiveDragItemIds([]);
  };

  const handleRecomputeDiscard = () => {
    offerUndo(workspace);
    onRecomputeDiscard();
  };

  const handleRecomputeKeep = () => {
    offerUndo(workspace);
    onRecomputeKeep();
  };

  const announcements: Announcements = {
    onDragStart: ({ active }) => `Picked up item ${active.id}.`,
    onDragOver: ({ active, over }) =>
      over ? `Item ${active.id} is over lane ${over.id}.` : `Item ${active.id} is not over a lane.`,
    onDragEnd: ({ active, over }) =>
      over ? `Item ${active.id} dropped into lane ${over.id}.` : `Item ${active.id} was dropped.`,
    onDragCancel: ({ active }) => `Drag of item ${active.id} cancelled.`,
  };

  const staleWarningIndex = warnings.findIndex((w) => w.includes("Line items changed"));

  return (
    <Card className="animate-reveal space-y-3" style={{ animationDelay: "190ms" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Item Allocation</h2>
        <div className="flex flex-wrap items-center gap-1">
          <Button variant="outline" size="sm" className="gap-1" onClick={onClearSelection} disabled={selectedItemIds.size === 0}>
            Clear selection
          </Button>
          <Button variant="solid" size="sm" className="gap-1" data-testid="recompute-keep" onClick={handleRecomputeKeep} disabled={isRecomputing} title="Re-run the AI allocation, keeping your manual amount adjustments">
            {isRecomputing ? "Thinking..." : "Re-suggest"}
          </Button>
          <Button variant="outline" size="sm" className="gap-1 border-amber-400 text-amber-800 hover:bg-amber-50" data-testid="recompute-discard" onClick={handleRecomputeDiscard} disabled={isRecomputing} title="Start completely fresh — discard all manual changes and let AI reallocate">
            Start fresh
          </Button>
          {undoAvailable && onWorkspaceChange ? (
            <Button
              variant="outline"
              size="sm"
              className="gap-1 border-sky-400 text-sky-700 hover:bg-sky-50"
              onClick={handleUndo}
            >
              Undo move
            </Button>
          ) : null}
        </div>
      </div>
      <p className="text-xs text-ink/65">Select items, then drag one to move the full selected group between lanes.</p>

      <DndContext
        sensors={sensors}
        accessibility={{ announcements }}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDragCancel={() => setActiveDragItemIds([])}
      >
        <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
          {workspace.lanes.map((lane) => (
            <LaneColumn
              key={lane.lane_id}
              lane={lane}
              title={laneLabel(lane.lane_id, lane.category_id, categories)}
              items={itemsByLane.get(lane.lane_id) ?? []}
              dollarTotal={laneDollarTotal(lane.lane_id, workspace.items, workspace.assignments)}
              selectedItemIds={selectedItemIds}
              assignments={workspace.assignments}
              onToggleItem={onToggleItem}
              onUnpin={handleUnpinLane}
              onMoveItems={(itemIds, laneId) => {
                offerUndo(workspace);
                onMoveItems(itemIds, laneId);
              }}
            />
          ))}
        </div>
        <DragOverlay>
          {activeItems.length > 0 ? (
            <div className="w-56 rounded-xl border border-sky-500 bg-white p-2 shadow-float">
              <p className="text-xs font-semibold text-sky-800">{activeItems.length} item(s)</p>
              {activeItems.slice(0, 4).map((item) => (
                <p key={item.item_id} className="truncate text-[11px] text-ink/75">
                  {item.label}
                </p>
              ))}
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>

      {warnings.length > 0 ? (
        <div className="space-y-1 rounded-xl border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
          {warnings.map((warning, index) => (
            <div key={`${warning}-${index}`} className={index === staleWarningIndex ? "flex items-center gap-2" : ""}>
              <p>- {warning}</p>
              {index === staleWarningIndex && onRefreshFromTwin ? (
                <button
                  type="button"
                  className="ml-1 shrink-0 rounded-md border border-amber-400 bg-white px-2 py-0.5 text-[11px] font-semibold text-amber-800 hover:bg-amber-100"
                  onClick={onRefreshFromTwin}
                >
                  Refresh allocation from twin
                </button>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </Card>
  );
}
