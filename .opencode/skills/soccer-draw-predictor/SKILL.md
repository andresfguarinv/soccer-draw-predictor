---
name: soccer-draw-predictor
description: >
  Identifica partidos de fútbol con alta probabilidad de empate, con cuota alrededor de 3.0 o menos y sin equipos
  grandes. Usa esta skill cuando el usuario quiera encontrar partidos propensos al empate, analizar ligas o equipos
  con alta tasa de empates, filtrar partidos evitando equipos grandes o calcular cuotas esperadas alrededor de 3.0.
  También sirve para regenerar el HTML diario con ESPN (gratis, sin API key) y revisar candidatos.
  Trigger con: "partidos para empate", "análisis de empates", "qué partido tiene más probabilidad de empatar",
  "cuota empate", "predicciones para [fecha]", o cualquier combinación de fútbol + empate + apuestas.
---

# Soccer Draw Predictor

## Objetivo

Identificar los partidos con mayor probabilidad estadística de terminar en empate y generar el HTML diario del
proyecto. La skill solo identifica y reporta los partidos que cumplen los criterios. No gestiona apuestas, montos ni
ciclos de progresión.

---

## Reglas de operación (siempre)

1. **Escanear las 10 ligas en cada consulta.** El generador ya lo hace; no hagas escaneos parciales ni omitas
   Colombia, Paraguay o MLS.
2. **Zona horaria: America/Bogota (UTC-5).** La fecha y la hora de corte del HTML son las de Bogotá. Un partido
   nocturno europeo puede caer en el día siguiente en Colombia.
3. **Mostrar solo el día consultado.** Omitir por completo los partidos que ya empezaron o terminaron; no mostrarlos
   ni siquiera como descartados.
4. **Mostrar todos los candidatos del día,** agrupados por liga. No elegir uno solo ni ocultar los demás.
5. **Recomendar día por día.** No agrupar varios días en una sola revisión.
6. **No inventar datos.** Si ESPN no publica una cuota o un criterio no se puede verificar, el partido queda como
   `pending` o el criterio como `?`. No rellenar con estimaciones.
7. **La cuota final se confirma en Betplay.** Las cuotas del HTML son una referencia de mercado, no una garantía.

---

## Flujo obligatorio

El proyecto es un generador local. La fuente de verdad es `generate_daily.py` y el snapshot que produce, no una
recolección manual de enlaces.

1. Determinar la fecha en America/Bogota.
2. Ejecutar el generador para esa fecha.
3. Leer el snapshot `data/YYYY-MM-DD.json`, el HTML generado y la página fechada `pages/YYYY-MM-DD.html`.
4. Si se va a publicar, ejecutar `python3 prepare_site.py` y revisar `docs/index.html`.
5. Reportar los candidatos y las alertas. Si no hay candidatos, recomendar esperar.
6. Si el usuario pide revisar la interfaz, abrir la página del día y comprobar el filtro de candidatos.

### Comando principal

```bash
# Día de hoy en Bogotá (fuente ESPN por defecto)
python3 generate_daily.py

# Una fecha concreta
python3 generate_daily.py --date 2026-10-20

# Simular una hora de corte para probar el filtro de partidos iniciados
python3 generate_daily.py --date 2026-10-20 --now 2026-10-20T15:42:00-05:00

# Regenerar el HTML desde un snapshot, sin red
python3 generate_daily.py --from-json data/2026-10-20.json

# Ver el JSON sin escribir archivos
python3 generate_daily.py --dry-run
```

Cada ejecución actualiza `index.html` con el día generado y crea o actualiza la página fechada
`pages/YYYY-MM-DD.html`. La carpeta `pages/` se crea automáticamente.

Para preparar la carpeta publicable:

```bash
python3 prepare_site.py
```

Esto crea `docs/` con `index.html` (archivo de fechas), `pages/YYYY-MM-DD.html` y `.nojekyll`. Solo contiene HTML;
no incluye `data/`, `.env`, `.cache/` ni código Python.

### Flags útiles

