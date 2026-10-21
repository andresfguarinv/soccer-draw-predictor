# Soccer Draw Predictor — generador diario

`generate_daily.py` consulta **ESPN** (endpoints públicos, gratis y sin API key), calcula los 8 criterios de la skill y genera el `index.html` de un solo día. Cada ejecución también crea una página fechada en `pages/YYYY-MM-DD.html` y guarda un snapshot en `data/YYYY-MM-DD.json`.

## Requisitos

- Python 3.9 o superior.
- Conexión a internet.
- **No necesitas API key ni registro.**

> API-Football se mantiene como modo opcional (`--source api-football`), pero su plan free no permite consultar la temporada 2026: solo habilita 2022–2024. Para datos actuales gratis, la fuente por defecto es ESPN.

## Uso rápido

Genera el día de hoy en Bogotá:

```bash
python3 generate_daily.py
```

Genera una fecha concreta:

```bash
python3 generate_daily.py --date 2026-10-20
```

Cada ejecución actualiza `index.html` con el día generado y deja una copia fechada en `pages/YYYY-MM-DD.html`. La carpeta se crea automáticamente.

Para preparar la carpeta que se publica en hosting estático:

```bash
python3 prepare_site.py
```

## Comandos útiles

```bash
# Ver las peticiones HTTP
python3 generate_daily.py --verbose

# Ignorar la caché local
python3 generate_daily.py --no-cache

# Ver el JSON sin escribir archivos
python3 generate_daily.py --dry-run

# Regenerar el HTML desde un snapshot (sin red)
python3 generate_daily.py --from-json data/2026-10-20.json

# Simular una hora concreta para probar el filtro de partidos iniciados
python3 generate_daily.py --date 2026-10-20 --now 2026-10-20T15:42:00-05:00
```

## Fuente de datos

### ESPN (por defecto)

No usa key. Consulta:

- `site.api.espn.com/.../scoreboard?dates=YYYYMMDD` para fixtures y cuotas de empate.
- `site.api.espn.com/apis/v2/.../standings?season=YYYY` para posiciones y puntos.
- `site.api.espn.com/.../teams/{id}/schedule?season=YYYY` para estadísticas local/visitante y H2H.
- `site.api.espn.com/.../statistics?season=YYYY` para goleadores.

La cuota de empate se toma de `drawOdds.moneyLine` (formato americano) y se convierte a decimal: `+195` → `2.95`, `+230` → `3.30`.

### API-Football (opcional)

Solo si tienes un plan con acceso a la temporada actual:

```bash
cp .env.example .env
# pega tu key en .env
python3 generate_daily.py --source api-football
```

El plan free de API-Football responde `Free plans do not have access to this season, try from 2022 to 2024` para 2026, así que no sirve para el objetivo diario.

## Qué hace el script

1. Consulta los fixtures del día para las 10 ligas.
2. Omite partidos que ya empezaron o terminaron, comparando la hora de inicio con `--now` (America/Bogota).
3. Excluye equipos grandes con la lista `BIG_TEAMS_RAW`.
4. Se queda con las cuotas de empate en **2.60–3.20**.
5. Para los partidos con cuota válida o cercana (2.40–3.40), consulta:
   - Standings: posición, puntos y tamaño de la tabla.
   - Estadísticas del local y del visitante: empates en casa/fuera y promedio de goles de la temporada actual.
   - H2H: últimos 5 partidos terminados (temporada actual + dos anteriores).
   - Goleadores: proxy de "goleador en forma" (≥ 8 goles en la temporada).
6. Calcula el score base de 8 criterios.
7. Marca:
   - `candidate`: score ≥ 5/8 y al menos una cuota en 2.60–3.20.
   - `rejected`: score < 5/8 o cuota fuera de rango.
   - `excluded`: equipo grande.
   - `pending`: no hay cuota de empate disponible.
8. Inyecta los datos en `index.html`, crea `pages/YYYY-MM-DD.html` y guarda `data/YYYY-MM-DD.json`.

## Score y ajustes

El score que se muestra es la **base de 8 criterios**. Los ajustes cualitativos de la skill (simetría motivacional, altura, copa continental, riesgo de expulsión, primeras fechas) se muestran como **alertas** para revisión manual. Esto evita que una regla automática descarte un candidato sin contexto.

Si no hay ningún candidato, el HTML recomienda esperar.

En la parte superior, la tarjeta **Candidatos** funciona como filtro: al hacer clic muestra solo los partidos candidatos; al volver a pulsarla, se quita el filtro.

## Estado actual

El `index.html` incluido es el snapshot generado con ESPN para el **20/10/2026 a las 15:42 (Bogotá)**. El candidato es **Deportivo Pereira vs Boyacá Chicó**, 6/8, con cuota de empate **2.95 en DraftKings**.

## Configuración de ligas y equipos

