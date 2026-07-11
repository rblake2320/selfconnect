"""B's generic reply script. B outputs: python b_send.py <message words>"""
import sys

sys.path.insert(0, 'C:/Users/techai/PKA testing/selfconnect')
from self_connect import list_windows, send_string

a = next((w for w in list_windows() if w.hwnd == 0x0ea80dfe), None)
msg = ' '.join(sys.argv[1:]) if len(sys.argv) > 1 else 'B-ACK'
if a:
    send_string(a, msg + '\r', char_delay=0.02)
    print(f'Sent to A: {msg}')
else:
    print('A not found')
