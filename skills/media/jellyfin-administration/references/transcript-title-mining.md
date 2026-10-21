# Transcript title-mining (content-aware gap audit)

When the question is 'which films do these documentaries talk about that the
library does not hold', transcribe and pattern-match — never rely on memory
of what a documentary covers.

## Recipe

1. Extract mono 16kHz WAV per item (`ffmpeg -map 0:a:0 -c:a pcm_s16le -ac 1
   -ar 16000`) on the SMB host; run faster-whisper there or wherever the
   models live (CT110 has faster-whisper; `tiny` is fast, `base` gets proper
   nouns right — prefer `base` for title mining). One `.txt` per item.
2. Normalise aggressively before matching (lowercase, strip punctuation /
   possessives, collapse whitespace) on BOTH the transcript and the title
   list — whisper renders `Postman Always Rings Twice` without the leading
   `The`, and titles with punctuation (`D.O.A.`, `Murder, My Sweet`) never
   match raw.
3. Match a fixed candidate list (~100-150 canon titles for the genre), not
   open vocabulary. Diff hits against library holdings (same normalisation).
4. **Verify every hit in context before reporting it.** Generic words
   collide with titles constantly (`caught`, `Niagara Falls` the place,
   `take over the racket`, `the wrong man` as trope-talk). Pull ±50 chars
   around each hit and drop the ones that are not film citations — typically
   a quarter of raw hits.
5. Present only verified-cited missing titles, tagged with which documentary
   cited each (that tag is the 'why this one' line the user picks from).

## Notes

- Bad source packets (mp2/AC3 decode errors on legacy rips) do not stop
audio extraction — the WAV still completes; note it and move on.
- Whisper language probability < 0.5 on a sample usually means music-only
audio, not a failure — sample a dialogue-heavy timestamp instead.
