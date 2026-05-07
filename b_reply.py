import sys
sys.path.insert(0, 'C:/Users/techai/PKA testing/selfconnect')
from self_connect import list_windows, send_string
a = next((w for w in list_windows() if w.hwnd == 0x0ea80dfe), None)
if a:
    send_string(a, 'B-REPLY: roundtrip confirmed\r', char_delay=0.02)
    print('Sent to A')
else:
    print('A not found')
