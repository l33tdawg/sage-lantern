"""Convert a synthesized answer to the rate the device actually accepts.

Why this exists, and why it is not a nicety: measured on unit C on 10 October 2026,
the audio output accepts **16000 Hz and nothing else** — `/proc/asound/card4/stream0`
offers `Format: S16_LE`, 2 channels, `Rates: 16000`. A 22050 Hz file handed to
`hw:4,0` is refused outright ("please, try the plug plugin"), and piping it anyway
would be wrong by 16000/22050 — about 27% fast and high. Piper's
`en_US-lessac-medium` voice emits **22050 Hz**, so the runtime has to convert.

`sox` and `ffmpeg` are both absent from the unit, so the only thing converting today
is ALSA's plug layer. That works, but it means a decision about audio quality and
latency is being made by plugin defaults rather than by anyone. This module makes it
a decision: a bounded, tested, in-process conversion with no new dependency.

**This is a box-filter resampler, and its limits are real.** Each output sample is
the mean of the input samples covering its time slot, which is a genuine
anti-aliasing low-pass for a reduction — better than dropping samples or than plain
linear interpolation — but it is not a windowed-sinc filter. It is the right trade
for 16 kHz speech: flat enough across the band that matters, exactly reproducible,
no dependency, and it cannot silently change pitch. It is **not** suitable for music
or for a rate change far from unity. If better fidelity is ever needed, install a
proper resampler and replace this rather than tuning it.

Integer arithmetic throughout, so the same input gives the same output on any host
and the result never depends on floating-point rounding.

`array` rather than `audioop`: `audioop` was removed in Python 3.13, the package
supports 3.11+, and unit C's *system* interpreter is already 3.14 even though its
service venv is 3.11.
"""

from __future__ import annotations

import array
import sys
import wave
from math import gcd
from pathlib import Path

MIN_RATE = 8000
MAX_RATE = 192000
MIN_CHANNELS = 1
MAX_CHANNELS = 8
SUPPORTED_WIDTHS = (1, 2, 3, 4)


