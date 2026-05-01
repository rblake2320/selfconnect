"""Scan all open Notepad windows and capture screenshots."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from self_connect import *

windows = [w for w in list_windows() if 'Notepad' in w.title]
print(f'Found {len(windows)} Notepad windows:\n')

os.makedirs('proofs/notepad_scan', exist_ok=True)

for i, w in enumerate(windows):
    safe_title = w.title.encode('ascii', 'replace').decode()
    print(f'  [{i:2d}] hwnd={w.hwnd} title={safe_title!r}')

    # Restore if minimized so we get a real capture
    restore_window(w.hwnd)
    import time; time.sleep(0.15)

    img = capture_window(w.hwnd)
    if img:
        cropped = crop_to_client(w.hwnd, img)
        final = cropped if cropped and cropped.size[0] > 50 else img
        path = f'proofs/notepad_scan/np_{i}.png'
        final.save(path)
        print(f'       -> {path} ({final.size[0]}x{final.size[1]})')
    else:
        print(f'       -> capture FAILED')

print(f'\nDone. {len(windows)} windows scanned.')
