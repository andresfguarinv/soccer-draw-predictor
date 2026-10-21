#!/usr/bin/env python3
"""
Genera el HTML diario del Soccer Draw Predictor.

Fuente por defecto: ESPN (pública, gratis y sin API key).
Fuente opcional: API-Football (requiere key y plan con acceso a la temporada actual).

Uso:
  python3 generate_daily.py

  python3 generate_daily.py --date 2026-10-20
  python3 generate_daily.py --source api-football
  python3 generate_daily.py --from-json data/2026-10-20.json
  python3 generate_daily.py --dry-run

El script:
  1. Consulta los fixtures del día para las 10 ligas.
  2. Descarta partidos ya iniciados.
  3. Lee cuotas de empate y analiza solo los partidos con cuota cercana al rango.
  4. Calcula los 8 criterios con standings, stats local/visitante, H2H y goleadores.
  5. Inyecta los datos en index.html y guarda una copia con la fecha en pages/YYYY-MM-DD.html.

La cuota final siempre se confirma en Betplay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None  # type: ignore

ROOT = Path(__file__).resolve().parent
BOGOTA = ZoneInfo("America/Bogota") if ZoneInfo else None
BASE_URL = "https://v3.football.api-sports.io"
ESPN_BASE = "https://site.api.espn.com/apis"
CACHE_DIR = ROOT / ".cache" / "api-football"
DATA_DIR = ROOT / "data"

# ---------------------------------------------------------------------------
# Configuración de ligas
# ---------------------------------------------------------------------------
# api_id: ID de API-Football (modo opcional). espn_slug: liga en ESPN (modo por defecto).
# calendar=True para torneos por año calendario.
LEAGUES = [
    {"id": "col",  "api_id": 239, "espn_slug": "col.1", "name": "Primera A",            "country": "Colombia",       "flag": "🇨🇴", "level": "Máxima", "calendar": True},
    {"id": "par",  "api_id": 250, "espn_slug": "par.1", "name": "División Profesional", "country": "Paraguay",       "flag": "🇵🇾", "level": "Máxima", "calendar": True},
    {"id": "itb",  "api_id": 136, "espn_slug": "ita.2", "name": "Serie B",              "country": "Italia",         "flag": "🇮🇹", "level": "Alta",   "calendar": False},
    {"id": "esp2", "api_id": 141, "espn_slug": "esp.2", "name": "Segunda División",     "country": "España",         "flag": "🇪🇸", "level": "Alta",   "calendar": False},
    {"id": "eng2", "api_id": 40,  "espn_slug": "eng.2", "name": "Championship",         "country": "Inglaterra",     "flag": "🇬🇧", "level": "Alta",   "calendar": False},
    {"id": "ger2", "api_id": 79,  "espn_slug": "ger.2", "name": "Bundesliga 2",         "country": "Alemania",       "flag": "🇩🇪", "level": "Media",  "calendar": False},
    {"id": "ita",  "api_id": 135, "espn_slug": "ita.1", "name": "Serie A",              "country": "Italia",         "flag": "🇮🇹", "level": "Media",  "calendar": False},
    {"id": "esp",  "api_id": 140, "espn_slug": "esp.1", "name": "La Liga",              "country": "España",         "flag": "🇪🇸", "level": "Media",  "calendar": False},
    {"id": "fra",  "api_id": 61,  "espn_slug": "fra.1", "name": "Ligue 1",              "country": "Francia",        "flag": "🇫🇷", "level": "Media",  "calendar": False},
    {"id": "usa",  "api_id": 253, "espn_slug": "usa.1", "name": "MLS",                  "country": "Estados Unidos", "flag": "🇺🇸", "level": "Media",  "calendar": True},
]

FINISHED_STATUS = {"FT", "AET", "PEN"}
MATCH_WINNER_NAMES = {"match winner", "1x2", "full time result"}
DRAW_NAMES = {"draw", "x", "empate"}

WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
          "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def build_ssl_context() -> ssl.SSLContext:
    """Contexto SSL con los certificados del sistema (macOS/Linux)."""
    context = ssl.create_default_context()
    candidates = [
        "/etc/ssl/cert.pem",
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                context.load_verify_locations(cafile=candidate)
                break
            except ssl.SSLError:
                continue
    return context


SSL_CONTEXT = build_ssl_context()


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def norm(value: str) -> str:
    """Minúsculas, sin tildes y sin puntuación."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def date_label(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day:02d}/{d.month:02d}/{d.year}"


def season_for(league: dict[str, Any], d: date) -> int:
    if league.get("calendar"):
        return d.year
    # Temporada europea: empieza en julio/agosto.
    return d.year if d.month >= 7 else d.year - 1


def public_league(league: dict[str, Any]) -> dict[str, Any]:
    return {k: league[k] for k in ("id", "name", "country", "flag", "level")}


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_datetime(value: str, default_tz=BOGOTA) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(default_tz)


def parse_now(value: str | None) -> datetime:
    if value:
        return parse_datetime(value)
    return datetime.now(BOGOTA)


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "sin dato"
    return f"{round(value * 100):.0f}%"


