# -*- coding: utf-8 -*-
"""
Created on Mon Aug 11 08:10:08 2025
@author: xmanu
Versión 12.0 - Versión final estable con lógica de SAPAL de R, CONAGUA en paralelo y flujo de UI corregido.
"""

# --- LIBRERÍAS PRINCIPALES ---
import warnings
from urllib3.exceptions import InsecureRequestWarning
warnings.filterwarnings("ignore", category=InsecureRequestWarning)
import rasterio
from rasterio.mask import mask # Importación corregida
 
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
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from pykrige.ok import OrdinaryKriging
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.model_selection import LeaveOneOut

from rasterio.transform import from_origin

from rasterio.plot import show
warnings.simplefilter('ignore', InsecureRequestWarning)
# --- CONFIGURACIÓN DE LA PÁGINA Y ESTADO DE SESIÓN ---
# --- CONFIGURACIÓN DE LA PÁGINA Y ESTADO DE SESIÓN ---
# 1. ESTABLECER LA CONFIGURACIÓN DE LA PÁGINA (DEBE SER EL PRIMER COMANDO DE STREAMLIT)
st.set_page_config(page_title="Reporte Pluvial de León", layout="wide")
# --- INICIO DEL BLOQUE DE ESTILOS PERSONALIZADOS (ACTUALIZADO) ---
st.markdown("""
<style>
/* --- ESTILO PARA BOTONES PRINCIPALES --- */
/* Apunta al botón principal (el que tiene el fondo de color) */
div[data-testid="stButton"] > button {
    background-color: #0D6AB7; /* Azul SAPAL */
    color: white;
    border: 1px solid #0D6AB7;
}
/* Estilo para cuando el cursor está sobre el botón */
div[data-testid="stButton"] > button:hover {
    background-color: #0A5591; /* Azul más oscuro */
    color: white;
    border: 1px solid #0A5591;
}

/* --- ESTILO PARA BOTONES DE OPCIÓN (RADIO) --- */
/* Apunta al círculo de color del radio button cuando está seleccionado */
div[data-testid="stRadio"] input:checked + div > span {
    background-color: #0D6AB7 !important; /* Forza el color azul SAPAL */
    border-color: #0D6AB7 !important;     /* Forza el borde azul SAPAL */
}
</style>
""", unsafe_allow_html=True)
# --- FIN DEL BLOQUE DE ESTILOS ---

# --- INICIALIZACIÓN DE ESTADO DE SESIÓN Y OTRAS CONFIGURACIONES ---
os.environ['PROJ_LIB'] = pyproj.datadir.get_data_dir()

# Intenta configurar el idioma y muestra la advertencia si falla (esto ya es seguro)
try:
    locale.setlocale(locale.LC_TIME, 'es_ES.UTF-8')
except locale.Error:
    st.warning("No se pudo configurar el idioma a español.")

# Inicializa el estado de la sesión si no existe
if 'map_generated' not in st.session_state:
    st.session_state.map_generated = False
    st.session_state.figure = None
    st.session_state.raster_io = None
    st.session_state.png_buffer = None
    st.session_state.report_date_str = ""
    st.session_state.stats_panel_md = None

