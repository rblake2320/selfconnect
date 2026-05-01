"""Save Notepad windows — auto-name from content, target RichEditD2DPT."""
import sys, os, time, ctypes, ctypes.wintypes, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from self_connect import list_windows, restore_window

SAVE_DIR = r"C:\Users\techai\Desktop\notepads"
os.makedirs(SAVE_DIR, exist_ok=True)

# Clear previous attempt
for f in os.listdir(SAVE_DIR):
    if f.endswith('.txt'):
        os.unlink(os.path.join(SAVE_DIR, f))

user32 = ctypes.windll.user32

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
EnumChildProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

JUNK = {"Non Client Input Sink Window", ""}

def get_richedit_text(parent_hwnd):
    """Find RichEditD2DPT child and get its text. Recurse into all descendants."""
    results = []

    def callback(hwnd, _):
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        cls = cls_buf.value

        if "RichEdit" in cls or "Edit" in cls or "TextBox" in cls:
            length = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
            if length > 0 and length < 500000:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, buf)
                text = buf.value
                if text.strip() and text.strip() not in JUNK:
                    results.append((cls, text))

        # Recurse
        user32.EnumChildWindows(hwnd, EnumChildProc(callback), 0)
        return True

    user32.EnumChildWindows(parent_hwnd, EnumChildProc(callback), 0)
    return results


def auto_filename(text, index):
    """Generate a filename from the first meaningful words of content."""
    # Take first 60 chars, strip whitespace/special chars
    sample = text.strip()[:100].replace('\r', ' ').replace('\n', ' ')
    # Remove emojis and special chars
    sample = re.sub(r'[^\w\s-]', '', sample)
    # Get first 5-8 words
    words = sample.split()[:6]
    if not words:
        return f"notepad_{index}.txt"
    name = "_".join(w.lower() for w in words)
    name = re.sub(r'_+', '_', name)[:60]
    return f"{name}.txt"


windows = [w for w in list_windows() if 'Notepad' in w.title]
print(f"Found {len(windows)} Notepad windows\n")

saved = 0
skipped = 0
used_names = set()

for i, w in enumerate(windows):
    safe_title = w.title.encode('ascii', 'replace').decode()

    restore_window(w.hwnd)
    time.sleep(0.15)

    results = get_richedit_text(w.hwnd)

    if not results:
        print(f"  [{i:2d}] SKIP (empty/no text) — {safe_title[:50]!r}")
        skipped += 1
        continue

    # Use longest text
    best_cls, text = max(results, key=lambda x: len(x[1]))

    if len(text.strip()) < 10:
        print(f"  [{i:2d}] SKIP (too short: {len(text)} chars) — {safe_title[:50]!r}")
        skipped += 1
        continue

    # Skip keys.txt (credentials)
    if 'keys.txt' in w.title.lower():
        print(f"  [{i:2d}] SKIP (credentials) — {safe_title[:50]!r}")
        skipped += 1
        continue

    filename = auto_filename(text, i)
    # Avoid duplicates
    base = filename
    counter = 2
    while filename in used_names:
        filename = base.replace('.txt', f'_{counter}.txt')
        counter += 1
    used_names.add(filename)

    filepath = os.path.join(SAVE_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)

    sample = text.strip()[:60].replace('\n', ' ').replace('\r', ' ')
    print(f"  [{i:2d}] SAVED: {filename} ({len(text)} chars)")
    print(f"       sample: {sample!r}")
    saved += 1

# Final count
files_in_dir = [f for f in os.listdir(SAVE_DIR) if f.endswith('.txt')]

print(f"\n{'='*50}")
print(f"Saved:   {saved}")
print(f"Skipped: {skipped}")
print(f"Files in folder: {len(files_in_dir)}")
print(f"Match: {'YES' if saved == len(files_in_dir) else 'NO'}")
print(f"\nFiles:")
for f in sorted(files_in_dir):
    sz = os.path.getsize(os.path.join(SAVE_DIR, f))
    print(f"  {f} ({sz} bytes)")