- Las ligas están en `LEAGUES`, dentro de `generate_daily.py`. Cada una tiene su `espn_slug`, país y prioridad.
- Los equipos grandes están en `BIG_TEAMS_RAW`. La comparación ignora tildes, mayúsculas y sufijos como `FC`, `CF`, `Club` o `AC`.
- Si un equipo recién descendido debe excluirse en una segunda división, agrégalo a la lista de esa liga.
- Si ESPN cambia un slug, actualízalo en `LEAGUES`.

## Temporadas

- Ligas europeas: temporada que empieza en julio/agosto. En octubre de 2026 se usa `season=2026`.
- Colombia, Paraguay y MLS: año calendario. En 2026 se usa `season=2026`.
- En Colombia, ESPN devuelve Apertura y Clausura en el mismo calendario; el script filtra por el `seasonType` actual (por ejemplo, `Clausura`).

## Caché y peticiones

La caché vive en `.cache/espn/`:

- Scoreboards y cuotas: 5 minutos.
- Standings, estadísticas y goleadores: 1 hora.
- Calendarios de equipo (stats y H2H): 6 horas.

El script imprime cuántas peticiones hizo y cuántas salieron de caché.

## Cron diario

Ejemplo para generar el HTML y actualizar el sitio publicable todos los días a las 00:05 de Bogotá:

```cron
5 0 * * * cd "/Users/andresguarin/Soccer Draw Predictor" && { /usr/bin/python3 generate_daily.py && /usr/bin/python3 prepare_site.py; } >> generate.log 2>&1
```

## Publicar en hosting estático

`prepare_site.py` crea la carpeta `docs/`, que contiene **solo HTML**:

```text
docs/
├── index.html              # archivo con enlaces a todas las fechas
├── .nojekyll
└── pages/
    ├── 2026-10-20.html
    └── 2026-10-21.html
```

No subas `data/`, `.env`, `.cache/` ni el código Python: no hacen falta para el sitio y pueden contener datos que no quieres publicar. La URL de cada día queda así:

```text
https://tu-sitio/pages/2026-10-21.html
```

### Netlify Drop (la opción más rápida)

1. Ejecuta `python3 generate_daily.py` y `python3 prepare_site.py`.
2. Entra a <https://app.netlify.com/drop>.
3. Arrastra la carpeta `docs/`.
4. Netlify te da una URL como `https://nombre.netlify.app/pages/2026-10-21.html`.
5. Puedes cambiar el nombre del sitio en la configuración.

### Cloudflare Pages

1. Entra a Cloudflare Pages y elige **Direct Upload**.
2. Sube la carpeta `docs/`.
3. Cloudflare te da una URL como `https://proyecto.pages.dev/pages/2026-10-21.html`.

### GitHub Pages

El repositorio incluye el workflow `.github/workflows/pages.yml`, que genera y publica el sitio todos los días a las 00:05 de Bogotá.

1. Crea un repositorio **vacío** en GitHub, sin README ni `.gitignore`, y copia su URL.
2. Conecta el repositorio local y sube la rama `main`:

   ```bash
   git remote add origin https://github.com/TUUSUARIO/TUREPO.git
   git push -u origin main
   ```

3. En **Settings → Pages → Build and deployment**, elige **GitHub Actions** como fuente.
4. Ejecuta el workflow una vez desde **Actions → Generar y publicar predicciones → Run workflow**.
5. El sitio queda en `https://tuusuario.github.io/turepo/` y cada fecha en
   `https://tuusuario.github.io/turepo/pages/2026-10-21.html`.

El workflow también hace commit de `index.html`, `pages/`, `data/` y `docs/` para conservar el archivo histórico. Si prefieres actualizar desde tu Mac, puedes usar el cron de arriba y luego hacer `git push`; GitHub Pages publicará los cambios.

## Archivos

- `index.html`: página del día (template + datos inyectados).
- `pages/YYYY-MM-DD.html`: copia fechada de cada día generado.
- `docs/`: sitio listo para publicar (solo HTML).
- `prepare_site.py`: genera el archivo `docs/index.html` y copia las páginas fechadas.
- `.github/workflows/pages.yml`: generación y despliegue diario en GitHub Pages.
- `generate_daily.py`: generador.
- `data/YYYY-MM-DD.json`: snapshot de datos del día.
- `data/2026-10-20-manual.json`: referencia manual verificada del 20/10/2026.
- `.env`: opcional, solo para `--source api-football`.
- `.cache/espn/`: caché local de ESPN.
- `.cache/api-football/`: caché local del modo opcional.

## Avisos

- Ninguna API pública garantiza la cuota exacta de **Betplay**. La API da una referencia; confirma la cuota final en Betplay.
- ESPN no siempre publica cuota de empate para todas las ligas/fechas (por ejemplo, Paraguay). En esos casos el partido queda como `pending` y muestra “cuota pendiente”.
- Las cuotas cambian. Esto no es consejo de apuestas. Juego responsable, +18.
