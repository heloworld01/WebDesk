# 离线 wheel 包闭环审计：在目标 Python 版本环境下求值所有 Requires-Dist
# 用法: python audit-wheels.py [--py 3.8]   （在本项目 wheels-win7 的上级目录运行，或先 cd 到含 wheels-win7 的目录）
# 检查两类问题：1) 缺件（含条件依赖按目标版本求值后的结果） 2) 版本约束冲突
import glob, os, re, sys, zipfile
from packaging.requirements import Requirement
from packaging.markers import default_environment
from packaging.version import Version

py = '3.8'
if '--py' in sys.argv:
    py = sys.argv[sys.argv.index('--py') + 1]

env = default_environment()
env['python_version'] = py
env['python_full_version'] = py + '.10'
env['platform_system'] = 'Windows'
env['sys_platform'] = 'win32'
env['os_name'] = 'nt'


def norm(name):
    return re.sub(r'[-_.]+', '-', name).lower()


here = os.path.dirname(os.path.abspath(__file__))
wheels = glob.glob(os.path.join(here, '*.whl'))

have = {}
for w in wheels:
    parts = os.path.basename(w).split('-')
    have[norm(parts[0])] = Version(parts[1])

missing, conflicts = [], []
for w in wheels:
    fn = os.path.basename(w)
    with zipfile.ZipFile(w) as z:
        meta = z.read([n for n in z.namelist() if n.endswith('.dist-info/METADATA')][0])
    for line in meta.decode('utf-8', 'replace').splitlines():
        if not line.startswith('Requires-Dist:'):
            continue
        try:
            r = Requirement(line.split(':', 1)[1].strip())
        except Exception:
            continue
        if r.marker and not r.marker.evaluate(env):
            continue
        key = norm(r.name)
        if key not in have:
            missing.append((fn, str(r)))
        elif r.specifier and not r.specifier.contains(have[key], prereleases=True):
            conflicts.append((fn, str(r), str(have[key])))

print('py env: %s | wheels: %d' % (py, len(wheels)))
print('MISSING: %d' % len(missing))
for src, req in missing:
    print('  ', src, '->', req)
print('VERSION CONFLICTS: %d' % len(conflicts))
for src, req, got in conflicts:
    print('  ', src, '->', req, '(have', got + ')')
sys.exit(1 if (missing or conflicts) else 0)
