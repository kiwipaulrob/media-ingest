---
name: ai-music-production
description: "AI music production: Suno, MusicGen, analysis."
tags: [music, songwriting, suno, heartmula, audio, spectrogram, lyrics, generation]
version: 1.0.0
consolidation:
  absorbed:
    - songwriting-and-ai-music: Songwriting craft, Suno AI prompts, parody adaptation
    - heartmula: HeartMuLa open-source music generation (3B/7B models, install, patches)
    - songsee: Audio spectrogram and feature visualization (mel, chroma, MFCC)
---

# AI Music Production

Class-level umbrella covering three areas of AI-powered music creation and analysis.

---

## Songwriting & AI Music Generation

*(Absorbed from `songwriting-and-ai-music`)*

Everything here is a GUIDELINE, not a rule. Art breaks rules on purpose. Use what serves the song.

### Song Structure

Common skeletons — mix, modify, or throw out as needed:

- **ABABCB** — Verse/Chorus/Verse/Chorus/Bridge/Chorus (most pop/rock)
- **AABA** — Verse/Verse/Bridge/Verse (jazz standards, ballads)
- **ABAB** — Verse/Chorus alternating (simple, direct)
- **AAA** — Verse/Verse/Verse strophic (folk, storytelling)

Building blocks: Intro, Verse, Pre-Chorus (tension ramp), Chorus (emotional core), Bridge (shift in perspective), Outro.

### Rhyme, Meter, and Sound

**Rhyme types** (from tight to loose): Perfect (lean/mean), Family (crate/braid), Assonance (had/glass), Consonance (scene/when), Near/slant. Mix them — all perfect rhymes sound like a nursery rhyme.

**Internal rhyme:** Rhyming within a line, not just at ends.

**Meter:** Stressed syllables matter more than total count. Say it out loud. Intentionally breaking meter creates emphasis.

### Suno AI Prompt Engineering

**Style/Genre Description Formula:** Genre + Mood + Era + Instruments + Vocal Style + Production + Dynamics.

- V4.5+ supports up to 1,000 chars in Style field — use them
- NO artist names or trademarks. Describe the sound instead.
- Specify BPM and key when known
- Describe the dynamic ARC ("whisper to roar to whisper") — matters more than listing genres
- Build a vocal PERSONA, not just a gender

**Metatags** (place in [brackets] inside lyrics):
- Structure: [Intro] [Verse] [Chorus] [Bridge] [Outro] [Guitar Solo] [Instrumental]
- Vocal: [Whispered] [Belted] [Falsetto] [Harmonies] [Choir]
- Dynamics: [High Energy] [Explosive] [Gradual swell] [Quiet arrangement]
- Atmosphere: [Melancholic] [Euphoric] [Dreamy] [Dark Atmosphere]

**Custom Mode:** Always for serious work. Separate Style + Lyrics. Always add structural tags — without them Suno defaults to flat verse/chorus with no emotional arc.

### Phonetic Tricks for AI Singers

Spell words as they sound: "through" → "thru", "Nous" → "Noose". Hyphenate to guide syllables. ALL CAPS = louder. Vowel extension "lo-o-o-ove" = sustained/melisma. Spell out numbers, space acronyms.

### Parody and Adaptation

Map original structure (syllables, rhyme, stress). Match stressed syllables to same beats. On long held notes, match vowel sound of original. Keep some original lines for recognizability.

---

## HeartMuLa — Open-Source Music Generation

*(Absorbed from `heartmula`)*

HeartMuLa generates music conditioned on lyrics and tags (Apache-2.0). Includes HeartCodec (12.5Hz codec), HeartTranscriptor (Whisper), HeartCLAP (audio-text alignment).

### Hardware Requirements

- **Minimum:** 8GB VRAM with `--lazy_load true`
- **Recommended:** 16GB+ VRAM
- **Multi-GPU:** Use `--mula_device cuda:0 --codec_device cuda:1`
- 3B model with lazy_load peaks at ~6.2GB VRAM

### Quick Install

```bash
git clone https://github.com/HeartMuLa/heartlib.git
cd heartlib
uv venv --python 3.10 .venv
. .venv/bin/activate
uv pip install -e .
uv pip install --upgrade datasets transformers
```

### Required Source Patches

**Patch 1 — RoPE cache fix** in `src/heartlib/heartmula/modeling_heartmula.py`:
Add Llama3ScaledRoPE reinitialization in `setup_caches()` after `reset_caches` try/except.

**Patch 2 — HeartCodec loading fix** in `src/heartlib/pipelines/music_generation.py`:
Add `ignore_mismatched_sizes=True` to ALL `HeartCodec.from_pretrained()` calls.

### Model Download

```bash
hf download --local-dir './ckpt' 'HeartMuLa/HeartMuLaGen'
hf download --local-dir './ckpt/HeartMuLa-oss-3B' 'HeartMuLa/HeartMuLa-oss-3B-happy-new-year'
hf download --local-dir './ckpt/HeartCodec-oss' 'HeartMuLa/HeartCodec-oss-20260123'
```

### Generation

```bash
cd heartlib && . .venv/bin/activate
python ./examples/run_music_generation.py \
  --model_path=./ckpt --version="3B" \
  --lyrics="./assets/lyrics.txt" --tags="./assets/tags.txt" \
  --lazy_load true
```

**Tags format:** comma-separated, no spaces: `piano,happy,romantic`
**Lyrics format:** bracketed structural tags: `[Verse]`, `[Chorus]`, `[Bridge]`, `[Outro]`

### Pitfalls

- Do NOT use bf16 for HeartCodec — use fp32
- Tags may be ignored — lyrics dominate; experiment with tag ordering
- RTX 5080 incompatibility reported upstream
- CPU mode is extremely slow (30-60+ min per song)

---

## Audio Spectrograms & Visualization (songsee)

*(Absorbed from `songsee`)*

Generate spectrograms and multi-panel audio feature visualizations using the `songsee` CLI tool.

### Install

```bash
go install github.com/steipete/songsee/cmd/songsee@latest
```

### Usage

```bash
# Basic spectrogram
songsee track.mp3

# Multi-panel visualization grid
songsee track.mp3 --viz spectrogram,mel,chroma,hpss,selfsim,loudness,tempogram,mfcc,flux

# Time slice
songsee track.mp3 --start 12.5 --duration 8 -o slice.jpg

# From stdin
cat track.mp3 | songsee - --format png -o out.png
```

### Visualization Types

- `spectrogram` — Standard frequency spectrogram
- `mel` — Mel-scaled spectrogram
- `chroma` — Pitch class distribution
- `hpss` — Harmonic/percussive separation
- `selfsim` — Self-similarity matrix
- `loudness` — Loudness over time
- `tempogram` — Tempo estimation
- `mfcc` — Mel-frequency cepstral coefficients
- `flux` — Spectral flux (onset detection)

Multiple types render as a grid in a single image. Output images can be inspected with `vision_analyze` for automated audio analysis.