def fmt_num(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "sin dato"
    return f"{value:.{decimals}f}"


def as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Cliente API-Football con caché
# ---------------------------------------------------------------------------
class ApiError(RuntimeError):
    pass


class ApiClient:
    def __init__(self, key: str, no_cache: bool = False, verbose: bool = False):
        self.key = key
        self.no_cache = no_cache
        self.verbose = verbose
        self.requests = 0
        self.cache_hits = 0
        if not no_cache:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _ttl(self, path: str) -> int:
        if path.startswith("/odds"):
            return 300
        if path.startswith("/fixtures/headtohead"):
            return 86400
        if path.startswith("/fixtures"):
            return 300
        if path.startswith("/teams/statistics"):
            return 3600
        if path.startswith("/players/topscorers"):
            return 3600
        if path.startswith("/standings"):
            return 3600
        return 3600

    def _cache_file(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return CACHE_DIR / f"{digest}.json"

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{BASE_URL}{path}?{query}"
        cache_file = self._cache_file(url)

        if not self.no_cache and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < self._ttl(path):
                self.cache_hits += 1
                if self.verbose:
                    print(f"[cache] {path} {params}")
                return json.loads(cache_file.read_text(encoding="utf-8"))

        req = Request(
            url,
            headers={
                "x-apisports-key": self.key,
                "Accept": "application/json",
                "User-Agent": "soccer-draw-predictor/1.0",
            },
        )

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urlopen(req, timeout=35, context=SSL_CONTEXT) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                last_error = exc
                if exc.code == 429:
                    wait = 8 * (attempt + 1)
                    if self.verbose:
                        print(f"[429] esperando {wait}s...")
                    time.sleep(wait)
                    continue
                if attempt == 2:
                    raise ApiError(f"HTTP {exc.code} en {path}") from exc
                time.sleep(2 * (attempt + 1))
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == 2:
                    raise ApiError(f"Error de red en {path}: {exc}") from exc
                time.sleep(2 * (attempt + 1))
        else:
            raise ApiError(f"Error de red en {path}: {last_error}")

        errors = payload.get("errors") or {}
        if errors:
            raise ApiError(f"API-Football {path}: {errors}")

        self.requests += 1
        if self.verbose:
            print(f"[api] {path} {params} -> {payload.get('results', '?')} resultados")

        if not self.no_cache:
            cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        # Ser amable con el límite de peticiones.
        time.sleep(0.25)
        return payload


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def fetch_fixtures(client: ApiClient, league: dict[str, Any], season: int, d: date) -> list[dict[str, Any]]:
    payload = client.get("/fixtures", {
        "league": league["api_id"],
        "season": season,
        "date": d.isoformat(),
        "timezone": "America/Bogota",
    })
    return payload.get("response") or []


def parse_odds_response(payload: dict[str, Any]) -> dict[int, list[tuple[str, float]]]:
    """Devuelve {fixture_id: [(casa, cuota_empate), ...]}."""
    result: dict[int, dict[str, float]] = defaultdict(dict)
    for item in payload.get("response") or []:
        fixture_id = (item.get("fixture") or {}).get("id")
        if not fixture_id:
            continue
        for bookmaker in item.get("bookmakers") or []:
            book_name = bookmaker.get("name") or "Casa"
            for bet in bookmaker.get("bets") or []:
                if norm(bet.get("name", "")) not in MATCH_WINNER_NAMES:
                    continue
                for value in bet.get("values") or []:
                    if norm(value.get("value", "")) not in DRAW_NAMES:
                        continue
                    try:
                        odd = float(value.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if odd <= 1:
                        continue
                    current = result[fixture_id].get(book_name)
                    if current is None or odd > current:
                        result[fixture_id][book_name] = odd
    return {
        fixture_id: sorted(books.items(), key=lambda item: item[1], reverse=True)
        for fixture_id, books in result.items()
    }


def fetch_league_odds(client: ApiClient, league: dict[str, Any], season: int, d: date) -> dict[int, list[tuple[str, float]]]:
    payload = client.get("/odds", {
        "league": league["api_id"],
        "season": season,
        "date": d.isoformat(),
    })
    return parse_odds_response(payload)


def fetch_standings(client: ApiClient, league: dict[str, Any], season: int) -> dict[int, dict[str, Any]]:
    payload = client.get("/standings", {
        "league": league["api_id"],
        "season": season,
    })
    response = payload.get("response") or []
    if not response:
        return {}
    standings_groups = (response[0].get("league") or {}).get("standings") or []
    table: dict[int, dict[str, Any]] = {}
    for group in standings_groups:
        group_size = len(group)
        for row in group:
            team = row.get("team") or {}
            team_id = team.get("id")
            if not team_id:
                continue
            all_stats = row.get("all") or {}
            goals = all_stats.get("goals") or {}
            table[team_id] = {
                "name": team.get("name") or "",
                "rank": row.get("rank"),
                "points": row.get("points"),
                "played": all_stats.get("played"),
                "draws": all_stats.get("draw"),
                "goals_for": goals.get("for"),
                "goals_against": goals.get("against"),
                "group": row.get("group") or "",
                "group_size": group_size,
            }
    return table


def fetch_team_stats(client: ApiClient, league: dict[str, Any], season: int, team_id: int) -> dict[str, Any]:
    payload = client.get("/teams/statistics", {
        "league": league["api_id"],
        "season": season,
        "team": team_id,
    })
    response = payload.get("response") or {}
    fixtures = response.get("fixtures") or {}
    goals = response.get("goals") or {}
    played = fixtures.get("played") or {}
    draws = fixtures.get("draws") or {}
    for_total = (goals.get("for") or {}).get("total") or {}
    against_total = (goals.get("against") or {}).get("total") or {}

    played_total = played.get("total") or 0
    gf_total = for_total.get("total")
    ga_total = against_total.get("total")
    avg_total = None
    if played_total and gf_total is not None and ga_total is not None:
        avg_total = (gf_total + ga_total) / played_total

    played_home = played.get("home") or 0
    played_away = played.get("away") or 0
    draws_home = draws.get("home") or 0
    draws_away = draws.get("away") or 0

    return {
        "played_total": played_total,
        "played_home": played_home,
        "played_away": played_away,
        "draws_home": draws_home,
        "draws_away": draws_away,
        "home_draw_rate": (draws_home / played_home) if played_home else None,
        "away_draw_rate": (draws_away / played_away) if played_away else None,
        "avg_total_goals": avg_total,
    }


def fetch_h2h(client: ApiClient, home_id: int, away_id: int, kickoff_ts: float) -> tuple[int | None, int]:
    payload = client.get("/fixtures/headtohead", {
        "h2h": f"{home_id}-{away_id}",
        "last": 10,
    })
    finished = []
    for item in payload.get("response") or []:
        fixture = item.get("fixture") or {}
        status = (fixture.get("status") or {}).get("short")
        ts = fixture.get("timestamp")
        if status in FINISHED_STATUS and isinstance(ts, (int, float)) and ts < kickoff_ts:
            finished.append(item)

    finished.sort(key=lambda item: item["fixture"]["timestamp"], reverse=True)
    last_five = finished[:5]
    if len(last_five) < 3:
        return None, len(last_five)

    draws = 0
    for item in last_five:
        goals = item.get("goals") or {}
        if goals.get("home") is not None and goals.get("home") == goals.get("away"):
            draws += 1
    return draws, len(last_five)


def fetch_top_scorers(client: ApiClient, league: dict[str, Any], season: int) -> tuple[dict[int, int], bool]:
    payload = client.get("/players/topscorers", {
        "league": league["api_id"],
        "season": season,
    })
    response = payload.get("response") or []
    if not response:
        return {}, False

    max_goals: dict[int, int] = defaultdict(int)
    for item in response:
        for stat in item.get("statistics") or []:
            team_id = (stat.get("team") or {}).get("id")
            goals = ((stat.get("goals") or {}).get("total"))
            if team_id and isinstance(goals, (int, float)):
                max_goals[int(team_id)] = max(max_goals[int(team_id)], int(goals))
    return dict(max_goals), True


# ---------------------------------------------------------------------------
# ESPN (gratis, sin API key)
# ---------------------------------------------------------------------------
class EspnError(RuntimeError):
    pass


class EspnClient:
    """Cliente para los endpoints públicos de ESPN (sin key, sin registro)."""

    def __init__(self, no_cache: bool = False, verbose: bool = False):
        self.no_cache = no_cache
        self.verbose = verbose
        self.requests = 0
        self.cache_hits = 0
        self.cache_dir = ROOT / ".cache" / "espn"
        if not no_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _ttl(self, url: str) -> int:
        if "/scoreboard" in url:
            return 300
        if "/statistics" in url:
            return 3600
        if "/standings" in url:
            return 3600
        if "/schedule" in url:
            return 21600
        return 3600

    def _cache_file(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def get(self, url: str) -> dict[str, Any]:
        cache_file = self._cache_file(url)
        if not self.no_cache and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < self._ttl(url):
                self.cache_hits += 1
                if self.verbose:
                    print(f"[espn cache] {url}")
                return json.loads(cache_file.read_text(encoding="utf-8"))

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "soccer-draw-predictor/1.0",
            },
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                last_error = exc
                if exc.code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                if attempt == 2:
                    raise EspnError(f"HTTP {exc.code} en {url}") from exc
                time.sleep(1.5 * (attempt + 1))
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == 2:
                    raise EspnError(f"Error de red en {url}: {exc}") from exc
                time.sleep(1.5 * (attempt + 1))
        else:
            raise EspnError(f"Error de red en {url}: {last_error}")

        self.requests += 1
        if self.verbose:
            print(f"[espn] {url}")
        if not self.no_cache:
            cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.2)
        return payload


def american_to_decimal(value: Any) -> float | None:
    """Convierte una cuota americana (+195, -110) a decimal (2.95, 1.91)."""
    if isinstance(value, str):
        value = value.strip().replace("+", "")
    try:
        american = float(value)
    except (TypeError, ValueError):
        return None
    if american > 0:
        return 1 + american / 100
    if american < 0:
        return 1 + 100 / abs(american)
    return None


def parse_espn_odds(competition: dict[str, Any]) -> list[tuple[str, float]]:
    odds: list[tuple[str, float]] = []
    for item in competition.get("odds") or []:
        if not isinstance(item, dict):
            continue
        draw = (item.get("drawOdds") or {}).get("moneyLine")
        decimal = american_to_decimal(draw)
        if decimal is None:
            continue
        provider = (item.get("provider") or {}).get("name") or "ESPN"
        odds.append((provider, round(decimal, 2)))
    return odds


def clean_team_name(name: str) -> str:
    name = (name or "").strip()
    for suffix in (" FC", " CF", " SC", " CEIF", " CD", " CA"):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return name


def espn_score_value(competitor: dict[str, Any]) -> float | None:
    score = competitor.get("score")
    if isinstance(score, dict):
        value = score.get("value")
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(score.get("displayValue"))
        except (TypeError, ValueError):
            return None
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def fetch_espn_scoreboard(client: EspnClient, league: dict[str, Any], d: date) -> dict[str, Any]:
    url = (
        f"{ESPN_BASE}/site/v2/sports/soccer/{league['espn_slug']}/scoreboard"
        f"?dates={d.strftime('%Y%m%d')}"
    )
    return client.get(url)


def parse_espn_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    leagues = payload.get("leagues") or []
    season_type_name = ""
    if leagues:
        season_type_name = ((leagues[0].get("season") or {}).get("type") or {}).get("name") or ""

    parsed: list[dict[str, Any]] = []
    for event in payload.get("events") or []:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]
        status = (
            (event.get("status") or {}).get("type")
            or (competition.get("status") or {}).get("type")
            or {}
        )
        detail = str(status.get("detail") or status.get("description") or "").lower()
        if any(word in detail for word in ("cancel", "postpon", "suspend", "abandon")):
            continue
        competitors = competition.get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        kickoff_raw = event.get("date") or competition.get("date")
        if not kickoff_raw:
            continue
        try:
            kickoff = parse_datetime(kickoff_raw)
        except ValueError:
            continue
        parsed.append({
            "event_id": event.get("id") or competition.get("id"),
            "kickoff": kickoff,
            "home_id": str((home.get("team") or {}).get("id") or ""),
            "away_id": str((away.get("team") or {}).get("id") or ""),
            "home_name": clean_team_name((home.get("team") or {}).get("displayName") or "Local"),
            "away_name": clean_team_name((away.get("team") or {}).get("displayName") or "Visitante"),
            "state": status.get("state") or "",
            "odds": parse_espn_odds(competition),
            "season_type_name": season_type_name,
        })
    return parsed


def fetch_espn_standings(
    client: EspnClient,
    league: dict[str, Any],
    season: int,
    season_type_name: str,
) -> dict[str, dict[str, Any]]:
    url = (
        f"{ESPN_BASE}/v2/sports/soccer/{league['espn_slug']}/standings"
        f"?season={season}"
    )
    payload = client.get(url)
    children = payload.get("children") or []
    if not children and payload.get("standings"):
        children = [{"name": season_type_name, "standings": payload.get("standings")}]

    matching = [
        child for child in children
        if (child.get("name") or "") == season_type_name
        or (child.get("abbreviation") or "") == season_type_name
    ]
    groups = matching or children

    table: dict[str, dict[str, Any]] = {}
    for group in groups:
        entries = ((group.get("standings") or {}).get("entries") or [])
        group_size = len(entries)
        for rank, entry in enumerate(entries, start=1):
            team = entry.get("team") or {}
            team_id = team.get("id")
            if not team_id:
                continue
            stats = {
                stat.get("name"): stat.get("value")
                for stat in entry.get("stats") or []
                if stat.get("name")
            }
            table[str(team_id)] = {
                "name": team.get("displayName") or "",
                "rank": rank,
                "points": as_int(stats.get("points")),
                "played": as_int(stats.get("gamesPlayed")),
                "draws": as_int(stats.get("ties")),
                "goals_for": as_int(stats.get("pointsFor")),
                "goals_against": as_int(stats.get("pointsAgainst")),
                "group": group.get("name") or "",
                "group_size": group_size,
            }
    return table


def fetch_espn_schedule(
    client: EspnClient,
    league: dict[str, Any],
    team_id: str,
    season: int,
) -> list[dict[str, Any]]:
    url = (
        f"{ESPN_BASE}/site/v2/sports/soccer/{league['espn_slug']}"
        f"/teams/{team_id}/schedule?season={season}"
    )
    payload = client.get(url)
    return payload.get("events") or []


def espn_schedule_stats(
    events: list[dict[str, Any]],
    team_id: str,
    season_type_name: str,
    before_ts: float,
) -> dict[str, Any]:
    played_home = played_away = 0
    draws_home = draws_away = 0
    goals_for = goals_against = 0.0
    total = 0

    for event in events:
        season_type = (event.get("seasonType") or {}).get("name") or ""
        if season_type_name and season_type and season_type != season_type_name:
            continue
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]
        state = ((competition.get("status") or {}).get("type") or {}).get("state")
        if state != "post":
            continue
        try:
            event_ts = parse_datetime(event.get("date")).timestamp()
        except (TypeError, ValueError):
            continue
        if event_ts >= before_ts:
            continue

        competitors = competition.get("competitors") or []
        me = next(
            (c for c in competitors if str((c.get("team") or {}).get("id") or "") == team_id),
            None,
        )
        opponent = next(
            (c for c in competitors if str((c.get("team") or {}).get("id") or "") != team_id),
            None,
        )
        if not me or not opponent:
            continue
        my_score = espn_score_value(me)
        rival_score = espn_score_value(opponent)
        if my_score is None or rival_score is None:
            continue

        total += 1
        goals_for += my_score
        goals_against += rival_score
        if me.get("homeAway") == "home":
            played_home += 1
        else:
            played_away += 1
        if my_score == rival_score:
            if me.get("homeAway") == "home":
                draws_home += 1
            else:
                draws_away += 1

    return {
        "played_total": total,
        "played_home": played_home,
        "played_away": played_away,
        "draws_home": draws_home,
        "draws_away": draws_away,
        "home_draw_rate": (draws_home / played_home) if played_home else None,
        "away_draw_rate": (draws_away / played_away) if played_away else None,
        "avg_total_goals": ((goals_for + goals_against) / total) if total else None,
    }


