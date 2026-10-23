#!/usr/bin/env python3
"""Fetch only hash-pinned ONNX artifacts from the checked-in privacy model lock."""
import argparse
import hashlib
import json
import logging
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def checksum(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=ROOT/'.retrieval/privacy-models')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    lock = json.loads((ROOT/'tools/privacy_models.lock.json').read_text())
    args.model_dir.mkdir(parents=True, exist_ok=True)
    for name, item in lock['models'].items():
        if Path(item['file']).name != item['file'] or not item['url'].startswith('https://'):
            raise ValueError('invalid_model_lock')
        target = args.model_dir/item['file']
        if target.exists() and checksum(target) == item['sha256']:
            logging.info('%s already hash verified', name)
            continue
        temporary = target.with_suffix('.download')
        logging.info('Downloading %s (%d bytes)', name, item['bytes'])
        try:
            count = 0
            with urllib.request.urlopen(item['url'], timeout=30) as response, temporary.open('wb') as output:
                while chunk := response.read(1 << 20):
                    count += len(chunk)
                    if count > item['bytes']:
                        raise ValueError('model_download_size_mismatch')
                    output.write(chunk)
            if count != item['bytes'] or checksum(temporary) != item['sha256']:
                raise ValueError('model_download_hash_mismatch')
            temporary.replace(target)
            logging.info('%s downloaded and hash verified', name)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
