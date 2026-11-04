"""The depot pilot kit (deploy/pilot_kit): a real database (never demo data) that needs an administrator, a hidden
password prompt for that administrator, and the clean certificate (pilot-verify)."""
from types import SimpleNamespace

import pytest

from app import cli
from app.config import BASE_DIR

KIT = BASE_DIR / "deploy" / "pilot_kit"
PROD = SimpleNamespace(is_production=True, show_demo_logins=False, is_sqlite=True, database_url="sqlite:///x.db")


def test_pilot_ready_passes_with_an_administrator_and_warns_about_demo_passwords(capsys):
    cli.main(["pilot-ready"])
    out = capsys.readouterr().out
    assert "Ready:" in out
    assert "WARNING" in out  # the seeded demo accounts still use the passwords printed in the documentation


def test_pilot_ready_stops_when_there_is_no_administrator(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_pilot_status", lambda db: (0, []))
    with pytest.raises(SystemExit) as stop:
        cli.main(["pilot-ready"])
    assert stop.value.code == 3
    out = capsys.readouterr().out
    assert "create-admin" in out and "SET_ADMIN_PASSWORD.bat" in out


def test_create_admin_asks_for_the_password_on_screen(monkeypatch):
    import getpass

    from fastapi.testclient import TestClient

    from app.main import app
    typed = iter(["short1", "Pilot#Gate77", "Pilot#Gate99",  # too weak; then the two entries differ
                  "Pilot#Gate77", "Pilot#Gate77"])  # then the same twice
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(typed))
    cli.main(["create-admin", "--username", "prompt.admin"])
    r = TestClient(app).post("/api/v1/auth/token", json={"username": "prompt.admin", "password": "Pilot#Gate77"})
    assert r.status_code == 200


def test_create_admin_gives_up_after_three_bad_tries(monkeypatch):
    import getpass
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "weak")
    with pytest.raises(SystemExit) as stop:
        cli.main(["create-admin", "--username", "never.created"])
    assert stop.value.code == 1


def test_pilot_verify_refuses_a_demo_database(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["pilot-verify"])
    out = capsys.readouterr().out
    assert stop.value.code == 1
    assert "STOP" in out and "demo password" in out and "Demo masters" in out


def test_pilot_verify_certifies_a_new_empty_database(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    import app.models  # noqa: F401  (registers every table)
    from app.db import Base
    from app.seed import seed_core
    from app.services import settings_service as cfg
    monkeypatch.setattr(cli, "get_settings", lambda: PROD)
    engine = create_engine(f"sqlite:///{tmp_path / 'new.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_core(db)
        db.commit()
        passed, problems, todo = cli._pilot_check(db, clean=False)
        assert problems == [] and len(todo) == 2  # only the organisation name and address are still to do
        assert any("Work records: 0 invoices, 0 gatepasses, 0 cases" in line for line in passed)
        _, problems, _ = cli._pilot_check(db, clean=True)  # the shipping certificate also wants them replaced
        assert len(problems) == 2 and all("placeholder" in line for line in problems)
        cfg.set_value(db, "org.name", "Tulsyan Pharma Distributors")
        cfg.set_value(db, "org.address", "Guma, Raipur")
        db.commit()
        _, problems, todo = cli._pilot_check(db, clean=True)
        assert problems == [] and todo == []


def test_clean_certificate_refuses_work_records(db, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: PROD)
    _, problems, _ = cli._pilot_check(db, clean=True)  # the seeded demo database has invoices, gatepasses and cases
    assert any("work records" in line for line in problems)
    _, problems, _ = cli._pilot_check(db, clean=False)  # an ordinary start does not mind work records
    assert not any("work records" in line for line in problems)


def test_gate_staff_have_the_gate_check_in_their_menu_and_start_on_it(db):
    from sqlalchemy import select

    from app.models import User
    from app.web.templating import home_for, nav_for
    gate = db.scalar(select(User).where(User.username == "gate.raipur"))
    assert home_for(gate) == "/gatepass/gate"  # was the pending-dispatch list, with no scan box in the menu
    assert ("Gate Check", "/gatepass/gate") in [(label, url) for _g, items in nav_for(gate) for label, url, _i in items]
    operator = db.scalar(select(User).where(User.username == "operator.raipur"))
    assert home_for(operator) == "/"  # everybody else keeps their first screen


def test_kit_launchers_are_a_real_database_start_on_the_network():
    expected = ["START_CTDMS.bat", "STOP_CTDMS.bat", "BACKUP_NOW.bat", "SET_ADMIN_PASSWORD.bat",
                "OPEN_FIREWALL_ONCE.bat", "CHECK_THIS_PC.bat", "CHECK_COPY.bat", "_ctdms_env.bat", "_run_server.bat"]
    for name in expected:
        raw = (KIT / name).read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), name  # batch files need CRLF, also in the zip
        text = raw.decode("ascii")  # plain ASCII: no garbled letters in any Windows console
        assert "prepare-demo" not in text and "seed-demo" not in text, name  # never loads demo data
    start = (KIT / "START_CTDMS.bat").read_text()
    for needed in ("init-db", "pilot-ready", "pilot-verify", "CTDMS_ENVIRONMENT=production", "CTDMS_SHOW_DEMO_LOGINS=false",
                   "SET_ADMIN_PASSWORD.bat"):
        assert needed in start, needed
    assert "--host 0.0.0.0" in (KIT / "_run_server.bat").read_text()
    admin = (KIT / "SET_ADMIN_PASSWORD.bat").read_text()
    assert "create-admin" in admin and "--password" not in admin  # the password is only ever typed on the screen
    assert "PYTHONHOME=" in (KIT / "_ctdms_env.bat").read_text()  # the kit never picks up another Python on the PC
    assert not (BASE_DIR / "start_pilot.bat").exists()  # replaced by the kit


def test_kit_tools_read_a_windows_import_table_and_catch_a_damaged_copy(tmp_path):
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("kit_tools", KIT / "kit_tools.py")
    kit_tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit_tools)
    if sys.platform == "win32":
        names = [n.lower() for n in kit_tools.imported_dlls(__import__("pathlib").Path(sys.executable))]
        assert any(n.startswith(("kernel32", "api-ms-win", "python3")) for n in names)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("print(1)\n")
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "x.bin").write_bytes(b"12345")
    assert kit_tools.manifest(tmp_path) == 0 and kit_tools.verify(tmp_path) == 0
    (tmp_path / "app" / "a.py").write_text("print(2)\n")  # same size, different content
    assert kit_tools.verify(tmp_path) == 1
    (tmp_path / "app" / "a.py").write_text("print(1)\n")
    (tmp_path / "python" / "x.bin").unlink()
    assert kit_tools.verify(tmp_path) == 1
