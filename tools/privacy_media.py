#!/usr/bin/env python3
"""Decode, detect, replace pixels, verify and commit sanitized-only evidence.

Integration candidate, not a universal anonymity/zero-miss guarantee. Model
errors are fatal. Raw frames stay in this process; no raw recording is created.
"""
from __future__ import annotations

import argparse
import errno
from fractions import Fraction
import hashlib
import io
import itertools
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
import uuid

from privacy_redactor import PrivacyRedactionEngine

ROOT = Path(__file__).resolve().parents[1]
POLICY = 'sanitized-pixels-v1'


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            result.update(chunk)
    return result.hexdigest()


def resolve_models(model_dir, lock_path=None):
    lock = json.loads(Path(lock_path or ROOT/'tools/privacy_models.lock.json').read_text())
    if lock.get('schema') != 'visn-privacy-models-v1':
        raise ValueError('invalid_privacy_model_lock')
    resolved = {}
    for kind in ('yunet', 'plate'):
        record = lock['models'][kind]
        path = Path(model_dir) / record['file']
        if Path(record['file']).name != record['file'] or not path.is_file() or digest(path) != record['sha256']:
            raise ValueError(f'privacy_model_missing_or_changed:{kind}')
        resolved[kind] = dict(record, path=str(path.resolve()))
    return resolved


class PrivacyDetector:
    """YuNet native-size faces plus published YOLOv9 plate ONNX, FP32 CPU.

    Plate preprocessing/output follows the author's documented letterbox and
    [batch,x1,y1,x2,y2,class,score] contract at revision f22000e02b30:
    https://github.com/ankandrew/open-image-models/tree/f22000e02b30642f317cdba7755c0631638b109e
    """
    def __init__(self, model_dir, threshold=.1, threads=1):
        import cv2
        import onnxruntime as ort
        ort.disable_telemetry_events()
        if not math.isfinite(threshold) or not 0 < threshold <= 1 or not 1 <= threads <= 32:
            raise ValueError('invalid_privacy_detector_configuration')
        self.models = resolve_models(model_dir)
        self.threshold = threshold
        self.face = cv2.FaceDetectorYN.create(self.models['yunet']['path'], '', (320, 320), threshold, .3, 5000)
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.plate = ort.InferenceSession(self.models['plate']['path'], sess_options=options,
                                         providers=['CPUExecutionProvider'])
        input_node = self.plate.get_inputs()[0]
        if input_node.type != 'tensor(float)' or len(input_node.shape) != 4 or input_node.shape[1] != 3:
            raise ValueError('unsupported_plate_input_contract')
        self.size = int(input_node.shape[-1])
        if input_node.shape[-2] != self.size or not 128 <= self.size <= 2048:
            raise ValueError('unsupported_plate_input_shape')
        self.input_name = input_node.name
        self.runtime = dict(opencv=cv2.__version__, onnxruntime=ort.__version__,
                            providers=self.plate.get_providers(), threads=threads, precision='FP32')

    def detect(self, image):
        import cv2
        import numpy as np
        height, width = image.shape[:2]
        padded_w, padded_h = math.ceil(width / 32)*32, math.ceil(height / 32)*32
        padded = cv2.copyMakeBorder(image, 0, padded_h-height, 0, padded_w-width, cv2.BORDER_CONSTANT)
        self.face.setInputSize((padded_w, padded_h))
        _, faces = self.face.detect(padded)
        found = {'face': [], 'plate': []}

        def add(kind, coordinates, confidence):
            values = [float(v) for v in coordinates]
            if not all(map(math.isfinite, values + [float(confidence)])):
                raise ValueError('nonfinite_privacy_detection')
            x0, y0, x1, y1 = values
            x0, x1 = max(0., min(width, x0)), max(0., min(width, x1))
            y0, y1 = max(0., min(height, y0)), max(0., min(height, y1))
            if x1 > x0 and y1 > y0 and confidence >= self.threshold:
                found[kind].append(dict(bbox=[x0/width, y0/height, (x1-x0)/width, (y1-y0)/height],
                                        confidence=float(confidence)))
        if faces is not None:
            if faces.ndim != 2 or faces.shape[1] != 15:
                raise ValueError('unsupported_yunet_output')
            for row in faces:
                add('face', [row[0], row[1], row[0]+row[2], row[1]+row[3]], row[-1])

        ratio = min(self.size/height, self.size/width)
        resized_size = (round(width*ratio), round(height*ratio))
        dw, dh = (self.size-resized_size[0])/2, (self.size-resized_size[1])/2
        resized = cv2.resize(image, resized_size, interpolation=cv2.INTER_LINEAR)
        boxed = cv2.copyMakeBorder(resized, round(dh-.1), round(dh+.1), round(dw-.1), round(dw+.1),
                                   cv2.BORDER_CONSTANT, value=(114, 114, 114))
        tensor = np.ascontiguousarray(boxed.transpose(2, 0, 1)[::-1][None], dtype=np.float32)/255.
        predictions = np.asarray(self.plate.run(None, {self.input_name: tensor})[0])
        if predictions.ndim == 3 and predictions.shape[0] == 1:
            predictions = predictions[0]
        if predictions.ndim != 2 or predictions.shape[1] != 7 or not np.isfinite(predictions).all():
            raise ValueError('unsupported_plate_output_contract')
        for row in predictions:
            if row[0] != 0 or row[5] != 0:
                raise ValueError('unsupported_plate_class_or_batch')
            add('plate', [(row[1]-dw)/ratio, (row[2]-dh)/ratio, (row[3]-dw)/ratio, (row[4]-dh)/ratio], row[6])
        return found


