---
name: converse-manual-stop
description: Start a voice conversation where YOU decide when your turn ends — press ESC (Claude Code) or a key (CLI) to stop recording, instead of waiting for silence detection.
argument-hint: [voice] [message]
---

# /voicemode:converse-manual-stop

Start a voice conversation with **manual stop** enabled: the recording ends when
*you* end your turn — by pressing **ESC** in Claude Code (or **Enter/Space** in the
`voicemode converse` CLI) — or when `listen_duration_max` is reached. VAD silence
auto-stop is **off**, so a thinking pause never cuts you off.

This is the opt-in `manual_stop` option exposed as a skill. Plain
`/voicemode:converse` uses classic silence detection; use this when you want to
control the turn boundary yourself.

## Implementation

Call the `voicemode:converse` MCP tool with **`manual_stop=true`**. Argument handling
matches `/voicemode:converse`:

- `$1` — optional voice name (e.g. `af_river`, `nova`). If non-empty, pass as `voice`;
  if it doesn't look like a voice (spaces/punctuation), treat it as the message.
- `$2` — optional initial message; if empty, choose an appropriate opener.

Example call:

```python
voicemode:converse(
    message=<opener or $2>,
    voice=<$1 if it looks like a voice, else omit>,
    manual_stop=true,        # YOU end the turn (ESC / key); VAD silence won't cut you off
    listen_duration_max=120  # default; raise/lower as needed so ESC has room to interrupt
)
```

### After the recording ends

- **If it completes normally** (you hit `listen_duration_max`, or — only in the
  `_with_silence_detection` variant below — a silence pause): the tool returns your
  transcript directly. Respond to it.

- **If you press ESC in Claude Code:** ESC cancels the tool **and the turn**, so
  nothing can run automatically afterward. VoiceMode saves your transcribed words to
  `~/.voicemode/last_esc_transcript.txt`. **Run `/voicemode:get-transcript`** (a new
  turn) to surface them and continue. (In the CLI, Enter/Space stops cleanly and the
  transcript is printed directly — no extra step.)

## Variant: stop on key/ESC OR silence

If you want a silence pause to *also* end the turn (keypress/ESC as an early-out, VAD
as a backstop), call the tool with **`manual_stop_with_silence_detection=true`**
instead of `manual_stop=true`. Same arguments otherwise.

## Transport note (Claude Code)

The ESC recovery runs briefly in the background after the tool is cancelled. Over
**stdio**, Claude Code does not auto-reconnect an MCP server after a cancellation, so
the *next* converse call may fail with "Connection closed" until you reconnect
(`/mcp` → Reconnect). Running VoiceMode over **HTTP** (`voicemode serve` +
`claude mcp add --transport http`) avoids this — HTTP/SSE auto-reconnects — so HTTP is
recommended for the smoothest in-chat manual stop. (The CLI keypress path is
unaffected.)

## Examples

- `/voicemode:converse-manual-stop` — manual stop, default voice, Claude opens
- `/voicemode:converse-manual-stop af_river` — voice `af_river`, manual stop
- `/voicemode:converse-manual-stop af_river "let's plan the day"` — voice + opener

## If MCP connection fails

Same as `/voicemode:converse`: run `/voicemode:install` (or `uvx voice-mode-install
--yes`), then reconnect with `/mcp`. For full docs, load the `voicemode` skill.
