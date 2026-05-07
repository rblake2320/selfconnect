from self_connect import send_string, list_windows

windows = list_windows()
if windows:
    target = windows[0]
    print(f"Sending 'DEMO-OK' to window: {target.title} (hwnd={target.hwnd})")
    send_string(target, "DEMO-OK")
    print("Done.")
else:
    print("No visible windows found.")
