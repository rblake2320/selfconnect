# UIA Echo Filter — TermControl Proof

**Branch:** `test/win32-hardening-v1`
**Probe:** `experiments/win32_probe/uia_echo_filter_probe.py`
**Tests:** `tests/test_uia_echo_filter.py`

---

## Purpose

Prove that SelfConnect can distinguish locally injected text (echo) from real
terminal output using UIA structured readback on Windows Terminal / ConPTY.
This closes the reliability gap in verified-delivery ACK logic: without echo
filtering, a sender that reads back its own injected nonce will misclassify
it as a peer response.

---

## What Is Proved

| Claim | Status |
|---|---|
| UIA TextPattern available on TermControl (focus-independent) | PROVEN — session 9, chained_channel.py |
| TextChanged event fires on ConPTY text update | PROVEN — session 9, chained_channel.py |
| Echo filter correctly labels injected nonce as local echo | PROVEN by this probe |
| Echo filter separates trailing terminal output from echo | PROVEN by this probe |
| Probe produces a structured record (hashes, hwnd, pid, method, latency) | PROVEN by this probe |
| Fallback from TextChanged to polling when events unavailable | IMPLEMENTED |

---

## Architecture

### Injection channel used by this proof

This recorded Windows Terminal / ConPTY proof used `PostMessage(WM_CHAR)` on
the tested CASCADIA surface. Current `send_string(mode="auto")` is
class-selected: `ConsoleWindowClass` uses `WriteConsoleInputW` instead. In both
cases, raw API acceptance is transport evidence only; the echo-filter/readback
stage is what establishes receiver observation for this proof.

### Read channel

Two methods, tried in order:

1. **TextChanged event** (`UIA_TextChangedEventId = 20015`)
   - COM event handler registered on the TermControl element.
   - COM message pump (`pythoncom.PumpWaitingMessages`) called in a polling loop.
   - Fires as soon as the ConPTY buffer is updated.
   - Method label: `TextChanged_event`.

2. **TextPattern polling** (fallback)
   - `tp.DocumentRange.GetText(-1)` polled every 250 ms.
   - Used when event registration fails or events never fire.
   - Method label: `TextPattern_poll`.

### UIA element discovery

Critical session-9 finding (preserved in memory, reproduced here):

- Filter by `IsTextPatternAvailable` (property 30040), **not** by ControlType.
- TermControl is `ControlType=Text(50020)`. Filtering by `ControlType.Document`
  misses it.
- Call `FindAll` + pick the element with the **longest** buffer, not
  `FindFirst` (the first match is the tab title header, not the scrollback).
- Reads are focus- and visibility-independent on ConPTY.

### Echo filter

`EchoFilter.classify_delta(delta, nonce)` returns
`(echo_part, output_part, classification)`.

Classification rules:
- **echo** — nonce found at or near the start of the stripped delta.
  The nonce plus any leading whitespace is the echo part. Text after the
  nonce is the output part (typically a prompt redraw).
- **output** — nonce not found at the start but also not at all. The whole
  delta is treated as external output. This should not happen in a controlled
  probe run; it produces `result=FAIL` with a diagnosis string.
- **empty** — delta is empty or whitespace only. No classification possible.

The filter strips `\r\n \t` from the left before searching. This handles
the common case where the ConPTY prepends a newline before echoing input.

### Record structure

Every run produces a `FilterRecord` with:

| Field | Description |
|---|---|
| `result` | `PASS` / `FAIL` / `NA` |
| `na_reason` | Human-readable diagnosis if not PASS |
| `hwnd` | Target window handle |
| `pid` | Target process ID |
| `uia_method` | `TextChanged_event` or `TextPattern_poll` |
| `nonce` | Injected token (`SC_ECHO_{8-hex-chars}`) |
| `sent_hash` | SHA-256 of the injected nonce bytes |
| `observed_hash` | SHA-256 of the full delta text |
| `echo_text` | Portion classified as local echo |
| `output_text` | Portion classified as external output |
| `raw_delta` | First 512 chars of the observed delta |
| `timestamp_send` | `time.time()` at injection |
| `timestamp_first` | `time.time()` when first delta observed |
| `latency_ms` | `(timestamp_first - timestamp_send) * 1000` |
| `uia_available` | Whether a TextPattern element was found |
| `event_supported` | Whether TextChanged handler registered successfully |

---

## Safety

- **Throwaway target only.** The probe spawns an isolated `conhost.exe /K
  echo SC_TARGET_READY` process. It terminates the process on completion.
- **No active agent terminals touched.** The probe never writes to the
  session-active Claude terminals (Team A, B, C, mesh-ctrl).
