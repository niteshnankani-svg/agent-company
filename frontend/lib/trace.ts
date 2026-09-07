import type { AuditTrailEntry } from "./types";

export interface TraceNode {
  id: string;
  order: number;
  firstSeenAt: number;
}

export interface TraceStep {
  index: number;
  entry: AuditTrailEntry;
  isRevisit: boolean;
  fromNodeOrder: number | null;
  toNodeOrder: number;
  isLoopBack: boolean;
  loopDepth: number;
}

export interface Trace {
  nodes: TraceNode[];
  steps: TraceStep[];
}

/**
 * Turns the flat, real audit_trail returned by the backend into a graph:
 * one visual node per distinct node name (positioned by first-seen order),
 * and one step per trail entry so a retry loop revisits the same node
 * instead of spawning a duplicate.
 */
export function buildTrace(trail: AuditTrailEntry[]): Trace {
  const nodeOrderMap = new Map<string, number>();
  const nodes: TraceNode[] = [];
  const steps: TraceStep[] = [];
  let prevOrder: number | null = null;
  let loopCounter = 0;

  trail.forEach((entry, i) => {
    let order = nodeOrderMap.get(entry.node);
    const isRevisit = order !== undefined;
    if (order === undefined) {
      order = nodes.length;
      nodeOrderMap.set(entry.node, order);
      nodes.push({ id: entry.node, order, firstSeenAt: i });
    }
    const isLoopBack = prevOrder !== null && order <= prevOrder;
    if (isLoopBack) loopCounter += 1;
    steps.push({
      index: i,
      entry,
      isRevisit,
      fromNodeOrder: prevOrder,
      toNodeOrder: order,
      isLoopBack,
      loopDepth: isLoopBack ? loopCounter : 0,
    });
    prevOrder = order;
  });

  return { nodes, steps };
}

export function nodeDisplayName(id: string): string {
  return id
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
