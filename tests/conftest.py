import os

# STOPGAP until Phase 2: orchestrator.py and hr_complaints.py build a TypeSafeClient
# when imported, and that needs *some* API key string to construct. This is a dummy;
# no test makes a real call (every external service is mocked). Phase 2 makes the
# clients lazy and this line goes away.
os.environ.setdefault("TYPESAFE_API_KEY", "dummy-value-for-tests-no-real-calls")
