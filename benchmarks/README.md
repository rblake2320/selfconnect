# Local Agent Model Benchmark

This benchmark compares Ollama models through the same SelfConnect runtime,
core knowledge, 32K context window, permission gates, prompts, and scoring.

Run one model at a time:

```powershell
ollama stop <previous-model>
python benchmarks/local_agent_model_benchmark.py `
  --model <ollama-model> `
  --output proofs/local-agent-benchmark/<report>.json
```

Each of the nine cases awards one point for the exact permitted tool sequence
and one point for the required answer content. Extra, skipped, or disallowed
tools lose the tool point. Raw reports are written beneath `proofs/`, which is
intentionally ignored because it can contain machine-specific window and GPU
state.

## 2026-07-25 comparison

| Model | Run | Score | Time | GPU memory after |
|---|---:|---:|---:|---:|
| `qwen3.6:27b` | extended | 18/18 | 50.31 s | 31,851 MB used; 337 MB free |
| `gpt-oss:20b` | extended | 16/18 | 14.77 s | 28,878 MB used; 3,310 MB free |
| `gpt-oss:20b` | repeat | 16/18 | 20.09 s | 28,833 MB used; 3,355 MB free |

Qwen followed every requested tool contract exactly. GPT-OSS twice skipped the
disabled send attempt, answering from the known permission state instead of
calling `send_role_message`. Across the two runs it also lost a point for an
incorrect missing-role discovery path and a point for reading without first
calling `verify_role_window`.

Recommendation: keep `qwen3.6:27b` as the default SelfConnect operator when
tool correctness and auditable behavior matter. Keep `gpt-oss:20b` as the
faster, lower-VRAM alternative for conversational or lower-risk work.
