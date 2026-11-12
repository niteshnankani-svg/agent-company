#!/usr/bin/env python3
"""Run the eval cases against a live Agent Company API and write a report. YOU run this; CI does not.

    python evals/run_evals.py --api-url https://your-api.example --api-key "$EVAL_API_KEY"
    python evals/run_evals.py --dry-run                 # list the cases and the coverage matrix, call nothing
    python evals/run_evals.py --only hr-headcount-answered,legal-section-357-answered

Cases marked ``break`` check the failed paths: the runner starts a local copy of the API (from this repo, using
your environment for the real routing and model keys) with just those service addresses pointed at nothing, so
"a service is down" is observed rather than assumed. Use --skip-failure-cases to leave them out.

Needs: the API reachable, a client or reviewer key (EVAL_API_KEY) unless auth is disabled there, and the same
environment as the API for the failure cases. Optional: GEMINI_API_KEY or XAI_API_KEY/GROK_API_KEY for the judge.
Exit status is 0 only if every scored case passed and none failed to run.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from cases import CASES  # noqa: E402
from harness import Api, FailureServers, coverage_gaps, render_report, run_case  # noqa: E402
from judge import judge_case, pick_judge  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agent Company eval cases.")
    parser.add_argument("--api-url", default=os.environ.get("AGENT_COMPANY_API_URL", "http://localhost:8080"))
    parser.add_argument("--api-key", default=os.environ.get("EVAL_API_KEY"))
    parser.add_argument("--out", help="report path (default: evals/results/<timestamp>.md)")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--dry-run", action="store_true", help="show the cases and coverage; call nothing")
    parser.add_argument("--skip-failure-cases", action="store_true", help="do not start local servers for the 'service down' cases")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    cases = CASES
    if args.only:
        wanted = {i.strip() for i in args.only.split(",")}
        unknown = wanted - {c["id"] for c in CASES}
        if unknown:
            print(f"unknown case id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        cases = [c for c in CASES if c["id"] in wanted]
    if args.skip_failure_cases:
        cases = [c for c in cases if not c.get("break")]

    gaps = coverage_gaps(CASES)
    if gaps:
        print("COVERAGE GAPS (no case for these department/status pairs): " + ", ".join(f"{d}/{s}" for d, s in gaps), file=sys.stderr)

    if args.dry_run:
        print(f"{len(cases)} case(s):")
        for c in cases:
            extra = f"  [breaks {', '.join(c['break'])}]" if c.get("break") else ""
            print(f"  {c['id']:<40} {str(c.get('department')):<18} {str(c.get('status')):<16}{extra}")
        from harness import matrix_rows
        print("\n" + "\n".join(matrix_rows([])))
        return 1 if gaps else 0

    judge = None if args.no_judge else pick_judge()
    live = Api(args.api_url, args.api_key, args.timeout)
    results = []
    servers = FailureServers() if any(c.get("break") for c in cases) else None
    try:
        with httpx.Client() as judge_client:
            for case in cases:
                print(f"[{case['id']}] running...", flush=True)
                if case.get("break"):
                    url, key = servers.url_and_key(case["break"])
                    api = Api(url, key, args.timeout)
                else:
                    api = live
                result = run_case(api, case)
                if api is not live:
                    api.close()
                if judge and not result.error and result.steps and result.steps[-1].response:
                    result.judge = judge_case(judge, judge_client, case, result.steps[-1].response)
                results.append(result)
                print(f"[{case['id']}] {result.outcome}", flush=True)
    finally:
        live.close()
        if servers:
            servers.close()

    out = Path(args.out) if args.out else Path(__file__).resolve().parent / "results" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(results, {
        "run_at": datetime.now(timezone.utc).isoformat(), "api": args.api_url, "judge": judge,
    }))
    print(f"\nWrote {out}")
    bad = [r for r in results if r.outcome in ("FAIL", "ERROR")]
    print(f"{len(results) - len(bad)}/{len(results)} cases ok" + (f"; not ok: {', '.join(r.case['id'] for r in bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
