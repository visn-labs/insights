"""Content identities for resolved retrieval artifacts, not model-quality claims."""
import hashlib
from importlib import metadata
import json
import platform
from pathlib import Path

class ProfileConflict(ValueError): pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def stamp(path):
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


class FrozenFiles:
    def __init__(self, files, trees=()):
        self.trees = [(Path(root), pattern, {str(path) for path in Path(root).glob(pattern) if path.is_file()}) for root, pattern in trees]
        self.files = {}; hashes = {}
        for name, path in sorted(files.items()):
            path = Path(path)
            before = stamp(path)
            result = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b''): result.update(chunk)
            if before != stamp(path): raise ValueError('Retrieval artifact changed while fingerprinting')
            self.files[str(path)] = before
            hashes[name] = result.hexdigest()
        if not hashes: raise ValueError('Empty retrieval artifact set')
        self.identity = dict(files=hashes, sha256=digest(hashes))

    def verify(self):
        for root, pattern, expected in self.trees:
            if {str(path) for path in root.glob(pattern) if path.is_file()} != expected:
                raise ProfileConflict('Retrieval artifact membership changed; stage a new profile')
        for path, expected in self.files.items():
            if stamp(Path(path)) != expected:
                raise ProfileConflict('Retrieval artifact changed; restart with a newly staged profile')


def model_files(directory):
    root = Path(directory)
    files = {str(path.relative_to(root)): path for path in root.rglob('*') if path.is_file()}
    if not 1 <= len(files) <= 4096: raise ValueError('Retrieval snapshot file capacity')
    if not any(name.endswith(('.safetensors', '.bin')) for name in files):
        raise ValueError('Retrieval snapshot has no model weights')
    return FrozenFiles(files, [(root, '**/*')])


def runtime_identity(torch, device):
    packages = {}
    for name in ['torch', 'transformers', 'qwen-vl-utils', 'numpy', 'pillow', 'imageio-ffmpeg']:
        packages[name] = metadata.version(name)
    return dict(packages=packages, device=device,
        dtype='float32' if device == 'cpu' else 'float16', attention='sdpa',
        machine=platform.machine(), torch_build=digest(torch.__config__.show()),
        threads=torch.get_num_threads(), interop_threads=torch.get_num_interop_threads(),
        float32_matmul_precision=torch.get_float32_matmul_precision(),
        deterministic_algorithms=torch.are_deterministic_algorithms_enabled(),
        cuda_version=torch.version.cuda, cudnn_version=torch.backends.cudnn.version(),
        cuda_tf32=torch.backends.cuda.matmul.allow_tf32,
        cudnn_tf32=torch.backends.cudnn.allow_tf32,
        cudnn_benchmark=torch.backends.cudnn.benchmark,
        cudnn_deterministic=torch.backends.cudnn.deterministic)


def storage_identity(service):
    value=service.root.stat()
    instance=service.revocations.execute('SELECT id FROM retrieval_storage_instance WHERE version=1').fetchone()
    if not instance:raise ProfileConflict('Retrieval storage instance is missing')
    return digest(dict(root=str(service.root),device=value.st_dev,inode=value.st_ino,instance=instance[0]))

def worker_profile(service):
    service.ensure_models()
    model = service.models
    if hasattr(model, 'verify_profile'): model.verify_profile()
    manifest = dict(schema='visn-retrieval-profile-v1', embedding=json.loads(model.version),
        revocation_contract='recording-scope-v1',
        reranker=getattr(model, 'reranker_identity', None),
        qualification='integration_candidate_not_quality_acceptance' if hasattr(model, 'verify_profile') else 'fixture_or_unsealed_model',
        vector_policy=service.vector_policy, decode_policy=service.decode_policy,
        index_schema='vectors-v1', vector_database=service.vector_databases[service.vector_policy],
        index_storage=storage_identity(service),
        work_caches=not getattr(service.args, 'disable_work_caches', False),
        interpretation=dict(model=getattr(service.args,'interpretation_model',None),
            revision=getattr(service.args,'interpretation_revision',None),
            identity='configuration_only_not_model_weight_attestation'))
    return dict(manifest=manifest, fingerprint=digest(manifest))
