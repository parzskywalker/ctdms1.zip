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


def test_caddy_serves_bilingual_down_page_on_proxy_errors():
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert "handle_errors" in caddy and "rewrite * /server_down.html" in caddy
    page = (ROOT / "deploy" / "server_down.html").read_text(encoding="utf-8")
    assert 'http-equiv="refresh" content="10"' in page and 'lang="hi"' in page
