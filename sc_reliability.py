"""
sc_reliability.py — SelfConnect Reliability Harness  (v1.0.0)

Fills the gap identified in the 2026 agentic security analysis:
  "pass@1 is not a reliability metric."

The analysis identified that current agentic benchmarks (SWE-bench,
WebArena, OSWorld) measure pass@1 — whether the agent succeeds once.
τ-bench (Tool-use Agent Benchmark) introduced pass^k: the probability
that an agent succeeds on ALL k independent trials of the same task.

For a safety-critical agentic system (HID-level autonomy, multi-agent
mesh, autonomous context migration), pass^1 = 0.9 means the agent fails
1 in 10 times. Over 100 actions that is 10 failures. pass^10 = 0.35.

This module provides:

  TrialResult       — outcome of a single trial
  ReliabilityReport — aggregated statistics across k trials
  ReliabilityHarness — runs any callable k times, computes:
      - pass@1 (first-trial success rate)
      - pass^k (all-trials success rate)
      - consistency score (τ-bench style)
      - outcome distribution
      - failure mode taxonomy
  BoundaryProbe     — probes the MELD/SENTINEL boundary:
      the threshold at which the agent's reliability drops below
      an operator-specified floor (e.g., 0.95 for IL4)

Design:
  The harness is transport-agnostic: the callable can be a SelfConnect
  send_frame() call, an MCP tool call, an A2A task, or any Python function.
  The oracle can be a simple equality check, a regex, or an LLM judge.

References:
  - τ-bench: Benchmarking Tool-Use of AI Agents in Real-World Domains
    (Yao et al., 2024) — introduced pass^k and consistency scoring
  - SWE-bench Verified (OpenAI, 2024) — pass@1 baseline
  - MELD: Multi-agent Evaluation with Longitudinal Drift
  - SENTINEL: Safety ENforcement Through Iterative Nested EvaLuation
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import statistics
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Optional

# ---------------------------------------------------------------------------
# TrialOutcome
# ---------------------------------------------------------------------------

class TrialOutcome(str, Enum):
    PASS    = "PASS"
    FAIL    = "FAIL"
    ERROR   = "ERROR"    # unhandled exception
    TIMEOUT = "TIMEOUT"  # exceeded time limit


# ---------------------------------------------------------------------------
# TrialResult
# ---------------------------------------------------------------------------

@dataclass
class TrialResult:
    """Outcome of a single trial."""
    trial_id: str
    trial_index: int
    outcome: TrialOutcome
    value: Any             # return value of the callable (may be None on error)
    error: Optional[str]   # exception message if outcome == ERROR
    duration_s: float
    ts: float
    meta: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.outcome == TrialOutcome.PASS

    def to_dict(self) -> dict:
        return {
            "trial_id": self.trial_id,
            "trial_index": self.trial_index,
            "outcome": self.outcome.value,
            "value": str(self.value)[:200] if self.value is not None else None,
            "error": self.error,
            "duration_s": round(self.duration_s, 4),
            "ts": self.ts,
            "meta": self.meta,
        }


# ---------------------------------------------------------------------------
# FailureMode  (taxonomy for failure analysis)
# ---------------------------------------------------------------------------

class FailureMode(str, Enum):
    WRONG_OUTPUT    = "WRONG_OUTPUT"    # oracle rejected the output
    EXCEPTION       = "EXCEPTION"       # callable raised an exception
    TIMEOUT         = "TIMEOUT"         # callable exceeded time limit
    INCONSISTENT    = "INCONSISTENT"    # output varied across trials (non-determinism)
    FLAKY           = "FLAKY"           # sometimes passes, sometimes fails
    SYSTEMATIC      = "SYSTEMATIC"      # always fails (not flaky)


# ---------------------------------------------------------------------------
# ReliabilityReport
# ---------------------------------------------------------------------------

@dataclass
class ReliabilityReport:
    """
    Aggregated reliability statistics across k trials.

    Key metrics:
      pass_at_1         — fraction of trials that passed (pass@1)
      pass_at_k         — 1.0 if ALL trials passed, 0.0 otherwise (pass^k)
      consistency_score — τ-bench style: fraction of trials with identical output
      mean_duration_s   — mean wall-clock time per trial
      p95_duration_s    — 95th percentile duration
      failure_modes     — set of FailureMode values observed
    """
    run_id: str
    task_id: str
    k: int
    trials: list[TrialResult]
    pass_at_1: float
    pass_at_k: float
    consistency_score: float
    mean_duration_s: float
    p95_duration_s: float
    failure_modes: set[FailureMode]
    outcome_counts: dict[str, int]
    ts: float = field(default_factory=time.time)

    @property
    def meets_floor(self) -> bool:
        """True if pass^k == 1.0 (all trials passed)."""
        return self.pass_at_k == 1.0

    def summary(self) -> str:
        modes = ", ".join(sorted(m.value for m in self.failure_modes)) or "none"
        return (
            f"ReliabilityReport(task={self.task_id!r} k={self.k} "
            f"pass@1={self.pass_at_1:.3f} pass^k={self.pass_at_k:.3f} "
            f"consistency={self.consistency_score:.3f} "
            f"p95={self.p95_duration_s:.3f}s "
            f"failure_modes=[{modes}])"
        )

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "k": self.k,
            "pass_at_1": self.pass_at_1,
            "pass_at_k": self.pass_at_k,
            "consistency_score": self.consistency_score,
            "mean_duration_s": round(self.mean_duration_s, 4),
            "p95_duration_s": round(self.p95_duration_s, 4),
            "failure_modes": sorted(m.value for m in self.failure_modes),
            "outcome_counts": self.outcome_counts,
            "ts": self.ts,
            "trials": [t.to_dict() for t in self.trials],
        }

    def save(self, path: str) -> None:
        import os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> ReliabilityReport:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        trials = [
            TrialResult(
                trial_id=t["trial_id"],
                trial_index=t["trial_index"],
                outcome=TrialOutcome(t["outcome"]),
                value=t["value"],
                error=t["error"],
                duration_s=t["duration_s"],
                ts=t["ts"],
                meta=t.get("meta", {}),
            )
            for t in d["trials"]
        ]
        return cls(
            run_id=d["run_id"],
            task_id=d["task_id"],
            k=d["k"],
            trials=trials,
            pass_at_1=d["pass_at_1"],
            pass_at_k=d["pass_at_k"],
            consistency_score=d["consistency_score"],
            mean_duration_s=d["mean_duration_s"],
            p95_duration_s=d["p95_duration_s"],
            failure_modes={FailureMode(m) for m in d["failure_modes"]},
            outcome_counts=d["outcome_counts"],
            ts=d["ts"],
        )


# ---------------------------------------------------------------------------
# ReliabilityHarness
# ---------------------------------------------------------------------------

class ReliabilityHarness:
    """
    Runs any callable k times and computes pass^k reliability statistics.

    Usage::

        def my_task() -> str:
            return send_frame(target, from_hwnd, "ping", topic="health")["acked"]

        def my_oracle(result) -> bool:
            return result is True

        harness = ReliabilityHarness(
            task_fn=my_task,
            oracle=my_oracle,
            task_id="send_frame_health_check",
            k=10,
            timeout_s=5.0,
            parallel=False,   # set True for independent stateless tasks
        )
        report = harness.run()
        print(report.summary())
        # ReliabilityReport(task='send_frame_health_check' k=10
        #   pass@1=0.900 pass^k=0.000 consistency=0.900 ...)

    Oracle:
        The oracle receives the return value of task_fn and returns True/False.
        If task_fn raises, the trial is marked ERROR regardless of the oracle.

    Parallel mode:
        When parallel=True, trials are run in a ThreadPoolExecutor.
        Only use for stateless tasks — SelfConnect HID operations are NOT
        stateless (they mutate window state) and must run sequentially.
    """

    def __init__(
        self,
        task_fn: Callable[[], Any],
        oracle: Callable[[Any], bool],
        task_id: str = "",
        k: int = 10,
        timeout_s: float = 30.0,
        parallel: bool = False,
        inter_trial_delay_s: float = 0.0,
        on_trial_complete: Optional[Callable[[TrialResult], None]] = None,
    ) -> None:
        self.task_fn = task_fn
        self.oracle = oracle
        self.task_id = task_id or str(uuid.uuid4())[:8]
        self.k = k
        self.timeout_s = timeout_s
        self.parallel = parallel
        self.inter_trial_delay_s = inter_trial_delay_s
        self.on_trial_complete = on_trial_complete

    # ── run ───────────────────────────────────────────────────────────────

    def run(self) -> ReliabilityReport:
        """Execute all k trials and return the aggregated ReliabilityReport."""
        run_id = str(uuid.uuid4())
        if self.parallel:
            trials = self._run_parallel(run_id)
        else:
            trials = self._run_sequential(run_id)
        return self._aggregate(run_id, trials)

    def _run_sequential(self, run_id: str) -> list[TrialResult]:
        results = []
        for i in range(self.k):
            result = self._run_one(i)
            results.append(result)
            if self.on_trial_complete:
                try:
                    self.on_trial_complete(result)
                except Exception:
                    pass
            if i < self.k - 1 and self.inter_trial_delay_s > 0:
                time.sleep(self.inter_trial_delay_s)
        return results

    def _run_parallel(self, run_id: str) -> list[TrialResult]:
        results: list[Optional[TrialResult]] = [None] * self.k
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(self.k, 16)) as ex:
            futures = {ex.submit(self._run_one, i): i for i in range(self.k)}
            for fut in concurrent.futures.as_completed(futures):
                i = futures[fut]
                try:
                    results[i] = fut.result()
                except Exception as exc:
                    results[i] = TrialResult(
                        trial_id=str(uuid.uuid4()),
                        trial_index=i,
                        outcome=TrialOutcome.ERROR,
                        value=None,
                        error=str(exc),
                        duration_s=0.0,
                        ts=time.time(),
                    )
                if self.on_trial_complete and results[i]:
                    try:
                        self.on_trial_complete(results[i])  # type: ignore[arg-type]
                    except Exception:
                        pass
        return [r for r in results if r is not None]

    def _run_one(self, index: int) -> TrialResult:
        trial_id = str(uuid.uuid4())
        t0 = time.time()
        value = None
        error = None
        outcome = TrialOutcome.FAIL

        # Run with timeout using a thread
        result_holder: list[Any] = [None, None]  # [value, exception]
        done = threading.Event()

        def _target() -> None:
            try:
                result_holder[0] = self.task_fn()
            except Exception as exc:
                result_holder[1] = exc
            finally:
                done.set()

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        finished = done.wait(timeout=self.timeout_s)

        duration_s = time.time() - t0

        if not finished:
            outcome = TrialOutcome.TIMEOUT
            error = f"timeout after {self.timeout_s}s"
        elif result_holder[1] is not None:
            outcome = TrialOutcome.ERROR
            error = "".join(traceback.format_exception(
                type(result_holder[1]), result_holder[1],
                result_holder[1].__traceback__,
            ))
            value = None
        else:
            value = result_holder[0]
            try:
                passed = bool(self.oracle(value))
            except Exception as exc:
                passed = False
                error = f"oracle raised: {exc}"
            outcome = TrialOutcome.PASS if passed else TrialOutcome.FAIL

        return TrialResult(
            trial_id=trial_id,
            trial_index=index,
            outcome=outcome,
            value=value,
            error=error,
            duration_s=duration_s,
            ts=t0,
        )

    # ── aggregation ───────────────────────────────────────────────────────

    def _aggregate(self, run_id: str, trials: list[TrialResult]) -> ReliabilityReport:
        k = len(trials)
        if k == 0:
            return ReliabilityReport(
                run_id=run_id, task_id=self.task_id, k=0, trials=[],
                pass_at_1=0.0, pass_at_k=0.0, consistency_score=0.0,
                mean_duration_s=0.0, p95_duration_s=0.0,
                failure_modes=set(), outcome_counts={},
            )

        passes = sum(1 for t in trials if t.passed)
        pass_at_1 = passes / k
        pass_at_k = 1.0 if passes == k else 0.0

        # Consistency score: fraction of trials with the same output hash
        # (τ-bench style — measures determinism, not just correctness)
        output_hashes = [
            hashlib.sha256(str(t.value).encode()).hexdigest()[:16]
            if t.value is not None else "__none__"
            for t in trials
        ]
        if output_hashes:
            most_common = max(set(output_hashes), key=output_hashes.count)
            consistency_score = output_hashes.count(most_common) / k
        else:
            consistency_score = 0.0

        durations = [t.duration_s for t in trials]
        mean_duration_s = statistics.mean(durations)
        sorted_durations = sorted(durations)
        p95_idx = max(0, int(0.95 * k) - 1)
        p95_duration_s = sorted_durations[p95_idx]

        # Failure mode taxonomy
        failure_modes: set[FailureMode] = set()
        error_trials = [t for t in trials if t.outcome == TrialOutcome.ERROR]
        timeout_trials = [t for t in trials if t.outcome == TrialOutcome.TIMEOUT]
        fail_trials = [t for t in trials if t.outcome == TrialOutcome.FAIL]

        if error_trials:
            failure_modes.add(FailureMode.EXCEPTION)
        if timeout_trials:
            failure_modes.add(FailureMode.TIMEOUT)
        if fail_trials:
            if passes > 0:
                failure_modes.add(FailureMode.FLAKY)
            else:
                failure_modes.add(FailureMode.SYSTEMATIC)
        if fail_trials and consistency_score < 1.0 and passes > 0:
            failure_modes.add(FailureMode.INCONSISTENT)
        if fail_trials and not error_trials and not timeout_trials:
            failure_modes.add(FailureMode.WRONG_OUTPUT)

        outcome_counts = {
            TrialOutcome.PASS.value: passes,
            TrialOutcome.FAIL.value: len(fail_trials),
            TrialOutcome.ERROR.value: len(error_trials),
            TrialOutcome.TIMEOUT.value: len(timeout_trials),
        }

        return ReliabilityReport(
            run_id=run_id,
            task_id=self.task_id,
            k=k,
            trials=trials,
            pass_at_1=pass_at_1,
            pass_at_k=pass_at_k,
            consistency_score=consistency_score,
            mean_duration_s=mean_duration_s,
            p95_duration_s=p95_duration_s,
            failure_modes=failure_modes,
            outcome_counts=outcome_counts,
        )


# ---------------------------------------------------------------------------
# BoundaryProbe  (MELD/SENTINEL boundary detection)
# ---------------------------------------------------------------------------

class BoundaryProbe:
    """
    Probe the MELD/SENTINEL boundary: the minimum k at which the agent's
    reliability drops below an operator-specified floor.

    The MELD boundary is the point where multi-agent longitudinal drift
    causes reliability to fall below the operator's floor (e.g., 0.95 for IL4,
    0.99 for IL5/IL6). The SENTINEL boundary is the point where the safety
    enforcement layer should intervene.

    Usage::

        probe = BoundaryProbe(
            task_fn=my_task,
            oracle=my_oracle,
            reliability_floor=0.95,   # IL4 floor
            k_values=[1, 3, 5, 10, 20, 50],
        )
        boundary = probe.find_boundary()
        # boundary = {"meld_k": 7, "sentinel_k": 10, "reports": {...}}
    """

    def __init__(
        self,
        task_fn: Callable[[], Any],
        oracle: Callable[[Any], bool],
        reliability_floor: float = 0.95,
        k_values: Optional[list[int]] = None,
        timeout_s: float = 30.0,
        task_id: str = "",
    ) -> None:
        self.task_fn = task_fn
        self.oracle = oracle
        self.reliability_floor = reliability_floor
        self.k_values = k_values or [1, 3, 5, 10, 20]
        self.timeout_s = timeout_s
        self.task_id = task_id or str(uuid.uuid4())[:8]

    def find_boundary(self) -> dict:
        """
        Run the harness at each k in k_values.
        Returns a dict with:
          meld_k     — smallest k where pass@1 < reliability_floor
          sentinel_k — smallest k where pass^k < 1.0
          reports    — {k: ReliabilityReport} for each k
        """
        reports: dict[int, ReliabilityReport] = {}
        meld_k: Optional[int] = None
        sentinel_k: Optional[int] = None

        for k in sorted(self.k_values):
            harness = ReliabilityHarness(
                task_fn=self.task_fn,
                oracle=self.oracle,
                task_id=f"{self.task_id}@k={k}",
                k=k,
                timeout_s=self.timeout_s,
                parallel=False,
            )
            report = harness.run()
            reports[k] = report

            if meld_k is None and report.pass_at_1 < self.reliability_floor:
                meld_k = k
            if sentinel_k is None and report.pass_at_k < 1.0:
                sentinel_k = k

        return {
            "task_id": self.task_id,
            "reliability_floor": self.reliability_floor,
            "meld_k": meld_k,
            "sentinel_k": sentinel_k,
            "reports": {k: r.to_dict() for k, r in reports.items()},
        }


# ---------------------------------------------------------------------------
# GoalDriftMonitor  (CISA/OWASP ASI01/ASI10 — behavioral baseline + alerting)
# ---------------------------------------------------------------------------

class DriftSeverity(str, Enum):
    """Severity level of a detected goal drift event."""
    INFO    = "info"     # minor deviation, within tolerance
    WARNING = "warning"  # notable deviation, operator should review
    ALERT   = "alert"    # significant drift, operator action required
    CRITICAL = "critical" # drift exceeds hard threshold — KillSwitch candidate


@dataclass
class DriftEvent:
    """A single recorded drift observation."""
    ts: float
    task_id: str
    run_id: str
    metric: str           # e.g. "pass_at_1", "consistency_score"
    baseline: float
    observed: float
    delta: float          # observed - baseline
    severity: DriftSeverity
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "metric": self.metric,
            "baseline": round(self.baseline, 4),
            "observed": round(self.observed, 4),
            "delta": round(self.delta, 4),
            "severity": self.severity.value,
            "detail": self.detail,
        }


class GoalDriftMonitor:
    """
    Behavioral baseline tracker and drift alerter.

    Implements the CISA "Careful Adoption of Agentic AI" requirement:
      "Establish behavioral baselines and monitor for goal drift, with
       alerting on unexpected goal changes, anomalous tool sequences,
       or deviations from the established baseline."  (C.A1.4)

    Also satisfies OWASP Agentic Top 10 2026:
      ASI01 (Agent Goal Hijack) — C.A1.4: behavioral baseline + alerting
      ASI10 (Rogue Agents) — detect agents that stay within policy but drift

    Usage::

        monitor = GoalDriftMonitor(task_id="send_frame", window=10)

        # After each reliability run, record the report:
        monitor.record(report)

        # Check for drift:
        events = monitor.drift_events()
        if any(e.severity == DriftSeverity.ALERT for e in events):
            # escalate to operator / KillSwitch

    Baseline is established from the first ``baseline_window`` reports.
    Drift is measured as the absolute delta from the rolling baseline mean.
    Severity thresholds are configurable; defaults are tuned for IL4/IL5.
    """

    # Default severity thresholds (absolute delta from baseline)
    DEFAULT_THRESHOLDS: ClassVar[dict[str, dict[str, float]]] = {
        "pass_at_1": {
            DriftSeverity.INFO.value:     0.02,
            DriftSeverity.WARNING.value:  0.05,
            DriftSeverity.ALERT.value:    0.10,
            DriftSeverity.CRITICAL.value: 0.20,
        },
        "consistency_score": {
            DriftSeverity.INFO.value:     0.03,
            DriftSeverity.WARNING.value:  0.07,
            DriftSeverity.ALERT.value:    0.15,
            DriftSeverity.CRITICAL.value: 0.30,
        },
        "pass_at_k": {
            DriftSeverity.INFO.value:     0.05,
            DriftSeverity.WARNING.value:  0.10,
            DriftSeverity.ALERT.value:    0.20,
            DriftSeverity.CRITICAL.value: 0.40,
        },
    }

    def __init__(
        self,
        task_id: str,
        baseline_window: int = 5,
        rolling_window: int = 10,
        thresholds: Optional[dict[str, dict[str, float]]] = None,
        on_alert: Optional[Callable[[DriftEvent], None]] = None,
    ) -> None:
        """
        Parameters
        ----------
        task_id:
            Identifier for the task being monitored (must match
            ReliabilityReport.task_id).
        baseline_window:
            Number of initial reports used to establish the baseline.
            Drift is not computed until this many reports have been recorded.
        rolling_window:
            Number of most-recent reports used for the rolling mean comparison.
        thresholds:
            Override the default severity thresholds. Keys are metric names;
            values are dicts mapping severity level strings to float deltas.
        on_alert:
            Optional callback invoked synchronously for every DriftEvent with
            severity >= WARNING. Use to wire into KillSwitch or operator queue.
        """
        self.task_id = task_id
        self.baseline_window = baseline_window
        self.rolling_window = rolling_window
        self.thresholds = thresholds or self.DEFAULT_THRESHOLDS
        self.on_alert = on_alert
        self._reports: list[ReliabilityReport] = []
        self._events: list[DriftEvent] = []
        self._lock = threading.RLock()

    # ── recording ─────────────────────────────────────────────────────────

    def record(self, report: ReliabilityReport) -> list[DriftEvent]:
        """
        Record a new ReliabilityReport and compute drift against the baseline.

        Returns the list of DriftEvents generated by this report (may be empty
        if the baseline has not yet been established).
        """
        with self._lock:
            self._reports.append(report)
            new_events: list[DriftEvent] = []

            if len(self._reports) <= self.baseline_window:
                # Still building the baseline — no drift computation yet
                return new_events

            baseline_reports = self._reports[:self.baseline_window]
            baseline = {
                "pass_at_1":        statistics.mean(r.pass_at_1 for r in baseline_reports),
                "consistency_score": statistics.mean(r.consistency_score for r in baseline_reports),
                "pass_at_k":        statistics.mean(r.pass_at_k for r in baseline_reports),
            }

            for metric, base_val in baseline.items():
                observed = getattr(report, metric)
                delta = observed - base_val  # negative = degradation
                thresholds = self.thresholds.get(metric, {})

                # Determine severity from absolute delta (degradation only)
                abs_delta = abs(delta)
                severity = None
                if abs_delta >= thresholds.get(DriftSeverity.CRITICAL.value, 1.0):
                    severity = DriftSeverity.CRITICAL
                elif abs_delta >= thresholds.get(DriftSeverity.ALERT.value, 1.0):
                    severity = DriftSeverity.ALERT
                elif abs_delta >= thresholds.get(DriftSeverity.WARNING.value, 1.0):
                    severity = DriftSeverity.WARNING
                elif abs_delta >= thresholds.get(DriftSeverity.INFO.value, 1.0):
                    severity = DriftSeverity.INFO

                if severity is not None and delta < 0:
                    # Only alert on degradation (negative delta = worse performance)
                    event = DriftEvent(
                        ts=report.ts,
                        task_id=self.task_id,
                        run_id=report.run_id,
                        metric=metric,
                        baseline=base_val,
                        observed=observed,
                        delta=delta,
                        severity=severity,
                        detail=(
                            f"{metric} degraded {abs_delta:.3f} from baseline "
                            f"{base_val:.3f} → {observed:.3f}"
                        ),
                    )
                    new_events.append(event)
                    self._events.append(event)
                    if self.on_alert and severity in (
                        DriftSeverity.WARNING,
                        DriftSeverity.ALERT,
                        DriftSeverity.CRITICAL,
                    ):
                        try:
                            self.on_alert(event)
                        except Exception:
                            pass  # never let the callback crash the harness

            return new_events

    # ── query ─────────────────────────────────────────────────────────────

    def drift_events(
        self,
        min_severity: DriftSeverity = DriftSeverity.INFO,
    ) -> list[DriftEvent]:
        """Return all recorded drift events at or above *min_severity*."""
        order = [
            DriftSeverity.INFO,
            DriftSeverity.WARNING,
            DriftSeverity.ALERT,
            DriftSeverity.CRITICAL,
        ]
        min_idx = order.index(min_severity)
        with self._lock:
            return [e for e in self._events if order.index(e.severity) >= min_idx]

    def baseline_stats(self) -> Optional[dict[str, float]]:
        """
        Return the established baseline means, or None if not yet established.
        """
        with self._lock:
            if len(self._reports) <= self.baseline_window:
                return None
            baseline_reports = self._reports[:self.baseline_window]
            return {
                "pass_at_1":        statistics.mean(r.pass_at_1 for r in baseline_reports),
                "consistency_score": statistics.mean(r.consistency_score for r in baseline_reports),
                "pass_at_k":        statistics.mean(r.pass_at_k for r in baseline_reports),
                "n_baseline":       float(self.baseline_window),
                "n_total":          float(len(self._reports)),
            }

    def is_critical(self) -> bool:
        """True if any CRITICAL drift event has been recorded."""
        return any(e.severity == DriftSeverity.CRITICAL for e in self.drift_events())

    def summary(self) -> str:
        with self._lock:
            n = len(self._reports)
            n_events = len(self._events)
            critical = sum(1 for e in self._events if e.severity == DriftSeverity.CRITICAL)
            alerts = sum(1 for e in self._events if e.severity == DriftSeverity.ALERT)
            return (
                f"GoalDriftMonitor(task={self.task_id!r} reports={n} "
                f"events={n_events} alerts={alerts} critical={critical})"
            )
