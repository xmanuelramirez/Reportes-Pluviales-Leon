# -*- coding: utf-8 -*-
"""
Created on Mon Aug 11 08:10:08 2025
@author: xmanu
Versión 12.0 - Versión final estable con lógica de SAPAL de R, CONAGUA en paralelo y flujo de UI corregido.
"""

# --- LIBRERÍAS PRINCIPALES ---
import zipfile # Para descomprimir el KMZ (que es un ZIP)
import xml.etree.ElementTree as ET # Para parsear el KML (XML)
from shapely.geometry import Point # Para construir las geometrías de los puntos
# Ya tienes geopandas, pero asegúrate de que esté importado al inicio
import geopandas as gpd
from matplotlib import patheffects
import warnings
from urllib3.exceptions import InsecureRequestWarning
warnings.filterwarnings("ignore", category=InsecureRequestWarning)
import rasterio
from rasterio.mask import mask # Importación corregida
import plotly.graph_objects as go
import streamlit as st
import pandas as pd
import geopandas as gpd
import requests
import time
from datetime import datetime
import numpy as np
import os
import io
import locale
from concurrent.futures import ThreadPoolExecutor, as_completed

# LIBRERÍAS DE VISUALIZACIÓN Y MAPEO
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.colorbar import ColorbarBase

import matplotlib.image as mpimg

from matplotlib.ticker import FuncFormatter
from matplotlib.patches import Patch, Polygon
from matplotlib.lines import Line2D
import pyproj
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# --- FUNCIÓN PARA OBTENER CHROME EN MODO HEADLESS ---
import tempfile
import uuid

@st.cache_data(ttl=3600) # Cachear para no procesar el KMZ en cada ejecución
def load_kmz_from_local(kmz_file_path):
    """
    Lee un archivo KMZ desde el sistema de archivos local (o clonado por Streamlit),
    lo descomprime y lo parsea para obtener un GeoDataFrame.
    """
    st.session_state.log_messages.append(f"📦 Procesando KMZ local: {kmz_file_path}...")
    st.session_state.log_container_placeholder.markdown("\n\n".join(st.session_state.log_messages))

    try:
        kml_content = None
        with zipfile.ZipFile(kmz_file_path, 'r') as zip_ref:
            for name in zip_ref.namelist():
                if name.lower().endswith('.kml'):
                    kml_content = zip_ref.read(name)
                    break
        
        if kml_content is None:
            raise ValueError("No se encontró ningún archivo KML dentro del KMZ.")

        st.session_state.log_messages.append("📝 KML extraído. Parseando placemarks...")
        st.session_state.log_container_placeholder.markdown("\n\n".join(st.session_state.log_messages))

        root = ET.fromstring(kml_content)
        namespace = '{http://www.opengis.net/kml/2.2}' # Namespace KML estándar
        
        placemarks = []
        for placemark in root.findall(f'.//{namespace}Placemark'):
            name_element = placemark.find(f'{namespace}name')
            name = name_element.text if name_element is not None else "Unnamed Sensor"
            
            point_node = placemark.find(f'{namespace}Point')
            if point_node is not None:
                coordinates_element = point_node.find(f'{namespace}coordinates')
                if coordinates_element is not None:
                    coordinates_text = coordinates_element.text.strip()
                    parts = coordinates_text.split(',')
                    if len(parts) >= 2:
                        lon, lat = float(parts[0]), float(parts[1])
                        placemarks.append({'Name': name, 'geometry': Point(lon, lat)})

        if not placemarks:
            raise ValueError("No se encontraron placemarks (puntos) en el KML.")

        gdf = gpd.GeoDataFrame(placemarks, crs="EPSG:4326") # KML usa WGS84 (4326)

        st.session_state.log_messages.append("✅ GeoDataFrame de sensores de río creado.")
        st.session_state.log_container_placeholder.markdown("\n\n".join(st.session_state.log_messages))
        
        return gdf

    except zipfile.BadZipFile:
        st.error(f"El archivo {kmz_file_path} no es un KMZ válido (no es un ZIP).")
        st.stop()
    except ValueError as e:
        st.error(f"Error al procesar el KML de {kmz_file_path}: {e}")
        st.stop()
    except FileNotFoundError:
        st.error(f"Archivo KMZ no encontrado en la ruta: {kmz_file_path}. Asegúrate de que esté en tu repositorio.")
        st.stop()
    except Exception as e:
        st.error(f"Ocurrió un error inesperado al cargar el KMZ {kmz_file_path}: {e}")
        st.stop()
    return gpd.GeoDataFrame() # Devuelve un GDF vacío en caso de fallo

def get_headless_chrome_driver():
    """
    Devuelve un WebDriver de Chrome en modo headless (sin interfaz gráfica), usando un user-data-dir realmente único y evitando conflictos de puerto.
    """
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    # Directorio temporal realmente único
    user_data_dir = os.path.join(tempfile.gettempdir(), f"chrome-user-data-{uuid.uuid4().hex}")
    chrome_options.add_argument(f'--user-data-dir={user_data_dir}')
    # Puerto de depuración aleatorio para evitar conflictos
    chrome_options.add_argument('--remote-debugging-port=0')
    return webdriver.Chrome(options=chrome_options)
from pykrige.ok import OrdinaryKriging
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.model_selection import LeaveOneOut

from rasterio.transform import from_origin

from rasterio.plot import show
# Agrégalo cerca de donde cargas los shapefiles
EXCEL_PATH = os.path.join("shapefiles", "GRÁFICA.xlsx")
warnings.simplefilter('ignore', InsecureRequestWarning)
# --- CONFIGURACIÓN DE LA PÁGINA Y ESTADO DE SESIÓN ---
# --- CONFIGURACIÓN DE LA PÁGINA Y ESTADO DE SESIÓN ---
# 1. ESTABLECER LA CONFIGURACIÓN DE LA PÁGINA (DEBE SER EL PRIMER COMANDO DE STREAMLIT)
st.set_page_config(page_title="Reporte Pluvial de León", layout="wide")


# --- INICIALIZACIÓN DE ESTADO DE SESIÓN Y OTRAS CONFIGURACIONES ---
os.environ['PROJ_LIB'] = pyproj.datadir.get_data_dir()
# --- INICIO DEL BLOQUE DE ESTILOS PERSONALIZADOS (CON EFECTO ORBITAL) ---
# --- INICIO DEL BLOQUE DE ESTILOS PERSONALIZADOS (CON SPINNER) ---
# --- INICIO DEL BLOQUE DE ESTILOS PERSONALIZADOS (CON BARRA DE PROGRESO) ---
# --- INICIO DEL BLOQUE DE ESTILOS LUZ (BLANCO Y NEGRO) ---
st.markdown("""
<style>
/* Fondo general y textos */
.stApp {
    background-color: #FFFFFF;
    color: #000000;
}

/* Forzar títulos y textos a negro */
h1, h2, h3, p, span, label {
    color: #000000 !important;
}

/* --- ESTILOS PARA BOTONES --- */
div[data-testid="stButton"] > button {
    background-color: #0D6AB7;
    color: white !important;
    border: 1px solid #0D6AB7;
}

/* --- BARRA DE PROGRESO CIRCULAR (FONDO BLANCO) --- */
.center-container { display: flex; justify-content: center; align-items: center; height: 400px; }
.progress-circle-container { position: relative; width: 120px; height: 120px; }
.progress-circle {
    width: 120px; height: 120px; border-radius: 50%;
    background: conic-gradient(#0D6AB7 var(--progress), #E0E0E0 0);
    display: flex; justify-content: center; align-items: center;
    transition: background 0.2s;
}
.progress-circle-inner {
    width: 100px; height: 100px; border-radius: 50%;
    background: #FFFFFF; /* FONDO BLANCO PARA EL CÍRCULO */
    display: flex; justify-content: center; align-items: center;
}
.progress-text { font-size: 1.8em; font-weight: bold; color: #000000; }

/* Ajuste de tablas para que se vean bien en blanco */
div[data-testid="stDataFrame"] {
    border: 1px solid #E0E0E0;
}
</style>
""", unsafe_allow_html=True)
# --- FIN DEL BLOQUE DE ESTILOS ---
# --- FIN DEL BLOQUE DE ESTILOS ---
# --- FIN DEL BLOQUE DE ESTILOS ---
# Intenta configurar el idioma y muestra la advertencia si falla (esto ya es seguro)
try:
    locale.setlocale(locale.LC_TIME, 'es_ES.UTF-8')
except locale.Error:
    st.warning("No se pudo configurar el idioma a español.")

# Inicializa el estado de la sesión de forma robusta, clave por clave
if 'map_generated' not in st.session_state:
    st.session_state.map_generated = False
if 'figure' not in st.session_state:
    st.session_state.figure = None
if 'raster_io' not in st.session_state:
    st.session_state.raster_io = None
if 'png_buffer' not in st.session_state:
    st.session_state.png_buffer = None
if 'report_date_str' not in st.session_state:
    st.session_state.report_date_str = ""
if 'stats_panel_md' not in st.session_state:
    st.session_state.stats_panel_md = None
if 'processing_state' not in st.session_state:
    st.session_state.processing_state = 'idle'
if 'progress_percent' not in st.session_state:
    st.session_state.progress_percent = 0
if 'log_messages' not in st.session_state:
    st.session_state.log_messages = []

# Ahora el resto de la interfaz puede comenzar
st.title("💧 Generador de Reportes Pluviales para León, Gto.")
st.markdown("Bienvenido al Generador de Reportes Pluviales. Visualiza de forma rápida cómo se distribuyó la lluvia más reciente en todo el municipio de León.")
st.caption("""
**Fuentes de Datos:** Este reporte se genera utilizando datos de acceso público.
- **SAPAL:** Extraído de [sapal.gob.mx/estaciones-metereologicas](https://www.sapal.gob.mx/estaciones-metereologicas)
- **CONAGUA:** Extraído de [sih.conagua.gob.mx/basedatos/climas/](https://sih.conagua.gob.mx/basedatos/climas/)
""")
# --- FUNCIONES CORE ---
def add_north_arrow(ax, x=0.92, y=0.92, size=0.04, text_size=10):
    ns_poly = Polygon([[x, y + size], [x + size*0.2, y], [x, y - size], [x - size*0.2, y]], facecolor='black', edgecolor='black', transform=ax.transAxes)
    ew_poly = Polygon([[x + size, y], [x, y + size*0.2], [x - size, y], [x, y - size*0.2]], facecolor='white', edgecolor='black', transform=ax.transAxes)
    ax.add_patch(ew_poly); ax.add_patch(ns_poly)
    ax.text(x, y + size * 1.3, 'N', ha='center', va='center', fontsize=text_size, transform=ax.transAxes)
    ax.text(x, y - size * 1.3, 'S', ha='center', va='center', fontsize=text_size, transform=ax.transAxes)
    ax.text(x + size * 1.3, y, 'E', ha='center', va='center', fontsize=text_size, transform=ax.transAxes)
    ax.text(x - size * 1.3, y, 'W', ha='center', va='center', fontsize=text_size, transform=ax.transAxes)


