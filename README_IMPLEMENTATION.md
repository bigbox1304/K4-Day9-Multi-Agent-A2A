# Implementation guide

## Run

Put `EC_001.json` through `EC_050.json` in `input/`, then run:

```powershell
Copy-Item .env.example .env
# Edit .env with the endpoint/model actually running on your machine.
python run_pipeline.py
```

The runner resumes by default: valid existing `output/EC_xxx.json` files are skipped after a crash. Use `python run_pipeline.py --fresh` only when you intentionally want to regenerate all cases.

The program loads the Olist CSV files once, runs the role-based agents for each case, validates the assembled result, writes matching files under `output/`, and writes the latest run trace to `trace.jsonl`.

The runner requires exactly 50 input files and an available LLM endpoint. It intentionally fails instead of silently switching to a rule-only implementation.

## Agent contract

The specialist agents are intentionally isolated by domain. They each call the configured LLM, then hand off structured JSON and evidence to the Policy Agent; they do not mutate one another's results. Python is used for CSV retrieval, context construction, output assembly and structural safety validation. The policy decision and domain analyses come from the LLM agents. The selected model identity is recorded in `metadata.json`.

Set `LLM_BASE_URL` to an OpenAI-compatible endpoint serving the declared sub-10B model. `LLM_API_KEY` is read from the environment and never written to trace or output. By default, all agents use deterministic CSV tools, so the 50-case run does not wait on model inference. Set `LLM_ORCHESTRATION_MODE=hybrid` to use one LLM policy handoff per case, or `LLM_ORCHESTRATION_MODE=full` to make all specialist and verifier calls use the LLM. The default example uses OpenRouter's `qwen/qwen3-8b:free` route.

`LLM_MAX_TOKENS` controls the per-request output budget. A value around `1536` is sufficient for the JSON agent contracts and reduces API credit usage.

`SPECIALIST_CONCURRENCY=2` limits simultaneous specialist calls. The client retries transient OpenRouter 429 responses with exponential backoff.

## Submission checklist

1. Add all 50 input files.
2. Run `python run_pipeline.py`.
3. Confirm that `output/` contains exactly 50 JSON files.
4. Inspect `trace.jsonl` and `metadata.json`.
5. Zip only the contents of `output/` for submission.
