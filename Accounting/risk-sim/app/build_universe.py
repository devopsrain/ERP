"""
Build / refresh the screener universe from the exchange symbol directories.

Why: the shipped config/universe.json is a hand-made list of ~600 companies
that were worth >= $20B. With the market-cap floor now at $1B that list is
far too small — most 1–20B names are simply not in it. This tool rebuilds it:

  1. download the official Nasdaq Trader symbol directories (NASDAQ-listed
     and "other listed" = NYSE / NYSE American / NYSE Arca / Cboe),
  2. drop ETFs, test issues, warrants, units, rights, preferreds, notes,
     funds/trusts and (by default) ADRs, and map symbols to Yahoo notation
     (BRK.B -> BRK-B),
  3. look up each remaining symbol's market cap with yfinance — ONE call per
     symbol, with a courtesy pause, remembered in a resumable cache so a
     rerun only touches new / stale symbols,
  4. write universe.json with every symbol whose cap >= --min-cap.

Run (from risk-sim/; on the server the config dir is mounted read-only in
the job container, so add a writable mount):

  python -m app.build_universe --min-cap 1e9
  docker compose run --rm -v "$PWD/config:/srv/config" correlation-job \
      python -m app.build_universe --min-cap 1e9

First run over ~5,000 symbols takes roughly an hour (pause-limited, not
CPU-limited); later runs reuse the cache (30 days by default). Interrupt
and rerun at any time — progress is saved continuously. The previous
universe.json is kept as universe.json.bak.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger("risk-sim.universe")

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
DEFAULT_MIN_CAP = 1e9
DEFAULT_PAUSE_S = 0.6          # between market-cap lookups (Yahoo courtesy)
DEFAULT_REFRESH_DAYS = 30      # cache validity for a symbol's cap
CACHE_NAME = "universe-caps-cache.json"

# Security-name fragments that mark non-operating-company instruments.
EXCLUDE_NAME_PATTERNS = (
    r"\bETF\b", r"\bETN\b", r"\bFund\b", r"\bWarrant", r"\bRight(s)?\b",
    r"\bUnit(s)?\b", r"\bPreferred\b", r"\bPfd\b", r"\bNotes? due\b",
    r"\bDebenture", r"\bSubordinated\b", r"\bTrust Units?\b",
    r"\bClosed[- ]End\b", r"\bIndex\b", r"\bPortfolio\b", r"\bShares of Beneficial Interest\b",
    r"\bAcquisition Corp", r"\bSPAC\b", r"\bBlank Check\b",
)
ADR_PATTERNS = (r"American Depositary", r"\bADR\b", r"\bADS\b", r"Depositary Shares")
# Other-listed exchange codes: A=NYSE American, N=NYSE, P=NYSE Arca, Z=Cboe BZX, V=IEX
KEEP_EXCHANGES = {"A", "N", "P", "Z", "V", "Q", "G", "S"}  # Q/G/S = Nasdaq tiers


def _fetch_text(url: str, timeout: int = 30) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — fixed public URLs
        return resp.read().decode("utf-8", errors="replace")


def parse_symbol_directory(text: str, source: str) -> list[dict]:
    """Parse a Nasdaq Trader pipe-delimited directory (header row + trailer
    'File Creation Time' row). Returns raw rows as dicts with normalised keys:
    symbol, name, exchange, etf (bool), test (bool), source."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split("|")]
    rows: list[dict] = []
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):
            continue
        parts = ln.split("|")
        if len(parts) != len(header):
            continue
        rec = dict(zip(header, (p.strip() for p in parts)))
        symbol = rec.get("Symbol") or rec.get("ACT Symbol") or rec.get("NASDAQ Symbol") or ""
        rows.append({
            "symbol": symbol,
            "name": rec.get("Security Name", ""),
            "exchange": rec.get("Exchange", "Q" if source == "nasdaq" else ""),
            "etf": rec.get("ETF", "N").upper() == "Y",
            "test": rec.get("Test Issue", "N").upper() == "Y",
            "financial_status": rec.get("Financial Status", ""),
            "source": source,
        })
    return rows


