# Runbook: Submit pending input in Claude Code TUI via WM_CHAR 0x0D
<!-- selfconnect-runbook: {"status":"historical","since":"2026-07-05","replacement":"enter_claude_tui.md","entrypoint":false} -->

> Historical reference as of 2026-07-05. Prefer `enter_claude_tui.md` for the
> current Claude Code submit procedure and `first_wake_selfconnect.md` for
> first-contact work.

## What
Submit text sitting in a Claude Code input bar when SetForegroundWindow+SendInput fails

## Prerequisites
- `self_connect` available on sys.path

## Steps
1. Check if input is pending: get_text_uia(hwnd); look for '❯' cursor with text after it
2. Post WM_CHAR 0x000D to the parent WT window: user32.PostMessageW(hwnd, 0x0102, 0x000D, 0x001C0001)
3. Verify: text length increases and '❯ ' becomes empty — submission succeeded
4. Note: WM_KEYDOWN VK_RETURN and SendInput both fail for Claude Code TUI. Only WM_CHAR 0x0D works as PostMessage. This bypasses Windows Terminal's XAML input routing.

## Known Failures
- None documented yet

## Verified
- 2026-05-07, session 15
