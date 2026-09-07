from difflib import SequenceMatcher


class DepartmentCache:
    def __init__(self, department: str, similarity_threshold: float = 0.92):
        self.department = department
        self.threshold = similarity_threshold
        self._store = {}

    def get(self, query: str) -> dict | None:
        best_similarity = 0.0
        best_output = None
        for cached_input, output in self._store.items():
            similarity = SequenceMatcher(
                None, query.lower(), cached_input.lower()
            ).ratio()
            if similarity > best_similarity:
                best_similarity = similarity
                best_output = output
        if best_similarity >= self.threshold:
            return best_output
        return None

    # Only the verifier should ever call this — never a worker agent writing
    # its own unverified output directly.
    def set(self, query: str, output: dict) -> None:
        self._store[query] = output
