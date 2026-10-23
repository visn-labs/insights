#!/usr/bin/env python3
"""Real detector replay across an injected observation gap, not an accuracy benchmark."""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import threading

import av

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = args.source.resolve(strict=True)
    def sha(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    original_hash = sha(source)
    fixture = output / 'gap.mkv'
    count = 0
    with av.open(str(source)) as original, av.open(str(fixture), 'w', format='matroska') as target:
        video = target.add_stream('ffv1', rate=25)
        video.width, video.height = original.streams.video[0].width, original.streams.video[0].height
        video.pix_fmt = 'bgr0'
        video.time_base = video.codec_context.time_base = Fraction(1, 1000)
        video.codec_context.thread_count = 1
        for index, frame in enumerate(original.decode(video=0)):
            if index == 60:
                break
            clean = av.VideoFrame.from_ndarray(frame.to_ndarray(format='bgr24'), format='bgr24')
            clean.pts = index * 40 + (2000 if index >= 25 else 0)
            clean.time_base = Fraction(1, 1000)
            for packet in video.encode(clean):
                target.mux(packet)
            count += 1
        for packet in video.encode():
            target.mux(packet)
    assert count == 60
    python = str(ROOT / '.venv/bin/python')
    model = str(ROOT / 'yolo26s.pt')
    command = [python, 'tools/yolo26_runner.py', '--source', str(fixture), '--model', model,
               '--fps', '5', '--max-seconds', '6', '--threads', '1', '--appearance-mode', 'off']
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
    (output / 'runner.stderr.log').write_text(result.stderr)
    assert result.returncode == 0, result.stderr[-2000:]
    record = next(json.loads(line.split(':', 1)[1]) for line in result.stdout.splitlines()
                  if line.startswith('VISN_DETECTOR_JSON:'))
    observations = {'command': record['observations']}
    with (output / 'worker.stderr.log').open('w') as errors:
        worker = subprocess.Popen([python, 'tools/yolo26_worker.py', '--model', model, '--threads', '1'],
                                  cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=errors, text=True)
        lines = queue.Queue()
        def read():
            for line in worker.stdout:
                lines.put(line)
            lines.put(None)
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        try:
            worker.stdin.write(json.dumps(dict(type='analyze', request_id='gap-replay', source=str(fixture),
                                              fps=5, max_seconds=6, appearance_mode='off')) + '\n')
            worker.stdin.flush()
            observations['persistent'] = []
            while True:
                line = lines.get(timeout=120)
                assert line is not None, 'worker exited before completion'
                if not line.startswith('VISN_WORKER_JSON:'):
                    continue
                message = json.loads(line.split(':', 1)[1])
                assert message['type'] != 'error', message
                observations['persistent'].extend(message.get('observations', []))
                if message['type'] == 'complete':
                    break
            worker.stdin.close()
            assert worker.wait(timeout=15) == 0
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
    summary = {}
    for name, rows in observations.items():
        before = {row['track_id'] for row in rows if row['frame_time_ms'] < 1000}
        after = {row['track_id'] for row in rows if row['frame_time_ms'] >= 3000}
        assert before and after, (name, 'need actual detections on both sides')
        assert not before & after, (name, 'association crossed observation gap')
        assert all(track.startswith('session-1:') for track in after), (name, after)
        summary[name] = dict(observations=len(rows), tracks_before=sorted(before), tracks_after=sorted(after))
    assert sha(source) == original_hash
    (output / 'results.json').write_text(json.dumps(dict(source_sha256=original_hash,
        model_sha256=sha(Path(model)), fixture_sha256=sha(fixture), frames=count,
        scope='Real model association isolation; injected spacing, not lost-frame or accuracy acceptance',
        results=summary, observations=observations), indent=2))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
