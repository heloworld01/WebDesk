# 在 Win7 上校验 wheel 拷贝完整性：python verify-wheels.py
# 与 SHA256SUMS.txt 逐一比对，全部 OK 则输出 ALL OK
import os, sys, hashlib

here = os.path.dirname(os.path.abspath(__file__))
bad = 0
with open(os.path.join(here, 'SHA256SUMS.txt'), encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        digest, size, name = line.split()
        path = os.path.join(here, name)
        if not os.path.exists(path):
            print('MISSING ', name)
            bad += 1
            continue
        h = hashlib.sha256()
        with open(path, 'rb') as fp:
            for chunk in iter(lambda: fp.read(1 << 20), b''):
                h.update(chunk)
        if h.hexdigest() != digest or os.path.getsize(path) != int(size):
            print('BROKEN  ', name)
            bad += 1
print('ALL OK' if bad == 0 else 'BROKEN/MISSING count: %d' % bad)
sys.exit(1 if bad else 0)