def fetch_conagua_data(stations, start_date, end_date, log_messages, log_container):
    """
    Extrae datos de CONAGUA en paralelo y suma la precipitación del rango [start_date, end_date] por estación.
    Correcciones clave:
      - Parseo robusto de fechas m/dd/yyyy o d/m/yyyy (heurística + fallbacks).
      - Normaliza a fecha pura (sin horas/tz) antes de filtrar.
      - Agrupa por día para consolidar duplicados diarios.
      - Maneja codificaciones utf-8/latin-1 y respuestas HTML.
    """
    # -------------------- Helpers internos --------------------
    def _parse_date_flex(series_like):
        """
        Convierte una serie de fechas con posibles formatos m/dd/yyyy o d/m/yyyy a datetime64[ns].
        Estrategia:
          1) Si ya es datetime -> normaliza (sin hora/zonas) y regresa.
          2) Si parece d?/d?/yyyy -> decidir con heurística: primer token > 12 => d/m/yyyy, si no => m/d/yyyy.
          3) Si no calza, intenta parseo general (dayfirst=False y luego True).
        Devuelve pandas datetime (sin tz) normalizado al inicio del día.
        """
        s = pd.Series(series_like)

        # Si ya es datetime:
        if np.issubdtype(s.dropna().dtype, np.datetime64):
            return pd.to_datetime(s, errors="coerce").dt.tz_localize(None).dt.normalize()

        stxt = s.astype(str)
        sample = stxt.dropna().head(40)

        # ¿Se parece a d?/d?/yyyy ?
        pat = r'^\s*\d{1,2}/\d{1,2}/\d{4}\s*$'
        share = (sample.str.match(pat)).mean()

        if share >= 0.6:
            # Primer token numérico (antes de la primera '/')
            try:
                first = sample.str.extract(r'^\s*(\d{1,2})/')[0].astype(int)
                if (first > 12).any():
                    # Seguro día/mes/año
                    dt = pd.to_datetime(stxt, format="%d/%m/%Y", errors="coerce")
                else:
                    # Probable mes/día/año (tu caso)
                    dt = pd.to_datetime(stxt, format="%m/%d/%Y", errors="coerce")
            except Exception:
                # Fallback por si el extract falla
                dt = pd.to_datetime(stxt, errors="coerce", dayfirst=False, infer_datetime_format=True)
                if dt.isna().all():
                    dt = pd.to_datetime(stxt, errors="coerce", dayfirst=True, infer_datetime_format=True)
        else:
            # Fallback general
            dt = pd.to_datetime(stxt, errors="coerce", dayfirst=False, infer_datetime_format=True)
            if dt.isna().all():
                dt = pd.to_datetime(stxt, errors="coerce", dayfirst=True, infer_datetime_format=True)

        return dt.dt.tz_localize(None).dt.normalize()

    def _to_date(obj):
        """Normaliza cualquier datetime-like a date puro (sin hora/tz)."""
        if isinstance(obj, pd.Timestamp):
            return (obj.tz_localize(None) if obj.tz is not None else obj).date()
        if isinstance(obj, datetime):
            return obj.date()
        if isinstance(obj, np.datetime64):
            return pd.to_datetime(obj).date()
        return obj  # si ya es 'date'

    # -------------------- Inicio de función --------------------
    results = []
    log_messages.append("--- Iniciando extracción de CONAGUA (en paralelo)... ---")
    log_container.markdown("\n\n".join(log_messages))

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/118.0.0.0 Safari/537.36"
        )
    }

    # Normaliza límites a 'date'
    sd = _to_date(pd.to_datetime(start_date))
    ed = _to_date(pd.to_datetime(end_date))

    def _fetch_one_station(station):
        url = f"https://sih.conagua.gob.mx/basedatos/climas/{station}.csv"

        # Intentar dos codificaciones comunes
        for encoding in ("utf-8", "latin-1"):
            try:
                resp = requests.get(url, headers=headers, verify=False, timeout=20)
                resp.raise_for_status()
                text = resp.content.decode(encoding, errors="replace")

                # Respuestas HTML/erróneas
                if len(text) < 100 or "</html>" in text.lower():
                    continue  # prueba con otra codificación

                # Detecta fila de cabecera real (donde aparece 'Fecha')
                lines = text.splitlines()
                header_row_index = next(
                    (idx for idx, line in enumerate(lines) if "fecha" in line.lower()),
                    -1
                )
                if header_row_index == -1:
                    continue

                # Leer CSV a partir de la cabecera
                df = pd.read_csv(io.StringIO(text), skiprows=header_row_index, header=0)

                # Localizar columnas
                date_col = next((c for c in df.columns if "fecha" in c.lower()), None)
                precip_col = next(
                    (c for c in df.columns if "precip" in c.lower() or "pp" in c.lower()),
                    None
                )
                if not date_col or not precip_col:
                    continue

                # --- PARSEO ROBUSTO ---
                df[date_col] = _parse_date_flex(df[date_col])     # ← convierte a datetime (día normalizado)
                df[precip_col] = pd.to_numeric(df[precip_col], errors="coerce")

                df = df.dropna(subset=[date_col, precip_col])
                if df.empty:
                    continue

                # Llevar a fecha pura para comparar inclusivo por día
                df["__DATE__"] = df[date_col].dt.date

                # Consolidar por día (suma si hay duplicados)
                daily = df.groupby("__DATE__", as_index=False)[precip_col].sum()

                # Filtrar por rango inclusivo
                mask = (daily["__DATE__"] >= sd) & (daily["__DATE__"] <= ed)
                daily_range = daily.loc[mask]

                total_precip = float(daily_range[precip_col].sum().round(1)) if not daily_range.empty else 0.0
                return station, total_precip, None

            except requests.exceptions.HTTPError as e:
                return station, None, f"Error HTTP {e.response.status_code}"
            except requests.exceptions.RequestException as e:
                # Timeout/red/DNS
                return station, None, f"Error de red ({type(e).__name__})"
            except Exception as e:
                # Si falla con esta codificación, intenta con la otra; si ya era la última, reporta
                if encoding == "latin-1":
                    return station, None, f"Error de procesamiento ({type(e).__name__})"
                continue

        return station, None, "Archivo vacío/HTML o codificación no soportada"

    # --- Paralelismo (respetando número de estaciones) ---
    with ThreadPoolExecutor(max_workers=min(10, max(1, len(stations)))) as executor:
        futures = {executor.submit(_fetch_one_station, st_code): st_code for st_code in stations}
        for future in as_completed(futures):
            station, precip, error = future.result()
            if error:
                log_messages.append(f"⚠️ **CONAGUA {station}:** {error}.")
            else:
                results.append({"Name": station, "ENTIDAD": "CONAGUA", "P_mm": precip})
                log_messages.append(f"✅ **CONAGUA {station}:** {precip} mm")
            log_container.markdown("\n\n".join(log_messages))

    # Devuelve DF incluso si quedó vacío (evita fallas posteriores)
    return pd.DataFrame(results, columns=["Name", "ENTIDAD", "P_mm"])


@st.cache_data(ttl=3600)
def get_latest_conagua_date(stations):
    # --- AÑADIR ESTAS LÍNEAS ---
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    # --- FIN DE LÍNEAS AÑADIDAS ---

    for station in stations:
        url = f"https://sih.conagua.gob.mx/basedatos/climas/{station}.csv"
        try:
            # --- MODIFICAR ESTA LÍNEA ---
            response = requests.get(url, headers=headers, verify=False, timeout=15)
            # --- FIN DE LÍNEA MODIFICADA ---

            response.raise_for_status()
            file_content = response.text
            if len(file_content) < 100 or "</html>" in file_content.lower(): continue
            lines = file_content.splitlines()
            header_row_index = next((idx for idx, line in enumerate(lines) if 'Fecha' in line), -1)
            if header_row_index == -1: continue
            df = pd.read_csv(io.StringIO(file_content), skiprows=header_row_index)
            date_col = next((col for col in df.columns if 'fecha' in col.lower()), None)
            if not date_col: continue
            df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
            df.dropna(subset=[date_col], inplace=True)
            if not df.empty:
                return df[date_col].max()
        except requests.exceptions.RequestException as e:
            # Opcional: imprimir el error para depuración
            # st.warning(f"Error al conectar con la estación {station}: {e}")
            continue
    return None
import re

# Asegúrate de que estas importaciones estén al principio de tu script

import time # Añade esta si no la tienes


def fetch_sapal_data(stations, report_date, log_messages, log_container):
    """
    Versión 25.0 - PRODUCCIÓN TOTAL.
    Lógica de ID técnico (ubicacion) para historial y Nombre para el mapa.
    """
    results = []
    is_today = report_date.date() == datetime.now().date()
    
    # Endpoints correctos según Postman
    url_lista = "https://services.sapal.gob.mx/portal/v1/climate/getMapStationList"
    url_historial = "https://services.sapal.gob.mx/portal/v1/climate/getHistory"
    
    headers = {
        "Accept": "application/json", 
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0", 
        "Referer": "https://www.sapal.gob.mx/"
    }
    session = requests.Session()

    try:
        # --- PASO 1: OBTENER EL CATÁLOGO (UBICACIONES REALES) ---
        r_cat = session.get(url_lista, headers=headers, verify=False, timeout=20)
        items_api = r_cat.json().get('items', {})

        if is_today:
            log_messages.append("--- [PRODUCCIÓN] Extrayendo datos de HOY ---")
            for st_id, info in items_api.items():
                name = str(info.get('nombre', '')).upper().strip()
                val = info.get('precipitacionAcumuladaAnual1', 0)
                try: val_float = float(str(val).replace(',', ''))
                except: val_float = 0.0
                results.append({'Name': name, 'ENTIDAD': 'SAPAL', 'P_mm': val_float})
                log_messages.append(f"✅ **{name}**: {val_float:.1f} mm")
                log_container.markdown("\n\n".join(log_messages))
        
        else:
            log_messages.append(f"--- [PRODUCCIÓN] Consultando historial para {report_date.strftime('%d-%m-%Y')} ---")
            log_container.markdown("\n\n".join(log_messages))

            def history_worker(info_estacion):
                # Usamos 'ubicacion' para el query técnico (Ej. Colombia)
                llave_tecnica = info_estacion.get('ubicacion')
                # Usamos 'nombre' para el Shapefile (Ej. CENTRO)
                nombre_mapa = str(info_estacion.get('nombre', '')).upper().strip()
                
                fecha_str = report_date.strftime('%d-%m-%Y')
                payload = {
                    "location": llave_tecnica,
                    "startDate": fecha_str,
                    "endDate": fecha_str,
                    "period": "M" 
                }
                try:
                    r = requests.post(url_historial, headers=headers, json=payload, verify=False, timeout=15)
                    if r.status_code == 200:
                        data = r.json()
                        # Estructura: items -> registros
                        regs = data.get('items', {}).get('registros', [])
                        if regs:
                            # Sacamos 'precipitacionAnual' del último registro
                            val_anual = regs[-1].get('precipitacionAnual', 0)
                            return {'Name': nombre_mapa, 'P_mm': float(val_anual), 'ok': True}
                    return {'Name': nombre_mapa, 'P_mm': 0.0, 'ok': False}
                except:
                    return {'Name': nombre_mapa, 'P_mm': np.nan, 'ok': False}

            # Paralelismo para GitHub
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(history_worker, info) for info in items_api.values()]
                for f in as_completed(futures):
                    res = f.result()
                    # Mapeo manual para asegurar match
                    n = res['Name']
                    if "MORELOS" in n: n = "BLVD MORELOS-MADRAZO"
                    
                    results.append({'Name': n, 'ENTIDAD': 'SAPAL', 'P_mm': res['P_mm']})
                    if res['ok']:
                        log_messages.append(f"✅ **{n}**: {res['P_mm']:.1f} mm")
                    else:
                        log_messages.append(f"⚠️ **{n}**: 0.0 mm")
                    log_container.markdown("\n\n".join(log_messages))

    except Exception as e:
        log_messages.append(f"❌ Error en SAPAL: {e}")
        log_container.markdown("\n\n".join(log_messages))

    return pd.DataFrame(results)