# Ahora el resto de la interfaz puede comenzar
st.title("💧 Generador de Reportes Pluviales para León, Gto.")
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
    Extrae datos de SAPAL. Versión 13.0 - API Nueva con mapeo de nombres refinado.
    Utiliza la nueva API que acepta nombres, con una función interna que traduce
    los nombres del shapefile a los nombres cortos que la API espera.
    """
    results = []
    log_messages.append("--- Iniciando extracción de SAPAL (API Dinámica, Mapeo Refinado)... ---")
    log_container.markdown("\n\n".join(log_messages))

    api_url = "https://www.sapal.gob.mx/api/v1/estaciones/concentrado"
    report_date_str = report_date.strftime('%d-%m-%Y')
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": "https://www.sapal.gob.mx/estaciones-meteorologicas",
    }
    
    # --- FUNCIÓN TRADUCTORA MEJORADA ---
    # Traduce los nombres de tu shapefile a los 11 nombres que la API pública entiende.
    def get_api_name(shp_name):
        name_lower = shp_name.lower()
        
        # Mapeo basado en palabras clave, ahora más preciso gracias a tu imagen.
        if "explora" in name_lower: return "Explora"
        if "ibarrilla" in name_lower: return "P Ibarrilla"
        if "santa rosa" in name_lower: return "Santa Rosa"
        if "morelos-madrazo" in name_lower: return "Blvd La Luz" # Este es el mapeo más probable
        if name_lower == "centro": return "Centro"
        if "jerez" in name_lower: return "Cervantes"
        if "san juan" in name_lower: return "Chapalita"
        if "insurgentes" in name_lower: return "Insurgentes"
        if "pta. santa ana" in name_lower or "pta sta ana" in name_lower: return "Pta Sta Ana"
        if "sapamilpa" in name_lower: return "Sapamilpa"
        if "torres landa" in name_lower: return "Torres Landa"
        
        # Si no coincide con ninguna de las 11 públicas, devuelve None
        return None 

    for station_name_shp in stations:
        api_station_name = get_api_name(station_name_shp)

        if not api_station_name:
            # Comportamiento esperado: Marca las estaciones no públicas como no disponibles.
            log_messages.append(f"ℹ️ **SAPAL {station_name_shp}:** No es una estación pública en la API.")
            results.append({'Name': station_name_shp, 'ENTIDAD': 'SAPAL', 'P_mm': np.nan})
            log_container.markdown("\n\n".join(log_messages))
            continue

        payload = {
            "location": api_station_name, "period": "D",
            "startDate": report_date_str, "endDate": report_date_str
        }

        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            registros = data.get("registro")
            
            if not registros:
                log_messages.append(f"⚠️ **SAPAL {station_name_shp}:** La API no devolvió datos para la fecha.")
                results.append({'Name': station_name_shp, 'ENTIDAD': 'SAPAL', 'P_mm': np.nan})
                continue
            
            df_station = pd.DataFrame(registros)
            
            if 'precipitacionanual' in df_station.columns:
                df_station['precipitacionanual'] = pd.to_numeric(df_station['precipitacionanual'], errors='coerce')
                last_valid_precip = df_station['precipitacionanual'].dropna().iloc[-1] if not df_station['precipitacionanual'].dropna().empty else np.nan
            else:
                last_valid_precip = np.nan

            if pd.notna(last_valid_precip):
                log_messages.append(f"✅ **SAPAL {station_name_shp}:** {last_valid_precip} mm")
                results.append({'Name': station_name_shp, 'ENTIDAD': 'SAPAL', 'P_mm': last_valid_precip})
            else:
                log_messages.append(f"⚠️ **SAPAL {station_name_shp}:** Dato no válido en la respuesta.")
                results.append({'Name': station_name_shp, 'ENTIDAD': 'SAPAL', 'P_mm': np.nan})
        
        except (requests.exceptions.RequestException, ValueError) as e:
            log_messages.append(f"⚠️ **SAPAL {station_name_shp}:** Error de API: {e}")
            results.append({'Name': station_name_shp, 'ENTIDAD': 'SAPAL', 'P_mm': np.nan})
        finally:
             log_container.markdown("\n\n".join(log_messages))
             time.sleep(0.2)

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
def load_geodata():
    shapefile_path = "shapefiles"
    try:
        data = {
            "boundary": gpd.read_file(os.path.join(shapefile_path, "LIMITE.shp")),
            "stations": gpd.read_file(os.path.join(shapefile_path, "ESTACIONES_actualizado.shp")),
            "hillshade": rasterio.open(os.path.join(shapefile_path, "HILLSHADE_LEON.tif")),
            "urban": gpd.read_file(os.path.join(shapefile_path, "LIMITE_URBANO.shp")),
            "cuenca": gpd.read_file(os.path.join(shapefile_path, "CUENCA_PALOTE.shp")),
            "presa": gpd.read_file(os.path.join(shapefile_path, "EL PALOTE.shp")),
            "streams": gpd.read_file(os.path.join(shapefile_path, "CORRIENTES_LEON_012025.shp"))
        }
        try:
            data["logo"] = mpimg.imread(os.path.join(shapefile_path, "logo_sapal.png"))
        except FileNotFoundError:
            data["logo"] = None
        return data
    except Exception as e:
        st.error(f"Error fatal al cargar archivos geoespaciales: {e}")
        st.stop()

geodata = load_geodata()
stations_gdf = geodata["stations"]
locations_sapal = stations_gdf[stations_gdf['ENTIDAD'] == 'SAPAL']['Name'].tolist()
locations_conagua = stations_gdf[stations_gdf['ENTIDAD'] == 'CONAGUA']['Name'].tolist()

def reset_analysis():
    keys_to_reset = ['map_generated', 'figure', 'raster_io', 'png_buffer', 'report_date_str', 'stats_panel_md']
    for key in keys_to_reset:
        if key in st.session_state:
            del st.session_state[key]
    st.session_state.map_generated = False

# --- LÓGICA DE INTERFAZ REESTRUCTURADA CON DOS COLUMNAS ---

# Función para no repetir la información de la barra lateral
def display_sidebar_info():
    """Muestra la información estática en la columna izquierda."""
    st.markdown("Bienvenido al Generador de Reportes Pluviales. Visualiza de forma rápida cómo se distribuyó la lluvia más reciente en todo el municipio de León.")
    st.caption("""
    **Fuentes de Datos:** Este reporte se genera utilizando datos de acceso público.
    - **SAPAL:** Extraído de [sapal.gob.mx/estaciones-metereologicas](https://www.sapal.gob.mx/estaciones-metereologicas)
    - **CONAGUA:** Extraído de [sih.conagua.gob.mx/basedatos/climas/](https://sih.conagua.gob.mx/basedatos/climas/)
    """)
    st.divider()

# Definimos las columnas fuera del if/else para que existan en ambos estados
col_info, col_mapa = st.columns([2, 3]) # Columna izquierda más angosta (ratio 2:3)

if st.session_state.map_generated:
    # --- VISTA DE RESULTADOS ---
    with col_info:
        display_sidebar_info()
        
        st.header("Descargar Resultados")
        
        # --- BOTONES CORREGIDOS ---
        # Se eliminó el parámetro 'use_container_width=True' y los emojis.
        st.download_button(
            "Descargar Datos Geoespaciales (.tif)", 
            st.session_state.raster_io, 
            f"Precipitacion_{st.session_state.report_date_str}.tif", 
            "image/tiff"
        )
        st.download_button(
            "Descargar Imagen del Mapa (.png)", 
            st.session_state.png_buffer, 
            f"Mapa_Precipitacion_{st.session_state.report_date_str}.png", 
            "image/png"
        )
        
        # Se eliminó el emoji del expander para consistencia.
        with st.expander("Ver Resumen y Detalles de los Datos"):
            if st.session_state.stats_panel_md:
                stats = st.session_state.stats_panel_md
                st.markdown(stats["header"])
                st.subheader("Datos Crudos Extraídos")
                st.dataframe(stats["total_df_con_na"].set_index('Name'))
                if not stats["outliers_df"].empty:
                    st.subheader("Valores Atípicos Excluidos")
                    st.dataframe(stats["outliers_df"][['Name', 'ENTIDAD', 'P_mm']].set_index('Name'))
                st.subheader("Estadísticas Descriptivas")
                st.dataframe(stats["desc_stats"])
                if stats["metrics_df"] is not None:
                    st.subheader("Rendimiento de Interpolación")
                    st.dataframe(stats["metrics_df"].set_index('Método'))
        
        # Se eliminó 'use_container_width=True' y el emoji.
        if st.button("Realizar Otro Análisis"):
            reset_analysis()
            st.rerun()

    with col_mapa:
        st.info("✔️ ¡Reporte generado con éxito!")
        st.header("Mapa de Distribución Pluvial")
        st.pyplot(st.session_state.figure)

else:
    # --- VISTA DE CONFIGURACIÓN ---
    with col_info:
        display_sidebar_info()
        
        st.header("1. Selecciona el tipo de reporte")
        report_option = st.radio(
            "Elige las estaciones a incluir:",
            ('Solo Estaciones SAPAL', 'SAPAL + CONAGUA (Recomendado)'),
            index=1,
            key="report_option"
        )
        st.info("Añadir las estaciones de CONAGUA mejora la precisión del mapa.")

        st.header("2. Confirma la fecha del reporte")
        report_date = None

        if report_option == 'Solo Estaciones SAPAL':
            report_date = datetime.now()
            st.info(f"Se usará la fecha de hoy: **{report_date.strftime('%d de %B de %Y')}**")
        else:
            with st.spinner("Buscando la última fecha de CONAGUA..."):
                latest_conagua_date = get_latest_conagua_date(locations_conagua)
            if latest_conagua_date:
                report_date = latest_conagua_date
                st.info(f"Fecha más reciente encontrada: **{report_date.strftime('%d de %B de %Y')}**")
            else:
                report_date = datetime.now()
                st.warning("No se pudo contactar a CONAGUA. Se usará la fecha de hoy.")
                st.info(f"Fecha de corte: **{report_date.strftime('%d de %B de %Y')}**")

        if st.button("Generar Reporte Pluvial", type="primary", use_container_width=True):
            if report_date is None:
                st.error("No se pudo determinar una fecha para el reporte.")
            else:
                # El código de procesamiento se ejecuta aquí dentro
                log_expander = st.expander("Ver progreso de la extracción...", expanded=True)
                log_container = log_expander.empty()
                log_messages = ["Iniciando proceso..."]
                log_container.markdown("\n\n".join(log_messages))
        
                report_date_pd = pd.to_datetime(report_date.date())
                start_of_year = pd.to_datetime(f"{report_date_pd.year}-01-01")
        
                with st.spinner('Extrayendo y procesando datos...'):
                    # ... [EL CÓDIGO DE PROCESAMIENTO LARGO NO CAMBIA] ...
                    total_df = pd.DataFrame()
                    sapal_df = fetch_sapal_data(locations_sapal, report_date_pd, log_messages, log_container)
                    if "CONAGUA" in report_option:
                        conagua_df = fetch_conagua_data(locations_conagua, start_of_year, report_date_pd, log_messages, log_container)
                        total_df = pd.concat([sapal_df, conagua_df], ignore_index=True)
                    else:
                        total_df = sapal_df
        
                    total_df_con_na = total_df.copy()
        
                    if total_df.dropna(subset=['P_mm']).empty:
                        st.error("Error Crítico: No se encontraron datos de precipitación válidos.")
                        st.stop()
        
                    log_messages.append("--- Extracción finalizada. Procesando datos... ---")
                    log_container.markdown("\n\n".join(log_messages))
        
                    updated_stations_gdf = stations_gdf.merge(total_df, on=['Name', 'ENTIDAD'], how='inner')
                    if 'P_mm_y' in updated_stations_gdf.columns:
                        updated_stations_gdf.rename(columns={'P_mm_y': 'P_mm'}, inplace=True)
                    if 'P_mm_x' in updated_stations_gdf.columns:
                        updated_stations_gdf = updated_stations_gdf.drop(columns=['P_mm_x'])
                    
                    stations_filtered_gdf = updated_stations_gdf.dropna(subset=['P_mm']).copy()
                    if not stations_filtered_gdf.empty:
                        stations_filtered_gdf, outliers_df = filter_outliers(stations_filtered_gdf)
                    else:
                        outliers_df = pd.DataFrame()
                    
                    if len(stations_filtered_gdf) < 5:
                        st.warning(f"Se necesitan al menos 5 estaciones válidas para interpolar. Se generará un mapa base.")
                        interpolation_results = None
                        metrics_df = None
                    else:
                        log_messages.append("--- Generando mapa de interpolación... ---")
                        log_container.markdown("\n\n".join(log_messages))
                        interpolation_results, metrics_df = find_best_interpolation_model(stations_filtered_gdf, geodata['boundary'])
                    
                    fig, ax = plt.subplots(figsize=(16, 12), facecolor='white')
                    ax.set_facecolor('white')
                    fig.patch.set_facecolor('white')
                    fig.subplots_adjust(right=0.7)
                    
                    limite_gdf = geodata['boundary'].to_crs(geodata['hillshade'].crs)
                    cuenca_gdf = geodata['cuenca'].to_crs(geodata['hillshade'].crs)
                    
                    lim_bounds = limite_gdf.total_bounds
                    cue_bounds = cuenca_gdf.total_bounds
                    
                    total_minx, total_miny, total_maxx, total_maxy = min(lim_bounds[0], cue_bounds[0]), min(lim_bounds[1], cue_bounds[1]), max(lim_bounds[2], cue_bounds[2]), max(lim_bounds[3], cue_bounds[3])
                    
                    total_width, total_height = total_maxx - total_minx, total_maxy - total_miny
                    x_margin, y_margin = total_width * 0.05, total_height * 0.05
                    
                    ax.set_xlim(total_minx - x_margin, total_maxx + x_margin)
                    ax.set_ylim(total_miny - y_margin, total_maxy + y_margin)
                    
                    boundary_geom = geodata['boundary'].to_crs(geodata['hillshade'].crs).geometry
                    clipped_hillshade, clipped_transform = mask(geodata['hillshade'], boundary_geom, crop=True, nodata=np.nan)
                    hillshade_data = clipped_hillshade[0].astype(float)
                    hillshade_data[hillshade_data == 255] = np.nan
                    
                    ax.imshow(hillshade_data, extent=[clipped_transform[2], clipped_transform[2] + clipped_transform[0] * hillshade_data.shape[1], clipped_transform[5] + clipped_transform[4] * hillshade_data.shape[0], clipped_transform[5]], cmap='gray', alpha=0.7, aspect='equal', zorder=1)
                    
                    if interpolation_results and np.any(interpolation_results["raster_image"]):
                        raster_image = np.ma.masked_invalid(interpolation_results["raster_image"])
                        raster_meta = interpolation_results["raster_meta"]
                        custom_cmap = LinearSegmentedColormap.from_list('custom_precip', ['#f03725', '#F3FD89', '#1FB6EA'])
                        precip_min, precip_max = stations_filtered_gdf['P_mm'].min(), stations_filtered_gdf['P_mm'].max()
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
                    geodata['cuenca'].to_crs(geodata['hillshade'].crs).plot(ax=ax, facecolor='none', edgecolor='#FF0000', linewidth=1.5, clip_on=False, zorder=4)
                    geodata['presa'].to_crs(geodata['hillshade'].crs).plot(ax=ax, facecolor='#00E6A9', edgecolor='#002673', linewidth=1, clip_on=True, zorder=5)
                    
                    if not stations_filtered_gdf.empty:
                        stations_filtered_gdf[stations_filtered_gdf['ENTIDAD'] == 'SAPAL'].to_crs(geodata['hillshade'].crs).plot(ax=ax, marker='s', color='#00C5FF', markersize=30, edgecolor='black', zorder=6)
                        stations_filtered_gdf[stations_filtered_gdf['ENTIDAD'] == 'CONAGUA'].to_crs(geodata['hillshade'].crs).plot(ax=ax, marker='s', color='#55FF00', markersize=30, edgecolor='black', zorder=6)

                    ax.set_title(f"PRECIPITACIÓN ACUMULADA ANUAL\nCORTE AL {report_date_pd.strftime('%d de %B de %Y').upper()}", fontsize=14, fontweight='bold', loc='left')
                    ax.tick_params(axis='both', which='major', labelsize=10, direction='in', color='black', labelcolor='black')
                    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, p: f'{int(x):,}')); ax.yaxis.set_major_formatter(FuncFormatter(lambda x, p: f'{int(x):,}'))
                    ax.set_xlabel(""); ax.set_ylabel("")
                    add_north_arrow(ax)
                    
                    scale_length_m, scale_segments = 5000, 5
                    scale_x, scale_y = (total_minx + total_width * 0.02), (total_miny + total_height * 0.02)
                    segment_length, bar_height = scale_length_m / scale_segments, total_height * 0.007
                    for i in range(scale_segments):
                        color = 'black' if i % 2 == 0 else 'white'
                        ax.add_patch(plt.Rectangle((scale_x + i * segment_length, scale_y), segment_length, bar_height, facecolor=color, edgecolor='black', linewidth=1, zorder=10))
                    text_y_pos = scale_y - total_height * 0.008
                    ax.text(scale_x, text_y_pos, '0', ha='center', va='top', fontsize=8, weight='bold', zorder=10)
                    ax.text(scale_x + scale_length_m / 2, text_y_pos, '2.5', ha='center', va='top', fontsize=8, weight='bold', zorder=10)
                    ax.text(scale_x + scale_length_m, text_y_pos, '5 km', ha='center', va='top', fontsize=8, weight='bold', zorder=10)
                    
                    legend_elements = [Patch(facecolor='none', edgecolor='#38A800', linewidth=2, label='MUNICIPIO DE LEÓN'), Patch(facecolor='none', edgecolor='black', linewidth=1, label='LÍMITE URBANO'), Patch(facecolor='none', edgecolor='#FF0000', linewidth=1.5, label='CUENCA P. PALOTE'), Patch(facecolor='#00E6A9', edgecolor='#002673', label='PRESA EL PALOTE'), Line2D([0], [0], color='#10008C', lw=1, label='CORRIENTES DE AGUA'), Line2D([0], [0], marker='s', color='#55FF00', label='CONAGUA', markerfacecolor='#55FF00', markeredgecolor='black', markersize=8, linestyle='None'), Line2D([0], [0], marker='s', color='#00C5FF', label='SAPAL', markerfacecolor='#00C5FF', markeredgecolor='black', markersize=8, linestyle='None')]
                    legend_ax = ax.legend(handles=legend_elements, bbox_to_anchor=(1.02, 1), loc='upper left', fontsize=10, title='SIMBOLOGÍA', title_fontsize=12, frameon=True, edgecolor='black', facecolor='white')
                    legend_ax.get_title().set_fontweight('bold')

                    if interpolation_results and np.any(interpolation_results["raster_image"]):
                        cbar_ax = fig.add_axes([0.77, 0.15, 0.02, 0.3])
                        norm = Normalize(vmin=precip_min, vmax=precip_max)
                        cb = ColorbarBase(cbar_ax, cmap=custom_cmap, norm=norm, orientation='vertical')
                        cb.ax.set_title('Precipitación\nAcumulada (mm)', size=10, weight='bold', pad=15)
                        cb.ax.tick_params(labelsize=9)
                        for spine in cbar_ax.spines.values(): spine.set_edgecolor('black'); spine.set_linewidth(1)
                    
                    if geodata["logo"] is not None:
                        aspect_ratio = geodata["logo"].shape[0] / geodata["logo"].shape[1]
                        logo_width = total_width * 0.15; logo_height = logo_width * aspect_ratio
                        logo_x, logo_y = (total_maxx - total_width * 0.02 - logo_width), (total_miny + total_height * 0.02)
                        ax.imshow(geodata["logo"], extent=[logo_x, logo_x + logo_width, logo_y, logo_y + logo_height], aspect='auto', zorder=10)
                    
                    ax.grid(True, linestyle=':', alpha=0.6, color='black')
                    
                    png_buffer = io.BytesIO()
                    fig.savefig(png_buffer, format="png", dpi=300, facecolor='white', edgecolor='none', bbox_inches='tight', pad_inches=0.2)
                    png_buffer.seek(0)
                    
                    st.session_state.figure, st.session_state.raster_io, st.session_state.png_buffer, st.session_state.report_date_str, st.session_state.map_generated = fig, raster_io, png_buffer, report_date_pd.strftime('%Y%m%d'), True
                    
                    desc_stats = stations_filtered_gdf['P_mm'].describe().to_frame().T.rename(columns={'count': 'Estaciones', 'mean': 'Promedio', 'std': 'Desv. Est.', 'min': 'Mínimo', 'max': 'Máximo'})
                    report_date_str_formatted = report_date_pd.strftime('%d de %B de %Y').title()
                    stats_md = f"### Resumen del Reporte\n- **Fecha de Corte:** {report_date_str_formatted}\n- **Estaciones Válidas:** {len(stations_filtered_gdf)}\n- **Método Interpolación:** {interpolation_results['best_method'] if interpolation_results else 'N/A'}"
                    st.session_state.stats_panel_md = {"header": stats_md, "total_df_con_na": total_df_con_na.rename(columns={'P_mm': 'Precip. (mm)'}), "outliers_df": outliers_df.rename(columns={'P_mm': 'Precip. (mm)'}), "desc_stats": desc_stats, "metrics_df": metrics_df}
                    
                    st.rerun()

    with col_mapa:
        st.markdown("### El mapa se mostrará aquí una vez que generes el reporte.")
        st.info("Utiliza los controles en el panel de la izquierda para comenzar.")
        # Opcional: Mostrar una imagen de fondo o el logo
        




