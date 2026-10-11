# Voice assistant update report

## Delivered

- Added stage timings: upload, decode, queue, STT, router, agent/LLM, TTS,
  and total.
- Changed the default to Whisper `tiny` int8 CPU, beam size 1, deterministic
  decoding, startup warm-up, and Docker build-time model prefetch.
- Added keyword routing for obvious English/Hindi school questions.
- Voice accepts only English and Hindi. The detected audio language and
  Devanagari text decide the reply language on every turn.
- Added hands-free Shizuka mode with browser silence detection, one-tap audio
  unlock, automatic playback, resumed listening, barge-in, idle timeout,
  captions, mute, and stop controls.
- Added an original inline-SVG Shizuka mascot, mobile bottom navigation,
  desktop sidebar entry, chat launcher, favicon, and sharing PNG.

## Measurements

The required before/after 15-question comparison could not be run because no
fixed set of ten English and five Hindi human recordings or deployed Render
service URL was available in this environment. No latency or accuracy number
is claimed.

The response now returns `timings_ms`, so the same fixed recordings can be
compared without another code change. Use the procedure in `RUNNING.md`.
Whisper is not a streaming model and gTTS produces a finished MP3, so zero
latency is not possible.

Previous local memory measurements remain:

- Whisper tiny int8 CPU: 245 MB working set, 332 MB peak.
- Whisper base int8 CPU: 288 MB working set, 426 MB peak.

`tiny` remains the Render free-tier default because it leaves more memory for
FastAPI, the workbook, LangGraph, and audio processing. Accuracy must be
validated with the fixed English/Hindi recordings before considering `base`.

## Browser checks

- Voice screen served successfully at 375px and 1280px.
- No horizontal overflow at 375px.
- Mobile bottom navigation and desktop fixed sidebar were present.
- English/Hindi-only selector, mascot, keyboard controls, captions, and
  disabled-service error state were visible in the accessibility tree.
- Real microphone, iOS Safari autoplay, Android Chrome, and the 300ms barge-in
  target still require physical-device testing.

## Known limits

- gTTS is unofficial, needs internet, and can be rate limited.
- The LLM graph is synchronous, so audio starts after the complete text reply;
  true sentence streaming would require a streaming agent/tool pipeline.
- In-memory generated audio and limits are process-local.
- Render free-tier latency and memory still require measurement on the live
  service.