def filter_outliers(gdf, column='P_mm'):
    Q1 = gdf[column].quantile(0.25); Q3 = gdf[column].quantile(0.75)
    IQR = Q3 - Q1
    lower_bound = Q1 - 3 * IQR; upper_bound = Q3 + 3 * IQR
    outliers = gdf[(gdf[column] < lower_bound) | (gdf[column] > upper_bound)]
    gdf_filtered = gdf[(gdf[column] >= lower_bound) & (gdf[column] <= upper_bound)]
    return gdf_filtered, outliers

def find_best_interpolation_model(points_gdf, boundary_gdf):
    resolution = 100
    if len(points_gdf) < 5: return None, None
    def _custom_idw(train_coords, train_values, test_coords, power):
        d = np.linalg.norm(train_coords - test_coords, axis=1)
        if np.any(d == 0): return train_values[d == 0][0]
        w = 1.0 / (d ** power)
        return np.sum(w * train_values) / np.sum(w)
    points_proj = points_gdf.to_crs("EPSG:32614"); boundary_proj = boundary_gdf.to_crs("EPSG:32614")
    coords = np.array(list(zip(points_proj.geometry.x, points_proj.geometry.y))); values = points_proj['P_mm'].to_numpy()
    loo = LeaveOneOut()
    idw_powers = np.arange(1.0, 4.1, 0.5); idw_rmse_scores = []
    for p in idw_powers:
        preds = [_custom_idw(coords[train_idx], values[train_idx], coords[test_idx][0], p) for train_idx, test_idx in loo.split(coords)]
        idw_rmse_scores.append(np.sqrt(mean_squared_error(values, preds)))
    best_power = idw_powers[np.argmin(idw_rmse_scores)]
    metrics = []
    idw_preds = [_custom_idw(coords[train_idx], values[train_idx], coords[test_idx][0], best_power) for train_idx, test_idx in loo.split(coords)]
    metrics.append({'Método': 'IDW Optimizado', 'RMSE': np.sqrt(mean_squared_error(values, idw_preds)), 'MAE': mean_absolute_error(values, idw_preds)})
    k_preds, k_reals = [], []
    for train_idx, test_idx in loo.split(coords):
        try:
            ok = OrdinaryKriging(coords[train_idx, 0], coords[train_idx, 1], values[train_idx], variogram_model='spherical', verbose=False, enable_plotting=False)
            pred, _ = ok.execute('points', coords[test_idx, 0], coords[test_idx, 1])
            k_preds.append(pred[0]); k_reals.append(values[test_idx][0])
        except Exception: continue
    if k_preds: metrics.append({'Método': 'Kriging', 'RMSE': np.sqrt(mean_squared_error(k_reals, k_preds)), 'MAE': mean_absolute_error(k_reals, k_preds)})
    metrics_df = pd.DataFrame(metrics).round(3)
    best_method_row = metrics_df.loc[metrics_df['RMSE'].idxmin()]
    xmin, ymin, xmax, ymax = boundary_proj.total_bounds
    grid_x, grid_y = np.arange(xmin, xmax, resolution), np.arange(ymin, ymax, resolution)
    if 'IDW' in best_method_row['Método']:
        gx, gy = np.meshgrid(grid_x, grid_y)
        flat_grid = np.c_[gx.ravel(), gy.ravel()]
        z_grid_flat = np.array([_custom_idw(coords, values, pt, best_power) for pt in flat_grid])
        z_grid = z_grid_flat.reshape(gx.shape)
    else:
        ok = OrdinaryKriging(coords[:, 0], coords[:, 1], values, variogram_model='spherical', verbose=False, enable_plotting=False)
        z_grid, _ = ok.execute('grid', grid_x, grid_y)
    z_grid = np.where(z_grid < 0, 0, z_grid)
    transform = from_origin(grid_x[0], grid_y[-1], resolution, resolution)
    with rasterio.io.MemoryFile() as memfile:
        with memfile.open(
            driver='GTiff', height=z_grid.shape[0], width=z_grid.shape[1],
            count=1, dtype=z_grid.dtype, crs="EPSG:32614", transform=transform
        ) as dataset:
            dataset.write(z_grid, 1)
        with memfile.open() as src:
            # Pasa el objeto src a la función mask
            # Línea corregida
            out_image, out_transform = mask(src, boundary_gdf.geometry, crop=True, all_touched=True, filled=True, nodata=np.nan)
            out_meta = src.meta.copy()

    # Se corrige el valor 'nodata' para que no sea 0
    out_meta.update({"driver": "GTiff", "height": out_image.shape[1], "width": out_image.shape[2], "transform": out_transform, "nodata": np.nan})
    final_raster_io = io.BytesIO()
    with rasterio.open(final_raster_io, "w", **out_meta) as dest: dest.write(out_image)
    final_raster_io.seek(0)
    return {"raster_io": final_raster_io, "raster_image": out_image, "raster_meta": out_meta, "best_method": best_method_row['Método']}, metrics_df
    
@st.cache_resource
@st.cache_resource # Se mantiene @st.cache_resource
def load_geodata():
    shapefile_path = "shapefiles" # Esta es la carpeta en tu repositorio de GitHub
    try:
        data = {
            "boundary": gpd.read_file(os.path.join(shapefile_path, "LIMITE.shp")),
            "stations": gpd.read_file(os.path.join(shapefile_path, "ESTACIONES_actualizado.shp")),
            "hillshade": rasterio.open(os.path.join(shapefile_path, "HILLSHADE_LEON.tif")),
            "urban": gpd.read_file(os.path.join(shapefile_path, "LIMITE_URBANO.shp")),
            "cuenca": gpd.read_file(os.path.join(shapefile_path, "CUENCA_PALOTE.shp")),
            "presa": gpd.read_file(os.path.join(shapefile_path, "EL PALOTE.shp")),
            "streams": gpd.read_file(os.path.join(shapefile_path, "CORRIENTES_LEON_012025.shp")),
            # AÑADIR ESTA LÍNEA PARA CARGAR LOS SENSORES DE RÍO DESDE EL KMZ
            "river_sensors": load_kmz_from_local(os.path.join(shapefile_path, "sensores_rios.kmz"))
        }
        
        # --- NUEVO: ASEGURAR CRS CONSISTENTE PARA TODAS LAS CAPAS VECTORIALES ---
        # El CRS de hillshade suele ser el ideal para la proyección del mapa
        target_crs = data["hillshade"].crs 
        for key in ["boundary", "stations", "urban", "cuenca", "presa", "streams", "river_sensors"]:
            # Solo reproyectar si el GeoDataFrame no está vacío y su CRS es diferente
            if not data[key].empty and data[key].crs != target_crs:
                data[key] = data[key].to_crs(target_crs)

        # CARGA DE LOGOS (AZUL Y BLANCO)
        try:
            data["logo_azul"] = mpimg.imread(os.path.join(shapefile_path, "logo_sapal_azul.png"))
            data["logo_blanco"] = mpimg.imread(os.path.join(shapefile_path, "logo_sapal_blanco.png"))
        except Exception as e_logo:
            data["logo_azul"] = None
            data["logo_blanco"] = None
            st.warning(f"Advertencia: No se pudieron cargar los logos desde '{shapefile_path}': {e_logo}")
            
        return data
    except Exception as e:
        st.error(f"Error fatal al cargar archivos geoespaciales: {e}")
        st.stop()

# --- Este bloque DEBE ir después de la definición de load_geodata() ---
geodata = load_geodata() 
stations_gdf = geodata["stations"]
locations_sapal = stations_gdf[stations_gdf['ENTIDAD'] == 'SAPAL']['Name'].tolist()
locations_conagua = stations_gdf[stations_gdf['ENTIDAD'] == 'CONAGUA']['Name'].tolist()

# --- NUEVO: OBTENER NOMBRES DE SENSORES DE RÍO DEL GEODATAFRAME CARGADO ---
river_sensors_gdf = geodata["river_sensors"]
if not river_sensors_gdf.empty:
    locations_river_sensors_kmz = river_sensors_gdf['Name'].tolist()
else:
    locations_river_sensors_kmz = []
    st.warning("No se pudieron cargar los sensores de río o el GeoDataFrame está vacío.")

# --- CONFIGURACIÓN DE SEMÁFORO DE RÍOS ---
RIVER_ALERTS = {
    "VERDE": {"label": "Nivel Normal", "color": "#00FF00", "symbol": "circle"},
    "AMARILLO": {"label": "Atención", "color": "#FFFF00", "symbol": "triangle-up"},
    "NARANJA": {"label": "Prevención", "color": "#FFA500", "symbol": "diamond"},
    "ROJO": {"label": "Alerta", "color": "#FF0000", "symbol": "square"}
}

# Mapeo de nombres largos (KMZ) a nombres cortos diplomáticos (para mostrar en leyenda)
RIVER_NAME_MAPPING = {
    "San Jose El Alto (Arroyo Tajo de Santa Ana)": "Tajo de Santa Ana",
    "Parque Metropolitano (Arroyo Los Castillos)": "Arroyo Los Castillos",
    "Pablo del Río (Arroyo Mariches)": "Arroyo Mariches",
    "Blvd. Vicente Valtierra (Río Los Gómez)": "Río Los Gómez (Valtierra)",
    "Blvd. Mariano Escobedo (Río Los Gómez)": "Río Los Gómez (Escobedo)",
    "Blvd. Juan Alonso de Torres (Arroyo La Patiña)": "Arroyo La Patiña",
    "Blvd. A. López Mateos (Río Los Gómez)": "Río Los Gómez (López Mateos)",
    "Blvd. JJ Torres Landa (Arroyo Alfaro)": "Arroyo Alfaro",
    "Autopista León-Aguascalientes (Río Turbio)": "Río Turbio"
}

# Mapeo inverso para asegurar la consistencia al unir
INVERSE_RIVER_NAME_MAPPING = {v: k for k, v in RIVER_NAME_MAPPING.items()}