def to_yahoo_symbol(symbol: str) -> str | None:
    """Exchange notation -> Yahoo: class shares 'BRK.B'/'BRK B' -> 'BRK-B'.
    None for preferreds ('$'), warrants/units/rights suffixes, odd tickers."""
    s = symbol.strip().upper()
    if not s or "$" in s or "^" in s or "/" in s or "+" in s or "=" in s:
        return None
    s = s.replace(" ", "-").replace(".", "-")
    if not re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", s):
        return None
    return s


def is_operating_company(row: dict, *, keep_adrs: bool = False) -> bool:
    if row.get("etf") or row.get("test"):
        return False
    if row.get("exchange") and row["exchange"] not in KEEP_EXCHANGES:
        return False
    if row.get("financial_status") and row["financial_status"] not in ("", "N"):
        return False  # delinquent / deficient / bankrupt flags on Nasdaq
    name = row.get("name", "")
    for pat in EXCLUDE_NAME_PATTERNS:
        if re.search(pat, name, flags=re.IGNORECASE):
            return False
    if not keep_adrs:
        for pat in ADR_PATTERNS:
            if re.search(pat, name, flags=re.IGNORECASE):
                return False
    return True


def candidate_symbols(directories: Iterable[tuple[str, str]], *, keep_adrs: bool = False) -> dict[str, str]:
    """{yahoo_symbol: security name} from (text, source) pairs, deduped."""
    out: dict[str, str] = {}
    for text, source in directories:
        for row in parse_symbol_directory(text, source):
            if not is_operating_company(row, keep_adrs=keep_adrs):
                continue
            ys = to_yahoo_symbol(row["symbol"])
            if ys and ys not in out:
                out[ys] = row["name"]
    return out


# ── market-cap cache ─────────────────────────────────────────────

def load_cache(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(path: Path, cache: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cache, indent=1, sort_keys=True))
    os.replace(tmp, path)


def cache_is_fresh(entry: dict | None, today: date, refresh_days: int) -> bool:
    if not entry or "checked" not in entry:
        return False
    try:
        checked = date.fromisoformat(str(entry["checked"])[:10])
    except ValueError:
        return False
    return (today - checked).days < refresh_days


def lookup_caps(symbols: list[str], cache: dict, *, fetch_cap: Callable[[str], float | None],
                cache_path: Path | None = None, pause_s: float = DEFAULT_PAUSE_S,
                refresh_days: int = DEFAULT_REFRESH_DAYS, limit: int | None = None,
                today: date | None = None, sleep=time.sleep) -> dict:
    """Fill `cache` with {symbol: {"cap": float|None, "checked": iso}} for
    every symbol whose entry is missing or stale; saves after every lookup
    so an interrupted run resumes. `limit` caps the number of LIVE lookups."""
    today = today or datetime.now(timezone.utc).date()
    todo = [s for s in symbols if not cache_is_fresh(cache.get(s), today, refresh_days)]
    if limit is not None:
        todo = todo[:limit]
    logger.info("market caps: %d symbols cached, %d to look up", len(symbols) - len(todo), len(todo))
    for i, s in enumerate(todo, 1):
        try:
            cap = fetch_cap(s)
        except Exception:  # noqa: BLE001
            cap = None
        cache[s] = {"cap": float(cap) if cap is not None else None, "checked": today.isoformat()}
        if cache_path is not None and (i % 25 == 0 or i == len(todo)):
            save_cache(cache_path, cache)
        if i % 250 == 0:
            logger.info("market caps: %d/%d looked up", i, len(todo))
        if pause_s and i < len(todo):
            sleep(pause_s)
    return cache


def select_universe(symbols: dict[str, str], cache: dict, min_cap: float) -> list[dict]:
    """Symbols with a known cap >= min_cap, largest first."""
    picked = []
    for s, name in symbols.items():
        cap = (cache.get(s) or {}).get("cap")
        if isinstance(cap, (int, float)) and cap >= min_cap:
            picked.append({"ticker": s, "name": name, "market_cap": float(cap)})
    picked.sort(key=lambda r: (-r["market_cap"], r["ticker"]))
    return picked


