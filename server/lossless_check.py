"""Refuse a "lossless" file made from lossy audio (#195).

An MP3 or AAC encoder discards everything above a cutoff (about 16 kHz at
128 kbps, up to about 20.5 kHz at 320 kbps); a FLAC made from it keeps that
hard shelf. Genuine recordings slope off gradually, or are quiet throughout.
The check averages the spectrum of short windows from the middle of the
song and finds a drop of at least SHELF_DB within 1 kHz whose top lies
between 16 and 21 kHz, when the audio below it stands clearly above the
file's own floor. A cutoff below REFUSE_BELOW refuses the file; one at or
above it is noted as a suspicion only, since a steep mastering filter at
20 kHz looks the same as an MP3 320. Plain Python (no numpy in the image):
about 0.2 s a song.
"""

import array
import cmath
import math
import shutil

from .providers.process import run_checked

SIZE = 4096  # FFT window: about 11 Hz a bin at 44.1 kHz.
WINDOWS = 32
SECONDS = 12
BAND = 500  # Hz
LOW, HIGH = 14_000, 21_500  # Bands from LOW to HIGH, BAND wide.
SHELF_DB = 30.0  # Drop within two bands (1 kHz) that marks a lossy cutoff.
ABOVE_FLOOR_DB = 30.0  # The audio below the drop must stand this far over the floor.
CUTOFFS = (16_000, 21_000)  # Where a lossy encoder's cutoff can lie.
# A cutoff from here up is only a suspicion, never a refusal (Codex round 1):
# an MP3 320 (about 20.5 kHz) and a genuine master's steep 20 kHz low-pass
# look alike. Below it lie MP3 256 kbps and lower and AAC's usual cutoffs.
REFUSE_BELOW = 20_000


def _fft(values):
    n = len(values)
    if n == 1:
        return values
    even, odd = _fft(values[0::2]), _fft(values[1::2])
    turn = [cmath.exp(-2j * math.pi * k / n) * odd[k] for k in range(n // 2)]
    return [even[k] + turn[k] for k in range(n // 2)] + [
        even[k] - turn[k] for k in range(n // 2)
    ]


def band_levels(samples, rate):
    """Average power (dB) of each BAND-wide band from LOW to HIGH."""
    hann = [0.5 - 0.5 * math.cos(2 * math.pi * i / (SIZE - 1)) for i in range(SIZE)]
    power = [0.0] * (SIZE // 2)
    starts = range(
        0, max(0, len(samples) - SIZE), max(1, (len(samples) - SIZE) // WINDOWS)
    )
    used = 0
    for start in list(starts)[:WINDOWS]:
        spectrum = _fft([samples[start + i] / 32768 * hann[i] for i in range(SIZE)])
        for k in range(SIZE // 2):
            power[k] += abs(spectrum[k]) ** 2
        used += 1
    if not used:
        return []
    width = rate / SIZE
    levels = []
    for low in range(LOW, HIGH, BAND):
        bins = power[int(low / width) : int((low + BAND) / width)]
        if bins:
            levels.append((low, 10 * math.log10(sum(bins) / len(bins) / used + 1e-20)))
    return levels


def shelf(levels):
    """The cutoff (Hz) of a hard lossy shelf in `levels`, or None."""
    if len(levels) < 4:
        return None
    floor = min(level for _, level in levels)
    for i in range(len(levels) - 2):
        top = levels[i + 2][0]  # The first band wholly past the drop.
        before = levels[i][1]
        after = max(level for _, level in levels[i + 2 :])
        if (
            CUTOFFS[0] <= top <= CUTOFFS[1]
            and before - after >= SHELF_DB
            and before - floor >= ABOVE_FLOOR_DB
        ):
            return levels[i + 1][0]
    return None


def lossy_cutoff(path, sample_rate, duration, check=lambda: None):
    """Where a lossless file shows a lossy encoder's cutoff, or None.

    None also when the file cannot be judged: below 44.1 kHz (no room above
    the cutoffs) or too short.
    """
    if sample_rate < 44_100 or not duration or duration < 2:
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("FFmpeg is required to check a lossless download")
    start = max(0.0, duration * 0.3) if duration > SECONDS * 2 else 0.0
    result = run_checked(
        [ffmpeg, "-v", "error", "-nostdin", "-ss", f"{start:.1f}", "-t", str(SECONDS)]
        + ["-i", str(path), "-ac", "1", "-f", "s16le", "-acodec", "pcm_s16le", "-"],
        check,
        timeout=60,
        max_output_bytes=SECONDS * sample_rate * 2 + 1_000_000,
    )
    if result.returncode:
        raise ValueError("The downloaded audio failed complete decoding")
    samples = array.array("h", result.stdout[: len(result.stdout) // 2 * 2])
    return shelf(band_levels(samples, sample_rate))