def replace_pixels(image, detections, trackers):
    """Conservative integer bounds; no smoothing or rounding may expose a box."""
    height, width = image.shape[:2]
    boxes = []
    for kind, tracker in trackers.items():
        if kind not in detections:
            raise ValueError('missing_privacy_detection_class')
        for mask in tracker.process_frame(detections[kind]):
            x, y, w, h = mask['dilated_bbox']
            x0, y0 = max(0, math.floor(x*width)), max(0, math.floor(y*height))
            x1, y1 = min(width, math.ceil((x+w)*width)), min(height, math.ceil((y+h)*height))
            if x1 > x0 and y1 > y0:
                image[y0:y1, x0:x1] = 0
                boxes.append(dict(kind=kind, xyxy=[x0, y0, x1, y1]))
    return boxes


def sanitize(source, target, seconds, detector, *, max_pixels=16_777_216,
             _decoded=None, _continuation=None):
    """Commit only after lossless decode verification. Sidecar is the commit marker.

    Encoded timestamps use Matroska milliseconds. Exact source PTS/timebase and
    their mapping are retained per frame; quantization is disclosed and checked.
    """
    import av
    import numpy as np
    if not math.isfinite(seconds) or not 0 < seconds <= 3600:
        raise ValueError('invalid_privacy_duration')
    target = Path(target)
    manifest_path = target.with_suffix(target.suffix + '.privacy.json')
    ledger_path = target.with_suffix(target.suffix + '.frames.jsonl')
    if any(p.exists() for p in (target, manifest_path, ledger_path)):
        raise FileExistsError('privacy_output_already_exists')
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / ('.privacy-' + uuid.uuid4().hex)
    temp.mkdir(mode=0o700)
    video_temp, ledger_temp = temp/'sanitized.mkv', temp/'frames.jsonl'
    trackers = {kind: PrivacyRedactionEngine(recovery_threshold=detector.threshold,
                primary_threshold=max(.6, detector.threshold), sanitization_mode='solid_replacement')
                for kind in ('face', 'plate')}
    started = time.perf_counter()
    counts = dict(face=0, plate=0)
    frame_count, first_pts, previous_ms, shape = 0, None, -1, None
    source_timebase = None
    input_container = output_container = None
    committed = False
    last_source_time = None
    source_pixel_format = None
    clock_kind = None
    arrival_origin_ns = None
    session_id=uuid.uuid4().hex
    transport_started_ms=time.time_ns()//1_000_000
    termination='end_of_stream'
    timestamp_jump_count=0
    timestamp_jump_samples=[]
    association_sessions=[]
    allow_arrival_clock = isinstance(source, str) and source.startswith(('http://','https://','rtsp://','rtsps://'))
    try:
        if _decoded is None:
            input_container = av.open(source, mode='r', timeout=(10., 10.))
            if len(input_container.streams.video) != 1:
                raise ValueError('privacy_requires_one_video_stream')
            source_stream = input_container.streams.video[0]
            rate = source_stream.average_rate or 25
            force_arrival_clock = allow_arrival_clock and any(
                name in ('mpjpeg','mjpeg') for name in input_container.format.name.split(','))
            decoded = ((frame, time.monotonic_ns()) for frame in input_container.decode(source_stream))
        else:
            decoded, rate, force_arrival_clock, allow_arrival_clock = _decoded
        output_container = av.open(str(video_temp), mode='w', format='matroska')
        out = output_container.add_stream('ffv1', rate=rate)
        out.pix_fmt = 'bgr0'
        out.time_base = out.codec_context.time_base = Fraction(1, 1000)
        out.codec_context.thread_count = 1
        with ledger_temp.open('w') as ledger:
            for frame, arrival_ns in decoded:
                if any(component.bits > 8 for component in frame.format.components):
                    raise ValueError('privacy_source_precision_unsupported')
                has_pts = not force_arrival_clock and frame.pts is not None and frame.time_base is not None
                frame_clock = 'source_pts' if has_pts else 'decoder_observed_monotonic_arrival'
                boundary = None
                if frame_count:
                    if frame.format.name != source_pixel_format:
                        boundary = 'source_pixel_format_changed'
                    elif shape != (frame.height, frame.width):
                        boundary = 'source_resolution_changed'
                    elif frame_clock != clock_kind:
                        boundary = 'source_clock_changed'
                    elif has_pts and frame.pts * frame.time_base < first_pts + last_source_time:
                        boundary = 'source_clock_regressed'
                if boundary and _continuation is not None:
                    # Keep the already decoded boundary frame in memory for
                    # the next independently timestamped, sanitized session.
                    _continuation.update(next=(frame, arrival_ns), reason=boundary)
                    termination = boundary
                    break
                if source_pixel_format is None:
                    source_pixel_format = frame.format.name
                if frame.format.name != source_pixel_format:
                    raise ValueError('privacy_source_pixel_format_changed')
                if clock_kind is not None and frame_clock != clock_kind:
                    raise ValueError('privacy_source_clock_changed')
                clock_kind = frame_clock
                if not has_pts:
                    if not allow_arrival_clock:
                        raise ValueError('privacy_missing_source_timestamp')
                    if arrival_origin_ns is None:
                        arrival_origin_ns = arrival_ns
                    exact_time = Fraction(arrival_ns-arrival_origin_ns,1_000_000_000)
                else:
                    exact_time = frame.pts*frame.time_base
                if first_pts is None:
                    first_pts = exact_time
                    source_timebase = str(frame.time_base) if has_pts else None
                relative = exact_time-first_pts
                if relative >= Fraction(str(seconds)):
                    termination='requested_limit_reached'
                    break
                timestamp_ms = round(relative*1000)
                if timestamp_ms <= previous_ms:
                    raise ValueError('privacy_nonmonotonic_or_submillisecond_timestamp')
                spacing_ms=timestamp_ms-previous_ms if previous_ms>=0 else None
                if spacing_ms is not None and spacing_ms>1000:
                    timestamp_jump_count+=1
                    if len(timestamp_jump_samples)<16:
                        timestamp_jump_samples.append([previous_ms,timestamp_ms])
                    # Do not carry predicted sensitive-region state over an
                    # uncertain observation boundary. Current detections still
                    # receive the same conservative solid replacement.
                    trackers = {kind: PrivacyRedactionEngine(recovery_threshold=detector.threshold,
                                primary_threshold=max(.6, detector.threshold), sanitization_mode='solid_replacement')
                                for kind in ('face', 'plate')}
                if not association_sessions or spacing_ms is not None and spacing_ms>1000:
                    if len(association_sessions) >= 256:
                        raise ValueError('privacy_association_session_limit')
                    association_sessions.append(dict(session_id=f'{session_id}:{timestamp_jump_count}',
                        first_frame_index=frame_count,last_frame_index=frame_count,
                        first_observed_ms=timestamp_ms,last_observed_ms=timestamp_ms,
                        start_reason='initial_capture' if frame_count==0 else 'observed_timestamp_spacing'))
                association_sessions[-1].update(last_frame_index=frame_count,last_observed_ms=timestamp_ms)
                previous_ms = timestamp_ms
                image = frame.to_ndarray(format='bgr24')
                height, width = image.shape[:2]
                if width*height > max_pixels or width < 16 or height < 16:
                    raise ValueError('privacy_frame_geometry_limit')
                if shape is None:
                    shape = (height, width)
                    out.width, out.height = width, height
                if shape != (height, width):
                    raise ValueError('privacy_resolution_changed')
                found = detector.detect(image)
                boxes = replace_pixels(image, found, trackers)
                for kind in counts:
                    counts[kind] += len(found[kind])
                clean_frame = av.VideoFrame.from_ndarray(image, format='bgr24')
                clean_frame.pts, clean_frame.time_base = timestamp_ms, Fraction(1, 1000)
                for packet in out.encode(clean_frame):
                    output_container.mux(packet)
                ledger.write(json.dumps(dict(index=frame_count, source_pts=frame.pts if has_pts else None,
                    source_time_base=str(frame.time_base) if has_pts else None,
                    clock_kind=frame_clock, decoder_observed_arrival_ns=arrival_ns,
                    association_session_id=f'{session_id}:{timestamp_jump_count}',
                    timestamp_spacing_ms=spacing_ms,
                    relative_time=str(relative),
                    sanitized_pts_ms=timestamp_ms, masks=boxes,
                    bgr_sha256=hashlib.sha256(image.tobytes()).hexdigest()), allow_nan=False)+'\n')
                image.fill(0)
                frame_count += 1
                last_source_time = relative
                if frame_count % 100 == 0:
                    logging.info('Privacy sanitized %d frames; source progress %.3fs', frame_count, float(relative))
            if _continuation and termination == 'end_of_stream':
                termination = _continuation.get('end_reason', termination)
            for packet in out.encode():
                output_container.mux(packet)
            ledger.flush()
            os.fsync(ledger.fileno())
        transport_finished_ms=time.time_ns()//1_000_000
        output_container.close()
        output_container = None
        if input_container is not None:
            input_container.close()
            input_container = None
        if frame_count == 0:
            raise ValueError('privacy_no_frames')
        # Verify every encoded frame, not a representative screenshot/sample.
        verified = 0
        with av.open(str(video_temp)) as check, ledger_temp.open() as ledger:
            if len(check.streams) != 1 or len(check.streams.video) != 1:
                raise ValueError('privacy_unapproved_stream')
            for frame in check.decode(video=0):
                entry = json.loads(next(ledger))
                image = frame.to_ndarray(format='bgr24')
                if digest_bytes(image.tobytes()) != entry['bgr_sha256'] or shape != image.shape[:2]:
                    raise ValueError('privacy_encoded_pixel_mismatch')
                if frame.pts*frame.time_base != Fraction(entry['sanitized_pts_ms'], 1000):
                    raise ValueError('privacy_encoded_timestamp_mismatch')
                for box in entry['masks']:
                    x0,y0,x1,y1 = box['xyxy']
                    if np.any(image[y0:y1,x0:x1]):
                        raise ValueError('privacy_encoded_mask_leakage')
                verified += 1
            if verified != frame_count or ledger.readline():
                raise ValueError('privacy_frame_count_mismatch')
        manifest = dict(schema='visn-sanitized-media-v1', policy=POLICY,
            qualification='integration_candidate_not_general_privacy_acceptance',
            sanitizer_code_sha256=digest(__file__), tracker_code_sha256=digest(ROOT/'tools/privacy_redactor.py'),
            models={k: {f:v for f,v in info.items() if f!='path'} for k,info in detector.models.items()},
            runtime=dict(getattr(detector, 'runtime', {}), pyav=av.__version__, numpy=np.__version__),
            source_pixel_format=source_pixel_format, decoded_pixel_contract='8-bit BGR; higher-bit input rejected, never silently reduced',
            detector_threshold=detector.threshold, sanitizer='solid_pixel_replacement',
            media_sha256=digest(video_temp), media_bytes=video_temp.stat().st_size,
            frames=frame_count, width=shape[1], height=shape[0], audio='removed',
            timestamp_policy='source_PTS_recorded; relative_PTS_rounded_to_ms; collisions_rejected',
            max_timestamp_quantization_ms=.5, requested_seconds=seconds,
            observed_source_end=str(last_source_time), source_time_base=source_timebase,
            clock_kind=clock_kind,
            capture=dict(session_id=session_id,requested_seconds=seconds,termination=termination,
                association_sessions=association_sessions,
                observed_frames=frame_count,last_observed_frame_ms=previous_ms,
                transport_started_wall_ms=transport_started_ms,transport_finished_wall_ms=transport_finished_ms,
                transport_time_is_not_capture_time=True,continuity_certified=False,
                timestamp_jump_threshold_ms=1000,timestamp_jump_count=timestamp_jump_count,
                timestamp_jump_samples=timestamp_jump_samples,
                gap_interpretation='Large timestamp spacing is observed; VFR and missing observations cannot be distinguished here'),
            clock_limitations=[] if clock_kind=='source_pts' else [
                'No source capture timestamps supplied; arrival is observed after decode/backpressure, not camera capture time',
                'Undelivered camera frames and pre-decoder network gaps cannot be reconstructed'],
            ledger_sha256=digest(ledger_temp), detections=counts,
            elapsed_seconds=time.perf_counter()-started)
        for path in (video_temp,):
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
        # A committed manifest is required by consumers; uncommitted files are not exposed.
        video_temp.replace(target)
        ledger_temp.replace(ledger_path)
        manifest_temp = temp/'manifest.json'
        manifest_temp.write_text(json.dumps(manifest, indent=2, allow_nan=False)+'\n')
        with manifest_temp.open('rb') as stream:
            os.fsync(stream.fileno())
        manifest_temp.replace(manifest_path)
        committed = True
        return manifest
    finally:
        if output_container is not None:
            output_container.close()
        if input_container is not None:
            input_container.close()
        if not committed:
            for path in (target, ledger_path, manifest_path):
                path.unlink(missing_ok=True)
        for path in temp.iterdir():
            path.unlink()
        temp.rmdir()


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _publish_capture_manifest(directory, manifest):
    path = directory / 'capture.json'
    temporary = directory / '.capture.pending.json'
    with temporary.open('w') as stream:
        stream.write(json.dumps(manifest, indent=2, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def capture_sessions(source, directory, seconds, detector, *, max_sessions=256, reconnect_attempts=0):
    """Keep independent source clocks in independent sanitized files.

    Reconnect is an explicit live-source policy; zero preserves finite-source EOF.
    No synthetic cross-session presentation clock or tracker state is carried.
    """
    import av
    if not math.isfinite(seconds) or not 0 < seconds <= 3600 or not 1 <= max_sessions <= 256:
        raise ValueError('invalid_privacy_capture_limits')
    network = isinstance(source, str) and source.startswith(('http://','https://','rtsp://','rtsps://'))
    if type(reconnect_attempts) is not int or not 0 <= reconnect_attempts <= 8 or reconnect_attempts and not network:
        raise ValueError('invalid_privacy_reconnect_policy')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    descriptor = os.open(directory.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    manifest = dict(schema='visn-sanitized-capture-v1', capture_id=uuid.uuid4().hex,
        state='capturing', requested_seconds=seconds, observed_session_span='0',
        time_semantics='Independent source-relative timelines; session spans are not elapsed wall time or continuous coverage',
        sessions=[], error_code=None)
    _publish_capture_manifest(directory, manifest)
    if reconnect_attempts:
        manifest['transport'] = dict(max_reconnect_attempts=reconnect_attempts, reconnect_attempts=0)
    started = time.monotonic()
    observed_span = Fraction(0)
    try:
        retryable = (av.error.TimeoutError, av.error.ConnectionAbortedError,
            av.error.ConnectionRefusedError, av.error.ConnectionResetError,
            av.error.BrokenPipeError, av.error.EOFError, av.error.HTTPServerError,
            av.error.HTTPTooManyRequestsError)
        def can_retry(error):
            return isinstance(error, retryable) or isinstance(error, av.error.OSError) and error.errno == errno.EIO
        reconnects = 0
        while True:
            disconnected = False
            remaining_wall = seconds - (time.monotonic() - started)
            if network and remaining_wall <= 0:
                break
            opening = True
            try:
                timeout = min(10., max(.001, remaining_wall)) if network else 10.
                with av.open(source, mode='r', timeout=(timeout, timeout)) as container:
                    opening = False
                    if len(container.streams.video) != 1:
                        raise ValueError('privacy_requires_one_video_stream')
                    stream = container.streams.video[0]
                    force_arrival = network and any(name in ('mpjpeg','mjpeg') for name in container.format.name.split(','))
                    def frames():
                        nonlocal disconnected
                        try:
                            for frame in container.decode(stream):
                                arrival = time.monotonic_ns()
                                if network and time.monotonic() - started >= seconds:
                                    continuation['end_reason'] = 'requested_limit_reached'
                                    return
                                yield frame, arrival
                        except av.error.FFmpegError as error:
                            if not reconnect_attempts or not can_retry(error):
                                raise
                            disconnected = True
                        if reconnect_attempts:
                            disconnected = True
                            continuation['end_reason'] = 'transport_disconnected'
                    decoded = iter(frames())
                    carry = None
                    start_reason = 'transport_reconnected' if manifest['sessions'] else 'initial_capture'
                    while observed_span < Fraction(str(seconds)):
                        if len(manifest['sessions']) >= max_sessions:
                            raise ValueError('privacy_capture_session_limit')
                        remaining = Fraction(str(seconds)) - observed_span
                        if network:
                            remaining = min(remaining, Fraction(str(max(0, seconds - (time.monotonic()-started)))))
                            if remaining <= 0:
                                if carry is None:
                                    break
                                # Preserve a boundary frame decoded before the deadline.
                                remaining = Fraction(1, 1000)
                        current_frames = itertools.chain([carry], decoded) if carry is not None else decoded
                        continuation = {}
                        filename = f"session-{len(manifest['sessions']):04d}.mkv"
                        target = directory / filename
                        try:
                            report = sanitize(source, target, float(remaining), detector,
                                _decoded=(current_frames, stream.average_rate or 25, force_arrival, network),
                                _continuation=continuation)
                        except ValueError as error:
                            if disconnected and str(error) == 'privacy_no_frames':
                                break
                            raise
                        observed_span += Fraction(report['observed_source_end'])
                        manifest['sessions'].append(dict(file=filename, session_id=report['capture']['session_id'],
                            start_reason=start_reason, termination=report['capture']['termination'],
                            frames=report['frames'], source_clock_kind=report['clock_kind'],
                            media_sha256=report['media_sha256'], ledger_sha256=report['ledger_sha256'],
                            manifest_sha256=digest(target.with_suffix(target.suffix+'.privacy.json'))))
                        manifest['observed_session_span'] = str(observed_span)
                        _publish_capture_manifest(directory, manifest)
                        logging.info('Committed capture session %d (%d frames; %s)',
                                     len(manifest['sessions']), report['frames'], report['capture']['termination'])
                        carry = continuation.get('next')
                        if carry is None:
                            break
                        start_reason = continuation['reason']
            except av.error.FFmpegError as error:
                if not reconnect_attempts or not opening or not can_retry(error):
                    raise
                disconnected = True
            if not disconnected or time.monotonic() - started >= seconds or observed_span >= Fraction(str(seconds)):
                break
            if reconnects >= reconnect_attempts:
                raise ValueError('privacy_reconnect_budget_exhausted')
            time.sleep(min(.25 * 2**reconnects, 2., max(0, seconds-(time.monotonic()-started))))
            if time.monotonic() - started >= seconds:
                break
            reconnects += 1
            manifest['transport']['reconnect_attempts'] = reconnects
            _publish_capture_manifest(directory, manifest)
            logging.info('Reopening live transport (attempt %d of %d); independent source session', reconnects, reconnect_attempts)
        if not manifest['sessions']:
            raise ValueError('privacy_no_frames')
        manifest['state'] = 'completed'
        _publish_capture_manifest(directory, manifest)
        return manifest
    except BaseException as error:
        manifest['state'] = 'failed'
        # Never copy decoder messages, source paths or credentials into journals.
        manifest['error_code'] = type(error).__name__
        _publish_capture_manifest(directory, manifest)
        raise


def verify_capture_sessions(directory, *, allow_partial=False):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('privacy_capture_symlink')
    marker = directory / 'capture.json'
    if marker.is_symlink() or marker.stat().st_size > 256 * 1024:
        raise ValueError('privacy_capture_manifest_limit')
    manifest = json.loads(marker.read_text())
    if manifest.get('schema') != 'visn-sanitized-capture-v1' or manifest.get('state') not in (
            ('completed', 'capturing', 'failed') if allow_partial else ('completed',)):
        raise ValueError('privacy_capture_incomplete')
    if not 1 <= len(manifest['sessions']) <= 256:
        raise ValueError('privacy_capture_session_contract')
    transport = manifest.get('transport')
    if transport is not None and (not isinstance(transport, dict)
            or type(transport.get('max_reconnect_attempts')) is not int
            or not 1 <= transport['max_reconnect_attempts'] <= 8
            or type(transport.get('reconnect_attempts')) is not int
            or not 0 <= transport['reconnect_attempts'] <= transport['max_reconnect_attempts']):
        raise ValueError('privacy_capture_transport_contract')
    session_ids = set()
    span = Fraction(0)
    for index, entry in enumerate(manifest['sessions']):
        if entry['file'] != f'session-{index:04d}.mkv' or entry['session_id'] in session_ids:
            raise ValueError('privacy_capture_session_contract')
        session_ids.add(entry['session_id'])
        expected_reason = 'initial_capture' if index == 0 else manifest['sessions'][index-1]['termination']
        if expected_reason == 'transport_disconnected' and manifest.get('transport', {}).get('max_reconnect_attempts', 0):
            expected_reason = 'transport_reconnected'
        if entry['start_reason'] != expected_reason:
            raise ValueError('privacy_capture_session_lineage_changed')
        target = directory / entry['file']
        paths = (target, target.with_suffix(target.suffix+'.privacy.json'), target.with_suffix(target.suffix+'.frames.jsonl'))
        if any(path.is_symlink() for path in paths):
            raise ValueError('privacy_capture_symlink')
        report = verify_commit(target)
        if (entry['session_id'] != report['capture']['session_id'] or entry['frames'] != report['frames']
                or entry['media_sha256'] != report['media_sha256'] or entry['ledger_sha256'] != report['ledger_sha256']
                or entry['manifest_sha256'] != digest(paths[1]) or entry['termination'] != report['capture']['termination']
                or entry['source_clock_kind'] != report['clock_kind']):
            raise ValueError('privacy_capture_provenance_changed')
        span += Fraction(report['observed_source_end'])
    if transport is not None and sum(entry['start_reason'] == 'transport_reconnected' for entry in manifest['sessions']) > transport['reconnect_attempts']:
        raise ValueError('privacy_capture_transport_contract')
    if span != Fraction(manifest['observed_session_span']):
        raise ValueError('privacy_capture_span_changed')
    return manifest


def verify_commit(target):
    target = Path(target)
    path = target.with_suffix(target.suffix+'.privacy.json')
    manifest = json.loads(path.read_text())
    ledger = target.with_suffix(target.suffix+'.frames.jsonl')
    if manifest.get('schema') != 'visn-sanitized-media-v1' or manifest.get('policy') != POLICY:
        raise ValueError('privacy_manifest_contract')
    if digest(target) != manifest['media_sha256'] or digest(ledger) != manifest['ledger_sha256']:
        raise ValueError('privacy_committed_artifact_changed')
    return manifest


def render_playback(source, target):
    """Browser derivative from verified sanitized pixels only; never stream-copy raw."""
    import av
    import cv2
    source, target = Path(source), Path(target)
    provenance = verify_commit(source)
    marker = target.with_suffix(target.suffix+'.privacy.json')
    if target.exists() or marker.exists():
        raise FileExistsError('privacy_playback_exists')
    temporary = target.with_suffix('.pending.mp4')
    if temporary.exists():
        raise FileExistsError('privacy_playback_pending')
    committed = False
    try:
        with av.open(str(source)) as original, av.open(str(temporary), 'w', format='mp4',
                                                      options={'movflags': '+faststart'}) as result:
            stream = result.add_stream('libx264',rate=original.streams.video[0].average_rate or 25)
            stream.width = provenance['width'] + provenance['width'] % 2
            stream.height = provenance['height'] + provenance['height'] % 2
            stream.pix_fmt = 'yuv420p'
            stream.options = {'crf':'18','preset':'medium'}
            stream.time_base = stream.codec_context.time_base = Fraction(1,1000)
            stream.codec_context.thread_count = 1
            for frame in original.decode(video=0):
                image = frame.to_ndarray(format='bgr24')
                image = cv2.copyMakeBorder(image,0,provenance['height']%2,0,provenance['width']%2,
                                           cv2.BORDER_CONSTANT)
                clean = av.VideoFrame.from_ndarray(image,format='bgr24')
                clean.pts = round(frame.pts*frame.time_base*1000)
                clean.time_base = Fraction(1,1000)
                for packet in stream.encode(clean):
                    result.mux(packet)
            for packet in stream.encode():
                result.mux(packet)
        count = 0
        with av.open(str(temporary)) as result, source.with_suffix(source.suffix+'.frames.jsonl').open() as ledger:
            if len(result.streams) != 1 or result.streams.video[0].codec_context.name != 'h264':
                raise ValueError('unsupported_privacy_playback_stream')
            for frame in result.decode(video=0):
                entry = json.loads(next(ledger))
                if frame.pts*frame.time_base != Fraction(entry['sanitized_pts_ms'],1000):
                    raise ValueError('privacy_playback_timestamp_mismatch')
                count += 1
            if count != provenance['frames'] or ledger.readline():
                raise ValueError('privacy_playback_frame_mismatch')
        report = dict(schema='visn-sanitized-playback-v1',source_sha256=provenance['media_sha256'],
                      media_sha256=digest(temporary),frames=count,codec='h264',audio='removed')
        temporary.replace(target)
        marker_temp = marker.with_suffix('.pending.json')
        marker_temp.write_text(json.dumps(report,indent=2)+'\n')
        marker_temp.replace(marker)
        committed = True
        return report
    finally:
        temporary.unlink(missing_ok=True)
        if not committed:
            target.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)


def main():
    from worker_lifeline import start
    start()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='URL/path, or - for bounded encoded stdin')
    parser.add_argument('--output', required=True)
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--model-dir', default=str(ROOT/'.retrieval/privacy-models'))
    parser.add_argument('--threshold', type=float, default=.1)
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--stdin-limit', type=int, default=64*1024*1024)
    parser.add_argument('--playback', action='store_true')
    parser.add_argument('--capture-sessions', action='store_true', help='Output a directory of independently timestamped sanitized sessions')
    parser.add_argument('--reconnect-attempts', type=int, default=0, help='Explicit live-source reconnect budget (0–8); requires session capture')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        import cv2
        cv2.setNumThreads(args.threads)
        source = args.source
        if source == '@stdin-url':
            source = sys.stdin.readline(16385).strip()
            if not source or len(source) > 16384:
                raise ValueError('privacy_source_input_limit')
        elif source == '-':
            data = sys.stdin.buffer.read(args.stdin_limit+1)
            if not data or len(data) > args.stdin_limit:
                raise ValueError('privacy_encoded_input_capacity')
            source = io.BytesIO(data)
        if args.reconnect_attempts and (not args.capture_sessions or args.playback):
            raise ValueError('privacy_reconnect_requires_session_capture')
        if args.playback and args.capture_sessions:
            raise ValueError('privacy_conflicting_output_modes')
        if args.playback:
            result = render_playback(source, args.output)
        else:
            detector = PrivacyDetector(args.model_dir, args.threshold, args.threads)
            result = capture_sessions(source, args.output, args.seconds, detector, reconnect_attempts=args.reconnect_attempts) if args.capture_sessions else sanitize(source, args.output, args.seconds, detector)
        print(json.dumps(result, allow_nan=False), flush=True)
    except Exception as error:
        # FFmpeg/model exceptions may embed raw camera credentials or local paths.
        logging.error('Privacy processing blocked (%s); no raw fallback. Session mode may retain a committed sanitized prefix.', type(error).__name__)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
