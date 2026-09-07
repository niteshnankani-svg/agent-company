"use client";

import { motion } from "framer-motion";
import type { DepartmentHistoryEntry } from "@/lib/types";
import { nodeDisplayName } from "@/lib/trace";
import SourceBadge from "./SourceBadge";

export default function SequentialPipeline({ history }: { history: DepartmentHistoryEntry[] }) {
  return (
    <div data-testid="department-history" className="glass-panel rounded-lg p-4">
      <div className="mb-3 font-mono text-[11px] uppercase tracking-widest" style={{ color: "var(--text-secondary)" }}>
        department pipeline
      </div>
      <div className="flex flex-wrap items-stretch gap-2">
        {history.map((entry, i) => {
          const isFinal = i === history.length - 1;
          return (
            <div key={entry.department} className="flex items-stretch gap-2">
              <motion.div
                data-testid={`department-card-${entry.department}`}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.15 }}
                className="flex w-56 flex-col gap-2 rounded-md border p-3"
                style={{
                  borderColor: isFinal ? "var(--source-local)" : "var(--border-hairline-strong)",
                  background: "var(--bg-panel-raised)",
                }}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] font-medium" style={{ color: "var(--text-primary)" }}>
                    {nodeDisplayName(entry.department)}
                  </span>
                  {isFinal && (
                    <span className="rounded-full px-1.5 py-0.5 font-mono text-[8px] uppercase tracking-wider" style={{ background: "var(--source-local)", color: "#06080c" }}>
                      final
                    </span>
                  )}
                </div>
                <div className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                  {entry.output.decision}
                </div>
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] mono-tabular" style={{ color: "var(--text-secondary)" }}>
                    {Math.round(entry.output.confidence * 100)}%
                  </span>
                  <SourceBadge source={entry.output.source} size="sm" />
                </div>
              </motion.div>
              {i < history.length - 1 && (
                <div className="flex items-center font-mono text-lg" style={{ color: "var(--text-tertiary)" }}>
                  →
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