| Flag | Uso |
|------|-----|
| `--source espn` | Fuente por defecto: pública, gratis y sin API key. |
| `--source api-football` | Modo opcional; requiere `API_FOOTBALL_KEY` y un plan con acceso a la temporada actual. |
| `--from-json PATH` | Genera el HTML desde un snapshot local. No consulta internet. |
| `--no-cache` | Ignora la caché local de ESPN. |
| `--verbose` | Muestra las peticiones HTTP. |
| `--include-started` | Solo para pruebas: incluye partidos ya iniciados. No usarlo para el HTML real. |
| `--template`, `--out`, `--data-dir` | Personalizan la plantilla, el HTML de salida y el directorio de snapshots. |
| `--archive-dir` | Directorio de las páginas fechadas (default: `pages`). |

Para una prueba que no sobrescriba el día real, usa `--out`, `--data-dir` y `--archive-dir` temporales.

---

## Ligas objetivo (10)

| ID | Slug ESPN | Liga | País | Prioridad |
|----|-----------|------|------|-----------|
| `col` | `col.1` | Primera A | Colombia | Máxima |
| `par` | `par.1` | División Profesional | Paraguay | Máxima |
| `itb` | `ita.2` | Serie B | Italia | Alta |
| `esp2` | `esp.2` | Segunda División | España | Alta |
| `eng2` | `eng.2` | Championship | Inglaterra | Alta |
| `ger2` | `ger.2` | Bundesliga 2 | Alemania | Media |
| `ita` | `ita.1` | Serie A | Italia | Media |
| `esp` | `esp.1` | La Liga | España | Media |
| `fra` | `fra.1` | Ligue 1 | Francia | Media |
| `usa` | `usa.1` | MLS | Estados Unidos | Media |

La configuración vive en `LEAGUES`, dentro de `generate_daily.py`. Si ESPN cambia un slug, actualízalo ahí.

---

## Reglas de datos

- **Temporada:** ligas europeas usan la temporada que empieza en julio/agosto; Colombia, Paraguay y MLS usan año
  calendario. En Colombia se filtra por el `seasonType` actual (Apertura/Clausura).
- **Cuota de empate:** se toma de `drawOdds.moneyLine` de ESPN, en formato americano, y se convierte a decimal:
  `+195` → `2.95`, `+230` → `3.30`.
- **Rango válido:** solo cuotas entre **2.60 y 3.20**. Por debajo de 2.60 el favorito es demasiado claro; por encima
  de 3.20 el mercado no ve el empate probable.
- **H2H:** últimos 5 partidos terminados entre los dos equipos, usando la temporada actual y las dos anteriores.
- **Estadísticas:** posición, puntos y tamaño de la tabla; empates del local en casa y del visitante fuera; promedio
  de goles de la temporada actual.
- **Goleadores:** proxy de “goleador en forma” con ≥ 8 goles en la temporada.
- **Cuota ausente:** si ESPN no publica cuota de empate, el partido queda `pending` y muestra “cuota pendiente”. No
  se convierte en candidato ni se inventa una cuota.

### Estados de un partido

| Estado | Significado |
|--------|-------------|
| `candidate` | Score ≥ 5/8 y al menos una cuota en 2.60–3.20. |
| `rejected` | Score < 5/8, o cuota fuera de 2.60–3.20. |
| `excluded` | Participa un equipo grande. No se evalúa. |
| `pending` | No hay cuota de empate disponible. |

---

## Filtro 1 — Equipos grandes (obligatorio)

Excluir cualquier partido donde participe:

- **España:** Real Madrid, Barcelona, Atlético Madrid.
- **Italia:** Juventus, Inter, AC Milan, Napoli.
- **Francia:** PSG / Paris Saint-Germain.
- **Alemania:** Bayern Múnich, Borussia Dortmund.
- **Inglaterra:** Manchester City, Arsenal, Liverpool, Chelsea, Manchester United, Tottenham.
- **Portugal:** Benfica, Porto, Sporting CP.
- **Colombia:** Atlético Nacional, Millonarios, América de Cali, Junior, Deportivo Cali.
- **Paraguay:** Olimpia, Cerro Porteño, Libertad, Guaraní.
- **Criterio general:** top 3-4 de cualquier liga en los últimos 3 años.

