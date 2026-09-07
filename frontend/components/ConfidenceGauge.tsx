"use client";

import { motion } from "framer-motion";

const R = 50;
const CX = 60;
const CY = 62;
const ARC_LEN = Math.PI * R;

function confidenceColor(c: number): string {
  const hue = Math.max(0, Math.min(120, c * 120));
  return `hsl(${hue}, 72%, 55%)`;
}

export default function ConfidenceGauge({
  confidence,
  size = 132,
}: {
  confidence: number;
  size?: number;
}) {
  const clamped = Math.max(0, Math.min(1, confidence));
  const color = confidenceColor(clamped);
  const pct = Math.round(clamped * 100);

  return (
    <div style={{ width: size }} className="flex flex-col items-center">
      <svg viewBox="0 0 120 70" className="w-full">
        <path
          d={`M 10 ${CY} A ${R} ${R} 0 0 1 110 ${CY}`}
          fill="none"
          stroke="var(--border-hairline-strong)"
          strokeWidth={9}
          strokeLinecap="round"
        />
        <motion.path
          d={`M 10 ${CY} A ${R} ${R} 0 0 1 110 ${CY}`}
          fill="none"
          stroke={color}
          strokeWidth={9}
          strokeLinecap="round"
          strokeDasharray={ARC_LEN}
          initial={{ strokeDashoffset: ARC_LEN }}
          animate={{ strokeDashoffset: ARC_LEN * (1 - clamped) }}
          transition={{ duration: 0.9, ease: "easeOut" }}
          style={{ filter: `drop-shadow(0 0 6px color-mix(in srgb, ${color} 55%, transparent))` }}
        />
        <text
          x={CX}
          y={CY - 6}
          textAnchor="middle"
          className="mono-tabular"
          style={{ fill: "var(--text-primary)", fontSize: 22, fontWeight: 600 }}
        >
          {pct}%
        </text>
      </svg>
      <span className="text-[10px] uppercase tracking-widest" style={{ color: "var(--text-tertiary)" }}>
        confidence
      </span>
    </div>
  );
}
