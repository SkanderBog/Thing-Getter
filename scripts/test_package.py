import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if sys.platform == 'darwin':
    executable = root / 'dist/Thing-Getter.app/Contents/MacOS/Thing-Getter'
elif sys.platform == 'win32':
    executable = root / 'dist/Thing-Getter/Thing-Getter.exe'
else:
    executable = root / 'dist/Thing-Getter/Thing-Getter'
output = root / 'build/smoke'
env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
# Exercise the frozen modules without the checkout's Python import path.
env.pop('PYTHONPATH', None)
output.mkdir(parents=True, exist_ok=True)
result = subprocess.run([str(executable), '--self-test', str(output)], cwd=output, env=env, timeout=120)
if result.returncode:
    for log in output.glob('*.log'):
        print(log.read_text(encoding='utf-8', errors='replace'))
    raise SystemExit(result.returncode)
data = json.loads((output / 'results.json').read_text())
assert data['passed'] and len(data['checks']) >= 20
print(json.dumps(data, indent=2))
