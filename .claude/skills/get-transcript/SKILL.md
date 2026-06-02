---
name: get-transcript
description: Surface the transcript captured when you pressed ESC during a voice recording, and respond to it as the user's message
---

# /voicemode:get-transcript

Surface the voice transcript that VoiceMode saved when the user pressed **ESC**
during a `converse` recording (the opt-in manual-stop feature), and respond to it
as if the user had typed it.

## Why this exists

When manual stop is enabled and the user presses ESC mid-recording, VoiceMode
transcribes the audio captured so far. The Claude Code harness, however, discards
a cancelled tool's return value, so that transcript can't be delivered in-chat
through the tool result. Instead VoiceMode writes it to a file. This command
reads that file and brings the user's spoken words into the conversation.

## Implementation

1. Read the transcript file at `~/.voicemode/last_esc_transcript.txt`
   (this is `VOICEMODE_BASE_DIR/last_esc_transcript.txt`; default base dir is
   `~/.voicemode`). Use the Read tool or `cat`.

2. **If the file is missing or empty:** tell the user there's no recent voice
   transcript to surface (they may not have pressed ESC during a converse
   recording, or manual stop may be disabled). Do nothing else.

3. **If it has content:**
   - First **echo** it so the user can confirm it captured the right words:
     `Transcript: "<text>"`
   - Then **treat that transcript as the user's message** and respond to it
     directly — answer the question, perform the request, continue the task,
     exactly as if the user had typed it.

4. **Do NOT delete or clear the file.** It persists so the user can re-run
   `/get-transcript` if needed; it is overwritten only on the next
   ESC-during-converse. (If the same transcript was clearly already handled
   earlier in this conversation, note that you may be surfacing a stale transcript
   and ask the user to confirm before acting.)

## Notes

- This pairs with the manual-stop options on `voicemode:converse`
  (`manual_stop` / `manual_stop_with_silence_detection`). If the user has never
  enabled manual stop, the file won't exist — guide them to enable it.
- The transcript is the user's own words; respond to its *content*, don't just
  acknowledge that you read a file.
- If the converse call *after* an ESC fails with "Connection closed", that's a
  stdio-transport limitation (Claude Code doesn't auto-reconnect stdio MCP
  servers after a cancellation), not a `/get-transcript` problem. Reconnect via
  `/mcp`, or run VoiceMode over HTTP (`voicemode serve`) where reconnection is
  automatic.
