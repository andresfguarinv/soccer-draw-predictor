# Efectividad de los candidatos — semana 20–26/10/2026

Revisión de los candidatos publicados por el generador y contraste con los resultados reales.
Las cuotas son la referencia de ESPN/DraftKings; no son la cuota final de Betplay.

## Resultado de los 6 candidatos publicados

| Fecha | Liga | Partido | Score | Cuota | Resultado | ¿Empate? |
|---|---|---|---:|---:|---|---|
| 23/10 | esp2 | Granada vs Real Valladolid | 5/8 | 3.10 | 2–1 | ❌ |
| 23/10 | par | Nacional Asunción vs Sportivo Trinidense | 8/8 | 3.00 | 0–1 | ❌ |
| 24/10 | itb | US Avellino vs Padova | 6/8 | 3.05 | 0–1 | ❌ |
| 24/10 | ita | Cagliari vs Bologna | 5/8 | 3.15 | 0–2 | ❌ |
| 25/10 | col | Alianza vs Internacional de Bogotá | 8/8 | 3.20 | 1–0 | ❌ |
| 25/10 | esp2 | CD Sabadell vs Tenerife | 7/8 | 3.15 | 1–1 | ✅ |

- **Aciertos:** 1 de 6 (16.7%).
- **Cuota media:** 3.10; break-even necesario: 32.3%.
- **ROI con 1 unidad plana:** 3.15 − 6 = −2.85 unidades ⇒ **−47.5%**.
- **IC95% del acierto (Wilson):** 3.0%–56.4%. La muestra no permite concluir que el modelo sea mejor o peor que el azar.

Incluyendo el candidato manual del 20/10 (Pereira 2–0 Chicó, 6/8, cuota 3.00, no publicado):
**1 de 7 (14.3%)**, ROI −55.0%.

## Contexto de la semana

- Partidos terminados en las 10 ligas: **101**.
- Empates: **26 (25.7%)**.
- Partidos con cuota de empate válida (2.60–3.20): **15**.
- Empates entre los válidos: **6 (40.0%)**.
- Empates entre los 9 partidos con cuota válida que el modelo **rechazó**: **5 (55.6%)**.

El filtro simple de cuota habría acertado 6/15 (40%), mejor que los 6 candidatos elegidos (1/6).
Con 6–15 partidos, la diferencia es descriptiva, no estadísticamente concluyente.

## Comparación por criterio (15 partidos con cuota válida)

| Criterio | Pasa: empates | No pasa: empates |
|---|---|---|
| Zona media | 1/6 (17%) | 5/9 (56%) |
| Diferencia de puntos ≤ 5 | 1/7 (14%) | 5/8 (62%) |
| Empates del local en casa ≥ 25% | 4/12 (33%) | 2/3 (67%) |
| Empates del visitante fuera ≥ 25% | 5/11 (45%) | 1/4 (25%) |
| Goles de temporada ≤ 2.5 | 4/8 (50%) | 2/7 (29%) |
| H2H ≥ 2 empates | 2/3 (67%) | 4/12 (33%) |
| Sin goleador en forma | 4/9 (44%) | 2/6 (33%) |
| Sin presión extrema | 2/6 (33%) | 4/9 (44%) |

Por score: 5/8 → 0/4; 6/8 → 0/1; 7/8 → 1/1; 8/8 → 0/2.

## Lectura

1. La semana fue mala para el modelo: el único acierto fue el candidato de 7/8, y los dos 8/8 fallaron.
2. Varios criterios base discriminaron al revés en esta muestra (zona media, diferencia de puntos, presión).
   No hay evidencia suficiente para invertirlos; el tamaño de muestra es demasiado pequeño.
3. El filtro de cuota por sí solo habría rendido mejor esta semana, lo que sugiere que el score base puede estar
   añadiendo ruido en lugar de señal.
4. Antes de cambiar `main`, se prueba una variante experimental con **forma reciente** (empates y goles de los
   últimos 5 partidos) y umbral **7/10**, validada con un backtest de 8 semanas sin look-ahead.

## Backtest de 12 semanas (03/08–25/10/2026)

Se reconstruyeron las cuotas de cierre de DraftKings vía ESPN, usando solo partidos anteriores al kickoff
para cada feature.

| Regla | Partidos | Empates | Acierto | ROI |
|---|---:|---:|---:|---:|
| Todas las cuotas válidas (base) | 205 | 65 | 31.7% | −2.7% |
| Forma reciente: ambos ≥2 empates | 30 | 6 | 20.0% | −38.7% |
| Goles recientes ≤ 2.60 | 105 | 31 | 29.5% | −10.0% |
| **Forma reciente + goles recientes (experimental)** | **22** | **4** | **18.2%** | **−45.0%** |
| Goles de temporada ≤ 2.5 | 94 | 29 | 30.9% | −5.6% |
| Cuota de cierre < apertura | 86 | 26 | 30.2% | −8.1% |

- La regla experimental quedó 13.5 puntos por debajo de la base (18.2% vs 31.7%). Fisher exacto bilateral
  p ≈ 0.22: la diferencia no es estadísticamente significativa, pero tampoco hay evidencia de mejora.
- La “base” es tomar todas las cuotas válidas (2.60–3.20), no el score de 8 criterios: el backtest no
  reconstruye standings ni goleadores históricos.
- El backtest de 8 semanas fue consistente: 17.6% vs 30.3%.
- Ninguna variante de forma reciente superó la base. La única señal positiva aislada (Colombia, 8 semanas)
  desapareció al ampliar a 12 semanas: 33.3% vs 37.7% de la base en Colombia.
- **Conclusión:** no hay evidencia para promover el modo experimental a `main`. La rama queda como
  investigación reproducible y `--experimental` sigue etiquetado como no validado.

Reporte completo: `reports/backtest-2026-10-25.md`, `.json` y `.html`.

## Decisión

- Mantener `main` intacto.
- Rama `experiment/backtest-forma-reciente` con:
  - `generate_daily.py --experimental` (10 criterios, 7/10, forma reciente obligatoria).
  - `backtest.py` para medir reglas con cuotas históricas de cierre de ESPN.
  - Reporte `reports/backtest-YYYY-MM-DD.{md,json,html}`.
- Promover a `main` solo si el backtest muestra una mejora consistente y con muestra suficiente.
  **El backtest de 12 semanas no mostró mejora, así que esta rama no se fusiona.**

## Aviso

No es consejo de apuestas. Confirma la cuota final en Betplay. Juego responsable, +18.
