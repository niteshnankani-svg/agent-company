"use client";

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { AuditTrailEntry } from "@/lib/types";
import { buildTrace, nodeDisplayName } from "@/lib/trace";
import { sourceMeta } from "@/lib/accents";
import SourceBadge from "./SourceBadge";

const TICK_MS = 550;
const NODE_W = 176;
const NODE_H = 60;
const COL_GAP = 208;
const ROW_Y = 118;
const PAD_X = 70;

function nodeCenter(order: number) {
  return { x: PAD_X + order * COL_GAP, y: ROW_Y };
}

export default function TraceGraph({ trail }: { trail: AuditTrailEntry[] }) {
  const trace = useMemo(() => buildTrace(trail), [trail]);
  const [visibleCount, setVisibleCount] = useState(0);
  const [playToken, setPlayToken] = useState(0);

  // Reset the replay whenever a new response (or a replay request) arrives.
  // Adjusting state during render — rather than in an effect — is the
  // pattern React recommends for "reset state when a prop changes".
  const [resetFor, setResetFor] = useState<{ trail: AuditTrailEntry[]; token: number } | null>(null);
  if (resetFor === null || resetFor.trail !== trail || resetFor.token !== playToken) {
    setResetFor({ trail, token: playToken });
    setVisibleCount(0);
  }

  useEffect(() => {
    if (trail.length === 0) return;
    let i = 0;
    const id = setInterval(() => {
      i += 1;
      setVisibleCount(i);
      if (i >= trail.length) clearInterval(id);
    }, TICK_MS);
    return () => clearInterval(id);
  }, [trail, playToken]);

  const width = PAD_X * 2 + Math.max(0, trace.nodes.length - 1) * COL_GAP + NODE_W - PAD_X;
  const isAnimating = visibleCount < trail.length;
  const currentStep = visibleCount > 0 ? trace.steps[visibleCount - 1] : null;

  return (
    <div className="glass-panel rounded-lg">
      <div className="flex items-center justify-between border-b px-4 py-2.5" style={{ borderColor: "var(--border-hairline)" }}>
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            {isAnimating && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60" style={{ background: "var(--source-local)" }} />
            )}
            <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: isAnimating ? "var(--source-local)" : "var(--text-tertiary)" }} />
          </span>
          <span className="font-mono text-[11px] uppercase tracking-widest" style={{ color: "var(--text-secondary)" }}>
            {trail.length === 0
              ? "graph traversal — awaiting request"
              : isAnimating
                ? "replaying the actual path taken"
                : "graph traversal — complete"}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {trail.length > 0 && (
            <span className="font-mono text-[11px] mono-tabular" style={{ color: "var(--text-tertiary)" }}>
              step {Math.min(visibleCount, trail.length)}/{trail.length}
            </span>
          )}
          <button
            disabled={trail.length === 0}
            onClick={() => setPlayToken((t) => t + 1)}
            className="rounded border px-2 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors disabled:opacity-30"
            style={{ borderColor: "var(--border-hairline-strong)", color: "var(--text-secondary)" }}
          >
            ↺ replay
          </button>
        </div>
      </div>

      <div className="overflow-x-auto px-2 py-6">
        <div style={{ position: "relative", width: Math.max(width, 320), height: 220 }}>
          <svg
            width={Math.max(width, 320)}
            height={220}
            className="absolute inset-0"
            style={{ overflow: "visible" }}
          >
            {trace.steps.map((step, i) => {
              if (i >= visibleCount || step.fromNodeOrder === null) return null;
              const from = nodeCenter(step.fromNodeOrder);
              const to = nodeCenter(step.toNodeOrder);
              const meta = sourceMeta(step.entry.source);

              if (step.isLoopBack) {
                const arc = 46 + (step.loopDepth % 3) * 22;
                const midX = (from.x + to.x) / 2;
                const path = `M ${from.x} ${from.y - NODE_H / 2} C ${from.x} ${from.y - NODE_H / 2 - arc}, ${to.x} ${to.y - NODE_H / 2 - arc}, ${to.x} ${to.y - NODE_H / 2}`;
                return (
                  <g key={i}>
                    <path d={path} fill="none" stroke="var(--source-llm)" strokeWidth={1.75} strokeDasharray="5 4" opacity={0.85} />
                    <text
                      x={midX}
                      y={from.y - NODE_H / 2 - arc - 6}
                      textAnchor="middle"
                      className="font-mono"
                      style={{ fill: "var(--source-llm)", fontSize: 9, letterSpacing: 1 }}
                    >
                      RETRY
                    </text>
                  </g>
                );
              }

              return (
                <line
                  key={i}
                  x1={from.x + NODE_W / 2}
                  y1={from.y}
                  x2={to.x - NODE_W / 2}
                  y2={to.y}
                  stroke={meta.color}
                  strokeWidth={1.75}
                  opacity={0.7}
                />
              );
            })}
          </svg>

          {/* traveling dot for the most recent hop */}
          <AnimatePresence>
            {currentStep && currentStep.fromNodeOrder !== null && (
              <motion.div
                key={visibleCount}
                initial={{
                  left: nodeCenter(currentStep.fromNodeOrder).x,
                  top: nodeCenter(currentStep.fromNodeOrder).y,
                  opacity: 1,
                }}
                animate={{
                  left: nodeCenter(currentStep.toNodeOrder).x,
                  top: currentStep.isLoopBack ? nodeCenter(currentStep.toNodeOrder).y - 90 : nodeCenter(currentStep.toNodeOrder).y,
                  opacity: [1, 1, 0],
                }}
                transition={{ duration: TICK_MS / 1000, ease: currentStep.isLoopBack ? "easeInOut" : "linear" }}
                style={{
                  position: "absolute",
                  width: 8,
                  height: 8,
                  marginLeft: -4,
                  marginTop: -4,
                  borderRadius: "50%",
                  background: sourceMeta(currentStep.entry.source).color,
                  boxShadow: `0 0 10px 2px ${sourceMeta(currentStep.entry.source).color}`,
                  zIndex: 5,
                }}
              />
            )}
          </AnimatePresence>

          {trace.nodes.map((node) => {
            const revealed = node.firstSeenAt < visibleCount;
            const { x, y } = nodeCenter(node.order);
            const lastEntry = [...trace.steps]
              .slice(0, visibleCount)
              .reverse()
              .find((s) => s.entry.node === node.id)?.entry;
            const isCurrent = currentStep?.entry.node === node.id && currentStep.index === visibleCount - 1;
            const meta = lastEntry ? sourceMeta(lastEntry.source) : null;
            const color = meta?.color ?? "var(--border-hairline-strong)";

            return (
              <div
                key={node.id}
                style={{
                  position: "absolute",
                  left: x - NODE_W / 2,
                  top: y - NODE_H / 2,
                  width: NODE_W,
                  height: NODE_H,
                }}
              >
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: revealed ? 1 : 0.25, y: 0, scale: isCurrent ? 1.06 : 1 }}
                  transition={{ duration: 0.3 }}
                  className="relative flex h-full w-full flex-col items-center justify-center rounded-lg border px-2 text-center"
                  style={{
                    borderColor: revealed ? color : "var(--border-hairline)",
                    background: "var(--bg-panel-raised)",
                    boxShadow: isCurrent ? `0 0 0 1px ${color}, 0 0 22px 2px color-mix(in srgb, ${color} 45%, transparent)` : "none",
                  }}
                >
                  {isCurrent && (
                    <span className="absolute inset-0 rounded-lg" style={{ boxShadow: `0 0 0 1px ${color}` }} />
                  )}
                  <span className="font-mono text-[11px] font-medium leading-tight" style={{ color: revealed ? "var(--text-primary)" : "var(--text-tertiary)" }}>
                    {nodeDisplayName(node.id)}
                  </span>
                  {revealed && lastEntry && (
                    <span className="mt-0.5 truncate max-w-full font-mono text-[9px]" style={{ color }}>
                      {lastEntry.decision}
                    </span>
                  )}
                </motion.div>

                <AnimatePresence>
                  {isCurrent && lastEntry && (
                    <motion.div
                      initial={{ opacity: 0, y: -4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      className="absolute left-1/2 z-10 -translate-x-1/2"
                      style={{ top: -34, whiteSpace: "nowrap" }}
                    >
                      <SourceBadge source={lastEntry.source} size="sm" />
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