- **No private content logged.** The `raw_delta` field is capped at 512
  characters and contains only the delta observed in the throwaway conhost
  buffer. No agent transcripts, prompts, or session content are captured.

---

## Usage

```powershell
# Default: spawn throwaway conhost, run probe, terminate it
python experiments\win32_probe\uia_echo_filter_probe.py

# Provide an existing throwaway hwnd (found via list_windows)
python experiments\win32_probe\uia_echo_filter_probe.py --hwnd 0x1A2B3C

# Verbose: print full JSON record regardless of result
python experiments\win32_probe\uia_echo_filter_probe.py --verbose

# Longer timeout (for slow machines or slow COM pump)
python experiments\win32_probe\uia_echo_filter_probe.py --timeout 20
```

---

## Manual Live Validation Steps

The following procedure validates the full path end-to-end. Run this on a
live Windows desktop session; the `tests/test_uia_echo_filter.py` suite
covers only the platform-independent logic.

1. **Open a fresh Windows Terminal tab** titled "SC_PROBE_TARGET". Run
   `cmd /K` in it (do not use an active Claude/agent tab).

2. **Find the hwnd:**
   ```powershell
   python -c "
   import sys; sys.path.insert(0, '.'); from self_connect import list_windows
   for w in list_windows():
       if 'SC_PROBE_TARGET' in w.title:
           print(f'hwnd={w.hwnd:#x}  pid={w.pid}')
   "
   ```

3. **Run the probe:**
   ```powershell
   python experiments\win32_probe\uia_echo_filter_probe.py --hwnd <hwnd> --verbose
   ```

4. **Expected output (PASS):**
   ```
   [UIA_ECHO_FILTER] result=PASS | method=TextChanged_event | latency=NNNms | ...
   {
     "result": "PASS",
     "uia_method": "TextChanged_event",
     "echo_text": "SC_ECHO_XXXXXXXX",
     "output_text": "C:\\>",
     ...
   }
   ```

5. **If result is NA — consult the diagnosis table below.**

---

## NA Diagnosis Table

| `na_reason` keyword | Likely cause | Fix |
|---|---|---|
| `No TextPattern element found` | UIA provider not loaded; window not a ConPTY surface | Ensure Windows Terminal (not conhost legacy) is the target |
| `IUIAutomation CreateObject failed` | comtypes not installed or broken gen cache | `pip install comtypes` then `python -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll')"` |
| `No text change observed within N s` | TextChanged not firing; COM pump single-threaded issue | Try `--timeout 20`; verify the target is interactive (not paused) |
| `hwnd not found in window list` | Target window closed before probe ran | Re-run without `--no-spawn` |
| `Win32 platform required` | Running on Linux/macOS (CI matrix) | Expected NA; no action needed |
| `comtypes/pythoncom not installed` | Missing optional deps | `pip install -e .[full]` |

---

## Relationship to Existing Probes

| Probe | What it proved | This probe's relation |
|---|---|---|
| `chained_channel.py` | UIA TextChanged + Ed25519 sign + named pipe DACL | Reuses UIA patterns; strips pipe/signing to focus on echo classification |
| `uia_textchanged_fire.py` (enterprise branch) | TextChanged fires focus-independent on minimized ConPTY | Confirms the event model this probe depends on |
| `named_pipe_identity.py` (enterprise) | OS-verified pipe SID via DACL + impersonation | Separate proof; not needed for echo filter |

---

## Patent Framing

The echo filter is a necessary component of the
**verified-delivery / ACK claim family**:

> A sender that injects text T into peer terminal P via `PostMessage(WM_CHAR)`
> can, via UIA TextPattern readback, observe the delta in P's scrollback buffer
> that corresponds to the injection. By comparing the delta against the known
> injected nonce, the sender classifies local echo vs. external terminal output,
> enabling ACK detection and preventing false-positive mesh routing decisions.

This strengthens patent claim families:
- Background PostMessage(WM_CHAR) to ConPTY (session 4)
- UIA structured readback as receive channel (session 9)
- Async interrupt-pattern watchdog (session 7, extended)

Two complementary embodiments preserved (do not consolidate):
- Software Ed25519 identity — runs on any machine, no TPM requirement.
- TPM ECDSA P-256 identity — hardware-attested, procurement-grade.
Both rely on the echo filter for correct ACK classification.

---

## CI

The unit tests run in CI without Win32:

```powershell
python -m pytest tests/test_uia_echo_filter.py -q
```

Live desktop tests are excluded from CI and documented above under
"Manual Live Validation Steps".
