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

Use `--context`, `--max-output`, and `--request-timeout` when a model cannot
safely run the defaults. The report records the actual context. Runtime requests
also have bounded output and time limits so an incompatible model cannot
generate indefinitely.

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
| `nemotron-cascade-2:30b` | extended | 12/18 | 23.44 s | 26,436 MB used; 5,751 MB free |
| `Nemotron-Terminal-32B Q4_K_M` | 8K compatibility | 10/18 | 33.23 s | 29,974 MB used; 2,214 MB free |

Qwen followed every requested tool contract exactly. GPT-OSS twice skipped the
disabled send attempt, answering from the known permission state instead of
calling `send_role_message`. Across the two runs it also lost a point for an
incorrect missing-role discovery path and a point for reading without first
calling `verify_role_window`.

Recommendation: keep `qwen3.6:27b` as the default SelfConnect operator when
tool correctness and auditable behavior matter. Keep `gpt-oss:20b` as the
faster, lower-VRAM alternative for conversational or lower-risk work.

### NVIDIA compatibility findings

`nemotron-cascade-2:30b` ran at the standard 32K context and emitted native
Ollama tool calls, but followed the exact tool contract on only four of nine
cases. It frequently inferred that disabled operations would fail instead of
calling the requested tool to produce an audit record.

The community Q4_K_M conversion of NVIDIA's `Nemotron-Terminal-32B` could not
fit fully on the RTX 5090 at 32K context: Ollama reported a 55 GB working set
split 43% CPU / 57% GPU. At 8K it ran on the GPU, but followed only one of nine
tool contracts. It narrated proposed calls instead of emitting native calls and
exposed reasoning text. NVIDIA trains this model for the specialized Terminus 2
structured-command scaffold, so this result does not establish that the base
model is weak; it establishes that this GGUF/template is a poor match for the
current SelfConnect/Ollama function-call interface.

Sources:

- [NVIDIA Nemotron-Terminal-32B model card](https://huggingface.co/nvidia/Nemotron-Terminal-32B)
- [Q4_K_M GGUF conversion used for the compatibility run](https://huggingface.co/mradermacher/Nemotron-Terminal-32B-GGUF)