def espn_h2h(
    events: list[dict[str, Any]],
    home_id: str,
    away_id: str,
    before_ts: float,
) -> tuple[int | None, int]:
    matches: list[tuple[float, float, float]] = []
    seen: set[str] = set()

    for event in events:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]
        state = ((competition.get("status") or {}).get("type") or {}).get("state")
        if state != "post":
            continue
        try:
            event_ts = parse_datetime(event.get("date")).timestamp()
        except (TypeError, ValueError):
            continue
        if event_ts >= before_ts:
            continue
        competitors = competition.get("competitors") or []
        ids = {str((c.get("team") or {}).get("id") or "") for c in competitors}
        if home_id not in ids or away_id not in ids:
            continue
        event_id = str(event.get("id") or "")
        if event_id in seen:
            continue
        seen.add(event_id)

        scores: dict[str, float | None] = {}
        for competitor in competitors:
            team_id = str((competitor.get("team") or {}).get("id") or "")
            scores[team_id] = espn_score_value(competitor)
        home_score = scores.get(home_id)
        away_score = scores.get(away_id)
        if home_score is None or away_score is None:
            continue
        matches.append((event_ts, home_score, away_score))

    matches.sort(key=lambda item: item[0], reverse=True)
    last_five = matches[:5]
    if len(last_five) < 3:
        return None, len(last_five)
    draws = sum(1 for _, home_score, away_score in last_five if home_score == away_score)
    return draws, len(last_five)


