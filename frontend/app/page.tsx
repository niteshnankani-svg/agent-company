"use client";

import { useEffect, useState } from "react";
import { checkHealth } from "@/lib/api";
import AskConsole from "@/components/AskConsole";

export default function Home() {
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      const ok = await checkHealth();
      if (!cancelled) setHealthy(ok);
    }
    poll();
    const id = setInterval(poll, 15000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b px-6 py-4" style={{ borderColor: "var(--border-hairline)", background: "color-mix(in srgb, var(--bg-deep) 88%, transparent)" }}>
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded border font-mono text-xs" style={{ borderColor: "var(--source-local)", color: "var(--source-local)" }}>
            AC
          </div>
          <div>
            <div className="font-mono text-sm font-semibold tracking-wide" style={{ color: "var(--text-primary)" }}>
              AGENT COMPANY
            </div>
            <div className="font-mono text-[10px] uppercase tracking-widest" style={{ color: "var(--text-tertiary)" }}>
              ask once · the system routes it
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 font-mono text-[11px]" style={{ color: "var(--text-secondary)" }} data-testid="health-indicator">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: healthy === null ? "var(--text-tertiary)" : healthy ? "var(--source-cache)" : "var(--source-human)" }}
          />
          API {healthy === null ? "checking" : healthy ? "online" : "unreachable"}
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 p-6">
        <AskConsole />
      </main>
    </div>
  );
}
