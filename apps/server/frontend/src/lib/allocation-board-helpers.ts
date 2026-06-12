/**
 * Pure helpers for allocation-board.tsx.
 * Extracted so they can be unit-tested independently of React.
 */

/**
 * Returns true when every item in selectedItemIds is already assigned to laneId.
 * A move of this selection to laneId would be a no-op, so "← Move here" should
 * be hidden for that lane and offerUndo should be skipped.
 */
export function isSelectionFullyInLane(
  selectedItemIds: Set<string>,
  laneId: string,
  assignments: { item_id: string; lane_id: string }[],
): boolean {
  if (selectedItemIds.size === 0) return false;
  return Array.from(selectedItemIds).every((itemId) => {
    const assignment = assignments.find((a) => a.item_id === itemId);
    return assignment?.lane_id === laneId;
  });
}
