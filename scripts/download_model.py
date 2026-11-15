"""Download a pinned, selected OCR checkpoint and verify file checksums."""
import argparse
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

MODEL = 'tencent/HunyuanOCR'
REVISION = '47644ecc4fc854efa4f505155158831f36773ee4'
WEIGHT_SHA256 = '632a1e082c4dd5a3284cf1ffcdba2fdaa06f435762c58c2f34aff0f3bd6c0249'
FILES = ['config.json', 'generation_config.json', 'model.safetensors',
         'preprocessor_config.json', 'special_tokens_map.json', 'tokenizer.json',
         'tokenizer_config.json', 'chat_template.jinja', 'video_preprocessor_config.json',
         'LICENSE', 'README.md']

def fetch_json(url):
    with urllib.request.urlopen(url, timeout=60) as stream:
        return json.load(stream)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--endpoint', default='https://hf-mirror.com')
    args = parser.parse_args()
    root = Path(args.directory)
    root.mkdir(parents=True, exist_ok=True)
    info = fetch_json(f'{args.endpoint}/api/models/{MODEL}/revision/{REVISION}?blobs=true')
    if info.get('sha') != REVISION:
        raise ValueError('模型提交不一致')
    entries = {x['rfilename']: x for x in info['siblings']}
    report = {'model': MODEL, 'revision': REVISION, 'download_endpoint': args.endpoint, 'files': {}}
    for name in FILES:
        entry = entries[name]
        target = root / name
        lfs = entry.get('lfs') or {}
        expected = lfs.get('sha256')
        if name == 'model.safetensors' and expected != WEIGHT_SHA256:
            raise ValueError('镜像权重哈希与官方固定提交元数据不一致')
        size = entry.get('size') or lfs.get('size')
        if target.exists() and size and target.stat().st_size == size:
            checksum = hashlib.file_digest(target.open('rb'), 'sha256').hexdigest()
            if not expected or expected == checksum:
                report['files'][name] = {'bytes': size, 'sha256': checksum, 'expected_sha256': expected, 'cached': True}
                print('cached', name, flush=True)
                continue
        part = target.with_suffix(target.suffix + '.part')
        for attempt in range(1,5):
            try:
                url = f'{args.endpoint}/{MODEL}/resolve/{REVISION}/{name}?download=true'
                req = urllib.request.Request(url, headers={'User-Agent': 'FDE-Doc-Lab/1.0'})
                total = 0
                checksum = hashlib.sha256()
                last = time.monotonic()
                with urllib.request.urlopen(req, timeout=90) as response, part.open('wb') as output:
                    while block := response.read(4*1024*1024):
                        output.write(block)
                        checksum.update(block)
                        total += len(block)
                        if time.monotonic()-last > 15:
                            print(name, total, '/', size, flush=True)
                            last = time.monotonic()
                digest = checksum.hexdigest()
                if size and total != size:
                    raise ValueError(f'{name}: size mismatch {total} != {size}')
                if expected and digest != expected:
                    raise ValueError(f'{name}: SHA256 mismatch')
                os.replace(part,target)
                report['files'][name] = {'bytes': total, 'sha256': digest, 'expected_sha256': expected}
                print('verified', name, total, digest, flush=True)
                break
            except Exception as exc:
                print('retry', name, attempt, type(exc).__name__, str(exc)[:200], flush=True)
                if attempt == 4:
                    raise
                time.sleep(attempt*2)
        (root/'download-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    report['complete'] = True
    (root/'download-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('complete', REVISION, flush=True)

if __name__ == '__main__':
    main()
