import asyncio
import os
import stat
import sys

from internmatch import __main__ as cli
from internmatch import config
from internmatch.sources import SOURCES, parse_muse


def test_config_save_load_and_env_override(monkeypatch):
    config.save({"usajobs_email": "me@school.edu", "usajobs_api_key": "abcdefgh1234"})
    assert config.get("usajobs_email") == "me@school.edu"
    path = config.config_dir() / "config.json"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    described = config.describe()
    assert described["usajobs_api_key"] == {"configured": True, "source": "settings", "value": "…1234"}
    assert described["usajobs_email"]["value"] == "me@school.edu"
    monkeypatch.setenv("USAJOBS_API_KEY", "from-env")
    assert config.get("usajobs_api_key") == "from-env"
    assert config.source_of("usajobs_api_key") == "environment"
    config.save({"usajobs_email": ""})
    assert config.get("usajobs_email") is None


def test_platform_folders(monkeypatch, tmp_path):
    monkeypatch.delenv("INTERNMATCH_CONFIG_DIR")
    monkeypatch.delenv("INTERNMATCH_CACHE_DIR")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.config_dir() == tmp_path / "Library" / "Application Support" / "internmatch"
    assert config.cache_dir() == tmp_path / "Library" / "Caches" / "internmatch"


def test_cli_config_commands(capsys):
    assert cli.main(["config", "set", "adzuna_app_id", "my-id"]) == 0
    assert config.get("adzuna_app_id") == "my-id"
    assert cli.main(["config"]) == 0
    assert "my-id" in capsys.readouterr().out
    assert cli.main(["config", "unset", "adzuna_app_id"]) == 0
    assert config.get("adzuna_app_id") is None
    assert cli.main(["config", "set", "bogus", "x"]) == 2


def test_cli_doctor(monkeypatch, capsys, rows):
    async def fake_fetch(client, settings):
        return parse_muse({"results": rows}), {"queries": {"Legal Services": {"kept": 3}}}

    monkeypatch.setattr(SOURCES["themuse"], "fetch", fake_fetch)
    assert cli.main(["doctor", "--verbose"]) == 0
    out = capsys.readouterr().out
    assert "Python 3.10 or newer" in out and "The Muse: 15 relevant internships" in out
    assert "USAJOBS: not configured" in out and "Everything looks good" in out

    async def broken(client, settings):
        raise RuntimeError("offline")

    monkeypatch.setattr(SOURCES["themuse"], "fetch", broken)
    assert cli.main(["doctor"]) == 0  # a network problem isn't a setup problem...
    assert cli.main(["doctor", "--strict"]) == 1  # ...unless you ask


def test_cli_analyze_offline(monkeypatch, capsys, rows, tmp_path):
    from .conftest import FIXTURES

    async def fake_fetch(client, settings):
        return parse_muse({"results": rows}), {}

    monkeypatch.setattr(SOURCES["themuse"], "fetch", fake_fetch)
    out_csv = tmp_path / "matches.csv"
    code = cli.main(["analyze", str(FIXTURES / "business_resume.txt"), "--track", "Business", "--no-enrich",
                     "--csv", str(out_csv), "--top", "5", "--explain"])
    assert code == 0
    out = capsys.readouterr().out
    assert "RESUME SCORE" in out and "Riverbend Credit Union" in out
    assert "Finance & Accounting · " in out and "job board's label: Accounting and Finance" in out
    assert out_csv.read_text().startswith("tier,odds,fit,employer")
    asyncio.set_event_loop(asyncio.new_event_loop())  # keep later tests' default loop usable
