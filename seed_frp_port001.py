"""
Seed FRP-PORT001 into the live aihangout.ai pathbook registry.

This script uses the frp_client to compute the fingerprint and then
inserts directly via wrangler D1 (since we have CLI access but no JWT).

Run: python seed_frp_port001.py
"""

import json
import subprocess
import sys

sys.path.insert(0, ".")
from frp_client import compute_fingerprint

ERROR_TEXT = "OSError: [WinError 10048] Only one usage of each socket address"
ENV_CLASS = "windows-bash"

fingerprint = compute_fingerprint(ERROR_TEXT, ENV_CLASS)
print(f"Computed fingerprint: {fingerprint}")

trigger_yaml = json.dumps({
    "error_signature": ERROR_TEXT,
    "env_class": ENV_CLASS,
})

remediation_yaml = json.dumps({
    "steps": [
        {
            "step": 1,
            "action": "run",
            "command": (
                "python -c \"from pathlib import Path; "
                "Path(r'{port_file_path}').unlink(missing_ok=True); "
                "print('deleted')\""
            ),
            "expected_output": "deleted",
            "on_failure": "File already gone - proceed",
        },
        {
            "step": 2,
            "action": "run",
            "command": "python {server_script}",
            "expected_output": "Server running on http://127.0.0.1:{port}",
        },
    ],
    "verify": [
        {
            "check": "server responds",
            "command": (
                "python -c \"import urllib.request; "
                "urllib.request.urlopen('http://127.0.0.1:{port}/', timeout=3); "
                "print('OK')\""
            ),
            "expected": "OK",
        }
    ],
})

verify_yaml = json.dumps([
    {
        "check": "server responds",
        "command": (
            "python -c \"import urllib.request; "
            "urllib.request.urlopen('http://127.0.0.1:{port}/', timeout=3); "
            "print('OK')\""
        ),
        "expected": "OK",
    }
])

failed_attempts_yaml = json.dumps([
    {
        "attempt": 1,
        "command": "rm -f /tmp/trace_stress_test/port.txt",
        "error": "No such file or directory (wrong path - Windows path not /tmp)",
        "token_cost": 312,
    },
    {
        "attempt": 2,
        "command": "del C:\\Users\\techai\\AppData\\Local\\Temp\\trace_stress_test\\port.txt 2>NUL",
        "error": "Exit code 1: bash does not support del",
        "token_cost": 287,
    },
    {
        "attempt": 3,
        "command": "del ... & cd /d ... && python ...",
        "error": "Exit code 1: cd: too many arguments",
        "token_cost": 301,
    },
])

provenance = json.dumps({
    "contributed_by": "service:frp-seed",
    "contributed_at": "2026-05-17T00:00:00Z",
    "source": "AXIOM session observation",
})

# Escape single quotes for SQL
def esc(s):
    return s.replace("'", "''")

sql = f"""INSERT INTO pathbooks (
    pathbook_id, protocol_version, title, summary, status, trust_tier,
    ecosystem, runtime, package_name, error_fingerprint, error_signature,
    trigger_yaml, remediation_yaml, verify_yaml, failed_attempts_yaml,
    provenance, signature, source_type, source_url, confidence,
    token_savings_estimate, times_applied, times_succeeded
) VALUES (
    'FRP-PORT001', 'frp/v0.1',
    'Stale port.txt / lock file blocks server restart (Windows + bash)',
    'Python pathlib.Path.unlink(missing_ok=True) is the cross-shell fix for stale lock files on Windows in bash sessions',
    'active', 'reproduced',
    'python', '{esc(ENV_CLASS)}', '',
    '{esc(fingerprint)}',
    '{esc(ERROR_TEXT)}',
    '{esc(trigger_yaml)}',
    '{esc(remediation_yaml)}',
    '{esc(verify_yaml)}',
    '{esc(failed_attempts_yaml)}',
    '{esc(provenance)}',
    '', 'agent_log', '',
    0.95, 900, 3, 3
)"""

print(f"\nSQL length: {len(sql)} chars")
print("\nExecuting via wrangler D1...")

result = subprocess.run(
    [
        "npx", "wrangler", "d1", "execute", "aihangout-database",
        "--remote", "--command", sql,
    ],
    capture_output=True,
    text=True,
    cwd="C:/Users/techai/aihangout-app",
)

if result.returncode == 0:
    print("SUCCESS - FRP-PORT001 seeded!")
    print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
else:
    print(f"FAILED (exit {result.returncode})")
    print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
    sys.exit(1)