def fetch_espn_scorers(
    client: EspnClient,
    league: dict[str, Any],
    season: int,
) -> tuple[dict[str, int], bool]:
    url = (
        f"{ESPN_BASE}/site/v2/sports/soccer/{league['espn_slug']}/statistics"
        f"?season={season}"
    )
    payload = client.get(url)
    leaders: list[dict[str, Any]] = []
    for stat in payload.get("stats") or []:
        if stat.get("name") == "goalsLeaders":
            leaders = stat.get("leaders") or []
            break
    if not leaders:
        return {}, False

    max_goals: dict[str, int] = defaultdict(int)
    for leader in leaders:
        athlete = leader.get("athlete") or {}
        team = athlete.get("team") or {}
        team_id = team.get("id")
        goals = leader.get("value")
        if team_id and isinstance(goals, (int, float)):
            key = str(team_id)
            max_goals[key] = max(max_goals[key], int(goals))
    return dict(max_goals), True


def build_data_from_espn(
    client: EspnClient,
    target_date: date,
    now: datetime,
    include_started: bool,
) -> dict[str, Any]:
    warnings: list[str] = []
    matches: list[dict[str, Any]] = []
    league_order = {league["id"]: index for index, league in enumerate(LEAGUES)}
    season = None

    for league in LEAGUES:
        season = season_for(league, target_date)
        try:
            payload = fetch_espn_scoreboard(client, league, target_date)
        except EspnError as exc:
            warnings.append(f"{league['name']}: no se pudo leer el scoreboard ({exc})")
            continue

        events = parse_espn_events(payload)
        if not events:
            continue

        standings: dict[str, dict[str, Any]] = {}
        standings_loaded = False
        scorers: dict[str, int] = {}
        scorers_ok = False
        scorers_loaded = False
        schedule_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}

        def get_schedule(team_id: str, year: int) -> list[dict[str, Any]]:
            key = (team_id, year)
            if key not in schedule_cache:
                try:
                    schedule_cache[key] = fetch_espn_schedule(client, league, team_id, year)
                except EspnError as exc:
                    warnings.append(
                        f"{league['name']}: sin calendario de {team_id} ({year}) ({exc})"
                    )
                    schedule_cache[key] = []
            return schedule_cache[key]

        for event in events:
            kickoff = event["kickoff"]
            # El HTML es de un solo día en America/Bogota. No mostrar días siguientes.
            if kickoff.astimezone(BOGOTA).date() != target_date:
                continue
            if not include_started and kickoff.timestamp() <= now.timestamp():
                continue

            fake_item = {
                "fixture": {
                    "id": event["event_id"],
                    "date": kickoff.isoformat(),
                    "timestamp": kickoff.timestamp(),
                    "status": {"short": "NS"},
                },
                "teams": {
                    "home": {"id": event["home_id"], "name": event["home_name"]},
                    "away": {"id": event["away_id"], "name": event["away_name"]},
                },
            }
            odds_pairs = event["odds"]
            basic = build_match_basic(fake_item, league, kickoff, odds_pairs)
            if basic["status"] != "__analyze__":
                matches.append(basic)
                continue

            season_type_name = event.get("season_type_name") or ""
            home_id = event["home_id"]
            away_id = event["away_id"]

            if not standings_loaded:
                try:
                    standings = fetch_espn_standings(
                        client, league, season, season_type_name
                    )
                except EspnError as exc:
                    warnings.append(f"{league['name']}: sin standings ({exc})")
                    standings = {}
                standings_loaded = True

            if not scorers_loaded:
                try:
                    scorers, scorers_ok = fetch_espn_scorers(client, league, season)
                except EspnError as exc:
                    warnings.append(f"{league['name']}: sin goleadores ({exc})")
                    scorers, scorers_ok = {}, False
                scorers_loaded = True

            home_schedule = get_schedule(home_id, season)
            away_schedule = get_schedule(away_id, season)
            home_stats = espn_schedule_stats(
                home_schedule, home_id, season_type_name, kickoff.timestamp()
            )
            away_stats = espn_schedule_stats(
                away_schedule, away_id, season_type_name, kickoff.timestamp()
            )

            h2h_events = list(home_schedule)
            for year in (season - 1, season - 2):
                h2h_events.extend(get_schedule(home_id, year))
            h2h_draws, h2h_finished = espn_h2h(
                h2h_events, home_id, away_id, kickoff.timestamp()
            )

            analyzed = analyze_fixture(
                item=fake_item,
                league=league,
                odds=odds_pairs,
                standings=standings,
                home_stats=home_stats,
                away_stats=away_stats,
                h2h_draws=h2h_draws,
                h2h_finished=h2h_finished,
                scorers=scorers,
                scorers_ok=scorers_ok,
                kickoff=kickoff,
            )
            matches.append(analyzed)

    matches.sort(key=lambda match: (league_order.get(match["league"], 99), match["kickoff"]))

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for match in matches:
        if match["status"] == "candidate":
            grouped[(match["league"], match["time"])].append(match)
    for group in grouped.values():
        if len(group) <= 1:
            continue
        group.sort(key=lambda match: (-(match.get("score") or 0), match.get("fixture_id") or 0))
        keep = group[0]
        for other in group[1:]:
            other["status"] = "rejected"
            other["note"] = (
                f"Misma hora que {keep['home']} vs {keep['away']}; "
                f"se prioriza el de mayor puntaje. " + other.get("note", "")
            ).strip()

    return {
        "date": target_date.isoformat(),
        "date_label": date_label(target_date),
        "generated_at": now.isoformat(),
        "cutoff": now.strftime("%H:%M"),
        "source": "ESPN gratis (sin API key) + revisión manual",
        "leagues": [public_league(league) for league in LEAGUES],
        "matches": matches,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------
def pick_display_odds(odds: list[tuple[str, float]]) -> list[list[Any]]:
    if not odds:
        return []
    best_by_book: dict[str, float] = {}
    for book, odd in odds:
        best_by_book[book] = max(best_by_book.get(book, 0), odd)
    pairs = sorted(best_by_book.items(), key=lambda item: item[1], reverse=True)
    valid = [pair for pair in pairs if 2.60 <= pair[1] <= 3.20]
    if valid:
        chosen = valid[:3]
        if len(chosen) < 3:
            chosen += [pair for pair in pairs if pair not in chosen][:3 - len(chosen)]
        return [[book, round(odd, 2)] for book, odd in chosen]
    pairs.sort(key=lambda pair: min(abs(pair[1] - 2.60), abs(pair[1] - 3.20)))
    return [[book, round(odd, 2)] for book, odd in pairs[:2]]


def analyze_fixture(
    item: dict[str, Any],
    league: dict[str, Any],
    odds: list[tuple[str, float]],
    standings: dict[int, dict[str, Any]],
    home_stats: dict[str, Any],
    away_stats: dict[str, Any],
    h2h_draws: int | None,
    h2h_finished: int,
    scorers: dict[int, int],
    scorers_ok: bool,
    kickoff: datetime,
) -> dict[str, Any]:
    fixture = item.get("fixture") or {}
    teams = item.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_id = home.get("id")
    away_id = away.get("id")
    home_name = home.get("name") or "Local"
    away_name = away.get("name") or "Visitante"

    home_row = standings.get(home_id, {}) if home_id else {}
    away_row = standings.get(away_id, {}) if away_id else {}
    home_pos = as_int(home_row.get("rank"))
    away_pos = as_int(away_row.get("rank"))
    home_pts = as_int(home_row.get("points"))
    away_pts = as_int(away_row.get("points"))
    group_size = as_int(home_row.get("group_size") or away_row.get("group_size"))

    criteria: list[list[Any]] = []
    alerts: list[str] = []

    # 1. Zona media
    if home_pos and away_pos and group_size:
        upper = 15 if group_size >= 18 else max(6, group_size - 2)
        zone_ok = 6 <= home_pos <= upper and 6 <= away_pos <= upper
        criteria.append([zone_ok, "Zona media (6-15)", f"{home_pos}° y {away_pos}°"])
    else:
        criteria.append([None, "Zona media (6-15)", "sin dato"])

    # 2. Diferencia de puntos
    if home_pts is not None and away_pts is not None:
        diff_ok = abs(home_pts - away_pts) <= 5
        criteria.append([diff_ok, "Diferencia de puntos ≤ 5", f"{home_pts} vs {away_pts}"])
    else:
        criteria.append([None, "Diferencia de puntos ≤ 5", "sin dato"])

    # 3. Empates del local en casa
    home_rate = home_stats.get("home_draw_rate")
    if home_rate is not None:
        criteria.append([
            home_rate >= 0.25,
            "Empates del local en casa ≥ 25%",
            f"{home_stats.get('draws_home', 0)}/{home_stats.get('played_home', 0)} = {fmt_pct(home_rate)}",
        ])
    else:
        criteria.append([None, "Empates del local en casa ≥ 25%", "sin dato"])

    # 4. Empates del visitante fuera
    away_rate = away_stats.get("away_draw_rate")
    if away_rate is not None:
        criteria.append([
            away_rate >= 0.25,
            "Empates del visitante fuera ≥ 25%",
            f"{away_stats.get('draws_away', 0)}/{away_stats.get('played_away', 0)} = {fmt_pct(away_rate)}",
        ])
    else:
        criteria.append([None, "Empates del visitante fuera ≥ 25%", "sin dato"])

    # 5. Promedio de goles
    home_goals = home_stats.get("avg_total_goals")
    away_goals = away_stats.get("avg_total_goals")
    if home_goals is not None and away_goals is not None:
        avg_goals = (home_goals + away_goals) / 2
        criteria.append([
            avg_goals <= 2.5,
            "Promedio de goles ≤ 2.5",
            f"{home_goals:.2f} y {away_goals:.2f} (prom. {avg_goals:.2f})",
        ])
    else:
        avg_goals = None
        criteria.append([None, "Promedio de goles ≤ 2.5", "sin dato"])

    # 6. H2H
    if h2h_draws is not None:
        criteria.append([
            h2h_draws >= 2,
            "H2H: ≥ 2 empates últimos 5",
            f"{h2h_draws} empates en {h2h_finished}",
        ])
    else:
        criteria.append([None, "H2H: ≥ 2 empates últimos 5", "sin dato"])

    # 7. Goleador en forma (proxy: ≥ 8 goles en la temporada)
    if scorers_ok:
        home_scorer = scorers.get(home_id, 0) if home_id else 0
        away_scorer = scorers.get(away_id, 0) if away_id else 0
        criteria.append([
            home_scorer < 8 and away_scorer < 8,
            "Sin goleador en forma (≥ 8 goles)",
            f"máx. {home_scorer} y {away_scorer} goles",
        ])
    else:
        criteria.append([None, "Sin goleador en forma (≥ 8 goles)", "sin dato"])

    # 8. Sin presión extrema
    if home_pos and away_pos and group_size:
        pressure_ok = (
            not (home_pos <= 3 or home_pos > group_size - 3)
            and not (away_pos <= 3 or away_pos > group_size - 3)
        )
        detail = f"{home_pos}° y {away_pos}°"
        if home_pos > group_size - 3 or away_pos > group_size - 3:
            detail += " · descenso"
        elif home_pos <= 3 or away_pos <= 3:
            detail += " · título"
        criteria.append([pressure_ok, "Sin presión extrema", detail])
    else:
        criteria.append([None, "Sin presión extrema", "sin dato"])

    score = sum(1 for ok, _, _ in criteria if ok is True)
    missing = [label for ok, label, _ in criteria if ok is None]
    failed = [label for ok, label, _ in criteria if ok is False]

    if failed:
        alerts.append("No cumple: " + ", ".join(failed) + ".")
    if missing:
        alerts.append("Sin verificar: " + ", ".join(missing) + ".")
    if home_pos and group_size and home_pos > group_size - 3:
        alerts.append("Ajuste potencial -2: local en zona de descenso; revisar simetría.")
    if (home_stats.get("played_total") or 0) < 5 or (away_stats.get("played_total") or 0) < 5:
        alerts.append("Ajuste potencial -1: pocos partidos jugados; tabla poco confiable.")

    # Nota compacta con los datos clave.
    pos_text = f"{home_name} {home_pos}°" if home_pos else home_name
    away_pos_text = f"{away_name} {away_pos}°" if away_pos else away_name
    pts_text = f" ({home_pts} pts)" if home_pts is not None else ""
    away_pts_text = f" ({away_pts} pts)" if away_pts is not None else ""
    draw_text = f"Empates {fmt_pct(home_rate)} casa / {fmt_pct(away_rate)} fuera."
    goals_text = f"Goles {avg_goals:.2f}." if avg_goals is not None else "Goles sin dato."
    h2h_text = f"H2H {h2h_draws}/{h2h_finished}." if h2h_draws is not None else "H2H sin dato."
    note = f"{pos_text}{pts_text} y {away_pos_text}{away_pts_text}. {draw_text} {goals_text} {h2h_text}"

    valid_odds = [odd for _, odd in odds if 2.60 <= odd <= 3.20]
    best_odd = max((odd for _, odd in odds), default=None)
    display_odds = pick_display_odds(odds)

    prefixes: list[str] = []
    if not odds:
        status = "pending"
        note = "Sin cuota de empate disponible. Revisar Betplay antes de considerar el partido."
    elif valid_odds:
        status = "candidate" if score >= 5 else "rejected"
        if score < 5:
            prefixes.append(f"No alcanza 5/8 ({score}/8).")
    else:
        status = "rejected"
        if best_odd is not None:
            prefixes.append(f"Cuota {best_odd:.2f} fuera de 2.60–3.20.")
        if score < 5:
            prefixes.append(f"Score {score}/8.")

    if prefixes:
        note = " ".join(prefixes) + " " + note

    return {
        "league": league["id"],
        "fixture_id": fixture.get("id"),
        "kickoff": kickoff.isoformat(),
        "time": kickoff.strftime("%H:%M"),
        "home": home_name,
        "away": away_name,
        "status": status,
        "score": score,
        "odds": display_odds,
        "criteria": criteria,
        "note": note.strip(),
        "alerts": " ".join(alerts).strip(),
    }


def build_match_basic(
    item: dict[str, Any],
    league: dict[str, Any],
    kickoff: datetime,
    odds_pairs: list[tuple[str, float]],
) -> dict[str, Any]:
    teams = item.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_name = home.get("name") or "Local"
    away_name = away.get("name") or "Visitante"
    display_odds = pick_display_odds(odds_pairs)

    if not odds_pairs:
        return {
            "league": league["id"],
            "fixture_id": (item.get("fixture") or {}).get("id"),
            "kickoff": kickoff.isoformat(),
            "time": kickoff.strftime("%H:%M"),
            "home": home_name,
            "away": away_name,
            "status": "pending",
            "score": None,
            "odds": [],
            "criteria": [],
            "note": "Sin cuota de empate disponible. Revisar Betplay antes de considerar el partido.",
            "alerts": "",
        }

    valid = [odd for _, odd in odds_pairs if 2.60 <= odd <= 3.20]
    near = [odd for _, odd in odds_pairs if 2.40 <= odd <= 3.40]
    if not valid and not near:
        best = max(odd for _, odd in odds_pairs)
        return {
            "league": league["id"],
            "fixture_id": (item.get("fixture") or {}).get("id"),
            "kickoff": kickoff.isoformat(),
            "time": kickoff.strftime("%H:%M"),
            "home": home_name,
            "away": away_name,
            "status": "rejected",
            "score": None,
            "odds": display_odds,
            "criteria": [],
            "note": f"Cuota {best:.2f} fuera de 2.60–3.20; no se analiza.",
            "alerts": "",
        }

    # Señal para que el llamador sepa que hay que pedir detalles.
    return {
        "league": league["id"],
        "fixture_id": (item.get("fixture") or {}).get("id"),
        "kickoff": kickoff.isoformat(),
        "time": kickoff.strftime("%H:%M"),
        "home": home_name,
        "away": away_name,
        "status": "__analyze__",
        "score": None,
        "odds": display_odds,
        "criteria": [],
        "note": "",
        "alerts": "",
    }


# ---------------------------------------------------------------------------
# Construcción del día
# ---------------------------------------------------------------------------
def build_data_from_api(client: ApiClient, target_date: date, now: datetime, include_started: bool) -> dict[str, Any]:
    warnings: list[str] = []
    matches: list[dict[str, Any]] = []
    league_order = {league["id"]: index for index, league in enumerate(LEAGUES)}

    for league in LEAGUES:
        season = season_for(league, target_date)
        try:
            fixtures = fetch_fixtures(client, league, season, target_date)
        except ApiError as exc:
            message = str(exc)
            if any(word in message.lower() for word in ("token", "key", "subscription")):
                raise
            warnings.append(f"{league['name']}: no se pudieron obtener fixtures ({message})")
            continue

        if not fixtures:
            continue

        try:
            odds_by_fixture = fetch_league_odds(client, league, season, target_date)
        except ApiError as exc:
            warnings.append(f"{league['name']}: sin cuotas ({exc})")
            odds_by_fixture = {}

        standings: dict[int, dict[str, Any]] = {}
        scorers: dict[int, int] = {}
        scorers_ok = False
        standings_loaded = False
        scorers_loaded = False
        stats_cache: dict[int, dict[str, Any]] = {}
        h2h_cache: dict[tuple[int, int], tuple[int | None, int]] = {}

        for item in fixtures:
            fixture = item.get("fixture") or {}
            status = (fixture.get("status") or {}).get("short")
            if status != "NS":
                continue

            timestamp = fixture.get("timestamp")
            kickoff_raw = fixture.get("date")
            if not kickoff_raw:
                continue
            try:
                kickoff = parse_datetime(kickoff_raw)
            except ValueError:
                continue

            if not include_started:
                if isinstance(timestamp, (int, float)) and timestamp <= now.timestamp():
                    continue
                if kickoff <= now:
                    continue

            fixture_id = fixture.get("id")
            odds_pairs = odds_by_fixture.get(fixture_id, [])
            basic = build_match_basic(item, league, kickoff, odds_pairs)

            if basic["status"] != "__analyze__":
                matches.append(basic)
                continue

            teams = item.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            home_id = home.get("id")
            away_id = away.get("id")

            if not standings_loaded:
                try:
                    standings = fetch_standings(client, league, season)
                except ApiError as exc:
                    warnings.append(f"{league['name']}: sin standings ({exc})")
                    standings = {}
                standings_loaded = True

            if not scorers_loaded:
                try:
                    scorers, scorers_ok = fetch_top_scorers(client, league, season)
                except ApiError as exc:
                    warnings.append(f"{league['name']}: sin goleadores ({exc})")
                    scorers, scorers_ok = {}, False
                scorers_loaded = True

            home_stats = {}
            away_stats = {}
            if home_id:
                if home_id not in stats_cache:
                    try:
                        stats_cache[home_id] = fetch_team_stats(client, league, season, home_id)
                    except ApiError as exc:
                        warnings.append(f"{league['name']}: sin stats de {home.get('name')} ({exc})")
                        stats_cache[home_id] = {}
                home_stats = stats_cache[home_id]
            if away_id:
                if away_id not in stats_cache:
                    try:
                        stats_cache[away_id] = fetch_team_stats(client, league, season, away_id)
                    except ApiError as exc:
                        warnings.append(f"{league['name']}: sin stats de {away.get('name')} ({exc})")
                        stats_cache[away_id] = {}
                away_stats = stats_cache[away_id]

            h2h_draws, h2h_finished = None, 0
            if home_id and away_id:
                key = (min(home_id, away_id), max(home_id, away_id))
                if key not in h2h_cache:
                    try:
                        h2h_cache[key] = fetch_h2h(client, home_id, away_id, kickoff.timestamp())
                    except ApiError as exc:
                        warnings.append(f"{league['name']}: sin H2H ({exc})")
                        h2h_cache[key] = (None, 0)
                h2h_draws, h2h_finished = h2h_cache[key]

            analyzed = analyze_fixture(
                item=item,
                league=league,
                odds=odds_pairs,
                standings=standings,
                home_stats=home_stats,
                away_stats=away_stats,
                h2h_draws=h2h_draws,
                h2h_finished=h2h_finished,
                scorers=scorers,
                scorers_ok=scorers_ok,
                kickoff=kickoff,
            )
            matches.append(analyzed)

    # Orden por liga y hora.
    matches.sort(key=lambda match: (league_order.get(match["league"], 99), match["kickoff"]))

    # Regla de la skill: si dos candidatos empiezan a la misma hora en la misma liga,
    # se queda el de mayor puntaje.
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for match in matches:
        if match["status"] == "candidate":
            grouped[(match["league"], match["time"])].append(match)
    for group in grouped.values():
        if len(group) <= 1:
            continue
        group.sort(key=lambda match: (-(match.get("score") or 0), match.get("fixture_id") or 0))
        keep = group[0]
        for other in group[1:]:
            other["status"] = "rejected"
            other["note"] = (
                f"Misma hora que {keep['home']} vs {keep['away']}; "
                f"se prioriza el de mayor puntaje. " + other.get("note", "")
            ).strip()

    data = {
        "date": target_date.isoformat(),
        "date_label": date_label(target_date),
        "generated_at": now.isoformat(),
        "cutoff": now.strftime("%H:%M"),
        "source": "API-Football + revisión manual",
        "leagues": [public_league(league) for league in LEAGUES],
        "matches": matches,
        "warnings": warnings,
    }
    return data


def load_from_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "leagues" not in data or "matches" not in data:
        raise SystemExit(f"{path} no tiene el formato esperado (leagues/matches).")
    data.setdefault("date_label", date_label(date.fromisoformat(data["date"])))
    data.setdefault("generated_at", "")
    data.setdefault("cutoff", "")
    data.setdefault("source", "Snapshot JSON")
    data.setdefault("warnings", [])
    return data


# ---------------------------------------------------------------------------
# Render HTML
# ---------------------------------------------------------------------------
DATA_BLOCK_RE = re.compile(
    r'(<script id="daily-data" type="application/json">)(.*?)(</script>)',
    re.DOTALL,
)


def render_html(data: dict[str, Any], template_path: Path, out_path: Path) -> None:
    template = template_path.read_text(encoding="utf-8")
    json_text = json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
    replacement = lambda match: f"{match.group(1)}\n{json_text}\n{match.group(3)}"
    html, count = DATA_BLOCK_RE.subn(replacement, template)
    if count != 1:
        raise SystemExit(
            f"No encontré el bloque <script id=\"daily-data\"> en {template_path}. "
            "Asegúrate de que el template tenga ese bloque."
        )
    out_path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera el HTML diario del Soccer Draw Predictor.")
    parser.add_argument("--date", help="Fecha YYYY-MM-DD (default: hoy en America/Bogota).")
    parser.add_argument("--now", help="Fecha/hora ISO para pruebas, ej. 2026-10-20T15:42:00-05:00.")
    parser.add_argument(
        "--source",
        choices=["espn", "api-football"],
        default="espn",
        help="Fuente de datos (default: espn, gratis y sin key).",
    )
    parser.add_argument("--template", default="index.html", help="HTML template con el bloque daily-data.")
    parser.add_argument("--out", default="index.html", help="HTML de salida (default: index.html).")
    parser.add_argument("--archive-dir", default="pages", help="Directorio para la copia fechada YYYY-MM-DD.html (default: pages).")
    parser.add_argument("--data-dir", default="data", help="Directorio para los snapshots JSON.")
    parser.add_argument("--from-json", help="Genera el HTML desde un snapshot JSON, sin llamar a la API.")
    parser.add_argument("--dry-run", action="store_true", help="Imprime el JSON y no escribe archivos.")
    parser.add_argument("--no-cache", action="store_true", help="Ignora la caché local.")
    parser.add_argument("--include-started", action="store_true", help="Incluye partidos ya iniciados (solo pruebas).")
    parser.add_argument("--verbose", action="store_true", help="Muestra las llamadas a la API.")
    args = parser.parse_args()

    load_env()

    template_path = (ROOT / args.template).resolve() if not Path(args.template).is_absolute() else Path(args.template)
    out_path = (ROOT / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    archive_dir = (ROOT / args.archive_dir).resolve() if not Path(args.archive_dir).is_absolute() else Path(args.archive_dir)
    data_dir = (ROOT / args.data_dir).resolve() if not Path(args.data_dir).is_absolute() else Path(args.data_dir)

    if args.from_json:
        from_path = Path(args.from_json)
        if not from_path.is_absolute():
            from_path = (ROOT / from_path).resolve()
        data = load_from_json(from_path)
    else:
        target_date = date.fromisoformat(args.date) if args.date else datetime.now(BOGOTA).date()
        now = parse_now(args.now)
        if args.source == "espn":
            client = EspnClient(no_cache=args.no_cache, verbose=args.verbose)
            try:
                data = build_data_from_espn(client, target_date, now, args.include_started)
            except EspnError as exc:
                print(f"Error de ESPN: {exc}", file=sys.stderr)
                return 1
            print(f"ESPN: {client.requests} peticiones, {client.cache_hits} desde caché.")
        else:
            api_key = os.environ.get("API_FOOTBALL_KEY", "").strip()
            if not api_key:
                print(
                    "Falta API_FOOTBALL_KEY para --source api-football.\n"
                    "Crea un archivo .env con:\n"
                    "  API_FOOTBALL_KEY=tu_key\n"
                    "O usa la fuente gratis por defecto: python3 generate_daily.py",
                    file=sys.stderr,
                )
                return 2
            client = ApiClient(api_key, no_cache=args.no_cache, verbose=args.verbose)
            try:
                data = build_data_from_api(client, target_date, now, args.include_started)
            except ApiError as exc:
                print(f"Error de API-Football: {exc}", file=sys.stderr)
                return 1
            print(f"API-Football: {client.requests} peticiones, {client.cache_hits} desde caché.")

    if args.dry_run:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = data_dir / f"{data['date']}.json"
    snapshot_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    render_html(data, template_path, out_path)

    archive_path = archive_dir / f"{data['date']}.html"
    if archive_path.resolve() != out_path.resolve():
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"HTML generado: {out_path}")
    print(f"Página del día: {archive_path}")
    print(f"Snapshot: {snapshot_path}")
    print(f"Partidos: {len(data['matches'])} · Candidatos: {sum(1 for m in data['matches'] if m['status'] == 'candidate')}")
    if data.get("warnings"):
        print(f"Advertencias: {len(data['warnings'])} (ver JSON)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
