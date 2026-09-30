# Agent Company

## Backend agent: trained DistilBERT

`backend_ticketing.backend_head_node` now invokes the evaluated local DistilBERT model through a persistent isolated worker. It predicts Access, Hardware, Network, Security or Software and a support team. It uses the actual model score rather than the previous fixed 0.95 confidence proxy from the remote ticket router.

The existing graph already sends Backend requests to this function. Predictions are retained in `GraphState.backend_triage`, returned by `POST /request`, and identified by model version in the audit trail. Classification is advisory: this adapter has no approved resolution source, so it returns an explicit human handoff with empty resolution evidence. Security incidents, access requests, low confidence and missing model files have distinct review reasons. The verifier can validate the handoff without calling an LLM or retrying through Finance.

The trained artifacts remain in the sibling updated project; they are shared rather than copied:

```text
../Multi_agent_ticketing_system/company/distilbert_worker.py
../Multi_agent_ticketing_system/models/helpdesk/distilbert/
```

Local defaults use those paths. `BACKEND_MODEL_RUNTIME` can select another installation of that runtime, `BACKEND_BERT_MODEL_PATH` can select its trained artifact directory, and `BACKEND_MODEL_ENABLED=0` disables inference and requires review. The Python environment running this API must contain the transformer dependencies. For a container deployment, mount the runtime's `company/` directory and model directory under `BACKEND_MODEL_RUNTIME`; the standalone Agent Company Docker context does not include its sibling project automatically.

Run the offline integration tests:

```bash
venv_hr/bin/python -m unittest test_backend_model -v
```

These tests exercise real model inference, the Backend→Verifier LangGraph path, the API response, redaction, security overrides and missing-model behavior. The API test substitutes the graph entry point with that same backend graph so unrelated HR/export services are not bootstrapped. No remote ticket is submitted. Restart an already running Agent Company API to load the changed adapter.

Model comparison: [DistilBERT evaluation](../Multi_agent_ticketing_system/docs/DISTILBERT_COMPARISON.md). The evaluated model scored 84.8% on the 99-description test; production use still requires specialist review.

## Setup

Install the complete system (Finance, Export, Backend, HR/Complaints, and API):

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Note: HR/Complaints also requires:

- [hr-agent](https://github.com/.../hr-agent) checked out at `/Users/apple/Projects/hr-agent` (Text-to-SQL + RAG over employee data) with `ollama serve` running and the `llama3.2` / `embeddinggemma` models pulled
- `TYPESAFE_API_KEY` set as an environment variable (gates complaints, which always escalate to a human, from general HR questions, which hr-agent answers)
