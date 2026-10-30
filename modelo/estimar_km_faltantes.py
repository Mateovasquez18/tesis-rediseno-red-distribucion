"""Estima los km por carretera de las rutas CEDI -> municipio que no existen en KM_Zonas.

Método:
- Ruta normal: km(j, k) = min sobre a [ km(j, a) + FACTOR_CARRETERA * linea_recta(a, k) ], donde a recorre
  los destinos con km conocido desde j en KM_Zonas (incluido el propio j). Es decir, se llega por carretera
  conocida hasta el punto más conveniente y el último tramo se aproxima con la distancia en línea recta
  multiplicada por el factor de tortuosidad mediano de KM_Zonas.
- Destinos sin conexión vial (Leticia, Inírida, Mitú): en KM_Zonas solo existen desde Bogotá, así que
  km(j, k) = km(j, Bogotá) + km(Bogotá, k).

Escribe el resultado en rutas_faltantes_fII.xlsx (columna "Km por carretera").
"""
import json
import math
import statistics
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill

from excel_a_json import (ARCHIVO_DEMANDA, BASE, COL_RUTA_DESTINO, COL_RUTA_ORIGEN, DATA_DIR, HOJA_RUTAS,
                          _codigos_por_nombre, _normalizar, _valor)

ARCHIVO_COORDENADAS = BASE / "datos" / "Datos_Rediseno_Electrodomesticos.xlsx"
HOJA_COORDENADAS = "Municipios_Demanda"   # columnas: A codigo_dane, G lat, H lon
COL_KM = "E"                              # Km por carretera en KM_Zonas
SIN_VIA = {"91001", "94001", "97001"}     # Leticia, Inírida, Mitú
HUB_SIN_VIA = "11001"                     # Bogotá
SALIDA = Path(__file__).resolve().parent / "rutas_faltantes_fII.xlsx"


def linea_recta(a, b):
    """Distancia haversine en km entre dos (lat, lon)."""
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def main():
    demanda = json.loads((DATA_DIR / "demanda.json").read_text(encoding="utf-8"))
    fase2 = json.loads((DATA_DIR / "costo_fase2.json").read_text(encoding="utf-8"))
    zonas, candidatos = demanda["zonas"], fase2["candidatos"]
    codigo = _codigos_por_nombre(zonas)

    ws = openpyxl.load_workbook(ARCHIVO_COORDENADAS, read_only=True, data_only=True)[HOJA_COORDENADAS]
    coord = {str(f[0]): (f[6], f[7]) for f in ws.iter_rows(min_row=2, values_only=True) if f[0]}
    sin_coord = [k for k in zonas if k not in coord]
    if sin_coord:
        raise ValueError(f"Zonas sin coordenadas: {sin_coord}")

    # km conocidos entre municipios de D (origen y destino traducidos a código DANE)
    rutas = openpyxl.load_workbook(ARCHIVO_DEMANDA, read_only=True, data_only=True)[HOJA_RUTAS]
    conocido = {}
    for f in rutas.iter_rows(min_row=2, values_only=True):
        o, d = codigo.get(_normalizar(_valor(f, COL_RUTA_ORIGEN))), codigo.get(_normalizar(_valor(f, COL_RUTA_DESTINO)))
        if o and d and isinstance(_valor(f, COL_KM), (int, float)):
            conocido.setdefault(o, {})[d] = float(_valor(f, COL_KM))

    factores = [km / linea_recta(coord[o], coord[d]) for o, ds in conocido.items() for d, km in ds.items()
                if o != d and d not in SIN_VIA and linea_recta(coord[o], coord[d]) > 50]
    factor = statistics.median(factores)

    def estimar(j, k):
        if k in SIN_VIA:
            return conocido[j][HUB_SIN_VIA] + conocido[HUB_SIN_VIA][k], \
                f"Sin vía: km({candidatos[j]['ciudad']}→Bogotá) + km(Bogotá→{zonas[k]['municipio']})"
        a = min(conocido[j], key=lambda a: conocido[j][a] + factor * linea_recta(coord[a], coord[k]))
        km = conocido[j][a] + factor * linea_recta(coord[a], coord[k])
        return km, f"Vía {zonas[a]['municipio']}: km conocido + {factor:.2f} × línea recta"

    wb = openpyxl.Workbook()
    hoja = wb.active
    hoja.title = "Rutas_Faltantes"
    hoja.append(["Departamento origen", "Capital origen", "Codigo DANE origen", "Departamento destino",
                 "Ciudad Destino", "Codigo DANE destino", "Km por carretera (estimado)",
                 "Costo Transporte Variable (col H)", "Método de estimación"])
    for c in hoja[1]:
        c.font = Font(bold=True)
    amarillo = PatternFill("solid", fgColor="FFF2CC")
    for j, ks in fase2["rutas_faltantes"].items():
        for k in ks:
            km, metodo = estimar(j, k)
            hoja.append([candidatos[j]["departamento"], candidatos[j]["ciudad"], int(j), zonas[k]["departamento"],
                         zonas[k]["municipio"], int(k), round(km), None, metodo])
            hoja.cell(hoja.max_row, 8).fill = amarillo
    for col, ancho in zip("ABCDEFGHI", [20, 16, 18, 22, 26, 20, 26, 30, 60]):
        hoja.column_dimensions[col].width = ancho
    hoja.freeze_panes = "A2"
    wb.save(SALIDA)
    print(f"{SALIDA.name}: {hoja.max_row - 1} rutas estimadas; factor de carretera = {factor:.3f} "
          f"(mediana de {len(factores)} rutas de {HOJA_RUTAS})")


if __name__ == "__main__":
    main()
