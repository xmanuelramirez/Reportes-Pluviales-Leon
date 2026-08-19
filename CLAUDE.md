# Reportes Pluviales León

Aplicación Streamlit que arma mapas de lluvia acumulada para León, Gto. Baja
datos de las estaciones de SAPAL y de CONAGUA, interpola con Kriging o IDW y
publica el mapa y sus estadísticas. La usa el Departamento de Planeación
Hídrica de SAPAL.

## Cómo se corre

```bash
pip install -r requirements.txt
streamlit run RAINFALL_MAPS_STREAMLIT.py
```

El contenedor de desarrollo (`.devcontainer/devcontainer.json`) instala
`packages.txt` y `requirements.txt` y levanta la app en el puerto 8501.
`runtime.txt` fija Python 3.11 para el despliegue.

## Cómo se verifica un cambio

```bash
python3 scripts/verificar.py          # sintaxis, dependencias, recursos, shapefiles
python3 scripts/verificar.py --red    # además prueba que SAPAL y CONAGUA respondan
python3 scripts/verificar.py --json   # misma revisión, salida para automatización
```

Sale con código 0 si aprueba y 1 si hay errores. **Córrelo antes de cerrar
cualquier cambio**: es lo que permite que un loop desatendido sepa si ya
terminó. Un aviso no tumba la verificación; un error sí.

Las dependencias pesadas (geopandas, rasterio, pykrige) no siempre están
instaladas. El verificador está escrito para eso: lo que no puede comprobar lo
marca `OMITIDO` en lugar de inventar un fallo.

## Estructura

| Ruta | Qué es |
| --- | --- |
| `RAINFALL_MAPS_STREAMLIT.py` | La aplicación completa, en un solo archivo (~1600 líneas) |
| `shapefiles/` | Capas base, raster de sombreado, logos institucionales |
| `scripts/verificar.py` | Verificador que cierra el loop |
| `scripts/corregir-hasta-aprobar.sh` | Loop de verificación y corrección, con tope de intentos |
| `docs/AGENTIC_LOOPS.md` | Cómo automatizar trabajo repetido sobre este repositorio |
| `.claude/skills/revisar-pluvial/` | Revisión diaria de disponibilidad de datos |

## Fuentes de datos

- **SAPAL** — `services.sapal.gob.mx/portal/v1/climate/getMapStationList` para el
  catálogo de estaciones y `.../getHistory` para el historial. Requieren
  cabecera `Referer: https://www.sapal.gob.mx/`.
- **CONAGUA** — `sih.conagua.gob.mx/basedatos/climas/{estacion}.csv`. Se piden
  con `verify=False` porque el certificado del sitio no valida, y con
  `User-Agent` de navegador porque si no rechaza la petición.

Las fechas de CONAGUA llegan en `m/d/yyyy` o `d/m/yyyy` sin marca de cuál. El
parseo heurístico vive en `fetch_conagua_data`; si tocas esa función, prueba
con estaciones de ambos formatos antes de dar por bueno el cambio.

## Reglas del proyecto

- Las estaciones se cruzan por `Name` + `ENTIDAD` contra
  `shapefiles/ESTACIONES_actualizado.shp`. Si agregas una estación en la API
  pero no en el shapefile, el `merge` la descarta en silencio y el mapa sale
  incompleto sin marcar error.
- Un shapefile nunca viaja solo: `.shp` sin su `.dbf`, `.shx` y `.prj` no abre.
  El verificador lo revisa.
- Toda dependencia nueva se declara en `requirements.txt` con versión fija. Que
  hoy llegue como dependencia indirecta no la hace declarada.
- Los colores institucionales están en `config.toml`: primario `#007dbf`, texto
  `#122045`. No metas otra paleta.
- El repositorio es público. No entra ninguna credencial, ni coordenadas
  puntuales de instalaciones, ni datos que identifiquen a un usuario.
