import { sourceMeta } from "@/lib/accents";

export default function SourceBadge({
  source,
  size = "md",
}: {
  source: string;
  size?: "sm" | "md";
}) {
  const meta = sourceMeta(source);
  const isDot = size === "sm";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-mono uppercase tracking-wider ${
        isDot ? "px-2 py-0.5 text-[10px]" : "px-2.5 py-1 text-[11px]"
      }`}
      style={{
        borderColor: "color-mix(in srgb, " + meta.color + " 40%, transparent)",
        color: meta.color,
        background: "color-mix(in srgb, " + meta.color + " 10%, transparent)",
      }}
      title={meta.description}
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ background: meta.color }}
      />
      {meta.label}
    </span>
  );
}