# --- Función para obtener datos de sensores de río (simulada) ---
def fetch_river_sensor_data(sensor_names_kmz, target_date, log_messages, log_container):
    """
    Simula la extracción de datos de sensores de río (nivel y categoría de alerta) para una fecha dada.
    En una aplicación real, esto consultaría una API real.
    `sensor_names_kmz` debe ser la lista de nombres originales del KMZ.
    """
    results = []
    log_messages.append(f"--- Extrayendo datos de sensores de río para {target_date.strftime('%d-%m-%Y')}... ---")
    log_container.markdown("\n\n".join(log_messages))

    for kmz_name in sensor_names_kmz:
        diplomatic_name = RIVER_NAME_MAPPING.get(kmz_name, kmz_name) # Obtener nombre diplomático

        # Simular datos: el nivel y la alerta cambian según la fecha/nombre del sensor
        # Esto es un ejemplo; la lógica real dependería de tu API.
        # Usamos un hash para una simulación algo consistente pero variable.
        seed = hash(f"{kmz_name}-{target_date.day}-{target_date.month}-{target_date.year}") % 100
        
        level_m = round(1.0 + (seed % 20) * 0.1, 2) # Nivel entre 1.0 y 2.9 m

        if level_m < 1.5:
            alert = "VERDE"
        elif level_m < 2.0:
            alert = "AMARILLO"
        elif level_m < 2.5:
            alert = "NARANJA"
        else:
            alert = "ROJO"
        
        results.append({
            "KMZ_Name": kmz_name,    # Nombre original del KMZ
            "Name_Display": diplomatic_name, # Nombre diplomático para mostrar
            "Level_m": level_m,
            "Alert": alert
        })
        log_messages.append(f"🌊 **{diplomatic_name}**: Nivel {level_m} m, Alerta: {RIVER_ALERTS[alert]['label']}")
        log_container.markdown("\n\n".join(log_messages))

    return pd.DataFrame(results)

if 'log_messages' not in st.session_state:
    st.session_state.log_messages = []
if 'log_container_placeholder' not in st.session_state:
    # Inicializar con un placeholder dummy que se usará en las funciones cacheadas
    # y será reemplazado por el real en el bloque de procesamiento de la UI.
    st.session_state.log_container_placeholder = st.empty()

def reset_analysis():
    keys_to_reset = ['map_generated', 'figure', 'raster_io', 'png_buffer', 'report_date_str', 'stats_panel_md']
    for key in keys_to_reset:
        if key in st.session_state:
            del st.session_state[key]
    st.session_state.map_generated = False

# --- LÓGICA DE INTERFAZ REESTRUCTURADA CON DOS COLUMNAS ---

# Función para no repetir la información de la barra lateral


# Definimos las columnas fuera del if/else para que existan en ambos estados
col_info, col_mapa = st.columns([2, 3]) # Columna izquierda más angosta (ratio 2:3)

if st.session_state.map_generated:
    # --- 1. ENCABEZADO Y BOTONES DE DESCARGA ---
    col_h1, col_h2, col_h3 = st.columns([1.3, 0.5, 0.5])
    with col_h1:
        # Formatear fecha para el título
        f_tit = pd.to_datetime(st.session_state.report_date_str).strftime('%d/%m/%Y')
        st.markdown(f"## 📊 Reporte Pluvial León (Corte: {f_tit})")
    
    with col_h2:
        st.download_button("📥 Descargar Mapa (PNG)", st.session_state.png_buffer, 
                           f"Mapa_{st.session_state.report_date_str}.png", "image/png", use_container_width=True)
    
    with col_h3:
        if 'fig_plotly' in st.session_state:
            # Exportar la gráfica interactiva a HTML
            buf_html = io.StringIO()
            st.session_state.fig_plotly.write_html(buf_html, full_html=False)
            st.download_button("📈 Descargar Gráfica", buf_html.getvalue(), 
                               f"Curvas_{st.session_state.report_date_str}.html", "text/html", use_container_width=True)

    # --- 2. FILA PRINCIPAL: MAPA Y GRÁFICA LADO A LADO ---
    col_mapa_viz, col_espacio, col_curva_viz = st.columns([1.3, 0.1,1]) # Proporción para que el mapa luzca grande
    
    with col_mapa_viz:
        # Mostramos el mapa de Matplotlib
        st.pyplot(st.session_state.figure, use_container_width=True, transparent = True)
    with col_espacio:
        st.write("")
    with col_curva_viz:
        # Mostramos la gráfica interactiva de Plotly
        if 'fig_plotly' in st.session_state:
            st.plotly_chart(st.session_state.fig_plotly, use_container_width=True, config={'displayModeBar': False})

    # --- 3. RESUMEN INFERIOR ---
    st.divider()
    if st.session_state.stats_panel_md:
        st.markdown("### 📋 Resumen Ejecutivo y Datos Detallados")
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            st.info(st.session_state.stats_panel_md["header"])
        with c2:
            st.markdown("**Estadísticas de Lluvia (mm)**")
            st.dataframe(st.session_state.stats_panel_md["desc_stats"], hide_index=True, use_container_width=True)
        with c3:
            st.markdown("**Lecturas por Estación**")
            with st.expander("Ver tabla de datos completa"):
                st.dataframe(st.session_state.stats_panel_md["total_df_con_na"], hide_index=True, use_container_width=True)
        
        # Botón para reiniciar abajo de las tablas
        if st.button("Nuevo Análisis", use_container_width=True):
            reset_analysis()
            st.rerun()