def write_universe(path: Path, picked: list[dict], *, min_cap: float, scanned: int,
                   unknown: int, name: str = "us-1b-plus") -> Path:
    if path.is_file():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_bytes(path.read_bytes())
    doc = {
        "name": name,
        "note": (f"Generated by app.build_universe on {date.today().isoformat()}: US-listed "
                 f"(NASDAQ/NYSE/NYSE American/Arca/Cboe) operating companies with Yahoo market cap "
                 f">= ${min_cap/1e9:g}B at generation time ({len(picked)} of {scanned} eligible "
                 f"symbols; {unknown} had no cap on Yahoo). ETFs, funds, trusts, warrants, units, "
                 f"rights, preferreds, notes, SPACs and ADRs excluded. Market caps drift — regenerate "
                 f"monthly; the screener re-checks caps live for finalists anyway."),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_market_cap": min_cap,
        "tickers": [r["ticker"] for r in picked],
        "names": {r["ticker"]: r["name"] for r in picked},
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2))
    os.replace(tmp, path)
    return path


def build(output: Path, *, min_cap: float = DEFAULT_MIN_CAP, cache_path: Path | None = None,
          fetch_text: Callable[[str], str] = _fetch_text,
          fetch_cap: Callable[[str], float | None] | None = None,
          pause_s: float = DEFAULT_PAUSE_S, refresh_days: int = DEFAULT_REFRESH_DAYS,
          limit: int | None = None, keep_adrs: bool = False, dry_run: bool = False,
          sleep=time.sleep) -> dict:
    cache_path = cache_path or output.parent / CACHE_NAME
    if fetch_cap is None:
        from app.momentum_screener import _default_fetch_market_cap as fetch_cap  # lazy: yfinance
    directories = [(fetch_text(NASDAQ_LISTED_URL), "nasdaq"), (fetch_text(OTHER_LISTED_URL), "other")]
    symbols = candidate_symbols(directories, keep_adrs=keep_adrs)
    logger.info("symbol directories: %d eligible operating-company symbols", len(symbols))
    cache = load_cache(cache_path)
    lookup_caps(list(symbols), cache, fetch_cap=fetch_cap, cache_path=cache_path, pause_s=pause_s,
                refresh_days=refresh_days, limit=limit, sleep=sleep)
    save_cache(cache_path, cache)
    picked = select_universe(symbols, cache, min_cap)
    unknown = sum(1 for s in symbols if (cache.get(s) or {}).get("cap") is None)
    pending = sum(1 for s in symbols if s not in cache)
    result = {"eligible": len(symbols), "selected": len(picked), "unknown_cap": unknown,
              "pending_lookups": pending, "min_cap": min_cap, "output": str(output)}
    if dry_run:
        logger.info("dry run: %s", result)
        return result
    write_universe(output, picked, min_cap=min_cap, scanned=len(symbols), unknown=unknown)
    logger.info("wrote %s: %d tickers (>= $%.1fB); %d symbols still without a cached cap",
                output, len(picked), min_cap / 1e9, pending)
    return result


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    from app.daily_correlation import DEFAULT_CONFIG_PATH
    parser = argparse.ArgumentParser(prog="python -m app.build_universe", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-cap", type=float, default=DEFAULT_MIN_CAP,
                        help="market-cap floor in USD (default 1e9)")
    parser.add_argument("--output", default=None,
                        help="universe.json to write (default: next to tickers.json)")
    parser.add_argument("--cache", default=None, help="caps cache file (default: next to the output)")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE_S, help="seconds between Yahoo lookups")
    parser.add_argument("--refresh-days", type=int, default=DEFAULT_REFRESH_DAYS,
                        help="re-check a symbol's cap after this many days")
    parser.add_argument("--limit", type=int, default=None,
                        help="max live lookups this run (resume later; the rest stay cached/pending)")
    parser.add_argument("--keep-adrs", action="store_true", help="include ADR/ADS listings")
    parser.add_argument("--dry-run", action="store_true", help="look up caps but do not write universe.json")
    args = parser.parse_args(argv)
    output = Path(args.output) if args.output else Path(DEFAULT_CONFIG_PATH).parent / "universe.json"
    res = build(output, min_cap=args.min_cap, cache_path=Path(args.cache) if args.cache else None,
                pause_s=args.pause, refresh_days=args.refresh_days, limit=args.limit,
                keep_adrs=args.keep_adrs, dry_run=args.dry_run)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
