"""Universe file resolution: explicit > $SCREENER_UNIVERSE > generated file in
the output dir > shipped file next to tickers.json."""
import json

import app.momentum_screener as ms


def test_resolution_order(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"; cfg_dir.mkdir()
    out_dir = tmp_path / "out"; out_dir.mkdir()
    cfg = cfg_dir / "tickers.json"; cfg.write_text("{}")
    shipped = cfg_dir / "universe.json"; shipped.write_text(json.dumps({"name": "shipped", "tickers": ["A"]}))
    monkeypatch.setattr(ms, "DEFAULT_UNIVERSE_PATH", "")

    # nothing generated yet -> shipped list
    assert ms.resolve_universe_path(cfg, None, out_dir) == shipped
    assert ms._resolve_universe(cfg, None, out_dir)["name"] == "shipped"

    # generated file in the data volume wins over the shipped one
    gen = out_dir / "universe.json"; gen.write_text(json.dumps({"name": "generated", "tickers": ["A", "B"]}))
    assert ms.resolve_universe_path(cfg, None, out_dir) == gen
    assert ms._resolve_universe(cfg, None, out_dir)["name"] == "generated"

    # env var beats the generated file; explicit path beats everything
    monkeypatch.setattr(ms, "DEFAULT_UNIVERSE_PATH", str(shipped))
    assert ms.resolve_universe_path(cfg, None, out_dir) == shipped
    explicit = tmp_path / "x.json"
    assert ms.resolve_universe_path(cfg, explicit, out_dir) == explicit

    # missing file -> None with a warning, never an exception
    monkeypatch.setattr(ms, "DEFAULT_UNIVERSE_PATH", str(tmp_path / "missing.json"))
    assert ms._resolve_universe(cfg, None, out_dir) is None
