# Local Agent Model Benchmark

This benchmark compares Ollama models through the same SelfConnect runtime,
core knowledge, 32K context window, permission gates, prompts, and scoring.

Run one model at a time:

```powershell
ollama stop <previous-model>
python benchmarks/local_agent_model_benchmark.py `
  --model <ollama-model> `
  --harness-mode raw `
  --suite known `
  --output proofs/local-agent-benchmark/<report>.json
```

Use `--context`, `--max-output`, and `--request-timeout` when a model cannot
safely run the defaults. The report records the actual context. Runtime requests
also have bounded output and time limits so an incompatible model cannot
generate indefinitely.

The benchmark has three deliberately separate modes:

- `raw`: the shared core prompt and complete tool catalog, with model-specific
  harness behavior disabled. This preserves historical model comparisons.
- `profile`: adds the resolved model profile. For Qwen 3.6 this means
  deterministic sampling, clearer audit-call descriptions, and concise
  no-narration instructions.
- `contract`: adds controller-supplied required/allowed tool contracts. The
  runtime narrows the visible catalog, retries one missing-call turn, rejects
  disallowed calls, and blocks completion if evidence remains missing.

Use `--suite holdout` for the separate five-case generalization check. Contracts
are supplied by a trusted workflow/controller; the runtime does not guess
required tools from arbitrary user prose.

Each of the nine cases awards one point for the exact permitted tool sequence
and one point for the required answer content. Extra, skipped, or disallowed
tools lose the tool point. Raw reports are written beneath `proofs/`, which is
intentionally ignored because it can contain machine-specific window and GPU
state.

## 2026-07-25 comparison

| Model | Run | Score | Time | GPU memory after |
|---|---:|---:|---:|---:|
| `qwen3.6:27b` | extended | 18/18 | 50.31 s | 31,851 MB used; 337 MB free |
| `qwen3.6:27b` | fresh repeat 2 | 18/18 | 51.85 s | 21,421 MB used; 10,767 MB free |
| `qwen3.6:27b` | fresh repeat 3 | 16/18 | 41.16 s | 21,442 MB used; 10,746 MB free |
| `gpt-oss:20b` | extended | 16/18 | 14.77 s | 28,878 MB used; 3,310 MB free |
| `gpt-oss:20b` | repeat | 16/18 | 20.09 s | 28,833 MB used; 3,355 MB free |
| `nemotron-cascade-2:30b` | extended | 12/18 | 23.44 s | 26,436 MB used; 5,751 MB free |
| `Nemotron-Terminal-32B Q4_K_M` | 8K compatibility | 10/18 | 33.23 s | 29,974 MB used; 2,214 MB free |
| `glm-4.7-flash` | extended | 12/18 | 11.34 s | 28,273 MB used; 3,915 MB free |
| `qwen3-coder:30b` | extended | 17/18 | 94.60 s | 31,764 MB used; 424 MB free |
| `qwen3-coder:30b` | repeat | 11/18 | 83.22 s | 31,844 MB used; 344 MB free |

Qwen followed every requested tool contract exactly in its first two extended
runs. A third fresh run scored 16/18 because it inferred the disabled write and
command outcomes instead of calling `file_write` and `command` to produce audit
records. Across the three runs it scored 52/54 overall and followed 25/27 exact
tool contracts. GPT-OSS twice skipped the disabled send attempt, answering from
the known permission state instead of calling `send_role_message`. Across the
two runs it also lost a point for an incorrect missing-role discovery path and
a point for reading without first calling `verify_role_window`.

Recommendation: keep `qwen3.6:27b` as the default SelfConnect operator when
tool correctness and auditable behavior matter, but do not treat tool
compliance as deterministic. The runtime should enforce mandatory audit calls
for governed workflows instead of relying on model instruction-following
alone. Keep `gpt-oss:20b` as the faster, lower-VRAM alternative for
conversational or lower-risk work.

### Harness-profile results

The NVIDIA-style harness pass was evaluated separately from the historical raw
model scores:

| Mode / suite | Runs | Result |
|---|---:|---:|
| Qwen profile, known | 1 | 18/18 |
| Qwen contract, known | 2 | 18/18, 18/18 |
| Qwen contract, sealed holdout | 2 | 10/10, 10/10 |
| Raw control, sealed holdout | 1 | 10/10 |

The four contract runs produced 56/56 points and exact ordered tool sequences.
This demonstrates repeatable harness enforcement, not a claim that the model's
weights improved. The raw holdout also passed, showing that the new holdout was
not constructed only around Qwen's known write/command failure mode.

The implementation follows NVIDIA's harness-profile loop: benchmark, inspect
failure traces, adjust model-specific system/tool guidance and middleware, then
rerun the full suite. SelfConnect adds a stricter security boundary: the model
still selects arguments, but a trusted controller owns tool exposure and the
completion contract. Ollama tool-result history now preserves `tool_name` and
`tool_call_id` when present.

Sources:

- [NVIDIA: Create a Deep Agents harness profile for Nemotron 3 Ultra](https://developer.nvidia.com/blog/create-a-langchain-deep-agents-harness-profile-for-nvidia-nemotron-3-ultra-to-improve-performance/)
- [NVIDIA: Nemotron and LangChain open agent stack](https://blogs.nvidia.com/blog/nemotron-langchain-agents-open-stack/)
- [LangChain Deep Agents harness profiles](https://docs.langchain.com/oss/python/deepagents/profiles)
- [Qwen-Agent](https://github.com/QwenLM/Qwen-Agent)
- [Ollama multi-turn tool calling](https://docs.ollama.com/capabilities/tool-calling)
- [NVIDIA NeMo Agent Toolkit](https://github.com/NVIDIA/NeMo-Agent-Toolkit)

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

### Additional challenger findings

`glm-4.7-flash` was the fastest model tested, but scored 12/18. It skipped
required mutation-gate calls, replaced window verification with an unrelated
mesh-history call, and misreported activity state.

`qwen3-coder:30b` produced one promising 17/18 run, followed immediately by an
11/18 repeat. The repeat skipped several required tool calls and called a tool
on the no-tool identity case. Its speed and 32K VRAM footprint were also worse
than `qwen3.6:27b`. The two-run result makes it unsuitable as a governed
SelfConnect operator despite the favorable first sample.

The newly evaluated poor-fit downloads—Nemotron-Terminal-32B, GLM-4.7-Flash,
and Qwen3-Coder-30B—were removed after their reports were recorded. Their exact
Ollama manifests and unreferenced weight blobs were verified absent. Existing
models that predated this evaluation were not deleted.

Sources:

- [GLM-4.7-Flash model card](https://huggingface.co/zai-org/GLM-4.7-Flash)
- [Qwen3-Coder-30B-A3B-Instruct model card](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct)
