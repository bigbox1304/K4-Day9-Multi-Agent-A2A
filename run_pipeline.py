from pathlib import Path
import json
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.llm_agents import LLMBackend, LLMCoordinator
from src.pipeline import DataStore


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    files = sorted((root / "input").glob("EC_*.json"))
    if len(files) != 50:
        raise SystemExit(f"Expected exactly 50 input cases, found {len(files)}")
    backend = LLMBackend()
    coordinator = LLMCoordinator(DataStore(root / "data"), backend)
    trace = []
    resume = "--fresh" not in sys.argv
    skipped = 0
    concurrency = max(1, int(os.getenv("CASE_CONCURRENCY", "2")))
    pending = []
    for path in files:
        output_path = root / "output" / path.name
        if resume and output_path.exists():
            try:
                existing = json.loads(output_path.read_text(encoding="utf-8"))
                errors = coordinator.verifier.validate(existing, coordinator.store)
                if not errors and existing.get("case_id") == path.stem:
                    trace.append({"case_id": path.stem, "agent": "coordinator", "status": "skipped_existing_output"})
                    skipped += 1
                    continue
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        pending.append(path)

    def process_one(path):
        case = json.loads(path.read_text(encoding="utf-8"))
        output, events = coordinator.process(case)
        output_path = root / "output" / path.name
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path.stem, events

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(process_one, path) for path in pending]
        completed = []
        for future in as_completed(futures):
            completed.append(future.result())
        for case_id, events in sorted(completed):
            trace.extend(events)
    trace_text = "\n".join(json.dumps(event, ensure_ascii=False) for event in trace) + "\n"
    # README requires the latest trace at repo root; logging/ is a convenient audit copy.
    (root / "trace.jsonl").write_text(trace_text, encoding="utf-8")
    (root / "logging" / "trace.jsonl").write_text(trace_text, encoding="utf-8")
    print(f"Processed {len(files) - skipped} cases; skipped {skipped} existing valid outputs; concurrency={concurrency}")