La lista real está en `BIG_TEAMS_RAW`. La comparación ignora tildes, mayúsculas y sufijos como `FC`, `CF`, `Club` o
`AC`. Si un recién descendido debe excluirse en una segunda división, agrégalo a la lista de esa liga.

---

## Filtro 2 — Cuota de empate

- Rango válido: **2.60–3.20**.
- Cuota ideal: entre **2.60 y 3.00**.
- Cuota < 2.60: favorito claro, ganancia baja.
- Cuota > 3.20: el mercado no ve el empate probable; evitar.
- El generador analiza además partidos con cuota cercana (2.40–3.40) para poder descartarlos con contexto, pero solo
  los que tienen una cuota dentro de 2.60–3.20 pueden quedar como `candidate`.

---

## Filtro 3 — Score estadístico (base de 8 criterios)

| # | Criterio | Descripción |
|---|----------|-------------|
| 1 | Zona media de la tabla | Ambos equipos en posiciones 6-15 de una liga de 18-20 equipos. |
| 2 | Diferencia de puntos ≤ 5 | Equipos muy igualados. |
| 3 | Empates del local en casa ≥ 25% | Historial de empates como local. |
| 4 | Empates del visitante fuera ≥ 25% | Historial de empates como visitante. |
| 5 | Promedio de goles ≤ 2.5 | Partidos de baja producción. |
| 6 | H2H: ≥ 2 empates en los últimos 5 | Historial directo favorable. |
| 7 | Sin goleador en forma | Ningún equipo con un jugador de ≥ 8 goles en la temporada. |
| 8 | Sin presión extrema | Ningún equipo en zona de descenso o de título. |

**Puntaje mínimo: 5/8.** El score que se muestra es la base de estos 8 criterios. Los criterios sin dato no suman.

Niveles de confianza:

- 🟢 **Alta:** 7-8 puntos.
- 🟡 **Media:** 5-6 puntos.
- 🔴 **Baja:** < 5 puntos; no recomendado.

### Ajustes cualitativos

Los ajustes no cambian automáticamente el score. El generador los muestra como **alertas** para revisión manual,
porque dependen de contexto que ESPN no siempre entrega:

- −2 si el local está en necesidad desesperada de descenso y juega en casa.
- +1 si el favorito tiene un partido de copa continental en los próximos 3-4 días.
- −1 por riesgo de expulsión temprana.
- −1 en las primeras 4-5 fechas del torneo (la tabla todavía no es confiable).
- +1 en partidos de ascenso/descenso en ligas secundarias.
- **Simetría motivacional:** si un equipo está matemáticamente eliminado o no se juega nada, tiende a perder por
  goleada y no a empatar. Penalizar o descartar.
- **Corrección de altura:** un local de altura con perfil ofensivo favorece la victoria local, no el empate.

Cuando una alerta cambie la recomendación, dilo explícitamente; no la escondas.

---

## Presentación y UI

El HTML lo genera `generate_daily.py`; no reconstruyas las cards a mano ni agregues un panel grande de “candidato del
día”. La información vive dentro de cada liga. Cada día tiene su propia página en `pages/YYYY-MM-DD.html`; `index.html`
queda como la última página generada.

- **Resumen superior:** ligas visibles, partidos del día, candidatos y descartados.
- **Filtro de candidatos:** la tarjeta **Candidatos** del resumen es un botón. Al hacer clic muestra solo los
  partidos candidatos y las ligas que los contienen; al volver a pulsarla, se quita el filtro. El estado se indica
  con borde/color activo y `aria-pressed`.
- **Candidatos por liga:** cada candidato aparece en la card de su liga, con todas sus señales. No se crea una card
  global separada.
- **Contenido de cada card:** liga, hora en Bogotá, equipos, score X/8, cuota y casa de apuestas, los 8 criterios
  (✓ cumple, ✗ no cumple, ? sin dato), nota y alertas.
- **Sin candidatos:** mostrar el mensaje de espera. Si el filtro está activo, el vacío debe decir que no hay
  candidatos con los filtros actuales.
