"""v1.8 S1 (first slice): cloud deploy settings - small server, backups include uploads, server-down page."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_fits_small_server_and_backs_up_uploads():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "GUNICORN_WORKERS: ${GUNICORN_WORKERS:-3}" in compose
    assert "appdata:/appdata:ro" in compose and "UPLOADS_DIR: /appdata/uploads" in compose
    assert "./deploy/server_down.html:/srv/server_down.html:ro" in compose


def test_backup_script_archives_uploads_with_retention():
    sh = (ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")
    assert 'tar -czf "$BACKUP_DIR/ctdms-uploads-$STAMP.tar.gz"' in sh
    assert "ctdms-uploads-*.tar.gz" in sh  # old upload archives are pruned too


def test_cloud_setup_script_is_valid_and_locks_the_app_port():
    import shutil
    import subprocess

    sh = (ROOT / "deploy" / "SETUP_CLOUD.sh").read_text(encoding="utf-8")
    assert "CTDMS_BIND=127.0.0.1" in sh and "openssl rand -hex 32" in sh and "[ -f .env ]" in sh
    assert "create-admin" in sh  # tells, never creates, the first account
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"${CTDMS_BIND:-0.0.0.0}:${CTDMS_PORT:-8000}:8000"' in compose
    if shutil.which("sh"):
        assert subprocess.run(["sh", "-n", str(ROOT / "deploy" / "SETUP_CLOUD.sh")]).returncode == 0
    ps = (ROOT / "deploy" / "PULL_BACKUP.ps1").read_text(encoding="utf-8")
    assert "BatchMode=yes" in ps and "ctdms-uploads-" in ps  # never prompts for a password; copies photos too


def test_caddy_serves_bilingual_down_page_on_proxy_errors():
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert "handle_errors" in caddy and "rewrite * /server_down.html" in caddy
    page = (ROOT / "deploy" / "server_down.html").read_text(encoding="utf-8")
    assert 'http-equiv="refresh" content="10"' in page and 'lang="hi"' in page


def test_pilot_v18_command_switches_the_settings_on(tmp_path):
    import os
    import subprocess
    import sys

    env = {**os.environ, "CTDMS_DATABASE_URL": f"sqlite:///{(tmp_path / 'p.db').as_posix()}", "PYTHONUTF8": "1"}
    assert subprocess.run([sys.executable, "-m", "app.cli", "init-db"], cwd=ROOT, env=env).returncode == 0
    r = subprocess.run([sys.executable, "-m", "app.cli", "pilot-v18"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "Tulsyan Pharmaceuticals Private Limited" in r.stdout and "rate.approve" in r.stdout