class ResampleError(ValueError):
    """The request cannot be honoured; nothing was written."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _check_rates(source_rate: int, target_rate: int) -> None:
    for name, value in (("source", source_rate), ("target", target_rate)):
        if type(value) is not int or not MIN_RATE <= value <= MAX_RATE:
            raise ResampleError(f"{name}_rate_out_of_range")


def _deinterleave(raw: bytes, width: int, channels: int) -> list[list[int]]:
    """One list of ints per channel.

    The sample width, not the array's item size, decides how the bytes are read.
    Reading a 2-byte sample as a 4-byte int silently halves the frame count and
    multiplies every value by 65536 — which is exactly the defect this function
    had on its first attempt.
    """
    if width in (1, 2, 4):
        # 8-bit WAV is *unsigned* (0..255); 16- and 32-bit are signed. Using a
        # signed array for width 1 turns 255 into -1, which is still the right
        # byte but the wrong number, and it made a constant 128 read back as -128.
        samples = array.array({1: "B", 2: "h", 4: "i"}[width])
        samples.frombytes(raw)
        if width > 1 and sys.byteorder == "big":
            samples.byteswap()
        values = list(samples)
    else:  # 3-byte samples have no array type
        values = []
        for offset in range(0, len(raw), width):
            chunk = raw[offset:offset + width]
            value = int.from_bytes(chunk, "little", signed=False)
            if value >= 1 << (width * 8 - 1):
                value -= 1 << (width * 8)
            values.append(value)
    per_channel: list[list[int]] = [[] for _ in range(channels)]
    for index, value in enumerate(values):
        per_channel[index % channels].append(value)
    return per_channel


def _interleave(per_channel: list[list[int]], width: int, channels: int) -> bytes:
    length = len(per_channel[0]) if per_channel else 0
    if width in (1, 2, 4):
        # The count here is ELEMENTS, not bytes. Building this as `bytes(n)` was a
        # real defect: an array type code reinterprets the buffer, so a `bytes(n)`
        # initialiser yields n elements of `width` bytes each and the array came
        # out `width` times too long. Only the first `length * channels` were ever
        # written, so the rest of the buffer stayed zero — with 16-bit samples that
        # is the second half of every converted file replaced by silence. It hid
        # because the short signals in the tests padded by only a sample or two,
        # and it surfaced as whisper reporting [BLANK_AUDIO] for a recording it
        # transcribes correctly when downmixed by hand.
        samples = array.array({1: "B", 2: "h", 4: "i"}[width], [0]) * (length * channels)
        for position in range(length):
            for channel in range(channels):
                samples[position * channels + channel] = per_channel[channel][position]
        if width > 1 and sys.byteorder == "big":
            samples.byteswap()
        return samples.tobytes()
    body = bytearray()
    for position in range(length):
        for channel in range(channels):
            body += (per_channel[channel][position] & ((1 << (width * 8)) - 1)).to_bytes(width, "little")
    return bytes(body)


def _resample_channel(values: list[int], numerator: int, denominator: int) -> list[int]:
    """One channel: `denominator` input frames to `numerator` output frames.

    Output frame `i` covers input frames
    `[i*denominator/numerator, (i+1)*denominator/numerator)`, and its value is the
    mean over that span — the weighted total divided by the weight **actually
    covered**, not by `denominator`.

    That distinction is the whole reason this is written as two accumulators. The
    final window is clipped by the end of the buffer, so its true weight is smaller
    than `denominator`; dividing by the full denominator instead scales that last
    sample down. A constant input makes it obvious while a sine hides it, which is
    why the tests treat a constant signal as the primary invariant.

    Computed from a prefix sum, so the cost is linear in the input rather than a
    scan per output sample.
    """
    count = len(values)
    if count == 0:
        return []
    output_count = (count * numerator) // denominator
    if output_count == 0:
        return []
    prefix = [0] * (count + 1)
    running = 0
    for index, value in enumerate(values):
        running += value
        prefix[index + 1] = running

    result = [0] * output_count
    for index in range(output_count):
        start = index * denominator
        end = start + denominator
        first = start // numerator
        last = -(-end // numerator)  # exclusive, clamped below
        if last > count:
            last = count
        if first >= last:
            # The whole window sits inside a single input frame.
            result[index] = values[first if first < count else count - 1]
            continue
        total = 0
        weight = 0
        # The left edge contributes in proportion to how much of its frame is covered.
        left = (first + 1) * numerator - start
        if left > 0:
            total += values[first] * left
            weight += left
        # Whole frames strictly between the two partial edges.
        if last - 1 > first + 1:
            total += (prefix[last - 1] - prefix[first + 1]) * numerator
            weight += (last - 1 - (first + 1)) * numerator
        # The right edge likewise, measured back from `end`.
        if last - 1 > first:
            right = end - (last - 1) * numerator
            if right > 0:
                total += values[last - 1] * right
                weight += right
        result[index] = total // weight if weight > 0 else values[-1]
    return result


def _clip(values: list[int], width: int) -> list[int]:
    if width == 1:
        low, high = 0, 255
    else:
        bits = width * 8
        low, high = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return [low if value < low else high if value > high else value for value in values]


def to_mono_pcm(raw: bytes, *, width: int, channels: int) -> bytes:
    """Take one channel out of interleaved PCM. Pure, and lossless for mono.

    **One channel is selected, not mixed, and that is a measured decision.**
    Averaging the channels is the textbook downmix and it was my first
    implementation, but on unit C it produces silence: the ES8323 capture is
    DIFFERENTIAL, so the two channels carry the same signal with opposite sign.
    Measured on a real capture - RMS left 436.1, right 413.4, but the average only
    29.4 while the difference is 423.9, and the channel correlation is -0.9920.
    Averaging two near-perfectly anti-correlated signals cancels them.

    The cost of that mistake was concrete rather than theoretical: whisper.cpp
    returned `[BLANK_AUDIO]` for the averaged file, and transcribed a channel of
    the very same recording as "is the capital of France". So every spoken
    question was discarded as silence.

    Taking a channel rather than mixing is also the safer default in general: it
    cannot cancel, it cannot overflow, and it introduces no rounding, so a
    correlated stereo pair behaves identically to the average while an
    anti-correlated one still works. WHICH channel is `_speech_channel`'s
    decision.
    """
    if type(width) is not int or width not in SUPPORTED_WIDTHS:
        raise ResampleError("unsupported_sample_width")
    if type(channels) is not int or not MIN_CHANNELS <= channels <= MAX_CHANNELS:
        raise ResampleError("unsupported_channel_count")
    if channels == 1:
        return raw
    if len(raw) % (width * channels) != 0:
        raise ResampleError("frames_not_whole")
    per_channel = _deinterleave(raw, width, channels)
    return _interleave([_speech_channel(per_channel)], width, 1)


def _speech_channel(per_channel: list[list[int]]) -> list[int]:
    """Which captured channel a speech recogniser should be handed.

    The first channel, except for a differential capture - and correlation, not
    level, decides which case this is.

    Unit C's capture is differential, and there the SECOND channel transcribes
    markedly better than the first: mean word error rate 0.146 against 0.358 over
    20 phrases spoken by four different voices, 13/20 exact against 5/20. That is
    not gain, offset or polarity, and each was ruled out on the unit rather than
    argued about - negating the first channel reproduces its transcript exactly (a
    mel spectrogram is magnitude-only, so polarity cannot matter), and so does
    scaling it to the second channel's level or removing its DC. The two channels
    differ in their own noise and distortion, which belongs to this capture path
    rather than to the signal, so preferring one is a measurement and not a taste.

    Only a strongly anti-correlated pair is read from the second channel, because
    only there do both channels necessarily carry the whole signal. A normal
    stereo pair keeps its first channel exactly as before, which also means a
    silent or absent second channel cannot turn a working capture into silence.
    The test is integer-only so it cannot disagree with itself across platforms.
    """
    first, second = per_channel[0], per_channel[1]
    product = sum(left * right for left, right in zip(first, second))
    energy = sum(value * value for value in first) * sum(value * value for value in second)
    # correlation < -0.5, without floating point: 4 * product ** 2 > energy
    if product < 0 and 4 * product * product > energy:
        return second
    return first


def convert_channels(per_channel: list[list[int]], target_channels: int) -> list[list[int]]:
    """Widen or match a channel layout, refusing anything that loses audio.

    Only mono -> stereo is supported, and deliberately so: duplicating the single
    channel to both outputs preserves every sample exactly. The reverse would
    discard one side, and more elaborate downmixes would need a stated rule about
    which channel wins - both are decisions a caller should make explicitly rather
    than have this function guess. Refusing is the safe half of the trade.
    """
    if type(target_channels) is not int or not MIN_CHANNELS <= target_channels <= MAX_CHANNELS:
        raise ResampleError("unsupported_target_channel_count")
    current = len(per_channel)
    if current == target_channels:
        return per_channel
    if current == 1 and target_channels == 2:
        return [per_channel[0], list(per_channel[0])]
    raise ResampleError(f"cannot_convert_channels:{current}->{target_channels}")


def resample_pcm(raw: bytes, *, width: int, channels: int,
                 source_rate: int, target_rate: int,
                 target_channels: int | None = None) -> bytes:
    """Resample interleaved PCM. Pure: no files, no device, no side effects."""
    if type(width) is not int or width not in SUPPORTED_WIDTHS:
        raise ResampleError("unsupported_sample_width")
    if type(channels) is not int or not MIN_CHANNELS <= channels <= MAX_CHANNELS:
        raise ResampleError("unsupported_channel_count")
    _check_rates(source_rate, target_rate)
    if len(raw) % (width * channels) != 0:
        raise ResampleError("frames_not_whole")
    wanted = channels if target_channels is None else target_channels
    # A layout change is still work even when the rate already matches.
    if source_rate == target_rate and wanted == channels:
        return raw
    per_channel = _deinterleave(raw, width, channels)
    if source_rate != target_rate:
        divisor = gcd(source_rate, target_rate)
        numerator = target_rate // divisor
        denominator = source_rate // divisor
        per_channel = [_clip(_resample_channel(values, numerator, denominator), width)
                       for values in per_channel]
    per_channel = convert_channels(per_channel, wanted)
    return _interleave(per_channel, width, wanted)


def resample_wav(source: Path, target: Path, *, target_rate: int,
                 target_channels: int | None = None) -> dict:
    """Resample a PCM WAV onto `target`. Refuses anything it cannot do exactly.

    Returns a small report rather than nothing, so a caller records what was
    converted instead of assuming it. Raises without writing on any refusal.
    """
    try:
        with wave.open(str(source), "rb") as stream:
            channels = stream.getnchannels()
            width = stream.getsampwidth()
            source_rate = stream.getframerate()
            frames = stream.getnframes()
            compression = stream.getcomptype()
            raw = stream.readframes(frames)
    except wave.Error as error:
        raise ResampleError(f"unreadable_source:{error}") from None
    except OSError as error:
        raise ResampleError(f"unreadable_source:{error}") from None
    # Anything but plain PCM cannot be converted by arithmetic on its frames.
    if compression != "NONE":
        raise ResampleError("compressed_source_unsupported")
    if frames <= 0:
        raise ResampleError("source_has_no_frames")
    wanted = channels if target_channels is None else target_channels
    converted = resample_pcm(raw, width=width, channels=channels,
                             source_rate=source_rate, target_rate=target_rate,
                             target_channels=target_channels)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as stream:
        stream.setnchannels(wanted)
        stream.setsampwidth(width)
        stream.setframerate(target_rate)
        stream.writeframes(converted)
    return {
        "source_rate": source_rate,
        "target_rate": target_rate,
        "channels": wanted,
        "source_channels": channels,
        "sample_width": width,
        "source_frames": frames,
        "target_frames": len(converted) // (width * wanted),
    }
