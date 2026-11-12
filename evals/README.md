# Live evals (you run these)

These are run by you, against a real deployment, with real keys. CI does **not** run them. Their results are the
first honest measurement of how the system behaves with your real services: nothing in this repository has been
measured that way yet.

## The main eval

```bash
python evals/run_evals.py --api-url https://your-api.example --api-key "$EVAL_API_KEY"
```

- `--api-key`: a `client` or `reviewer` key (or set `EVAL_API_KEY`). Not needed if the API has authentication disabled.
- `--dry-run`: list the cases and the coverage matrix and call nothing.
- `--only id1,id2`: run some cases.
- `--skip-failure-cases`: leave out the "service down" cases (see below).
- `--no-judge`: skip the optional judge even if a judge key is set.

It writes a report to `evals/results/<timestamp>.md` and exits 0 only if every scored case passed and none failed to run.
A case that could not run (API unreachable, wrong key) is reported as an **ERROR**, never as a pass.

### What it checks

20 cases cover every department's `answered`, `needs_human`, `needs_more_info` and `failed` paths. Cells that cannot
happen by design (for example Finance never answers by policy) are listed with the reason in `evals/cases.py`, and a
unit test fails if a cell has neither a case nor a reason. Each case is checked **deterministically**: status,
department, reason, how it was verified, required words in the answer, that an answered result carries evidence, that the
Backend answer is labelled `unchecked` and never `verified`, and that no traceback, path or password appears anywhere in a
response. The case notes say what each one is testing.

### The "service down" cases

To observe a failed path rather than assume it, the runner starts a **local copy of the API** from this repository with just
the named service addresses (for example `LEGAL_QUERY_URL`) pointed at a port nothing listens on. Everything else comes from
your environment, so routing and the models are real. It uses its own throwaway SQLite file and a generated key, never your
database. Run the eval from an environment that has the API's variables (see `.env.example`).

### The optional judge

If `GEMINI_API_KEY` (model `$GEMINI_MODEL`) or `XAI_API_KEY`/`GROK_API_KEY` (model `$GROK_MODEL`) is set, a model comments on
whether each answer is correct and supported by its evidence. It **never changes a pass or fail**. A judge error is recorded
as an error, not as a verdict.

### Things worth reading in the first report

- `legal-section-357-answered` ending in `needs_human` with reason `verification_failed` is the effect of the Legal service
  returning summaries instead of passage text (DECISIONS D-039), not necessarily a bug.
- `cache-near-miss-sections` only means something with the answer cache on (`EMBEDDING_PROVIDER` set). Run it with the cache on to
  decide whether `CACHE_SIMILARITY_THRESHOLD` (default 0.95, uncalibrated) is safe.
- `routing-two-departments` is informational (there is no single right answer).
- The HR ground truth (Sales = 1035) comes from the HR dataset you supplied. Change it if the data changed.

## RAGAS (optional)

```bash
pip install -r evals/requirements.txt
GEMINI_API_KEY=... python evals/run_ragas.py --api-url https://your-api.example --api-key "$EVAL_API_KEY"
```

Faithfulness, context precision and factual correctness for Legal and HR, using the evidence the API returns as the retrieved
contexts. **This script has never been run** (no key was available): treat its first run as the test of the script itself.

## Tests of the eval tooling

`tests/test_evals.py` tests the tooling without any live service: the coverage rule, every kind of check, the follow-up and
pair flows, report rendering (and that the API key never appears in it), and a real local API started with a service broken.