- **Layout:** grilla de ligas a 2 columnas; cards a 1 columna en paneles angostos y 2 columnas en ligas destacadas o
  pantallas anchas. Evitar cards demasiado angostas.

Al responder en el chat, resume el snapshot: fecha, hora de corte, candidatos agrupados por liga, score, cuota y
alertas principales. No repitas los 8 criterios salvo que el usuario los pida.

---

## Publicación estática

La carpeta `docs/` es el sitio publicable. `docs/index.html` es un archivo con enlaces a todas las fechas y
`docs/pages/YYYY-MM-DD.html` son las páginas diarias. La URL de una fecha queda así:

```text
https://tu-sitio/pages/2026-10-21.html
```

Opciones de hosting:

- **Netlify Drop:** arrastrar `docs/` a <https://app.netlify.com/drop>.
- **Cloudflare Pages:** crear un proyecto con **Direct Upload** y subir `docs/`.
- **GitHub Pages:** subir el repositorio y elegir **GitHub Actions** como fuente. El workflow
  `.github/workflows/pages.yml` genera y publica el sitio todos los días a las 00:05 de Bogotá.

Nunca publicar `data/`, `.env`, `.cache/` ni el código Python. El sitio solo necesita HTML.

---

## Fuentes, caché y límites

### ESPN (por defecto)

No usa key. Endpoints principales:

- `site.api.espn.com/.../scoreboard?dates=YYYYMMDD` para fixtures y cuotas de empate.
- `site.api.espn.com/apis/v2/.../standings?season=YYYY` para posiciones y puntos.
- `site.api.espn.com/.../teams/{id}/schedule?season=YYYY` para estadísticas local/visitante y H2H.
- `site.api.espn.com/.../statistics?season=YYYY` para goleadores.

La caché vive en `.cache/espn/`:

- Scoreboards y cuotas: 5 minutos.
- Standings, estadísticas y goleadores: 1 hora.
- Calendarios de equipo (stats y H2H): 6 horas.

### API-Football (opcional)

Solo para planes pagos con acceso a la temporada actual:

```bash
cp .env.example .env
# pega tu key en .env
python3 generate_daily.py --source api-football
```

El plan free de API-Football no sirve para 2026: responde
`Free plans do not have access to this season, try from 2022 to 2024`.

### Límites

- ESPN no publica cuota de empate para todas las ligas/fechas (por ejemplo, Paraguay y algunos partidos de Colombia o
  Championship). Esos partidos quedan `pending`.
- Betplay es una app dinámica; el usuario revisa la cuota final directamente ahí.
- Las cuotas cambian. Recordar al usuario confirmarlas en su casa de apuestas.
- Si el generador falla, reportar el error y ofrecer el último snapshot disponible. No inventar partidos, cuotas ni
  criterios.

---

## Archivos del proyecto

- `generate_daily.py`: generador, scoring, filtros y fuente ESPN/API-Football.
- `index.html`: plantilla y última página generada.
- `pages/YYYY-MM-DD.html`: página fechada de cada día generado.
- `docs/`: sitio listo para publicar (solo HTML).
- `prepare_site.py`: genera el archivo `docs/index.html` y copia las páginas fechadas.
- `.github/workflows/pages.yml`: generación y despliegue diario en GitHub Pages.
- `data/YYYY-MM-DD.json`: snapshot de datos del día.
- `data/2026-10-20-manual.json`: referencia manual verificada del 20/10/2026.
- `.cache/espn/`: caché local de ESPN.
- `.cache/api-football/`: caché local del modo opcional.
- `.env`: opcional, solo para `--source api-football`.
- `README.md`: uso, flags y configuración.

---

## Notas importantes

- Si el usuario pregunta por la tarde en Colombia, muchos partidos europeos del fin de semana ya habrán empezado o
  terminado (la hora CEST va 7 horas por delante de Colombia). El generador los omite.
- No agrupar revisiones de varios días.
- Si no hay ningún partido con score ≥ 5/8 y cuota válida, recomendar **esperar**.
- Esto no es consejo de apuestas. Juego responsable, +18.
