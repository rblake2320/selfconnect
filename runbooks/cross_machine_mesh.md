# Runbook: Start Cross-Machine AI Mesh (Windows ↔ Spark-1 ↔ Spark-2)
<!-- selfconnect-runbook: {"status":"current","since":"2026-05-05","replacement":null,"entrypoint":false,"kind":"specialized"} -->

## What
Establish the multi-machine agent mesh so Windows PC, Spark-1 (GB10, 192.168.12.132),
and Spark-2 (GB10, 10.0.0.2) can all coordinate and share messages in real time.

## Prerequisites
- `hub_relay.py` present in selfconnect repo root
- `spark2_client.py` present in selfconnect repo root
- SSH access to Spark-1: `ssh rblake2320@192.168.12.132`
- SSH access to Spark-2 (via Spark-1 jump): `ssh -J rblake2320@192.168.12.132 rblake2320@10.0.0.2`
- Port 8765 open on Spark-1 (hub relay listens here)
- selfconnect repo cloned on Spark-2 at a known path

## Steps

### 1. Start hub relay on Spark-1 (or Windows PC)
```bash
# On Spark-1 (preferred — it's the network hub):
ssh rblake2320@192.168.12.132
cd ~/selfconnect   # or wherever the repo is
python hub_relay.py
# Expected output: "Hub relay listening on 0.0.0.0:8765"
```

```python
# Alternatively, start from Windows PC (if Spark-1 port 8765 is reachable):
# The relay can run anywhere reachable by all machines.
HUB_URL = "http://192.168.12.132:8765"
```

### 2. Verify hub is reachable from Windows PC
```python
import urllib.request, json

HUB_URL = "http://192.168.12.132:8765"
try:
    r = urllib.request.urlopen(f"{HUB_URL}/health", timeout=5)
    print(f"Hub health: {json.loads(r.read())}")
except Exception as e:
    print(f"Hub unreachable: {e}")
    raise
```

### 3. Start spark2_client on Spark-2
```bash
# SSH to Spark-2 (via Spark-1 jump host):
ssh -J rblake2320@192.168.12.132 rblake2320@10.0.0.2
cd ~/selfconnect
python spark2_client.py --hub http://192.168.12.132:8765
# Expected output: "Spark-2 peer registered with hub"
```

### 4. Verify Spark-2 is visible to Windows PC
```python
import urllib.request, json

r = urllib.request.urlopen(f"{HUB_URL}/peers", timeout=5)
peers = json.loads(r.read())
print(f"Connected peers: {peers}")
# Expected: {"peers": ["spark2", ...]}

spark2_online = any("spark2" in str(p) for p in peers.get("peers", []))
if not spark2_online:
    raise RuntimeError("Spark-2 not registered — check spark2_client.py on Spark-2")
```

### 5. Send a message to Spark-2 (test round-trip)
```python
import urllib.request, json

msg = {"to": "spark2", "from": "windows_pc", "payload": "ping"}
data = json.dumps(msg).encode()
req = urllib.request.Request(
    f"{HUB_URL}/send",
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST",
)
r = urllib.request.urlopen(req, timeout=5)
print(f"Send result: {json.loads(r.read())}")
```

## Known Failures

- **Hub relay fails in Windows Session 0 (service)**: `hub_relay.py` uses standard socket
  binds that work fine, but Windows Terminal (wt.exe) used to spawn agents cannot run in
  Session 0. Run the hub from a logged-in user session, or on Spark-1 (Linux — no issue).
- **Port 8765 blocked by Windows Firewall**: Add an inbound rule:
  `netsh advfirewall firewall add rule name="Hub Relay" dir=in action=allow protocol=TCP localport=8765`
- **Spark-2 client exits silently**: Check that `HUB_URL` is correct and reachable from
  Spark-2. The hub URL must be the LAN IP (`192.168.12.132`), not `localhost`.
- **Peer registered but messages not delivered**: hub_relay has a queue — check
  `/queue` endpoint on the hub to see if messages are stuck undelivered.

## Verified
- Session 9 (2026-04-xx) — cross-machine mesh LIVE (Patent Claim #10)
- Session 14 (2026-05-04) — hub_relay.py + spark2_client.py still present in repo root
