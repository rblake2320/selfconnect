"""Open Notepad, draw ASCII art using clipboard + aggressive focus."""
from self_connect import *
import subprocess, time, sys, ctypes

user32 = ctypes.windll.user32

# Snapshot existing Notepad hwnds
before = {w.hwnd for w in list_windows() if "Notepad" in w.title}

# Launch new one
proc = subprocess.Popen(["notepad.exe"])
time.sleep(2.5)

# Find the new hwnd
t = None
for w in list_windows():
    if "Notepad" in w.title and w.hwnd not in before:
        t = w
        break

if not t:
    print("ERROR: no new Notepad window found")
    sys.exit(1)

print(f"Notepad hwnd={t.hwnd}")

# ASCII art rocket
art = r"""         ^
        /|\
       / | \
      /  |  \
     / * | * \
    /    |    \
   /  *  |  *  \
  / **   |   ** \
 /*      |      *\
/========|========\
|   O    |    O   |
|        |        |
|  [  ]  |  [  ]  |
|        |        |
|========|========|
|  S E L F       |
|  C O N N E C T |
|=================|
    |   | |   |
    |   | |   |
   /|   | |   |\
  / |   | |   | \
 *  *   | |   *  *
      * | | *
        * *
"""

# Write art to a temp file, then open that file in Notepad
import tempfile, os
tmpfile = os.path.join(tempfile.gettempdir(), "selfconnect_art.txt")
with open(tmpfile, "w") as f:
    f.write(art)

# Close the empty Notepad and open with our file
proc.terminate()
time.sleep(0.5)

# Open the file in Notepad
proc2 = subprocess.Popen(["notepad.exe", tmpfile])
time.sleep(2.5)

# Find the new window
t2 = None
for w in list_windows():
    if "selfconnect_art" in w.title.lower() or ("Notepad" in w.title and w.hwnd not in before):
        t2 = w
        break

if not t2:
    # Fallback: find any Notepad with the filename
    for w in list_windows():
        if "Notepad" in w.title and w.hwnd not in before:
            t2 = w
            break

if not t2:
    print("ERROR: could not find Notepad with art file")
    sys.exit(1)

print(f"Art Notepad hwnd={t2.hwnd} title={t2.title!r}")

# Capture it
path = save_capture(t2.hwnd, path="proofs/ascii_art_live.png")
print(f"Captured: {path}")

# Clean up
os.unlink(tmpfile)
