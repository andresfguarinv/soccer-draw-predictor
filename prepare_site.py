#!/usr/bin/env python3
"""
Construye la carpeta `docs/` lista para publicar en hosting estático.

Solo copia HTML: el índice del archivo y las páginas fechadas. No copia datos,
código, caché ni credenciales, así que es seguro subir `docs/` a Netlify,
Cloudflare Pages o GitHub Pages.

Uso:
  python3 prepare_site.py
"""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
PAGES = ROOT / "pages"
DATA = ROOT / "data"
DATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def load_entries() -> list[dict]:
    entries: list[dict] = []
    for snapshot in sorted(DATA.glob("*.json")):
        if not DATE_FILE_RE.fullmatch(snapshot.name):
            continue
        date = snapshot.stem
        page = PAGES / f"{date}.html"
        if not page.exists():
            continue
        try:
            data = json.loads(snapshot.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        matches = data.get("matches") or []
        candidates = [m for m in matches if m.get("status") == "candidate"]
        entries.append(
            {
                "date": date,
                "label": data.get("date_label") or date,
                "matches": len(matches),
                "candidates": candidates,
            }
        )
    entries.sort(key=lambda item: item["date"], reverse=True)
    return entries


def candidate_summary(candidates: list[dict]) -> str:
    if not candidates:
        return "Sin candidatos"
    parts = []
    for match in candidates[:3]:
        odds = match.get("odds") or []
        best = max((odd for _, odd in odds), default=None)
        score = match.get("score")
        detail = f"{match.get('home', 'Local')} vs {match.get('away', 'Visitante')}"
        if score is not None:
            detail += f" · {score}/8"
        if best is not None:
            detail += f" · {best:.2f}"
        parts.append(detail)
    if len(candidates) > 3:
        parts.append(f"+{len(candidates) - 3} más")
    return " · ".join(parts)


def render_index(entries: list[dict]) -> str:
    latest = entries[0] if entries else None
    cards = []
    for entry in entries:
        count = len(entry["candidates"])
        count_text = f"{count} candidato{'s' if count != 1 else ''}" if count else "Sin candidatos"
        cards.append(
            f"""
      <a class="day" href="pages/{entry['date']}.html">
        <div class="day-top">
          <strong>{html.escape(entry['label'])}</strong>
          <span>{entry['matches']} partido{'s' if entry['matches'] != 1 else ''} · {count_text}</span>
        </div>
        <p>{html.escape(candidate_summary(entry['candidates']))}</p>
      </a>"""
        )

    latest_link = ""
    if latest:
        latest_link = (
            f'<a class="latest" href="pages/{latest["date"]}.html">'
            f"Abrir la última fecha →</a>"
        )

    cards_html = "\n".join(cards) if cards else (
        '<p class="empty">Todavía no hay páginas generadas. Ejecuta '
        "<code>python3 generate_daily.py</code> y luego <code>python3 prepare_site.py</code>.</p>"
    )

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Soccer Draw Predictor — Archivo</title>
<style>
  :root{{
    --bg:#0b0f14;--card:#121923;--line:rgba(255,255,255,.09);--text:#e8eef7;
    --muted:#91a0b4;--yellow:#fbbf24;--green:#34d399;
  }}
  *{{box-sizing:border-box}}
  body{{
    margin:0;background:radial-gradient(900px 500px at 20% -10%,rgba(251,191,36,.10),transparent 60%),var(--bg);
    color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    line-height:1.5;min-height:100vh;
  }}
  main{{max-width:860px;margin:0 auto;padding:48px 20px 64px}}
  .eyebrow{{margin:0 0 10px;color:var(--yellow);font-size:12px;letter-spacing:.14em;text-transform:uppercase;font-weight:700}}
  h1{{margin:0;font-size:clamp(30px,6vw,48px);letter-spacing:-.035em;line-height:1.05}}
  .sub{{margin:14px 0 22px;color:var(--muted);max-width:650px}}
  .latest{{
    display:inline-block;margin-bottom:28px;padding:11px 16px;border-radius:999px;
    color:#111;background:var(--yellow);font-weight:700;text-decoration:none;font-size:14px;
  }}
  .latest:hover{{filter:brightness(1.06)}}
  .list{{display:grid;gap:12px}}
  .day{{
    display:block;padding:18px 18px 16px;border:1px solid var(--line);border-radius:16px;
    background:linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,.015));
    color:inherit;text-decoration:none;transition:.15s;
  }}
  .day:hover{{border-color:rgba(251,191,36,.45);transform:translateY(-1px)}}
  .day-top{{display:flex;align-items:baseline;justify-content:space-between;gap:14px;flex-wrap:wrap}}
  .day-top strong{{font-size:17px}}
  .day-top span{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}}
  .day p{{margin:8px 0 0;color:var(--muted);font-size:14px}}
  .empty{{border:1px solid var(--line);border-radius:16px;padding:22px;color:var(--muted);background:var(--card)}}
  code{{color:var(--yellow);font-size:.92em}}
  footer{{margin-top:34px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:18px}}
  @media(max-width:560px){{main{{padding-top:32px}}}}
</style>
</head>
<body>
<main>
  <p class="eyebrow">Soccer Draw Predictor</p>
  <h1>Archivo de predicciones</h1>
  <p class="sub">Páginas diarias generadas con ESPN. Cada fecha conserva sus candidatos, cuotas y criterios. Confirma siempre la cuota final en Betplay.</p>
  {latest_link}
  <section class="list" aria-label="Fechas disponibles">
    {cards_html}
  </section>
  <footer>No es consejo de apuestas. Juego responsable, +18.</footer>
</main>
</body>
</html>
"""


def main() -> int:
    entries = load_entries()
    if DOCS.exists():
        shutil.rmtree(DOCS)
    (DOCS / "pages").mkdir(parents=True, exist_ok=True)

    for entry in entries:
        source = PAGES / f"{entry['date']}.html"
        shutil.copy2(source, DOCS / "pages" / source.name)

    (DOCS / "index.html").write_text(render_index(entries), encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    print(f"Sitio listo: {DOCS}")
    print(f"Páginas: {len(entries)}")
    if entries:
        print(f"Última fecha: {entries[0]['label']} → docs/pages/{entries[0]['date']}.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
