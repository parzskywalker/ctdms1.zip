"""Prepare the scratch folder of one video: fresh copy of the base DB + off-camera set-up.  usage: v19_prep.py <v1|v2|v3|v4>"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import importlib.util  # noqa: E402

# v19lib imports Playwright (video venv); prep only needs Work, which has no such need
spec = importlib.util.spec_from_file_location("v19lib", Path(__file__).parent / "v19lib.py")
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
PORTS = {"v1": 8041, "v2": 8042, "v3": 8043, "v4": 8044}
key = sys.argv[1]
w = v.Work(key, PORTS[key])
w.fresh_copy()
r = subprocess.run([str(v.PY), str(Path(__file__).parent / "v19_setup.py"), key, str(PORTS[key] + 50), str(w.dir)],
                   cwd=v.ROOT, env=w.env(), capture_output=True, text=True)
print(r.stdout[-1500:], r.stderr[-2500:])
sys.exit(r.returncode)