else:
    # --- VISTA DE CONFIGURACIÓN Y PROCESAMIENTO ---
    with col_info:
        
        st.header("1. Selecciona el tipo de reporte")
        report_option = st.radio("Elige las estaciones a incluir:", ('Solo Estaciones SAPAL', 'SAPAL + CONAGUA (Recomendado)'), index=1, key="report_option", disabled=(st.session_state.processing_state == 'processing'))
        st.info("Añadir las estaciones de CONAGUA mejora la precisión del mapa.")
        st.header("2. Confirma la fecha del reporte")
        
        # Lógica para mostrar la fecha y el botón
        if st.session_state.processing_state == 'idle':
                  # --- INSERTAR ESTE BLOQUE DENTRO DEL ELSE (VISTA DE CONFIGURACIÓN) ---
                 # --- FECHA + BOTÓN (BLOQUE ÚNICO, SIN NameError) ---
         
        # 0) Siempre inicialice
         report_date = None
         
         # 1) Selector de fecha manual
         use_manual_date = st.checkbox(
             "Usar fecha específica (Calendario)",
             value=st.session_state.get("use_manual_date", False),
             disabled=(st.session_state.processing_state == 'processing'),
             key="use_manual_date"
         )
         
         if use_manual_date:
             # Selector de calendario interactivo
             selected_date = st.date_input(
                 "Selecciona el día del reporte:",
                 value=st.session_state.get("manual_date_val", datetime.now()),
                 min_value=datetime(2000, 1, 1),
                 max_value=datetime.now(),
                 disabled=(st.session_state.processing_state == 'processing'),
                 key="manual_date_val"
             )
             
             # Convertir el objeto 'date' a 'datetime' para que no truene el resto del código
             report_date = datetime.combine(selected_date, datetime.min.time())
             st.info(f"📅 Se usará la fecha seleccionada: **{report_date.strftime('%d-%m-%Y')}**")
         
         else:
             # 2) Fecha automática (Solo se escribe una vez)
             if report_option == 'Solo Estaciones SAPAL':
                 report_date = datetime.now()
                 st.info(f"📅 Se usará la fecha de hoy: **{report_date.strftime('%d-%m-%Y')}**")
             else:
                 with st.spinner("Buscando la última fecha de CONAGUA..."):
                     latest_conagua_date = get_latest_conagua_date(locations_conagua)
         
                 if latest_conagua_date:
                     report_date = pd.to_datetime(latest_conagua_date).to_pydatetime()
                     st.info(f"📅 Fecha más reciente encontrada: **{report_date.strftime('%d-%m-%Y')}**")
                 else:
                     report_date = datetime.now()
                     st.warning("⚠️ No se pudo contactar a CONAGUA. Se usará la fecha de hoy.")
                     st.info(f"📅 Fecha de corte: **{report_date.strftime('%d-%m-%Y')}**")
         
         # 3) Botón para disparar pipeline
         can_run = (report_date is not None)
         
         if st.button(
             "Generar reporte",
             type="primary",
             use_container_width=True,
             disabled=(st.session_state.processing_state == 'processing' or not can_run)
         ):
             st.session_state.processing_state = 'processing'
             st.session_state.progress_percent = 0
             st.session_state.report_option_to_process = report_option
             st.session_state.report_date_to_process = pd.to_datetime(report_date)
             st.session_state.log_messages = ["--- Iniciando procesamiento ---"]
         
             for k in ["sapal_df_processed", "total_df_processed", "stations_filtered_gdf",
                       "outliers_df", "interpolation_results", "metrics_df", "total_df_con_na"]:
                 if k in st.session_state:
                     del st.session_state[k]
         
             st.rerun()



        else: # Si está procesando, muestra el log
            log_expander = st.expander("Ver progreso detallado...", expanded=True)
            log_expander.markdown("\n\n".join(st.session_state.log_messages))

    with col_mapa:
        if st.session_state.processing_state == 'processing':
            progress = st.session_state.progress_percent
            st.markdown(f'<div class="center-container"><div class="progress-circle-container"><div class="progress-circle" style="--progress: {progress}%;"><div class="progress-circle-inner"><span class="progress-text">{progress}%</span></div></div></div></div>', unsafe_allow_html=True)
            st.info("Procesando datos... Por favor, espera.")
        else:
            st.markdown('<div class="center-container"><h3 style="text-align: center;">Listo para generar el reporte</h3></div>', unsafe_allow_html=True)

    # --- LÓGICA DE PROCESAMIENTO POR ETAPAS (CORREGIDA) ---
    if st.session_state.processing_state == 'processing':
        log_container_placeholder = col_info.empty() 
        st.session_state.log_container_placeholder = log_container_placeholder # Actualizar session state con el placeholder visible

        # ETAPA 1: Progreso 0% -> 25% (Extracción SAPAL)
        if st.session_state.progress_percent == 0:
            report_date_pd = pd.to_datetime(st.session_state.report_date_to_process.date())
            sapal_df = fetch_sapal_data(locations_sapal, report_date_pd, st.session_state.log_messages, log_container_placeholder)
            st.session_state.sapal_df_processed = sapal_df # Guardar resultado intermedio
            st.session_state.progress_percent = 20
            st.rerun()

        # ETAPA 2: Progreso 25% -> 60% (Extracción CONAGUA)
        elif st.session_state.progress_percent == 20:
            report_date_pd = pd.to_datetime(st.session_state.report_date_to_process.date())
            start_of_year = pd.to_datetime(f"{report_date_pd.year}-01-01")
            sapal_df = st.session_state.sapal_df_processed # Recuperar resultado anterior
            
            if "CONAGUA" in st.session_state.report_option_to_process:
                conagua_df = fetch_conagua_data(locations_conagua, start_of_year, report_date_pd, st.session_state.log_messages, log_container_placeholder)
                total_df = pd.concat([sapal_df, conagua_df], ignore_index=True)
            else:
                total_df = sapal_df

            st.session_state.total_df_processed = total_df
            st.session_state.progress_percent = 40
            st.rerun()
        # ETAPA 2.5: Progreso 40% -> 60% (Extracción Sensores de Río) - NUEVA ETAPA
        elif st.session_state.progress_percent == 40: # <--- COMIENZA AQUÍ esta nueva etapa
            if locations_river_sensors_kmz: # Solo si hay sensores cargados (del KMZ)
                report_date_pd = pd.to_datetime(st.session_state.report_date_to_process.date())
                # Llama a la función simulada para obtener datos de ríos
                river_data_df = fetch_river_sensor_data(locations_river_sensors_kmz, report_date_pd, st.session_state.log_messages, log_container_placeholder)
                st.session_state.river_data_processed = river_data_df # Guarda el DataFrame con los datos de los ríos
            else:
                st.session_state.river_data_processed = pd.DataFrame() # Guarda un DataFrame vacío si no hay sensores
                st.session_state.log_messages.append("⚠️ No hay sensores de río disponibles para obtener datos.")
                log_container_placeholder.markdown("\n\n".join(st.session_state.log_messages))

            st.session_state.progress_percent = 60 # <--- ESTE ES EL NUEVO PORCENTAJE AL FINAL DE ESTA ETAPA
            st.rerun()

        # ETAPA 3: Progreso 60% -> 90% (Cálculos, Excel, Gráfica e Interpolación)
        elif st.session_state.progress_percent == 60:
            # --- 0. VARIABLES BASE ---
            total_df = st.session_state.total_df_processed
            river_data_df = st.session_state.river_data_processed
            report_date_pd = pd.to_datetime(st.session_state.report_date_to_process)
            ano_act = report_date_pd.year
            mes_idx = report_date_pd.month - 1
            EXCEL_PATH = os.path.join("shapefiles", "GRÁFICA.xlsx")

            # --- 1. CÁLCULO PONDERADO (SOLO SAPAL) ---
            pesos_dict = {
                'AMALIAS': 10.758, 'CENTRO': 14.641, 'CERRITO DE JEREZ': 15.97,
                'BLVD. MORELOS': 11.149, 'PRESA EL PALOTE': 55.778, 'EL FARO': 12.157,
                'SANTA ROSA PLAN DE AYALA': 5.129, 'CIUDAD INDUSTRIAL': 7.153,
                'VILLAS DE SAN JUAN': 16.754, 'SAPAL TORRES LANDA': 12.577,
                'EXPLORA': 9.659, 'IBERO': 20.305, 'INSURGENTES': 11.222,
                'LOMAS DEL MIRADOR': 21.261, 'PARAISO REAL': 116.968,
                'SAPAL HIDALGO': 9.843, 'SACROMONTE': 14.205, 'LOZA DE LOS PADRES': 45.67,
                'LOMAS DE IBARRILLA': 223.794, 'MACROCENTRO DEPORTIVO': 10.608,
                'EL AVELIN': 56.614
            }
            
            sapal_only = total_df[total_df['ENTIDAD'] == 'SAPAL'].copy()
            sapal_only['Name_Norm'] = sapal_only['Name'].str.upper()
            suma_productos = 0
            for st_name, peso in pesos_dict.items():
                match = sapal_only[sapal_only['Name_Norm'].str.contains(st_name.split()[0])]
                if not match.empty:
                    precip = match['P_mm'].values[0] # Acumulado Anual de la API
                    if pd.notna(precip): suma_productos += (precip * peso)
            
            ponderado_anual_api = suma_productos / 702.215

            # --- 2. ACTUALIZACIÓN DE EXCEL Y MEDIA DINÁMICA ---
            df_hist = pd.read_excel(EXCEL_PATH)
            df_hist.columns = [str(c).strip() for c in df_hist.columns]
            col_media_base = next((c for c in df_hist.columns if "MEDIA LEÓN" in c.upper() and "2011-2023" in c), None)
            
            if str(ano_act) not in df_hist.columns:
                df_hist[str(ano_act)] = 0.0

            # Diferencia: Total_API - Suma_Meses_Anteriores_Excel = Valor_Mes_Actual
            suma_previos = df_hist[str(ano_act)].iloc[:mes_idx].sum()
            df_hist.loc[mes_idx, str(ano_act)] = max(0, ponderado_anual_api - suma_previos)
            df_hist.to_excel(EXCEL_PATH, index=False)
            

            # Media Dinámica Original
            anos_extra = [c for c in df_hist.columns if c.isdigit() and int(c) > 2023]
            u_completo = 2023
            for a in anos_extra:
                if df_hist[a].notna().all(): u_completo = max(u_completo, int(a))
            
            def calc_media_real(row):
                s_base = row[col_media_base] * 13
                vals_extra = [row[a] for a in anos_extra if pd.notna(row[a])]
                return (s_base + sum(vals_extra)) / (13 + len(vals_extra))

            label_media = f"MEDIA (2011 - {u_completo})"
            cols_old = [c for c in df_hist.columns if "MEDIA" in c.upper() and c != col_media_base]
            df_hist.drop(columns=cols_old, inplace=True)
            df_hist[label_media] = df_hist.apply(calc_media_real, axis=1)
            df_hist.to_excel(EXCEL_PATH, index=False)

            # --- 3. GENERACIÓN DE GRÁFICA PLOTLY (VERSIÓN BLANCA / TEXTO NEGRO) ---
            meses_labels = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE']
            fig_p = go.Figure()
            # A. ESCALA DE AZULES PARA DEGRADADO MARCADO (Izquierda a Derecha)
            bar_colors = [
                '#E3F2FD', '#BBDEFB', '#90CAF9', '#64B5F6', '#42A5F5', '#2196F3', 
                '#1E88E5', '#1976D2', '#1565C0', '#0D47A1', '#08225E', '#051233'
            ]
            y_actual_acum = df_hist[str(ano_act)].fillna(0).cumsum()
            fig_p.add_trace(go.Bar(
                x=meses_labels[:mes_idx+1], y=y_actual_acum[:mes_idx+1], 
                name=f"Acumulado {ano_act}", 
                marker=dict(color=bar_colors, line=dict(color='black', width=0.5)),
                hovertemplate='Total: %{y:.1f} mm<extra></extra>'
            ))

            # --- 3. CONFIGURACIÓN DE LÍNEAS (Asegúrate de queconfigs esté así) ---
            configs = [
                {'c':str(ano_act),   'color':'#FFFF00', 'name':f'CURVA {ano_act}', 'sym':'circle'},
                {'c':str(ano_act-1), 'color':'#39FF14', 'name':str(ano_act-1),      'sym':'square'},
                {'c':str(ano_act-2), 'color':'#FF00FF', 'name':str(ano_act-2),      'sym':'diamond'},
                {'c':label_media,    'color':'#FF5F1F', 'name':label_media,        'sym':'star'}
            ]
            
            # --- CURVAS ACUMULADAS ---
            for i, lc in enumerate(configs):
                if lc['c'] in df_hist.columns:
                    is_current = (lc['c'] == str(ano_act))
                    
                    # Si es un año (2018-2025), acumulamos los mm/mes del Excel
                    if lc['c'].isdigit():
                        y_v = df_hist[lc['c']].fillna(0).cumsum()
                    else:
                        y_v = df_hist[lc['c']].fillna(0) # La Media ya viene calculada
                    
                    limit = mes_idx + 1 if is_current else 12
                    dx, dy = meses_labels[:limit], y_v[:limit]
                    
                    if len(dy) > 0:
                        last_x, last_y = dx[-1], dy.iloc[-1]
                        fig_p.add_trace(go.Scatter(
                            x=dx, y=dy, mode='lines+markers', name=lc['name'], 
                            line=dict(color=lc['color'], width=3, dash='dash' if 'MEDIA' in lc['name'] else 'solid', shape='spline'), 
                            marker=dict(size=8, symbol=lc['sym'], line=dict(color='black', width=1)),
                            hovertemplate='%{y:.1f} mm<extra></extra>'
                        ))

                        # 3. LÓGICA DE CALLOUTS (YA NO FALLARÁ)
                        if is_current:
                            # Callout VERTICAL para el año actual (Hacia arriba)
                            fig_p.add_trace(go.Scatter(
                                x=[last_x, last_x], y=[last_y, last_y + 40],
                                mode='lines+text', text=["", f"<b>{last_y:.1f}</b>"],
                                textposition="top center", textfont=dict(color='black', size=13),
                                line=dict(color='black', width=1.5), showlegend=False, hoverinfo='skip'
                            ))
                        else:
                            # Callout HORIZONTAL para años anteriores (Hacia la derecha)
                            y_pos_text = last_y + (i * 2) 
                            fig_p.add_trace(go.Scatter(
                                x=[last_x], y=[y_pos_text],
                                mode='markers+text', 
                                text=[f"  <b>—  {last_y:.1f}</b>"], 
                                textposition="middle right", textfont=dict(color='black', size=12),
                                marker=dict(opacity=0), showlegend=False, hoverinfo='skip'
                            ))

            fig_p.update_layout(
                height=600, margin=dict(b=100, l=10, r=120, t=50),
                plot_bgcolor='white', paper_bgcolor='white',
                xaxis=dict(
                    tickangle=-45, showgrid=False, tickfont=dict(color="black", family="Arial Black"), 
                    showline=True, linecolor='black',
                    range=[-0.5, 12.5] # Da espacio a la derecha para el texto
                ),
                yaxis=dict(showgrid=True, gridcolor='rgba(0,0,0,0.1)', griddash='dash', showticklabels=False, zeroline=False),
                legend=dict(orientation="v", yanchor="top", y=0.98, xanchor="left", x=0.02, font=dict(color="black", size=10), bgcolor="rgba(255,255,255,0.8)", bordercolor="black", borderwidth=1)
            )
            st.session_state.fig_plotly = fig_p

            # --- 4. LÓGICA ESPACIAL (MATCH TOTAL) ---
            stations_shp = geodata['stations'].copy()
            stations_shp['Name'] = stations_shp['Name'].astype(str).str.upper().str.strip()
            stations_shp['ENTIDAD'] = stations_shp['ENTIDAD'].astype(str).str.upper().str.strip()
            data_clean = total_df.copy()
            data_clean['Name'] = data_clean['Name'].astype(str).str.upper().str.strip()
            data_clean['ENTIDAD'] = data_clean['ENTIDAD'].astype(str).str.upper().str.strip()

            updated_stations_gdf = stations_shp.merge(data_clean, on=['Name', 'ENTIDAD'], how='inner')
            if 'P_mm_y' in updated_stations_gdf.columns: updated_stations_gdf['P_mm'] = updated_stations_gdf['P_mm_y']
            stations_filtered_gdf = updated_stations_gdf.dropna(subset=['P_mm']).copy()

            river_sensors_gdf_merged = gpd.GeoDataFrame() # Inicializar un GeoDataFrame vacío
            if not river_sensors_gdf.empty and not river_data_df.empty:
                # Unir el GeoDataFrame de sensores (cargado de load_geodata) con los datos extraídos (de fetch_river_sensor_data)
                # El 'Name' en river_sensors_gdf es el nombre largo del KMZ
                # El 'KMZ_Name' en river_data_df es también el nombre largo del KMZ
                river_sensors_gdf_merged = river_sensors_gdf.merge(
                    river_data_df, 
                    left_on='Name', 
                    right_on='KMZ_Name', 
                    how='inner'
                )
            st.session_state.river_sensors_gdf_merged = river_sensors_gdf_merged # <--- GUARDAR ESTE NUEVO GEODATAFRAME UNIDO          

            st.session_state.stations_filtered_gdf = stations_filtered_gdf
            st.session_state.outliers_df = pd.DataFrame()
            if len(stations_filtered_gdf) >= 5:
                interpolation_results, metrics_df = find_best_interpolation_model(stations_filtered_gdf, geodata['boundary'])
            else:
                interpolation_results, metrics_df = None, None
            
            st.session_state.interpolation_results = interpolation_results
            st.session_state.metrics_df = metrics_df
            st.session_state.progress_percent = 80
            st.rerun()

        # ETAPA 4: RENDERIZADO DEL MAPA PROFESIONAL (DISEÑO ORIGINAL)
        elif st.session_state.progress_percent == 80:
            if 'stations_filtered_gdf' not in st.session_state:
                st.session_state.progress_percent = 60
                st.rerun()
                
            stations_filtered_gdf = st.session_state.stations_filtered_gdf
            interpolation_results = st.session_state.interpolation_results
            river_sensors_gdf_merged = st.session_state.river_sensors_gdf_merged
            report_date_pd = pd.to_datetime(st.session_state.report_date_to_process.date())

            # --- 1. CONFIGURACIÓN DE LIENZO ---
            fig, ax = plt.subplots(figsize=(16, 12), facecolor='white')
            ax.set_facecolor('white')
            fig.subplots_adjust(right=0.7)
            
            # Trazado de elementos base que siempre deben estar presentes
            limite_gdf = geodata['boundary'].to_crs(geodata['hillshade'].crs)
            cuenca_gdf = geodata['cuenca'].to_crs(geodata['hillshade'].crs)
            
            # Obtener los límites de ambas capas para asegurar que todo quepa en el mapa
            lim_bounds = limite_gdf.total_bounds
            cue_bounds = cuenca_gdf.total_bounds
            
            # Combinar los límites para obtener la extensión total
            total_minx = min(lim_bounds[0], cue_bounds[0])
            total_miny = min(lim_bounds[1], cue_bounds[1])
            total_maxx = max(lim_bounds[2], cue_bounds[2])
            total_maxy = max(lim_bounds[3], cue_bounds[3])
            
            # Calcular el margen basándose en la extensión total combinada
            total_width = total_maxx - total_minx
            total_height = total_maxy - total_miny
            x_margin = total_width * 0.05  # 5% de margen a cada lado
            y_margin = total_height * 0.05 # 5% de margen arriba y abajo

            # Establecer los límites finales del mapa para que todo sea visible
            ax.set_xlim(total_minx - x_margin, total_maxx + x_margin)
            ax.set_ylim(total_miny - y_margin, total_maxy + y_margin)

            # --- 3. CAPAS RASTER (Relieve y Lluvia) ---
            boundary_geom = geodata['boundary'].to_crs(geodata['hillshade'].crs).geometry
            clipped_hillshade, clipped_transform = mask(geodata['hillshade'], boundary_geom, crop=True, nodata=np.nan)
            hillshade_data = clipped_hillshade[0].astype(float)
            hillshade_data[hillshade_data == 255] = np.nan
            
            # --- ASIGNACIÓN DE ZORDER CORREGIDA ---
            # ZORDER 1: Capa base de relieve (lo más bajo)
            im = ax.imshow(hillshade_data,
                           extent=[clipped_transform[2],
                                   clipped_transform[2] + clipped_transform[0] * hillshade_data.shape[1],
                                   clipped_transform[5] + clipped_transform[4] * hillshade_data.shape[0],
                                   clipped_transform[5]],
                           cmap='gray', alpha=0.7, aspect='equal', zorder=1)

            # Lógica de trazado de la capa de precipitación con enmascaramiento
            if interpolation_results and np.any(interpolation_results["raster_image"]):
                raster_image = np.ma.masked_invalid(interpolation_results["raster_image"])
                raster_meta = interpolation_results["raster_meta"]
                custom_cmap = LinearSegmentedColormap.from_list('custom_precip', ['#f03725', '#F3FD89', '#1FB6EA'])
                precip_min = stations_filtered_gdf['P_mm'].min()
                precip_max = stations_filtered_gdf['P_mm'].max()
                # ### CAMBIO AQUÍ ###: ZORDER 2 para la precipitación (debajo de los ríos)
                show(raster_image, ax=ax, transform=raster_meta['transform'], cmap=custom_cmap, alpha=0.6, vmin=precip_min, vmax=precip_max, zorder=2)
                raster_io = interpolation_results['raster_io']
            else:
                log_messages.append("⚠️ No se trazó la capa de precipitación por falta de datos o error de interpolación.")
                raster_io = None

            # --- 4. CAPAS VECTORIALES ---
            # Corrientes de Agua (Azul fuerte)
            streams_gdf = geodata['streams'].to_crs(geodata['hillshade'].crs)
            if 'order_1' in streams_gdf.columns:
                order_col = 'order_1'
                unique_orders = sorted(streams_gdf[order_col].dropna().unique())
                for order in unique_orders:
                    subset = streams_gdf[streams_gdf[order_col] == order]
                    linewidth = 0.1 + (order * 0.1) if pd.notna(order) else 0.1
                    subset.plot(ax=ax, color='#10008C', linewidth=linewidth, label=f'Orden {order}', zorder=3)
            else:
                streams_gdf.plot(ax=ax, color='#10008C', linewidth=0.7, zorder=3)

            # Asegurar fondo blanco y bordes de mapa
            ax.set_facecolor('white')
            fig.patch.set_facecolor('white')
            ax.patch.set_facecolor('white')
            for spine in ax.spines.values():
                spine.set_edgecolor('black')
                spine.set_linewidth(1)
            # Límite Municipal (Verde)
            limite_gdf.plot(ax=ax, facecolor='none', edgecolor='#38A800', linewidth=2.5, zorder=4)
            
            # Límite Urbano (Negro)
            geodata['urban'].to_crs(geodata['hillshade'].crs).plot(ax=ax, facecolor='none', edgecolor='#000000', linewidth=1.2, zorder=4)
            
            # Cuenca Palote (Rojo)
            cuenca_gdf.plot(ax=ax, facecolor='none', edgecolor='#FF0000', linewidth=1.5, zorder=4)
            
            # Presa El Palote (Cian con borde azul)
            geodata['presa'].to_crs(geodata['hillshade'].crs).plot(ax=ax, facecolor='#00E6A9', edgecolor='#002673', linewidth=1, zorder=5)
            
            # Estaciones (Cuadros con borde negro)
            s_f = stations_filtered_gdf
            s_f[s_f['ENTIDAD']=='SAPAL'].to_crs(geodata['hillshade'].crs).plot(ax=ax, marker='s', color='#00C5FF', markersize=40, edgecolor='black', zorder=6)
            s_f[s_f['ENTIDAD']=='CONAGUA'].to_crs(geodata['hillshade'].crs).plot(ax=ax, marker='s', color='#55FF00', markersize=40, edgecolor='black', zorder=6)

            # --- 5. ESTÉTICA DE EJES Y TÍTULOS ---
            ax.xaxis.set_major_formatter(FuncFormatter(lambda x, p: f'{int(x):,}'))
            ax.yaxis.set_major_formatter(FuncFormatter(lambda x, p: f'{int(x):,}'))
            ax.tick_params(axis='both', labelsize=10, colors='black', direction='in')
            for spine in ax.spines.values(): spine.set_edgecolor('black')
            ax.grid(True, linestyle=':', alpha=0.4, color='gray')

            ax.set_title(f"PRECIPITACIÓN ACUMULADA ANUAL\nCORTE AL {report_date_pd.strftime('%d de %B de %Y').upper()}", fontsize=14, fontweight='bold', loc='left')
            ax.tick_params(axis='both', which='major', labelsize=10, direction='in', color='black', labelcolor='black')
            for label in ax.get_xticklabels(): label.set_fontweight('bold'); label.set_rotation(0)
            for label in ax.get_yticklabels(): label.set_fontweight('bold'); label.set_rotation(90)
            ax.xaxis.set_major_formatter(FuncFormatter(lambda x, p: f'{int(x):,}')); ax.yaxis.set_major_formatter(FuncFormatter(lambda x, p: f'{int(x):,}'))
            ax.set_xlabel(""); ax.set_ylabel("")
            add_north_arrow(ax)
            # 1. Parámetros de la barra de escala
            scale_length_m = 5000  # Longitud total de la barra en metros (5 km)
            scale_segments = 5     # Número de divisiones (blanco y negro)
            
            # 2. Calcular la posición de anclaje (esquina inferior izquierda)
            # Usaremos el mismo margen que para el logo para mantener la consistencia
            map_width = total_maxx - total_minx
            map_height = total_maxy - total_miny
            margin_x = map_width * 0.02
            margin_y = map_height * 0.02
            
            # Coordenada 'x' e 'y' de la esquina inferior izquierda de la barra
            scale_x = total_minx + margin_x
            scale_y = total_miny + margin_y
            
            # 3. Dibujar los segmentos de la barra (rectángulos)
            segment_length = scale_length_m / scale_segments
            bar_height = map_height * 0.007 # Altura de la barra, relativa al mapa

            for i in range(scale_segments):
                color = 'black' if i % 2 == 0 else 'white'
                rect = plt.Rectangle(
                    (scale_x + i * segment_length, scale_y),  # Posición (x, y)
                    segment_length,                           # Ancho
                    bar_height,                               # Alto
                    facecolor=color,
                    edgecolor='black',
                    linewidth=1,
                    zorder=10  # zorder alto para que esté encima de todo
                )
                ax.add_patch(rect)

            # 4. Dibujar las etiquetas de texto UNA SOLA VEZ (fuera del bucle)
            text_offset = map_height * 0.008 # Distancia del texto a la barra
            text_y_pos = scale_y - text_offset
            
            ax.text(scale_x, text_y_pos, '0', ha='center', va='top', fontsize=8, weight='bold', zorder=10)
            ax.text(scale_x + scale_length_m / 2, text_y_pos, '2.5', ha='center', va='top', fontsize=8, weight='bold', zorder=10)
            ax.text(scale_x + scale_length_m, text_y_pos, '5 km', ha='center', va='top', fontsize=8, weight='bold', zorder=10)
            legend_elements = [
                Patch(facecolor='none', edgecolor='#38A800', linewidth=2, label='MUNICIPIO DE LEÓN'),
                Patch(facecolor='none', edgecolor='black', linewidth=1, label='LÍMITE URBANO'),
                Patch(facecolor='none', edgecolor='#FF0000', linewidth=1.5, label='CUENCA P. PALOTE'),
                Patch(facecolor='#00E6A9', edgecolor='#002673', label='PRESA EL PALOTE'),
                Line2D([0], [0], color='#10008C', lw=1, label='CORRIENTES DE AGUA'),
                Line2D([0], [0], marker='s', color='#55FF00', label='CONAGUA',
                       markerfacecolor='#55FF00', markeredgecolor='black', markersize=8, linestyle='None'),
                Line2D([0], [0], marker='s', color='#00C5FF', label='SAPAL',
                       markerfacecolor='#00C5FF', markeredgecolor='black', markersize=8, linestyle='None')
            ]
            
            legend_ax = ax.legend(handles=legend_elements,
                                  bbox_to_anchor=(1.02, 1),
                                  loc='upper left',
                                  fontsize=10,
                                  title='SIMBOLOGÍA',
                                  title_fontsize=12,
                                  frameon=True,
                                  edgecolor='black',
                                  facecolor='white')
            legend_ax.get_title().set_fontweight('bold')

            # BARRA DE COLOR VERTICAL DENTRO DEL CUADRO DE LEYENDA
            if interpolation_results and np.any(interpolation_results["raster_image"]):
                # Posicionar la barra debajo de la leyenda
                cbar_ax = fig.add_axes([0.77, 0.15, 0.02, 0.3])  # [left, bottom, width, height] - VERTICAL
                norm = Normalize(vmin=precip_min, vmax=precip_max)
                cb = ColorbarBase(cbar_ax, cmap=custom_cmap, norm=norm, orientation='vertical')
                cb.ax.set_title('Precipitación\nAcumulada (mm)', size=10, weight='bold', pad=15)
                cb.ax.tick_params(labelsize=9)
                
                # Marco alrededor de la barra
                for spine in cbar_ax.spines.values():
                    spine.set_edgecolor('black')
                    spine.set_linewidth(1)

            # --- 7. LOGO AZUL (ESQUINA INFERIOR DERECHA) ---
            if geodata["logo_azul"] is not None:
                # 1. Definir el tamaño del logo relativo al ancho del mapa
                map_width = total_maxx - total_minx
                logo_width = map_width * 0.15  # El logo ocupará el 15% del ancho del mapa
            
                # 2. Calcular la altura del logo para mantener su proporción original
                aspect_ratio = geodata["logo_azul"].shape[0] / geodata["logo_azul"].shape[1] # alto / ancho en píxeles
                logo_height = logo_width * aspect_ratio
            
                # 3. Definir el margen desde los bordes del mapa
                margin_x = map_width * 0.02 # 2% de margen horizontal
                margin_y = (total_maxy - total_miny) * 0.02 # 2% de margen vertical
            
                # 4. Calcular la coordenada de la esquina inferior-izquierda (x, y) del logo
                # Para la X: Borde derecho del mapa - margen - ancho del logo
                logo_x = total_maxx - margin_x - logo_width
                # Para la Y: Borde inferior del mapa + margen
                logo_y = total_miny + margin_y
            
                # 5. Dibujar el logo en la posición calculada
                ax.imshow(geodata["logo_azul"], 
                          extent=[logo_x, logo_x + logo_width, logo_y, logo_y + logo_height],
                          aspect='auto', zorder=10) # Usar un zorder alto para que siempre esté encima
            ax.grid(True, linestyle=':', alpha=0.6, color='black')

            # Guardar y enviar a Streamlit
            png_buf = io.BytesIO()
            fig.savefig(png_buf, format="png", dpi=300, facecolor='white', bbox_inches='tight')
            png_buf.seek(0)

            st.session_state.figure = fig
            st.session_state.png_buffer = png_buf
            st.session_state.report_date_str = report_date_pd.strftime('%Y%m%d')

            report_date_str_formatted = report_date_pd.strftime('%d de %B de %Y').title()
            stats_md = f"### Resumen del Reporte\n- **Fecha de Corte:** {report_date_str_formatted}\n- **Estaciones Válidas:** {len(stations_filtered_gdf)}\n- **Método:** {interpolation_results['best_method'] if interpolation_results else 'N/A'}"
            # Panel de estadísticas
            st.session_state.stats_panel_md = {

                "header": stats_md, 
                "desc_stats": s_f['P_mm'].describe().to_frame().T.rename(columns={'mean':'Promedio','max':'Máximo','min':'Mínimo'}),
                "total_df_con_na": s_f[['Name', 'P_mm']].sort_values(by='P_mm', ascending=False)
            }
            
            st.session_state.map_generated = True
            st.session_state.processing_state = 'idle'
            st.session_state.progress_percent = 100
            st.rerun()

        
            
            # --- 4. LÓGICA ESPACIAL (MATCH TOTAL) ---
            total_df_con_na = total_df.copy()
            st.session_state.total_df_con_na = total_df_con_na
            
            # 1. Preparamos el Shapefile (Copia limpia)
            stations_shp = geodata['stations'].copy()
            stations_shp['Name'] = stations_shp['Name'].astype(str).str.upper().str.strip()
            stations_shp['ENTIDAD'] = stations_shp['ENTIDAD'].astype(str).str.upper().str.strip()
            
            # 2. Preparamos los datos descargados
            data_downloaded = total_df.copy()
            data_downloaded['Name'] = data_downloaded['Name'].astype(str).str.upper().str.strip()
            data_downloaded['ENTIDAD'] = data_downloaded['ENTIDAD'].astype(str).str.upper().str.strip()

            # 3. Unión (Merge)
            # Unimos por Nombre y Entidad para evitar confusiones
            updated_stations_gdf = stations_shp.merge(data_downloaded, on=['Name', 'ENTIDAD'], how='inner')
            
            # 4. Verificación de columna de lluvia
            if 'P_mm_y' in updated_stations_gdf.columns:
                updated_stations_gdf['P_mm'] = updated_stations_gdf['P_mm_y']
            
            stations_filtered_gdf = updated_stations_gdf.dropna(subset=['P_mm']).copy()

            # --- DIAGNÓSTICO PARA TI (Aparecerá si hay menos de 15 estaciones) ---
            if len(stations_filtered_gdf) < 15:
                with st.expander("🕵️ Debug: ¿Por qué faltan estaciones?"):
                    st.write("Nombres en tu Shapefile:", stations_shp[stations_shp['ENTIDAD']=='SAPAL']['Name'].tolist())
                    st.write("Nombres en la API:", data_downloaded[data_downloaded['ENTIDAD']=='SAPAL']['Name'].tolist())
            
            # 5. Guardar resultados
            st.session_state.stations_filtered_gdf = stations_filtered_gdf
            st.session_state.outliers_df = pd.DataFrame()
            
            num_final = len(stations_filtered_gdf)
            if num_final < 5:
                interpolation_results, metrics_df = None, None
            else:
                interpolation_results, metrics_df = find_best_interpolation_model(stations_filtered_gdf, geodata['boundary'])
            
            st.session_state.interpolation_results = interpolation_results
            st.session_state.metrics_df = metrics_df
            st.session_state.progress_percent = 90
            st.rerun()
            
        # ETAPA 4: Progreso 90% -> 100% (Renderizado de Mapa)
        # ETAPA 4: Progreso 90% -> 100% (Renderizado de Mapa con Diseño Original)
        elif st.session_state.progress_percent == 90:
            # Recuperamos todas las variables necesarias del estado de la sesión
            stations_filtered_gdf = st.session_state.stations_filtered_gdf
            interpolation_results = st.session_state.interpolation_results
            outliers_df = st.session_state.outliers_df
            total_df_con_na = st.session_state.total_df_con_na
            metrics_df = st.session_state.metrics_df
            report_date_pd = pd.to_datetime(st.session_state.report_date_to_process.date())

            # --- INICIO DE TU CÓDIGO DE PLOTEO ORIGINAL RESTAURADO ---
            fig, ax = plt.subplots(figsize=(16, 12), facecolor='white') # Restaurado a (16, 12)
            ax.set_facecolor('white')
            fig.patch.set_facecolor('white')
            fig.subplots_adjust(right=0.7)
            
            limite_gdf = geodata['boundary'].to_crs(geodata['hillshade'].crs)
            cuenca_gdf = geodata['cuenca'].to_crs(geodata['hillshade'].crs)
            
            lim_bounds = limite_gdf.total_bounds
            cue_bounds = cuenca_gdf.total_bounds
            
            total_minx = min(lim_bounds[0], cue_bounds[0])
            total_miny = min(lim_bounds[1], cue_bounds[1])
            total_maxx = max(lim_bounds[2], cue_bounds[2])
            total_maxy = max(lim_bounds[3], cue_bounds[3])
            
            total_width = total_maxx - total_minx
            total_height = total_maxy - total_miny
            x_margin = total_width * 0.05
            y_margin = total_height * 0.05
            
            ax.set_xlim(total_minx - x_margin, total_maxx + x_margin)
            ax.set_ylim(total_miny - y_margin, total_maxy + y_margin)
            
            boundary_geom = geodata['boundary'].to_crs(geodata['hillshade'].crs).geometry
            clipped_hillshade, clipped_transform = mask(geodata['hillshade'], boundary_geom, crop=True, nodata=np.nan)
            hillshade_data = clipped_hillshade[0].astype(float)
            hillshade_data[hillshade_data == 255] = np.nan
            
            ax.imshow(hillshade_data,
                           extent=[clipped_transform[2],
                                   clipped_transform[2] + clipped_transform[0] * hillshade_data.shape[1],
                                   clipped_transform[5] + clipped_transform[4] * hillshade_data.shape[0],
                                   clipped_transform[5]],
                           cmap='gray', alpha=0.7, aspect='equal', zorder=1)
            
            if interpolation_results and np.any(interpolation_results["raster_image"]):
                raster_image = np.ma.masked_invalid(interpolation_results["raster_image"])
                raster_meta = interpolation_results["raster_meta"]
                custom_cmap = LinearSegmentedColormap.from_list('custom_precip', ['#f03725', '#F3FD89', '#1FB6EA'])
                precip_min = stations_filtered_gdf['P_mm'].min()
                precip_max = stations_filtered_gdf['P_mm'].max()
                show(raster_image, ax=ax, transform=raster_meta['transform'], cmap=custom_cmap, alpha=0.6, vmin=precip_min, vmax=precip_max, zorder=2)
                raster_io = interpolation_results['raster_io']
            else:
                raster_io = None
            
            streams_gdf = geodata['streams'].to_crs(geodata['hillshade'].crs)
            streams_gdf.plot(ax=ax, color='#10008C', linewidth=0.7, zorder=3)
            
            for spine in ax.spines.values():
                spine.set_edgecolor('black')
                spine.set_linewidth(1)
            
            geodata['boundary'].to_crs(geodata['hillshade'].crs).plot(ax=ax, facecolor='none', edgecolor='#38A800', linewidth=2, zorder=4)
            geodata['urban'].to_crs(geodata['hillshade'].crs).plot(ax=ax, facecolor='none', edgecolor='#000000', linewidth=1.5, clip_on=True, zorder=4)
            geodata['cuenca'].to_crs(geodata['hillshade'].crs).plot(ax=ax, facecolor='none', edgecolor='#FF0000', linewidth=1.5, clip_on=False, zorder=4) # Color rojo original
            geodata['presa'].to_crs(geodata['hillshade'].crs).plot(ax=ax, facecolor='#00E6A9', edgecolor='#002673', linewidth=1, clip_on=True, zorder=5)
            
            if not stations_filtered_gdf.empty:
                stations_filtered_gdf[stations_filtered_gdf['ENTIDAD'] == 'SAPAL'].to_crs(geodata['hillshade'].crs).plot(ax=ax, marker='s', color='#00C5FF', markersize=30, edgecolor='black', zorder=6)
                stations_filtered_gdf[stations_filtered_gdf['ENTIDAD'] == 'CONAGUA'].to_crs(geodata['hillshade'].crs).plot(ax=ax, marker='s', color='#55FF00', markersize=30, edgecolor='black', zorder=6)
                
            if not river_sensors_gdf_merged.empty:
                for alert_category, alert_info in RIVER_ALERTS.items():
                    subset = river_sensors_gdf_merged[river_sensors_gdf_merged['Alert'] == alert_category]
                    if not subset.empty:
                        subset.plot(ax=ax, marker=alert_info['symbol'], 
                                    color=alert_info['color'], 
                                    markersize=100, # Aumentar tamaño para visibilidad
                                    edgecolor='black', # Borde negro para contraste
                                    linewidth=1,
                                    label=f"Ríos: {alert_info['label']}", # Para la leyenda
                                    zorder=7, # Zorder más alto para que estén encima
                                    path_effects=[patheffects.withStroke(linewidth=2, foreground='white')]) # Efecto de borde blanco para resaltar    

            ax.set_title(f"PRECIPITACIÓN ACUMULADA ANUAL\nCORTE AL {report_date_pd.strftime('%d de %B de %Y').upper()}", fontsize=14, fontweight='bold', loc='left')
            ax.tick_params(axis='both', which='major', labelsize=10, direction='in', color='black', labelcolor='black')
            for label in ax.get_xticklabels(): label.set_fontweight('bold'); label.set_rotation(0)
            for label in ax.get_yticklabels(): label.set_fontweight('bold'); label.set_rotation(90)
            ax.xaxis.set_major_formatter(FuncFormatter(lambda x, p: f'{int(x):,}')); ax.yaxis.set_major_formatter(FuncFormatter(lambda x, p: f'{int(x):,}'))
            ax.set_xlabel(""); ax.set_ylabel("")
            add_north_arrow(ax)
            
            scale_length_m = 5000; scale_segments = 5
            scale_x = total_minx + (total_width * 0.02); scale_y = total_miny + (total_height * 0.02)
            segment_length = scale_length_m / scale_segments; bar_height = total_height * 0.007
            for i in range(scale_segments):
                color = 'black' if i % 2 == 0 else 'white'
                rect = plt.Rectangle((scale_x + i * segment_length, scale_y), segment_length, bar_height, facecolor=color, edgecolor='black', linewidth=1, zorder=10)
                ax.add_patch(rect)
            
            text_offset = total_height * 0.008; text_y_pos = scale_y - text_offset
            ax.text(scale_x, text_y_pos, '0', ha='center', va='top', fontsize=8, weight='bold', zorder=10)
            ax.text(scale_x + scale_length_m / 2, text_y_pos, '2.5', ha='center', va='top', fontsize=8, weight='bold', zorder=10)
            ax.text(scale_x + scale_length_m, text_y_pos, '5 km', ha='center', va='top', fontsize=8, weight='bold', zorder=10)
            
            legend_elements = [
                Patch(facecolor='none', edgecolor='#38A800', linewidth=2, label='MUNICIPIO DE LEÓN'),
                Patch(facecolor='none', edgecolor='black', linewidth=1, label='LÍMITE URBANO'),
                Patch(facecolor='none', edgecolor='#FF0000', linewidth=1.5, label='CUENCA P. PALOTE'),
                Patch(facecolor='#00E6A9', edgecolor='#002673', label='PRESA EL PALOTE'),
                Line2D([0], [0], color='#10008C', lw=1, label='CORRIENTES DE AGUA'),
                Line2D([0], [0], marker='s', color='#55FF00', label='CONAGUA', markerfacecolor='#55FF00', markeredgecolor='black', markersize=8, linestyle='None'),
                Line2D([0], [0], marker='s', color='#00C5FF', label='SAPAL', markerfacecolor='#00C5FF', markeredgecolor='black', markersize=8, linestyle='None'),
                # NUEVAS ENTRADAS PARA LOS SENSORES DE RÍO
                Line2D([0], [0], marker=RIVER_ALERTS["VERDE"]["symbol"], color='none', label=f'Ríos: {RIVER_ALERTS["VERDE"]["label"]}', markerfacecolor=RIVER_ALERTS["VERDE"]["color"], markeredgecolor='black', markersize=8, linestyle='None'),
                Line2D([0], [0], marker=RIVER_ALERTS["AMARILLO"]["symbol"], color='none', label=f'Ríos: {RIVER_ALERTS["AMARILLO"]["label"]}', markerfacecolor=RIVER_ALERTS["AMARILLO"]["color"], markeredgecolor='black', markersize=8, linestyle='None'),
                Line2D([0], [0], marker=RIVER_ALERTS["NARANJA"]["symbol"], color='none', label=f'Ríos: {RIVER_ALERTS["NARANJA"]["label"]}', markerfacecolor=RIVER_ALERTS["NARANJA"]["color"], markeredgecolor='black', markersize=8, linestyle='None'),
                Line2D([0], [0], marker=RIVER_ALERTS["ROJO"]["symbol"], color='none', label=f'Ríos: {RIVER_ALERTS["ROJO"]["label"]}', markerfacecolor=RIVER_ALERTS["ROJO"]["color"], markeredgecolor='black', markersize=8, linestyle='None')
            ]
            legend_ax = ax.legend(handles=legend_elements, bbox_to_anchor=(1.02, 1), loc='upper left', fontsize=10, title='SIMBOLOGÍA', title_fontsize=12, frameon=True, edgecolor='black', facecolor='white')
            legend_ax.get_title().set_fontweight('bold')

            if interpolation_results and np.any(interpolation_results["raster_image"]):
                cbar_ax = fig.add_axes([0.77, 0.15, 0.02, 0.3])
                norm = Normalize(vmin=precip_min, vmax=precip_max)
                cb = ColorbarBase(cbar_ax, cmap=custom_cmap, norm=norm, orientation='vertical')
                cb.ax.set_title('Precipitación\nAcumulada (mm)', size=10, weight='bold', pad=15)
                cb.ax.tick_params(labelsize=9)
                for spine in cbar_ax.spines.values(): spine.set_edgecolor('black'); spine.set_linewidth(1)
            
            # --- PREPARACIÓN DE POSICIÓN DEL LOGO ---
            if geodata["logo_azul"] is not None:
                aspect_ratio = geodata["logo_azul"].shape[0] / geodata["logo_azul"].shape[1]
                logo_w = total_width * 0.15
                logo_h = logo_w * aspect_ratio
                l_x = total_maxx - (total_width * 0.03) - logo_w
                l_y = total_miny + (total_height * 0.03)

                # A. INSERTAR LOGO AZUL PARA EXPORTACIÓN
                # Guardamos el objeto en una variable para poder borrarlo luego
                img_logo_obj = ax.imshow(geodata["logo_azul"], extent=[l_x, l_x + logo_w, l_y, l_y + logo_h], aspect='auto', zorder=15)

            # --- 1. GUARDAR VERSIÓN ESTÁNDAR (CON LOGO AZUL Y FONDO BLANCO) ---
            png_buffer = io.BytesIO()
            fig.savefig(png_buffer, format="png", dpi=300, facecolor='white', edgecolor='none', pad_inches=0.2)
            png_buffer.seek(0)

            # --- 2. TRANSFORMACIÓN A MODO OSCURO (PARA LA APP) ---
            # Borrar el logo azul del mapa
            if 'img_logo_obj' in locals():
                img_logo_obj.remove()
                # INSERTAR LOGO BLANCO
                ax.imshow(geodata["logo_blanco"], extent=[l_x, l_x + logo_w, l_y, l_y + logo_h], aspect='auto', zorder=15)

            # Fondo transparente
            fig.patch.set_facecolor('none')
            ax.set_facecolor('none')

            # --- APLICAR BLANCO A TODO EL TEXTO ---
            ax.title.set_color('white')
            ax.tick_params(axis='both', colors='white')
            from matplotlib import patheffects # Asegurar import local
            for label in ax.get_xticklabels() + ax.get_yticklabels():
                label.set_color('white')
                label.set_path_effects([patheffects.withStroke(linewidth=3, foreground='black', alpha=0.5)])

            # Marcos, Grilla y Leyenda en Blanco
            for spine in ax.spines.values(): spine.set_edgecolor('white')
            ax.grid(True, linestyle=':', alpha=0.3, color='white')

            leg = ax.get_legend()
            if leg:
                leg.get_frame().set_facecolor('none')
                leg.get_frame().set_edgecolor('white')
                leg.get_title().set_color('white')
                for text in leg.get_texts(): text.set_color('white')

            # Barra de color y otros textos flotantes
            if 'cb' in locals():
                cb.ax.yaxis.set_tick_params(color='white', labelcolor='white')
                cb.ax.title.set_color('white')
                cb.outline.set_edgecolor('white')
            
            for t in ax.texts: t.set_color('white')
            
            # Ajustar Escala Gráfica
            for patch in ax.patches:
                if isinstance(patch, plt.Rectangle):
                    fc = patch.get_facecolor()
                    if fc[0] > 0.8: # Segmentos blancos
                        patch.set_facecolor('none'); patch.set_edgecolor('white')
                    else: # Segmentos negros
                        patch.set_facecolor('white'); patch.set_edgecolor('white')

            # --- 3. GUARDAR EN SESSION STATE Y TERMINAR ---
            st.session_state.figure = fig
            st.session_state.raster_io = raster_io
            st.session_state.png_buffer = png_buffer
            st.session_state.report_date_str = report_date_pd.strftime('%Y%m%d')

            # Estadísticas descriptivas
            desc_stats = stations_filtered_gdf['P_mm'].describe().to_frame().T.rename(
                columns={'count': 'Estaciones', 'mean': 'Promedio', 'std': 'Desv. Est.', 'min': 'Mínimo', 'max': 'Máximo'}
            )
            report_date_str_formatted = report_date_pd.strftime('%d de %B de %Y').title()
            
             river_stats_str = ""
            river_detail_df = pd.DataFrame() # Inicializar vacío
            if not river_sensors_gdf_merged.empty:
                verde_count = len(river_sensors_gdf_merged[river_sensors_gdf_merged['Alert'] == 'VERDE'])
                amarillo_count = len(river_sensors_gdf_merged[river_sensors_gdf_merged['Alert'] == 'AMARILLO'])
                naranja_count = len(river_sensors_gdf_merged[river_sensors_gdf_merged['Alert'] == 'NARANJA'])
                rojo_count = len(river_sensors_gdf_merged[river_sensors_gdf_merged['Alert'] == 'ROJO'])
                
                river_stats_str = f"\n- **Sensores de Río:** {len(river_sensors_gdf_merged)} activos"
                if verde_count: river_stats_str += f"\n  - {RIVER_ALERTS['VERDE']['label']}: {verde_count}"
                if amarillo_count: river_stats_str += f"\n  - {RIVER_ALERTS['AMARILLO']['label']}: {amarillo_count}"
                if naranja_count: river_stats_str += f"\n  - {RIVER_ALERTS['NARANJA']['label']}: {naranja_count}"
                if rojo_count: river_stats_str += f"\n  - {RIVER_ALERTS['ROJO']['label']}: {rojo_count}"

                river_detail_df = river_sensors_gdf_merged[['Name_Display', 'Level_m', 'Alert']].rename(
                    columns={'Name_Display': 'Sensor', 'Level_m': 'Nivel (m)', 'Alert': 'Alerta'}
                )

            stats_md = f"### Resumen del Reporte\n- **Fecha de Corte:** {report_date_str_formatted}\n- **Estaciones Válidas:** {len(stations_filtered_gdf)}\n- **Método:** {interpolation_results['best_method'] if interpolation_results else 'N/A'}{river_stats_str}"
            
            st.session_state.stats_panel_md = {
                "header": stats_md, 
                "total_df_con_na": total_df_con_na, 
                "outliers_df": pd.DataFrame(), 
                "desc_stats": desc_stats, 
                "metrics_df": metrics_df,
                "river_detail_df": river_detail_df # <--- NUEVO: Datos detallados de los ríos
            }
            
            st.session_state.map_generated = True
            st.session_state.processing_state = 'idle'
            st.session_state.progress_percent = 100
            st.rerun()

           

            

            

    with col_mapa:
        # --- NUEVO PLACEHOLDER CON SPINNER PERSONALIZADO ---
        st.markdown("""
        <div class="center-container">
            <div class="custom-spinner"></div>
        </div>
        """, unsafe_allow_html=True)
        








































































