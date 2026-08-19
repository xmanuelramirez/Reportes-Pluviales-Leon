#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verificador del repositorio Reportes-Pluviales-Leon.

Su razón de existir es cerrar el agentic loop: un loop sin verificador es un
temporizador; con verificador, itera hasta converger. Devuelve 0 cuando todo
pasa y 1 cuando hay al menos un ERROR, para que un `while` de bash, un cron o
un `claude -p` sepan si deben volver a intentar.

Uso:
    python3 scripts/verificar.py            # reporte legible
    python3 scripts/verificar.py --json     # salida para consumo automático
    python3 scripts/verificar.py --red      # además prueba los endpoints

Diseñado para correr sin las dependencias pesadas instaladas: lo que no puede
comprobar lo marca OMITIDO en lugar de fallar.
"""

import argparse
import ast
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = "RAINFALL_MAPS_STREAMLIT.py"
DIR_SHAPES = "shapefiles"

# Módulo importado -> paquete en requirements.txt
PAQUETE_DE = {
    "sklearn": "scikit-learn",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "yaml": "pyyaml",
}

ESTANDAR = set(sys.stdlib_module_names)

ENDPOINTS = {
    "SAPAL lista de estaciones": "https://services.sapal.gob.mx/portal/v1/climate/getMapStationList",
    "CONAGUA base de datos": "https://sih.conagua.gob.mx/basedatos/climas/",
}


class Reporte:
    def __init__(self):
        self.items = []

    def anota(self, nivel, control, mensaje):
        self.items.append({"nivel": nivel, "control": control, "mensaje": mensaje})

    def ok(self, control, mensaje):
        self.anota("OK", control, mensaje)

    def aviso(self, control, mensaje):
        self.anota("AVISO", control, mensaje)

    def error(self, control, mensaje):
        self.anota("ERROR", control, mensaje)

    def omitido(self, control, mensaje):
        self.anota("OMITIDO", control, mensaje)

    def cuenta(self, nivel):
        return sum(1 for i in self.items if i["nivel"] == nivel)

    @property
    def hay_errores(self):
        return self.cuenta("ERROR") > 0


def _arbol(rep):
    """Control 1: la aplicación compila. Si esto falla, nada más importa."""
    ruta = os.path.join(RAIZ, APP)
    if not os.path.exists(ruta):
        rep.error("sintaxis", f"No existe {APP} en la raíz del repositorio.")
        return None
    with open(ruta, encoding="utf-8") as fh:
        fuente = fh.read()
    try:
        arbol = ast.parse(fuente, filename=APP)
    except SyntaxError as exc:
        rep.error("sintaxis", f"{APP}:{exc.lineno} — {exc.msg}")
        return None
    rep.ok("sintaxis", f"{APP} compila ({len(fuente.splitlines())} líneas).")
    return arbol


def _importaciones(arbol):
    modulos = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                modulos.add(alias.name.split(".")[0])
        elif isinstance(nodo, ast.ImportFrom):
            if nodo.level == 0 and nodo.module:
                modulos.add(nodo.module.split(".")[0])
    return {m for m in modulos if m not in ESTANDAR}


def control_dependencias(rep, arbol):
    """Control 2: todo lo que se importa está declarado en requirements.txt."""
    ruta = os.path.join(RAIZ, "requirements.txt")
    if not os.path.exists(ruta):
        rep.error("dependencias", "Falta requirements.txt.")
        return
    declarados = set()
    with open(ruta, encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            declarados.add(re.split(r"[=<>!~\[ ]", linea)[0].lower())

    faltantes = sorted(
        m for m in _importaciones(arbol)
        if PAQUETE_DE.get(m, m).lower() not in declarados
    )
    if faltantes:
        rep.aviso(
            "dependencias",
            "Se importan sin declarar en requirements.txt: "
            + ", ".join(faltantes)
            + ". Hoy llegan como dependencia indirecta; si el paquete que los "
              "arrastra cambia, el despliegue se rompe sin aviso.",
        )
    else:
        rep.ok("dependencias", "Todo import de terceros está declarado.")

    sin_fijar = []
    with open(ruta, encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if linea and not linea.startswith("#") and "==" not in linea:
                sin_fijar.append(linea)
    if sin_fijar:
        rep.aviso(
            "dependencias",
            "Sin versión fija (el despliegue deja de ser reproducible): "
            + ", ".join(sin_fijar),
        )
    else:
        rep.ok("dependencias", "Todas las versiones están fijas con ==.")


def control_shapefiles(rep):
    """Control 3: cada .shp trae sus archivos hermanos obligatorios."""
    base = os.path.join(RAIZ, DIR_SHAPES)
    if not os.path.isdir(base):
        rep.error("shapefiles", f"No existe la carpeta {DIR_SHAPES}/.")
        return
    existentes = {n.lower(): n for n in os.listdir(base)}
    shps = sorted(n for n in os.listdir(base) if n.lower().endswith(".shp"))
    if not shps:
        rep.error("shapefiles", f"No hay ningún .shp en {DIR_SHAPES}/.")
        return
    incompletos = []
    for shp in shps:
        tronco = shp[:-4]
        faltan = [
            ext for ext in (".dbf", ".shx", ".prj")
            if f"{tronco}{ext}".lower() not in existentes
        ]
        if faltan:
            incompletos.append(f"{shp} (falta {', '.join(faltan)})")
    if incompletos:
        rep.error("shapefiles", "Shapefiles incompletos: " + "; ".join(incompletos))
    else:
        rep.ok("shapefiles", f"Los {len(shps)} shapefiles traen .dbf, .shx y .prj.")


def control_recursos(rep, arbol):
    """Control 4: todo archivo geoespacial citado en el código existe en disco."""
    extensiones = (".shp", ".tif", ".tiff", ".png", ".kmz", ".xlsx")
    citados = set()

    def recorre(nodo):
        # Un nombre armado en tiempo de ejecución (f-string) no se puede
        # comprobar en disco: se ignora el subárbol completo.
        if isinstance(nodo, ast.JoinedStr):
            return
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            valor = nodo.value
            raiz, _, ext = valor.rpartition(".")
            if raiz and f".{ext.lower()}" in extensiones and "/" not in valor:
                citados.add(valor)
        for hijo in ast.iter_child_nodes(nodo):
            recorre(hijo)

    recorre(arbol)
    if not citados:
        rep.omitido("recursos", "No se detectaron nombres de archivo literales.")
        return
    base = os.path.join(RAIZ, DIR_SHAPES)
    faltantes = sorted(
        n for n in citados
        if not os.path.exists(os.path.join(base, n))
        and not os.path.exists(os.path.join(RAIZ, n))
    )
    if faltantes:
        rep.error(
            "recursos",
            "El código carga archivos que no están en el repositorio: "
            + ", ".join(faltantes),
        )
    else:
        rep.ok("recursos", f"Los {len(citados)} archivos citados en el código existen.")


def control_endpoints(rep, probar_red):
    """Control 5: las fuentes de datos responden. Solo con --red."""
    if not probar_red:
        rep.omitido("endpoints", "No se probó la red (usa --red para incluirlo).")
        return
    try:
        import requests
        import urllib3

        urllib3.disable_warnings()
    except ImportError:
        rep.omitido("endpoints", "requests no está instalado en este entorno.")
        return
    for nombre, url in ENDPOINTS.items():
        try:
            resp = requests.get(url, timeout=20, verify=False)
            if resp.status_code < 400:
                rep.ok("endpoints", f"{nombre} responde HTTP {resp.status_code}.")
            else:
                rep.error("endpoints", f"{nombre} responde HTTP {resp.status_code}.")
        except Exception as exc:
            detalle = str(exc).split("\n")[0][:90]
            rep.error(
                "endpoints",
                f"{nombre} no responde: {type(exc).__name__} — {detalle}. "
                "Si corres detrás de un proxy restringido, esto es del entorno, "
                "no de la fuente.",
            )


def main():
    ap = argparse.ArgumentParser(description="Verificador del repositorio.")
    ap.add_argument("--json", action="store_true", help="Salida JSON.")
    ap.add_argument("--red", action="store_true", help="Probar los endpoints.")
    args = ap.parse_args()

    rep = Reporte()
    arbol = _arbol(rep)
    if arbol is not None:
        control_dependencias(rep, arbol)
        control_recursos(rep, arbol)
    control_shapefiles(rep)
    control_endpoints(rep, args.red)

    if args.json:
        print(json.dumps(
            {
                "aprobado": not rep.hay_errores,
                "errores": rep.cuenta("ERROR"),
                "avisos": rep.cuenta("AVISO"),
                "items": rep.items,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        marca = {"OK": "  ok  ", "AVISO": " aviso", "ERROR": " ERROR", "OMITIDO": "  --  "}
        for item in rep.items:
            print(f"[{marca[item['nivel']]}] {item['control']:14s} {item['mensaje']}")
        print()
        if rep.hay_errores:
            print(f"NO APROBADO — {rep.cuenta('ERROR')} error(es), {rep.cuenta('AVISO')} aviso(s).")
        else:
            print(f"APROBADO — 0 errores, {rep.cuenta('AVISO')} aviso(s).")

    return 1 if rep.hay_errores else 0


if __name__ == "__main__":
    sys.exit(main())
