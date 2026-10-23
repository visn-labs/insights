"""Bounded single-pass FFmpeg candidate; callers retain independent-seek fallback."""
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

MAX_GROUP_SPAN_MS = 20000
MAX_GROUP_FRAMES = 32
MAX_GROUP_BYTES = 32 * 1024 * 1024


def eligible(targets, end_ms, size):
    # Conservative PNG bound, including 16-bit RGBA and container overhead.
    return (2 <= len(targets) <= MAX_GROUP_FRAMES and
            0 < end_ms - min(targets) <= MAX_GROUP_SPAN_MS and
            len(targets) * (size * size * 8 + 65536) <= MAX_GROUP_BYTES)


def decode_group(executable, path, targets, end_ms, scale):
    """Map six-decimal second targets to PNG/legacy-rounded source PTS.

    select emits the first decoded frame crossing any still-unmet target. One
    frame can satisfy several targets; no FPS conversion or interpolation occurs.
    """
    targets = sorted(set(targets), key=float)
    end = f'{end_ms/1000:.6f}'
    conditions = '+'.join(f'if(gte(t,{t}),if(isnan(prev_selected_t),1,lt(prev_selected_t,{t})),0)' for t in targets)
    selection = f"select='lt(t,{end})*gt({conditions},0)'"
    with TemporaryDirectory(prefix='visn-decode-') as tmp:
        result = subprocess.run([
            executable, '-hide_banner', '-loglevel', 'info', '-copyts', '-start_at_zero',
            '-ss', targets[0], '-t', f'{end_ms/1000-float(targets[0]):.6f}', '-i', str(path),
            '-frames:v', str(len(targets)), '-vf', f'{selection},{scale},showinfo',
            '-vsync', '0', '-threads', '1', '-f', 'image2', '-vcodec', 'png', str(Path(tmp)/'%03d.png')
        ], capture_output=True, timeout=30, check=True)
        if re.search(rb'\b(corrupt|invalid|error|damaged|concealing|premature|truncat|non.monoton)', result.stderr, re.I):
            raise ValueError('Decoder reported damaged or discontinuous evidence')
        bases = set(re.findall(rb'config in time_base:\s*(\d+)/(\d+)', result.stderr))
        records = re.findall(rb'\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:([0-9.eE+-]+).*?\bfmt:(\S+).*?\bs:(\d+)x(\d+)', result.stderr)
        files = sorted(Path(tmp).glob('*.png'))
        if len(bases) != 1 or len(files) != len(records) or len(records) > len(targets):
            raise ValueError('Grouped PNG/PTS correspondence is ambiguous')
        numerator, denominator = map(int, next(iter(bases)))
        if denominator <= 0 or len({r[3:] for r in records}) > 1:
            raise ValueError('Grouped source format or clock changed')
        if sum(p.stat().st_size for p in files) > MAX_GROUP_BYTES:
            raise ValueError('Grouped PNG payload exceeded its byte limit')
        frames = []
        for index, (record, file) in enumerate(zip(records, files)):
            n, pts, legacy_time, *_ = record
            # Match select's pts * av_q2d(time_base) double arithmetic exactly.
            # Dividing after multiplication can cross a six-decimal boundary
            # (e.g. 60 FPS at 1.85s) and assign a frame to the wrong target.
            timestamp = int(pts) * (numerator / denominator)
            if int(n) != index or not float(targets[0]) <= timestamp < float(end) or (frames and timestamp < frames[-1][0]):
                raise ValueError('Grouped frame order or source PTS is invalid')
            frames.append((timestamp, file.read_bytes(), round(float(legacy_time)*1000)))
        return {target:next(((raw, actual) for timestamp, raw, actual in frames if timestamp >= float(target)), (b'', None)) for target in targets}
