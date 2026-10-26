#!/usr/bin/env python3
"""
Backtest histórico del Soccer Draw Predictor.

Fuente: ESPN (scoreboard + summary). El summary conserva las cuotas de cierre
aunque el partido ya haya terminado, así que permite reconstruir la cuota de
empate que había en el mercado.

El backtest NO usa standings ni goleadores actuales para evitar look-ahead.
Las features se calculan solo con partidos anteriores al kickoff:

  - empates y goles del local/visitante en la temporada, como local/visitante;
  - empates y goles de los últimos 5 partidos de cada equipo;
  - cuota de apertura y cierre del empate.

Uso:
  python3 backtest.py --weeks 8
  python3 backtest.py --start 2026-08-31 --end 2026-10-25
  python3 backtest.py --weeks 4 --leagues col,esp2,ita

Salida:
  reports/backtest-YYYY-MM-DD.json
  reports/backtest-YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import generate_daily as gd

ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
MIN_ODDS = 2.60
MAX_ODDS = 3.20
RECENT_N = 5
RECENT_MIN_PLAYED = 4
RECENT_GOALS_MAX = 2.60
SEASON_GOALS_MAX = 2.50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest histórico del predictor de empates.")
    parser.add_argument("--weeks", type=int, default=8, help="Semanas hacia atrás (por defecto 8).")
    parser.add_argument("--start", help="Fecha inicial YYYY-MM-DD. Si se omite, se calcula con --weeks.")
    parser.add_argument("--end", help="Fecha final YYYY-MM-DD. Por defecto, ayer en America/Bogota.")
    parser.add_argument("--leagues", help="IDs separados por coma. Por defecto, las 10 ligas.")
    parser.add_argument("--out-dir", default=str(REPORTS_DIR), help="Directorio de reportes.")
    parser.add_argument("--no-cache", action="store_true", help="No usar la caché de ESPN.")
    parser.add_argument("--limit", type=int, default=0, help="Máximo de partidos con cuota a procesar (debug).")
    parser.add_argument("--quiet", action="store_true", help="No imprimir progreso.")
    return parser.parse_args()


def log(message: str, quiet: bool) -> None:
    if not quiet:
        print(message, file=sys.stderr, flush=True)


def yesterday_bogota() -> date:
    return (datetime.now(gd.BOGOTA) - timedelta(days=1)).date()


def daterange(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def selected_leagues(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return list(gd.LEAGUES)
    wanted = {item.strip() for item in raw.split(",") if item.strip()}
    leagues = [league for league in gd.LEAGUES if league["id"] in wanted]
    missing = wanted - {league["id"] for league in leagues}
    if missing:
        raise SystemExit(f"Ligas desconocidas: {', '.join(sorted(missing))}")
    return leagues


def fractional_to_decimal(value: Any) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    try:
        numerator, denominator = value.split("/", 1)
        return 1 + float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def decimal_odds(value: Any) -> float | None:
    decimal = gd.american_to_decimal(value)
    if decimal is not None:
        return round(decimal, 3)
    fractional = fractional_to_decimal(value)
    if fractional is not None:
        return round(fractional, 3)
    return None


def parse_summary_odds(summary: dict[str, Any]) -> tuple[str | None, float | None, float | None]:
    """Devuelve (proveedor, cuota de apertura, cuota de cierre)."""
    provider: str | None = None
    opening: float | None = None
    closing: float | None = None

    # DraftKings / pickcenter: la cuota que muestra ESPN como cierre.
    for item in summary.get("pickcenter") or []:
        draw = (item.get("drawOdds") or {}).get("moneyLine")
        decimal = decimal_odds(draw)
        if decimal is not None:
            provider = (item.get("provider") or {}).get("name") or "ESPN"
            closing = decimal
            break

    # La lista odds trae apertura y cierre; sirve también de fallback.
    for item in summary.get("odds") or []:
        if closing is None:
            draw = (item.get("drawOdds") or {}).get("moneyLine")
            decimal = decimal_odds(draw)
            if decimal is not None:
                provider = (item.get("provider") or {}).get("name") or "ESPN"
                closing = decimal
        moneyline = item.get("moneyline") or {}
        draw_line = moneyline.get("draw") or {}
        if closing is None:
            decimal = decimal_odds((draw_line.get("close") or {}).get("odds"))
            if decimal is not None:
                closing = decimal
        if opening is None:
            decimal = decimal_odds((draw_line.get("open") or {}).get("odds"))
            if decimal is not None:
                opening = decimal
        if closing is not None and opening is not None:
            break

    return provider, opening, closing


def fetch_summary(
    client: gd.EspnClient,
    league: dict[str, Any],
    event_id: str,
) -> dict[str, Any] | None:
    url = (
        f"{gd.ESPN_BASE}/site/v2/sports/soccer/{league['espn_slug']}"
        f"/summary?event={event_id}"
    )
    try:
        return client.get(url)
    except gd.EspnError:
        return None


def event_scores(event: dict[str, Any]) -> tuple[int, int] | None:
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competitors = competitions[0].get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    home_score = gd.espn_score_value(home)
    away_score = gd.espn_score_value(away)
    if home_score is None or away_score is None:
        return None
    return int(home_score), int(away_score)


def event_team_ids(event: dict[str, Any]) -> tuple[str, str] | None:
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competitors = competitions[0].get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    return (
        str((home.get("team") or {}).get("id") or ""),
        str((away.get("team") or {}).get("id") or ""),
    )


def recent_form(events: list[dict[str, Any]], team_id: str, before_ts: float, n: int = RECENT_N) -> dict[str, Any]:
    rows: list[tuple[float, float, float]] = []
    for event in events:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]
        state = ((competition.get("status") or {}).get("type") or {}).get("state")
        if state != "post":
            continue
        try:
            event_ts = gd.parse_datetime(event.get("date")).timestamp()
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
        my_score = gd.espn_score_value(me)
        rival_score = gd.espn_score_value(opponent)
        if my_score is None or rival_score is None:
            continue
        rows.append((event_ts, my_score, rival_score))

    rows.sort(key=lambda item: item[0], reverse=True)
    last = rows[:n]
    played = len(last)
    draws = sum(1 for _, my_score, rival_score in last if my_score == rival_score)
    goals_avg = (
        sum(my_score + rival_score for _, my_score, rival_score in last) / played
        if played
        else None
    )
    return {"played": played, "draws": draws, "goals_avg": goals_avg}


def wilson_interval(successes: int, total: int) -> tuple[float | None, float | None]:
    if not total:
        return None, None
    z = 1.96
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        (proportion * (1 - proportion) / total + z * z / (4 * total * total))
    ) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def summarize(subset: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(subset)
    wins = sum(1 for match in subset if match["draw"])
    odds = [match["odds_close"] for match in subset if match.get("odds_close")]
    avg_odds = sum(odds) / len(odds) if odds else None
    returns = sum(match["odds_close"] for match in subset if match["draw"] and match.get("odds_close"))
    net = returns - total
    roi = net / total if total else None
    low, high = wilson_interval(wins, total)
    return {
        "matches": total,
        "draws": wins,
        "hit_rate": wins / total if total else None,
        "hit_ci95": [low, high],
        "avg_odds": avg_odds,
        "net_units": net,
        "roi": roi,
    }


def rule_definitions() -> list[tuple[str, str, Callable[[dict[str, Any]], bool]]]:
    return [
        ("all_valid", "Todas las cuotas válidas", lambda m: True),
        ("recent_both", "Forma reciente: ambos ≥2 empates", lambda m: m["recent_both_ge2"]),
        ("recent_goals", "Goles recientes ≤2.6", lambda m: m["recent_goals_ok"]),
        ("recent_both_goals", "Forma reciente + goles recientes", lambda m: m["recent_both_goals"]),
        ("season_draw_rates", "Ambos draw rates de temporada ≥25%", lambda m: m["both_season_draw_rates_ok"]),
        ("season_goals", "Goles de temporada ≤2.5", lambda m: m["season_goals_ok"]),
        ("odds_shortened", "Cuota de cierre más baja que la de apertura", lambda m: m["odds_shortened"]),
        ("recent_both_season_goals", "Forma reciente + goles de temporada", lambda m: m["recent_both_ge2"] and m["season_goals_ok"]),
        ("recent_both_draw_rates", "Forma reciente + draw rates", lambda m: m["recent_both_ge2"] and m["both_season_draw_rates_ok"]),
        ("recent_both_goals_draw_rates", "Forma + goles recientes + draw rates", lambda m: m["recent_both_goals"] and m["both_season_draw_rates_ok"]),
        ("recent_both_goals_shortened", "Forma + goles recientes + cuota bajó", lambda m: m["recent_both_goals"] and m["odds_shortened"]),
        ("confirmations_2", "≥2 confirmaciones", lambda m: m["confirmations"] >= 2),
        ("confirmations_3", "≥3 confirmaciones", lambda m: m["confirmations"] >= 3),
        ("confirmations_4", "≥4 confirmaciones", lambda m: m["confirmations"] >= 4),
    ]


def format_pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def format_num(value: float | None, decimals: int = 2) -> str:
    return "—" if value is None else f"{value:.{decimals}f}"


def build_summary(matches: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [match for match in matches if match["odds_valid"]]
    rules = []
    for rule_id, label, predicate in rule_definitions():
        subset = [match for match in valid if predicate(match)]
        rules.append({"id": rule_id, "label": label, **summarize(subset)})

    by_week: dict[str, Any] = {}
    weeks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for match in valid:
        day = date.fromisoformat(match["date"])
        iso = day.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        weeks[key].append(match)
    for key in sorted(weeks):
        subset = weeks[key]
        entry = {"all_valid": summarize(subset)}
        for rule_id in ("recent_both", "recent_both_goals", "confirmations_2"):
            predicate = next(p for rid, _, p in rule_definitions() if rid == rule_id)
            entry[rule_id] = summarize([match for match in subset if predicate(match)])
        by_week[key] = entry

    by_league: dict[str, Any] = {}
    leagues: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for match in valid:
        leagues[match["league"]].append(match)
    for league_id in sorted(leagues):
        subset = leagues[league_id]
        entry = {"all_valid": summarize(subset)}
        for rule_id in ("recent_both", "recent_both_goals", "confirmations_2"):
            predicate = next(p for rid, _, p in rule_definitions() if rid == rule_id)
            entry[rule_id] = summarize([match for match in subset if predicate(match)])
        by_league[league_id] = entry

    return {
        "matches_total": len(matches),
        "matches_with_odds": sum(1 for match in matches if match.get("odds_close")),
        "matches_valid": len(valid),
        "draws_valid": sum(1 for match in valid if match["draw"]),
        "draw_rate_valid": (
            sum(1 for match in valid if match["draw"]) / len(valid) if valid else None
        ),
        "rules": rules,
        "by_week": by_week,
        "by_league": by_league,
    }


def html_escape(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def rule_tone(rule: dict[str, Any], best_id: str | None) -> str:
    if rule["id"] == best_id:
        return "best"
    if rule["roi"] is not None and rule["roi"] > 0:
        return "positive"
    return ""


def write_html(
    path: Path,
    meta: dict[str, Any],
    summary: dict[str, Any],
    matches: list[dict[str, Any]],
) -> None:
    valid = [match for match in matches if match["odds_valid"]]
    baseline = next(
        (rule for rule in summary["rules"] if rule["id"] == "all_valid"),
        {"hit_rate": None, "roi": None, "matches": len(valid)},
    )
    eligible = [
        rule
        for rule in summary["rules"]
        if rule["id"] != "all_valid"
        and rule["matches"] >= 10
        and rule["hit_rate"] is not None
        and baseline["hit_rate"] is not None
        and rule["hit_rate"] > baseline["hit_rate"]
    ]
    best = max(eligible, key=lambda rule: rule["hit_rate"], default=None)
    best_id = best["id"] if best else None

    draw_rate = summary["draw_rate_valid"]
    avg_odds = summarize(valid)["avg_odds"]

    def pct(value: float | None) -> str:
        return "—" if value is None else f"{100 * value:.1f}%"

    def num(value: float | None) -> str:
        return "—" if value is None else f"{value:.2f}"

    def ci(rule: dict[str, Any]) -> str:
        low, high = rule["hit_ci95"]
        if low is None:
            return "—"
        return f"{100 * low:.0f}–{100 * high:.0f}%"

    if best:
        verdict = (
            f'La regla con mejor acierto es <b>{html_escape(best["label"])}</b> '
            f'({pct(best["hit_rate"])} en {best["matches"]} partidos), por encima de la base '
            f'({pct(baseline["hit_rate"])}). Aun así, el IC95% es amplio y el ROI debe leerse con cautela.'
        )
    else:
        verdict = (
            f'<b>Ninguna regla superó el acierto de tomar todas las cuotas válidas</b> '
            f'({pct(baseline["hit_rate"])}). Con esta muestra, las reglas de forma reciente no '
            f'mejoran la selección base.'
        )

    rule_rows: list[str] = []
    for rule in summary["rules"]:
        tone = rule_tone(rule, best_id)
        tag = ' <span class="tag">mejor acierto</span>' if rule["id"] == best_id else ""
        rule_rows.append(
            f'<tr class="{tone}"><td>{html_escape(rule["label"])}{tag}</td>'
            f'<td class="num">{rule["matches"]}</td>'
            f'<td class="num">{rule["draws"]}</td>'
            f'<td class="num">{pct(rule["hit_rate"])}</td>'
            f'<td class="num">{ci(rule)}</td>'
            f'<td class="num">{num(rule["avg_odds"])}</td>'
            f'<td class="num">{pct(rule["roi"])}</td></tr>'
        )

    week_rows: list[str] = []
    for week, entry in summary["by_week"].items():
        base = entry["all_valid"]
        recent = entry["recent_both"]
        confirmed = entry["confirmations_2"]
        week_rows.append(
            f"<tr><td>{html_escape(week)}</td>"
            f'<td class="num">{base["matches"]}</td>'
            f'<td class="num">{base["draws"]} ({pct(base["hit_rate"])})</td>'
            f'<td class="num">{recent["matches"]}</td>'
            f'<td class="num">{pct(recent["hit_rate"])}</td>'
            f'<td class="num">{confirmed["matches"]}</td>'
            f'<td class="num">{pct(confirmed["hit_rate"])}</td></tr>'
        )

    league_rows: list[str] = []
    for league_id, entry in summary["by_league"].items():
        base = entry["all_valid"]
        recent = entry["recent_both"]
        league_rows.append(
            f"<tr><td>{html_escape(league_id)}</td>"
            f'<td class="num">{base["matches"]}</td>'
            f'<td class="num">{base["draws"]} ({pct(base["hit_rate"])})</td>'
            f'<td class="num">{recent["matches"]}</td>'
            f'<td class="num">{pct(recent["hit_rate"])}</td></tr>'
        )

    html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backtest — Soccer Draw Predictor</title>
<style>
  :root{{
    --bg:#070b14;--panel:#0d1524;--line:#1c2942;--text:#e8eefc;--muted:#93a4c2;
    --accent:#4cc9f0;--violet:#a78bfa;--green:#34d399;--yellow:#fbbf24;--red:#f87171;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:radial-gradient(1200px 600px at 15% -10%,rgba(76,201,240,.16),transparent 60%),
    radial-gradient(900px 500px at 95% 0%,rgba(167,139,250,.13),transparent 55%),var(--bg);
    color:var(--text);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
  .wrap{{max-width:1100px;margin:0 auto;padding:36px 20px 70px}}
  .brand{{color:var(--accent);font-weight:800;font-size:13px;letter-spacing:.08em;text-transform:uppercase}}
  h1{{font-size:clamp(30px,4.6vw,48px);letter-spacing:-.04em;margin:10px 0 6px;line-height:1.05}}
  .sub{{color:var(--muted);font-size:14px;line-height:1.6;margin:0}}
  .banner{{margin-top:16px;padding:11px 14px;border-radius:12px;font-size:12.5px;line-height:1.5;
    border:1px solid rgba(167,139,250,.4);background:rgba(167,139,250,.12);color:#e4dcff}}
  .cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0 8px}}
  .card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px}}
  .card b{{display:block;font-size:24px;letter-spacing:-.02em;font-variant-numeric:tabular-nums}}
  .card span{{color:var(--muted);font-size:11.5px;line-height:1.35;display:block;margin-top:2px}}
  h2{{font-size:18px;margin:34px 0 10px;letter-spacing:-.01em}}
  table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);
    border-radius:14px;overflow:hidden;font-size:12.5px}}
  th,td{{padding:9px 11px;text-align:left;border-top:1px solid var(--line);vertical-align:top}}
  th{{background:#0a1220;color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;
    position:sticky;top:0}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
  tr.best td{{background:rgba(76,201,240,.08)}}
  tr.positive td:last-child{{color:var(--green)}}
  .tag{{display:inline-block;margin-left:6px;padding:2px 6px;border-radius:999px;font-size:9.5px;
    background:rgba(76,201,240,.16);color:var(--accent);border:1px solid rgba(76,201,240,.3)}}
  .note{{color:var(--muted);font-size:11.5px;line-height:1.7;margin-top:10px}}
  footer{{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);
    font-size:11.5px;line-height:1.7}}
  footer b{{color:#cbd5e1}}
  @media(max-width:760px){{.cards{{grid-template-columns:repeat(2,1fr)}}table{{font-size:11.5px}}
    th,td{{padding:7px 8px}}}}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">⚽ Soccer Draw Predictor</div>
  <h1>Backtest de efectividad</h1>
  <p class="sub">Del <b>{html_escape(meta["start"])}</b> al <b>{html_escape(meta["end"])}</b>
    ({meta["days"]} días) · {len(meta["leagues"])} ligas ·
    cuota de empate {meta["min_odds"]:.2f}–{meta["max_odds"]:.2f}</p>
  <div class="banner">🧪 <b>Documento de investigación.</b> Mide reglas sobre datos históricos y una muestra
    pequeña. No es una recomendación de apuestas ni garantiza resultados futuros. Las cuotas son de cierre de
    DraftKings vía ESPN, no de Betplay.</div>

  <section class="cards">
    <div class="card"><b>{summary["matches_with_odds"]}</b><span>partidos con cuota de empate</span></div>
    <div class="card"><b>{summary["matches_valid"]}</b><span>con cuota válida 2.60–3.20</span></div>
    <div class="card"><b>{summary["draws_valid"]}</b><span>empates reales ({pct(draw_rate)})</span></div>
    <div class="card"><b>{num(avg_odds)}</b><span>cuota media · break-even {pct(1 / avg_odds if avg_odds else None)}</span></div>
  </section>

  <h2>Reglas evaluadas</h2>
  <table>
    <thead><tr><th>Regla</th><th class="num">n</th><th class="num">Empates</th>
      <th class="num">Acierto</th><th class="num">IC95%</th><th class="num">Cuota</th>
      <th class="num">ROI</th></tr></thead>
    <tbody>{"".join(rule_rows)}</tbody>
  </table>
  <p class="note">{verdict} ROI con 1 unidad plana por partido. El IC95% es Wilson; con n pequeño es muy ancho,
    así que diferencias de pocos puntos porcentuales no son concluyentes.</p>

  <h2>Por semana</h2>
  <table>
    <thead><tr><th>Semana</th><th class="num">Cuota válida</th><th class="num">Empates</th>
      <th class="num">Forma reciente n</th><th class="num">Acierto forma</th>
      <th class="num">Conf. ≥2 n</th><th class="num">Acierto conf.</th></tr></thead>
    <tbody>{"".join(week_rows)}</tbody>
  </table>

  <h2>Por liga</h2>
  <table>
    <thead><tr><th>Liga</th><th class="num">Cuota válida</th><th class="num">Empates</th>
      <th class="num">Forma reciente n</th><th class="num">Acierto forma</th></tr></thead>
    <tbody>{"".join(league_rows)}</tbody>
  </table>

  <footer>
    <b>Metodología.</b> Cuotas de cierre de DraftKings publicadas por ESPN
    (<code>summary?event=…</code>); el scoreboard histórico ya no conserva cuotas.
    Las features de cada partido usan solo partidos terminados antes del kickoff, sin standings ni goleadores
    actuales. La forma reciente son los últimos 5 partidos; si faltan, se completa con la temporada anterior.
    Los partidos sin cuota de empate se excluyen del denominador.
    <br><br>
    <b>Aviso.</b> No es consejo de apuestas. Confirma la cuota final en Betplay. Juego responsable, +18.
  </footer>
</div>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def write_markdown(
    path: Path,
    meta: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    lines: list[str] = []
    lines.append("# Backtest — Soccer Draw Predictor")
    lines.append("")
    lines.append(f"- **Rango:** {meta['start']} a {meta['end']} ({meta['days']} días)")
    lines.append(f"- **Ligas:** {', '.join(meta['leagues'])}")
    lines.append(f"- **Partidos con cuota:** {summary['matches_with_odds']}")
    lines.append(f"- **Partidos con cuota válida 2.60–3.20:** {summary['matches_valid']}")
    lines.append(f"- **Empates en cuota válida:** {summary['draws_valid']} ({format_pct(summary['draw_rate_valid'])})")
    lines.append("")

    lines.append("## Reglas")
    lines.append("")
    lines.append("| Regla | Partidos | Empates | Acierto | IC95% | Cuota media | ROI |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for rule in summary["rules"]:
        low, high = rule["hit_ci95"]
        ci = "—" if low is None else f"{format_pct(low)}–{format_pct(high)}"
        lines.append(
            "| {label} | {matches} | {draws} | {hit} | {ci} | {odds} | {roi} |".format(
                label=rule["label"],
                matches=rule["matches"],
                draws=rule["draws"],
                hit=format_pct(rule["hit_rate"]),
                ci=ci,
                odds=format_num(rule["avg_odds"]),
                roi=format_pct(rule["roi"]),
            )
        )
    lines.append("")

    baseline = next((rule for rule in summary["rules"] if rule["id"] == "all_valid"), None)
    experimental_rule = next(
        (rule for rule in summary["rules"] if rule["id"] == "recent_both_goals"), None
    )
    lines.append("## Lectura")
    lines.append("")
    if baseline:
        lines.append(
            f"- Base (todas las cuotas válidas): {baseline['draws']}/{baseline['matches']} "
            f"({format_pct(baseline['hit_rate'])}), ROI {format_pct(baseline['roi'])}."
        )
    if experimental_rule:
        lines.append(
            f"- Regla experimental (forma reciente + goles recientes): "
            f"{experimental_rule['draws']}/{experimental_rule['matches']} "
            f"({format_pct(experimental_rule['hit_rate'])}), "
            f"ROI {format_pct(experimental_rule['roi'])}."
        )
        if baseline and baseline["hit_rate"] is not None and experimental_rule["hit_rate"] is not None:
            if experimental_rule["hit_rate"] < baseline["hit_rate"]:
                lines.append(
                    "- La regla experimental **no supera** la base en esta muestra. "
                    "No se promueve a `main`; queda como hipótesis a seguir midiendo."
                )
            else:
                lines.append(
                    "- La regla experimental supera la base en esta muestra, pero el IC95% es amplio: "
                    "hace falta más historial antes de promoverla."
                )
    lines.append(
        "- Los intervalos de confianza son muy anchos con esta muestra; ninguna diferencia de pocos "
        "puntos porcentuales es concluyente."
    )
    lines.append("")

    lines.append("## Por semana")
    lines.append("")
    lines.append("| Semana | Cuota válida | Empates | Acierto | Forma reciente | Acierto forma | Confirmaciones ≥2 | Acierto conf. |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for week, entry in summary["by_week"].items():
        base = entry["all_valid"]
        recent = entry["recent_both"]
        confirmed = entry["confirmations_2"]
        lines.append(
            "| {week} | {bn} | {bd} | {bh} | {rn} | {rh} | {cn} | {ch} |".format(
                week=week,
                bn=base["matches"],
                bd=base["draws"],
                bh=format_pct(base["hit_rate"]),
                rn=recent["matches"],
                rh=format_pct(recent["hit_rate"]),
                cn=confirmed["matches"],
                ch=format_pct(confirmed["hit_rate"]),
            )
        )
    lines.append("")

    lines.append("## Por liga")
    lines.append("")
    lines.append("| Liga | Cuota válida | Empates | Acierto | Forma reciente | Acierto forma |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for league_id, entry in summary["by_league"].items():
        base = entry["all_valid"]
        recent = entry["recent_both"]
        lines.append(
            "| {league} | {bn} | {bd} | {bh} | {rn} | {rh} |".format(
                league=league_id,
                bn=base["matches"],
                bd=base["draws"],
                bh=format_pct(base["hit_rate"]),
                rn=recent["matches"],
                rh=format_pct(recent["hit_rate"]),
            )
        )
    lines.append("")

    lines.append("## Notas metodológicas")
    lines.append("")
    lines.append("- La cuota es la de cierre de DraftKings publicada por ESPN; no es Betplay.")
    lines.append("- El ROI usa 1 unidad plana por partido y paga la cuota de cierre si hubo empate.")
    lines.append("- La forma reciente usa los últimos 5 partidos terminados antes del kickoff.")
    lines.append("- Si un equipo tenía menos de 5 partidos en la temporada, se completó con la temporada anterior.")
    lines.append("- Las features de temporada se calculan solo con partidos anteriores al kickoff; no se usan standings ni goleadores actuales.")
    lines.append("- Los partidos sin cuota de empate se excluyen de las reglas y del denominador.")
    lines.append("- Muestra pequeña: los resultados son descriptivos, no una garantía de rendimiento futuro.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    end = date.fromisoformat(args.end) if args.end else yesterday_bogota()
    start = (
        date.fromisoformat(args.start)
        if args.start
        else end - timedelta(days=max(1, args.weeks) * 7 - 1)
    )
    if start > end:
        raise SystemExit("La fecha inicial no puede ser posterior a la final.")

    leagues = selected_leagues(args.leagues)
    client = gd.EspnClient(no_cache=args.no_cache)

    schedule_cache: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    summary_cache: dict[str, dict[str, Any] | None] = {}

    def get_schedule(league: dict[str, Any], team_id: str, season: int) -> list[dict[str, Any]]:
        key = (league["id"], team_id, season)
        if key not in schedule_cache:
            try:
                schedule_cache[key] = gd.fetch_espn_schedule(client, league, team_id, season)
            except gd.EspnError:
                schedule_cache[key] = []
        return schedule_cache[key]

    def get_recent_events(
        league: dict[str, Any],
        team_id: str,
        season: int,
        before_ts: float,
    ) -> list[dict[str, Any]]:
        current = get_schedule(league, team_id, season)
        if recent_form(current, team_id, before_ts)["played"] >= RECENT_N:
            return current
        previous = get_schedule(league, team_id, season - 1)
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for event in current + previous:
            event_id = str(event.get("id") or "")
            if event_id and event_id in seen:
                continue
            seen.add(event_id)
            merged.append(event)
        return merged

    matches: list[dict[str, Any]] = []
    days = daterange(start, end)
    processed = 0

    for day in days:
        for league in leagues:
            try:
                payload = gd.fetch_espn_scoreboard(client, league, day)
            except gd.EspnError as exc:
                log(f"{day} {league['id']}: scoreboard error ({exc})", args.quiet)
                continue

            season_type_name = ""
            league_payload = payload.get("leagues") or []
            if league_payload:
                season_type_name = (
                    ((league_payload[0].get("season") or {}).get("type") or {}).get("name") or ""
                )

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
                if status.get("state") != "post":
                    continue
                try:
                    kickoff = gd.parse_datetime(event.get("date"))
                except (TypeError, ValueError):
                    continue
                if kickoff.astimezone(gd.BOGOTA).date() != day:
                    continue

                scores = event_scores(event)
                team_ids = event_team_ids(event)
                if scores is None or team_ids is None:
                    continue
                home_goals, away_goals = scores
                home_id, away_id = team_ids
                event_id = str(event.get("id") or competition.get("id") or "")
                if not event_id:
                    continue

                if event_id not in summary_cache:
                    summary_cache[event_id] = fetch_summary(client, league, event_id)
                summary = summary_cache[event_id]
                if summary is None:
                    continue
                provider, opening, closing = parse_summary_odds(summary)
                if closing is None:
                    continue

                season = gd.season_for(league, day)
                home_schedule = get_schedule(league, home_id, season)
                away_schedule = get_schedule(league, away_id, season)
                home_stats = gd.espn_schedule_stats(
                    home_schedule, home_id, season_type_name, kickoff.timestamp()
                )
                away_stats = gd.espn_schedule_stats(
                    away_schedule, away_id, season_type_name, kickoff.timestamp()
                )

                home_recent = recent_form(
                    get_recent_events(league, home_id, season, kickoff.timestamp()),
                    home_id,
                    kickoff.timestamp(),
                )
                away_recent = recent_form(
                    get_recent_events(league, away_id, season, kickoff.timestamp()),
                    away_id,
                    kickoff.timestamp(),
                )

                recent_ok = (
                    home_recent["played"] >= RECENT_MIN_PLAYED
                    and away_recent["played"] >= RECENT_MIN_PLAYED
                )
                recent_both_ge2 = (
                    recent_ok
                    and home_recent["draws"] >= 2
                    and away_recent["draws"] >= 2
                )
                recent_goals_avg = None
                if (
                    home_recent["goals_avg"] is not None
                    and away_recent["goals_avg"] is not None
                ):
                    recent_goals_avg = (
                        home_recent["goals_avg"] + away_recent["goals_avg"]
                    ) / 2
                recent_goals_ok = (
                    recent_ok
                    and recent_goals_avg is not None
                    and recent_goals_avg <= RECENT_GOALS_MAX
                )
                season_goals_avg = None
                if (
                    home_stats.get("avg_total_goals") is not None
                    and away_stats.get("avg_total_goals") is not None
                ):
                    season_goals_avg = (
                        home_stats["avg_total_goals"] + away_stats["avg_total_goals"]
                    ) / 2
                season_goals_ok = (
                    season_goals_avg is not None and season_goals_avg <= SEASON_GOALS_MAX
                )
                both_season_draw_rates_ok = (
                    home_stats.get("home_draw_rate") is not None
                    and away_stats.get("away_draw_rate") is not None
                    and home_stats["home_draw_rate"] >= 0.25
                    and away_stats["away_draw_rate"] >= 0.25
                )
                odds_valid = MIN_ODDS <= closing <= MAX_ODDS
                odds_shortened = (
                    opening is not None and closing is not None and closing < opening
                )

                confirmations = sum(
                    1
                    for condition in (
                        recent_both_ge2,
                        recent_goals_ok,
                        season_goals_ok,
                        both_season_draw_rates_ok,
                        odds_shortened,
                    )
                    if condition
                )

                competitions_teams = competition.get("competitors") or []
                home_team = next(
                    (c for c in competitions_teams if c.get("homeAway") == "home"), None
                )
                away_team = next(
                    (c for c in competitions_teams if c.get("homeAway") == "away"), None
                )
                home_name = gd.clean_team_name(
                    ((home_team or {}).get("team") or {}).get("displayName") or "Local"
                )
                away_name = gd.clean_team_name(
                    ((away_team or {}).get("team") or {}).get("displayName") or "Visitante"
                )

                matches.append(
                    {
                        "date": day.isoformat(),
                        "league": league["id"],
                        "league_name": league["name"],
                        "event_id": event_id,
                        "kickoff": kickoff.isoformat(),
                        "home": home_name,
                        "away": away_name,
                        "home_goals": home_goals,
                        "away_goals": away_goals,
                        "draw": home_goals == away_goals,
                        "odds_provider": provider,
                        "odds_open": opening,
                        "odds_close": closing,
                        "odds_valid": odds_valid,
                        "odds_shortened": odds_shortened,
                        "home_draw_rate": home_stats.get("home_draw_rate"),
                        "away_draw_rate": away_stats.get("away_draw_rate"),
                        "home_goals_avg": home_stats.get("avg_total_goals"),
                        "away_goals_avg": away_stats.get("avg_total_goals"),
                        "season_goals_avg": season_goals_avg,
                        "season_goals_ok": season_goals_ok,
                        "both_season_draw_rates_ok": both_season_draw_rates_ok,
                        "recent_home": home_recent,
                        "recent_away": away_recent,
                        "recent_both_ge2": recent_both_ge2,
                        "recent_goals_avg": recent_goals_avg,
                        "recent_goals_ok": recent_goals_ok,
                        "recent_both_goals": recent_both_ge2 and recent_goals_ok,
                        "confirmations": confirmations,
                    }
                )
                processed += 1
                if processed % 25 == 0:
                    log(
                        f"{processed} partidos procesados "
                        f"(último: {day} {home_name} vs {away_name})",
                        args.quiet,
                    )
                if args.limit and processed >= args.limit:
                    break
            if args.limit and processed >= args.limit:
                break
        if args.limit and processed >= args.limit:
            break

    if not matches:
        raise SystemExit("No se encontraron partidos con cuota en el rango solicitado.")

    summary = build_summary(matches)
    meta = {
        "generated_at": datetime.now(gd.BOGOTA).isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": len(days),
        "weeks": max(1, args.weeks),
        "leagues": [league["id"] for league in leagues],
        "recent_n": RECENT_N,
        "recent_min_played": RECENT_MIN_PLAYED,
        "recent_goals_max": RECENT_GOALS_MAX,
        "season_goals_max": SEASON_GOALS_MAX,
        "min_odds": MIN_ODDS,
        "max_odds": MAX_ODDS,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"backtest-{end.isoformat()}"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    json_path.write_text(
        json.dumps({"meta": meta, "summary": summary, "matches": matches}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_markdown(md_path, meta, summary)
    write_html(html_path, meta, summary, matches)

    print(f"Partidos: {len(matches)} · con cuota: {summary['matches_with_odds']} · válidos: {summary['matches_valid']}")
    print(f"Reporte: {md_path}")
    print(f"Página: {html_path}")
    print(f"Datos: {json_path}")
    print("")
    print("| Regla | n | Empates | Acierto | ROI |")
    print("|---|---:|---:|---:|---:|")
    for rule in summary["rules"]:
        print(
            f"| {rule['label']} | {rule['matches']} | {rule['draws']} | "
            f"{format_pct(rule['hit_rate'])} | {format_pct(rule['roi'])} |"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
