from self_connect import list_windows, send_string

windows = list_windows()
if windows:
    target = windows[0]
    print(f"Sending 'DEMO-OK' to window: {target.title} (hwnd={target.hwnd})")
    send_string(target, "DEMO-OK")
    print("Done.")
else:
    print("No visible windows found.")
