---
name: revisar-pluvial
description: Revisa si hay corte nuevo de datos pluviales de SAPAL o CONAGUA para León y reporta solo si hay novedad. Úsala para la vigilancia diaria en temporada de lluvias, como carga de un /loop o de una Routine programada, y cuando alguien pregunte si ya salieron los datos de hoy, si llovió ayer o si las estaciones están respondiendo. Úsala aunque solo digan "checa si ya hay datos".
---

# Revisión pluvial diaria

Esta revisión corre sola, casi siempre sin nadie viéndola, y muchas veces
arranca en frío sin memoria de la vuelta anterior. Está escrita para eso.

## Qué se revisa, en este orden

### 1. ¿Responden las fuentes?

```bash
python3 scripts/verificar.py --red
```

Si algún endpoint no responde, **repórtalo y detente ahí**. No sirve de nada
seguir analizando datos que no bajaron: un acumulado de cero porque no llovió y
un acumulado de cero porque la API no contestó se ven igual en el mapa, y esa
confusión es exactamente lo que hay que evitar.

Distingue en el reporte cuál de las dos fuentes falló. SAPAL y CONAGUA se caen
por separado y por razones distintas.

### 2. ¿Hay corte nuevo?

Compara la fecha más reciente disponible contra la del último reporte. La
lógica ya existe en `RAINFALL_MAPS_STREAMLIT.py`:

- `get_latest_conagua_date(stations)` devuelve la fecha máxima de CONAGUA.
- `fetch_sapal_data(...)` trae el historial de SAPAL por estación.

**Si no hay corte nuevo, termina sin escribir nada.** Un aviso diario de "sin
novedad" se vuelve ruido en dos semanas, y para entonces ya nadie lo lee el día
que sí traía algo.

### 3. ¿Qué reportar cuando sí hay novedad?

Cuatro datos, en este orden, y nada más:

1. La fecha del corte.
2. Cuántas estaciones reportaron y cuántas quedaron sin dato.
3. El acumulado máximo y en qué estación.
4. Cualquier valor que se salga de lo razonable.

Sobre el punto 4: la app tiene `filter_outliers()` porque las estaciones sí
mandan basura. Un acumulado de tres dígitos en 24 horas puede ser un evento
real —y entonces es justo lo que hay que avisar— o un sensor descompuesto.
**Márcalo como "hay que verificar", no como hecho.** No lo descartes en
silencio ni lo reportes como si estuviera confirmado.

## Lo que esta revisión no hace

- **No genera el mapa.** El mapa se genera en la aplicación, donde alguien
  escoge fecha, estaciones y método de interpolación. Esta revisión solo dice
  si ya hay con qué.
- **No corrige datos.** Si una estación viene rara, se reporta. La decisión de
  descartarla es de quien firma el reporte.
- **No publica nada fuera del área.** Los datos crudos de estaciones no salen
  sin revisión.

## Estaciones sin cruce

Si una estación aparece en la API pero no en
`shapefiles/ESTACIONES_actualizado.shp`, el `merge` por `Name` + `ENTIDAD` la
descarta **sin marcar error** y el mapa sale incompleto sin que nada avise. Si
lo detectas, repórtalo aunque no haya llovido: es un problema que crece callado
y solo se nota cuando ya salió un mapa mal.
