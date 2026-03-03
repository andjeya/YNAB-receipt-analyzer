"use client";

import { useMemo, useState } from "react";
import { DndContext, DragEndEvent, DragOverlay, DragStartEvent, PointerSensor, TouchSensor, useDraggable, useDroppable, useSensor, useSensors } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";

import { AllocationItem, AllocationWorkspace } from "@/lib/types";
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

function DraggableAllocationItem({
  item,
  selected,
  onToggle,
}: {
  item: AllocationItem;
  selected: boolean;
  onToggle: (itemId: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: item.item_id,
  });
  const style = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <button
      ref={setNodeRef}
      style={style}
      type="button"
      className={`w-full rounded-xl border px-2 py-1.5 text-left text-xs transition ${
        selected ? "border-sky-500 bg-sky-100 text-sky-900" : "border-ink/15 bg-white text-ink hover:bg-sand/50"
      }`}
      onClick={() => onToggle(item.item_id)}
      {...listeners}
      {...attributes}
    >
      <p className="font-semibold">{item.label || `Item ${item.source_index + 1}`}</p>
      <p className="text-[11px] text-ink/70">{item.amount == null ? "Unknown amount" : `$${Math.abs(item.amount).toFixed(2)}`}</p>
    </button>
  );
}

function LaneColumn({
  laneId,
  title,
  items,
  selectedItemIds,
  onToggleItem,
}: {
  laneId: string;
  title: string;
  items: AllocationItem[];
  selectedItemIds: Set<string>;
  onToggleItem: (itemId: string) => void;
}) {
  const { isOver, setNodeRef } = useDroppable({
    id: laneId,
  });

  return (
    <div ref={setNodeRef} className={`rounded-2xl border p-2 ${isOver ? "border-sky-500 bg-sky-50" : "border-ink/15 bg-sand/40"}`}>
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink/70">{title}</p>
        <p className="text-[11px] text-ink/60">{items.length}</p>
      </div>
      <div className="space-y-1">
        {items.length === 0 ? <p className="rounded-lg border border-dashed border-ink/20 px-2 py-2 text-[11px] text-ink/50">Drop items here</p> : null}
        {items.map((item) => (
          <DraggableAllocationItem
            key={item.item_id}
            item={item}
            selected={selectedItemIds.has(item.item_id)}
            onToggle={onToggleItem}
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
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 100, tolerance: 8 } }),
  );
  const [activeDragItemIds, setActiveDragItemIds] = useState<string[]>([]);

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
    return activeDragItemIds.map((itemId) => itemMap.get(itemId)).filter((item): item is AllocationItem => !!item);
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
      onMoveItems(activeDragItemIds, laneId);
    }
    setActiveDragItemIds([]);
  };

  return (
    <Card className="animate-reveal space-y-3" style={{ animationDelay: "190ms" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Item Allocation</h2>
        <div className="flex flex-wrap items-center gap-1">
          <Button variant="outline" size="sm" onClick={onClearSelection} disabled={selectedItemIds.size === 0}>
            Clear selection
          </Button>
          <Button variant="outline" size="sm" onClick={onRecomputeKeep} disabled={isRecomputing}>
            {isRecomputing ? "Recomputing..." : "Recompute (Keep Pinned)"}
          </Button>
          <Button size="sm" onClick={onRecomputeDiscard} disabled={isRecomputing}>
            Recompute (Discard Pinned)
          </Button>
        </div>
      </div>
      <p className="text-xs text-ink/65">Select items, then drag one to move the full selected group between lanes.</p>

      <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd} onDragCancel={() => setActiveDragItemIds([])}>
        <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
          {workspace.lanes.map((lane) => (
            <LaneColumn
              key={lane.lane_id}
              laneId={lane.lane_id}
              title={laneLabel(lane.lane_id, lane.category_id, categories)}
              items={itemsByLane.get(lane.lane_id) ?? []}
              selectedItemIds={selectedItemIds}
              onToggleItem={onToggleItem}
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
            <p key={`${warning}-${index}`}>- {warning}</p>
          ))}
        </div>
      ) : null}
    </Card>
  );
}
