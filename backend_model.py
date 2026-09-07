"""Reuse the evaluated DistilBERT worker without importing torch into this API."""
from functools import lru_cache
import importlib.util
import os
from pathlib import Path
import threading

DEFAULT_RUNTIME = Path(__file__).resolve().parent.parent/'Multi_agent_ticketing_system'
_LOCK = threading.Lock()
_WORKER = None


@lru_cache(maxsize=1)
def _worker(runtime, model_path, metadata_mtime, weights_mtime):
    global _WORKER
    if _WORKER is not None:
        _WORKER.close()
    # Load only the stdlib IPC client, without changing sys.path/state imports.
    spec = importlib.util.spec_from_file_location('_backend_distilbert_ipc', Path(runtime)/'company/distilbert_worker.py')
    if spec is None or spec.loader is None:
        raise ImportError('Local model runtime unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _WORKER = module.DistilBertWorker(model_path)
    return _WORKER


def predict_backend(text):
    if os.getenv('BACKEND_MODEL_ENABLED', '1') != '1':
        return {'status':'disabled', 'accepted':False, 'advisory_only':True}
    runtime = Path(os.getenv('BACKEND_MODEL_RUNTIME') or DEFAULT_RUNTIME).resolve()
    model = Path(os.getenv('BACKEND_BERT_MODEL_PATH') or runtime/'models/helpdesk/distilbert').resolve()
    try:
        metadata, weights = (model/'triage.json').stat(), (model/'model.safetensors').stat()
        with _LOCK:
            return _worker(str(runtime), str(model), metadata.st_mtime_ns, weights.st_mtime_ns).predict(text)
    except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError):
        return {'status':'unavailable', 'accepted':False, 'advisory_only':True}
