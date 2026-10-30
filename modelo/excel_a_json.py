"""Lectura de parámetros del modelo de rediseño de red desde Excel y exportación a JSON.

Parámetro d[i,k]: demanda en posiciones de estiba de la categoría i ∈ C en la zona de demanda k ∈ D.
Parámetro q[v]: capacidad, en posiciones de estiba (carga no apilable), de un vehículo tipo v ∈ V.
Parámetro cI[v,j]: costo de un viaje del vehículo v ∈ V desde la planta hasta el punto candidato j ∈ W \ {j0}.
Parámetro phi: costo por posición de estiba desde la planta hasta el CEDI acoplado a ella (j0), que se
               abastece en camión sencillo (V002).
Parámetro cII[v,j,k]: costo de un viaje del vehículo v ∈ V desde el punto candidato j ∈ W hasta la zona k ∈ D.
Parámetro g[j,s]: costo anual de operación del CEDI candidato j ∈ W con tamaño s ∈ S.
Parámetro h[j,s]: costo unitario de manejo por posición de estiba en el CEDI j ∈ W con tamaño s ∈ S.
Parámetro a[s]: capacidad máxima anual, en posiciones de estiba, de un CEDI de tamaño s ∈ S.
"""
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter

BASE = Path(__file__).resolve().parent.parent
DATOS_DIR = BASE / "datos"     # archivos Excel de entrada
DATA_DIR = Path(__file__).resolve().parent / "data"

ARCHIVO_DEMANDA = DATOS_DIR / "Datos_Demanda_Normalizados.xlsx"
HOJA_DEMANDA = "Demanda Historica"
COL_ZONA = "G"        # Código DANE del municipio = zona de demanda (k ∈ D)
COL_MUNICIPIO = "F"   # Nombre del municipio (descriptivo)
COL_DEPARTAMENTO = "E"  # Departamento (descriptivo)
COL_CATEGORIA = "AE"  # Código homologado de la categoría (i ∈ C)
COL_NOMBRE_CATEGORIA = "J"  # Nombre descriptivo de la categoría
COL_PALLETS = "Y"     # Demanda en posiciones de estiba
COL_CEDI_ACTUAL = "AA"  # Ciudad del CEDI que atiende hoy la zona (red actual, para la línea de base)

HOJA_RUTAS = "KM_Zonas"
PLANTA = "Cali"            # Ciudad de la planta (origen de la fase 1)
# Puntos candidatos j ∈ W: los CEDI con costo anual en Costeo_anual_CEDIS.xlsx (nombres como en KM_Zonas).
# En los JSON se identifican con el código DANE de su municipio, igual que las zonas de D.
CANDIDATOS = ["Barranquilla", "Bogotá", "Cali", "Medellín", "Pereira"]
COL_RUTA_ORIGEN = "B"      # Capital origen
COL_RUTA_DESTINO = "D"     # Ciudad destino
COL_RUTA_KM = "E"          # Km por carretera
# El costo por viaje de cada vehículo está en la columna de KM_Zonas cuyo encabezado es el nombre del vehículo
# (= Fijo cargue/descargue + km × costo medio por km del vehículo en Ref_Veh)

HOJA_VEHICULOS = "Ref_Veh"
COL_VEH_CODIGO = "A"       # Código del vehículo (v ∈ V)
COL_VEH_NOMBRE = "B"       # Tipo de vehículo (= encabezado de su columna de costo en KM_Zonas)
COL_VEH_COSTO_KM = "G"     # Costo medio por km
COL_VEH_CAPACIDAD = "I"    # Posiciones de estiba por vehículo (carga no apilable)
# El CEDI acoplado a la planta (j0) se abastece en camión sencillo; el costo por posición de estiba de ese
# traslado es el costo medio por km del V002 en Ref_Veh
VEHICULO_CEDI_PLANTA = "V002"

ARCHIVO_COSTEO = DATOS_DIR / "Costeo_anual_CEDIS.xlsx"
HOJA_COSTEO = "Matriz CEDI x Tamaño"
FILA_TITULOS = 4           # Títulos de los bloques de costo
FILA_TAMANOS = 5           # Encabezados XS..XL
FILA_PRIMER_CEDI = 6
COL_CEDI = "A"             # Nombre del CEDI (contiene la ciudad candidata)
# Los bloques se ubican por su título en FILA_TITULOS; cada uno abarca las columnas con tamaño en FILA_TAMANOS
TITULO_COSTO_ANUAL = "COSTO TOTAL ANUAL"          # g: COP/año para cada tamaño s ∈ S
TITULO_COSTO_TONELADA = "COSTO POR TONELADA"      # h: COP/t para cada tamaño s ∈ S
# CEDI con apertura y tamaño fijos (no son decisión del modelo): ciudad -> tamaño s ∈ S.
# Cali está acoplado a la planta: siempre abierto y con capacidad XL; sus costos salen de la matriz como los demás.
CEDI_FIJOS = {"Cali": "XL"}

HOJA_TAMANOS = "Desción Tamaños CEDIS"   # en ARCHIVO_DEMANDA
ENCABEZADO_TAMANOS = "Tamaño"            # Tabla: Tamaño | Mínimo | Máximo (posiciones de estiba por día)
DIAS_OPERACION_MES = 25
MESES_ANO = 12
PERIODOS_POR_ANO = DIAS_OPERACION_MES * MESES_ANO   # a[s] = máximo diario × 25 días × 12 meses

# Toneladas por posición de estiba para convertir h de COP/t a COP/posición. Se fija con la demanda 2025 cuyos
# pesos y estibas eran consistentes (2.000,1 t / 18.191 posiciones); las columnas de peso de Demanda Historica no
# se actualizaron con las nuevas cantidades, así que no se recalcula desde ellas.
TONELADAS_POR_POSICION = 2000.1 / 18191


def _valor(fila, letra):
    """Valor de la columna `letra` en la fila; None si la fila termina antes (celdas vacías al final)."""
    i = column_index_from_string(letra) - 1
    return fila[i] if i < len(fila) else None


def _normalizar(texto):
    """Nombre sin tildes, en minúsculas y sin espacios extremos, para cruzar nombres entre hojas."""
    return unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode().lower().strip()


def _registrar(catalogo, clave, descripcion, que):
    """Guarda la descripción de una clave y verifica que sea única."""
    if catalogo.setdefault(clave, descripcion) != descripcion:
        raise ValueError(f"{que} {clave} tiene dos descripciones: {catalogo[clave]!r} y {descripcion!r}")


def leer_demanda(archivo=ARCHIVO_DEMANDA, hoja=HOJA_DEMANDA,
                 col_zona=COL_ZONA, col_categoria=COL_CATEGORIA, col_pallets=COL_PALLETS,
                 col_nombre_categoria=COL_NOMBRE_CATEGORIA, col_municipio=COL_MUNICIPIO,
                 col_departamento=COL_DEPARTAMENTO, col_cedi_actual=COL_CEDI_ACTUAL):
    """Agrega la demanda por (categoría, zona).

    Devuelve (C, D, d, nombres, zonas) con d[i][k], nombres[i] y zonas[k] = {municipio, departamento}.
    """
    ws = openpyxl.load_workbook(archivo, read_only=True, data_only=True)[hoja]
    d = defaultdict(lambda: defaultdict(float))
    nombres, zonas = {}, {}
    for fila in ws.iter_rows(min_row=2, values_only=True):
        zona, categoria, pallets = (_valor(fila, col_zona), _valor(fila, col_categoria),
                                    _valor(fila, col_pallets))
        if zona is None or categoria is None or pallets is None:
            continue
        i = str(categoria).strip()
        k = str(int(zona))
        _registrar(nombres, i, str(_valor(fila, col_nombre_categoria)).strip(), "La categoría")
        _registrar(zonas, k, {"municipio": str(_valor(fila, col_municipio)).strip(),
                              "departamento": str(_valor(fila, col_departamento)).strip(),
                              "cedi_actual": str(_valor(fila, col_cedi_actual)).strip()}, "La zona")
        d[i][k] += float(pallets)

    C = sorted(d)
    D = sorted({k for por_zona in d.values() for k in por_zona}, key=int)
    d = {i: {k: d[i][k] for k in sorted(d[i], key=int)} for i in C}
    return C, D, d, {i: nombres[i] for i in C}, {k: zonas[k] for k in D}


def _codigos_por_nombre(zonas):
    """Código DANE de cada zona indexado por el nombre normalizado de su municipio."""
    codigos = {}
    for k, z in zonas.items():
        _registrar(codigos, _normalizar(z["municipio"]), k, "El municipio")
    return codigos


def codificar_candidatos(zonas, nombres=CANDIDATOS):
    """Asigna a cada ciudad candidata el código DANE de su municipio. Devuelve {código: ciudad} ordenado."""
    codigos = _codigos_por_nombre(zonas)
    sin_codigo = [j for j in nombres if _normalizar(j) not in codigos]
    if sin_codigo:
        raise ValueError(f"Candidatos sin código DANE en las zonas de demanda: {sin_codigo}")
    return {codigos[_normalizar(j)]: j for j in sorted(nombres, key=lambda j: int(codigos[_normalizar(j)]))}


def leer_vehiculos(archivo=ARCHIVO_DEMANDA, hoja=HOJA_VEHICULOS):
    """Tipos de vehículo. Devuelve {código: {nombre, capacidad, costo_km}} en el orden de la hoja."""
    ws = openpyxl.load_workbook(archivo, read_only=True, data_only=True)[hoja]
    vehiculos = {}
    for fila in ws.iter_rows(min_row=2, values_only=True):
        codigo = _valor(fila, COL_VEH_CODIGO)
        if codigo is None:
            continue
        nombre, costo_km, capacidad = (_valor(fila, COL_VEH_NOMBRE), _valor(fila, COL_VEH_COSTO_KM),
                                       _valor(fila, COL_VEH_CAPACIDAD))
        if not all(isinstance(v, (int, float)) and v > 0 for v in (costo_km, capacidad)):
            raise ValueError(f"El vehículo {codigo} tiene costo/km o capacidad inválidos: {costo_km}, {capacidad}")
        _registrar(vehiculos, str(codigo).strip(), {"nombre": str(nombre).strip(), "capacidad": float(capacidad),
                                                    "costo_km": float(costo_km)}, "El vehículo")
    return vehiculos


def leer_costos_viaje(zonas, candidatos, vehiculos, archivo=ARCHIVO_DEMANDA, hoja=HOJA_RUTAS, planta=PLANTA):
    """Costos por viaje de cada vehículo en las dos fases, leídos de la hoja de rutas.

    Fase 1: planta -> punto candidato j (j identificado por código DANE); fase 2: candidato j -> zona k.
    El destino de la hoja se traduce a código DANE comparando el nombre normalizado con el municipio de la zona.
    Devuelve (cI[v][j], cII[v][j][k], km_I[j], km_II[j][k], faltantes[j], columnas[v]).
    """
    ws = openpyxl.load_workbook(archivo, read_only=True, data_only=True)[hoja]
    filas = ws.iter_rows(values_only=True)
    encabezado = next(filas)
    columnas = {}
    for v, info in vehiculos.items():
        cols = [get_column_letter(n + 1) for n, t in enumerate(encabezado)
                if t is not None and _normalizar(t) == _normalizar(info["nombre"])]
        if len(cols) != 1:
            raise ValueError(f"Se esperaba una columna {info['nombre']!r} (vehículo {v}) en {hoja}; hay {len(cols)}")
        columnas[v] = cols[0]

    codigo_por_nombre = _codigos_por_nombre(zonas)
    codigo_candidato = {ciudad: j for j, ciudad in candidatos.items()}
    origenes = set()
    viaje_I, km_I = {}, {}
    viaje_II, km_II = {j: {} for j in candidatos}, {j: {} for j in candidatos}
    for fila in filas:
        origen, destino = _valor(fila, COL_RUTA_ORIGEN), _valor(fila, COL_RUTA_DESTINO)
        if origen is None or destino is None:
            continue
        origen = str(origen).strip()
        origenes.add(origen)
        costos = {v: _valor(fila, c) for v, c in columnas.items()}
        if not all(isinstance(c, (int, float)) for c in costos.values()):
            raise ValueError(f"Ruta {origen} -> {destino} con costos por vehículo vacíos: {costos}")
        costos = {v: float(c) for v, c in costos.items()}
        km = float(_valor(fila, COL_RUTA_KM))
        if origen == planta and str(destino).strip() in codigo_candidato:
            j = codigo_candidato[str(destino).strip()]
            _registrar(viaje_I, j, costos, "La ruta planta ->")
            km_I[j] = km
        j, k = codigo_candidato.get(origen), codigo_por_nombre.get(_normalizar(destino))
        if j is not None and k is not None:
            _registrar(viaje_II[j], k, costos, f"La ruta {origen} ->")
            km_II[j][k] = km

    sin_origen = [c for c in candidatos.values() if c not in origenes]
    if sin_origen:
        raise ValueError(f"Candidatos que no aparecen como origen en {hoja}: {sin_origen}")
    sin_ruta_I = [candidatos[j] for j in candidatos if j not in viaje_I]
    if sin_ruta_I:
        raise ValueError(f"Sin ruta desde {planta} hacia los candidatos: {sin_ruta_I}")

    orden = {k: n for n, k in enumerate(zonas)}
    cI = {v: {j: viaje_I[j][v] for j in candidatos} for v in vehiculos}
    cII = {v: {j: {k: viaje_II[j][k][v] for k in sorted(viaje_II[j], key=orden.get)} for j in candidatos}
           for v in vehiculos}
    km_II = {j: {k: km_II[j][k] for k in sorted(km_II[j], key=orden.get)} for j in candidatos}
    faltantes = {j: [k for k in zonas if k not in viaje_II[j]] for j in candidatos}
    return cI, cII, km_I, km_II, faltantes, columnas


def _bloque(titulos, tamanos, titulo):
    """Letras de las columnas del bloque cuyo título contiene `titulo`.

    El bloque va desde la columna del título mientras haya tamaño y hasta donde empiece el título siguiente.
    """
    inicio = [n for n, v in enumerate(titulos) if v is not None and _normalizar(titulo) in _normalizar(v)]
    if len(inicio) != 1:
        raise ValueError(f"Se esperaba exactamente un bloque {titulo!r} en la fila de títulos; hay {len(inicio)}")
    fin = inicio[0] + 1
    while fin < len(tamanos) and tamanos[fin] is not None and _valor(titulos, get_column_letter(fin + 1)) is None:
        fin += 1
    return [get_column_letter(n + 1) for n in range(inicio[0], fin)]


def leer_costos_cedi(candidatos, archivo=ARCHIVO_COSTEO, hoja=HOJA_COSTEO,
                     fila_titulos=FILA_TITULOS, fila_tamanos=FILA_TAMANOS, fila_inicio=FILA_PRIMER_CEDI,
                     col_cedi=COL_CEDI, titulo_fijo=TITULO_COSTO_ANUAL, titulo_variable=TITULO_COSTO_TONELADA):
    """Costos de cada CEDI candidato por tamaño. Devuelve (S, g, h_t, nombres_cedi, columnas).

    g[j][s]: costo anual (COP); h_t[j][s]: costo variable por tonelada recibida (COP/t).
    Cada fila se asigna al candidato cuyo nombre de ciudad aparece en el nombre del CEDI (columna A).
    columnas = {"costo_anual": [...], "costo_por_tonelada": [...]} con las letras usadas.
    """
    ws = openpyxl.load_workbook(archivo, read_only=True, data_only=True)[hoja]
    filas = list(ws.iter_rows(min_row=fila_titulos, values_only=True))
    titulos, filas = filas[0], filas[fila_tamanos - fila_titulos:]
    cols_fijo = _bloque(titulos, filas[0], titulo_fijo)
    cols_variable = _bloque(titulos, filas[0], titulo_variable)
    S = [str(_valor(filas[0], c)).strip() for c in cols_fijo]
    S_variable = [str(_valor(filas[0], c)).strip() for c in cols_variable]
    if S_variable != S:
        raise ValueError(f"Los tamaños de costo fijo {S} y variable {S_variable} no coinciden")

    def numeros(fila, cols, cedi):
        valores = [_valor(fila, c) for c in cols]
        if not all(isinstance(v, (int, float)) for v in valores):
            raise ValueError(f"El CEDI {cedi!r} tiene costos vacíos o no numéricos en {cols}: {valores}")
        return dict(zip(S, map(float, valores)))

    g, h_t, nombres_cedi, omitidos = {}, {}, {}, []
    for fila in filas[fila_inicio - fila_tamanos:]:
        cedi = _valor(fila, col_cedi)
        if cedi is None:  # la tabla de CEDI termina en la primera fila vacía
            break
        coincidencias = [j for j, ciudad in candidatos.items() if _normalizar(ciudad) in _normalizar(cedi)]
        if not coincidencias:  # CEDI de la hoja que no está en CANDIDATOS: no entra al modelo
            omitidos.append(str(cedi).strip())
            continue
        if len(coincidencias) != 1:
            raise ValueError(f"El CEDI {cedi!r} corresponde a varios candidatos: {coincidencias}")
        j = coincidencias[0]
        _registrar(nombres_cedi, j, str(cedi).strip(), "El candidato")
        h_t[j] = numeros(fila, cols_variable, cedi)
        g[j] = numeros(fila, cols_fijo, cedi)

    faltantes = [ciudad for j, ciudad in candidatos.items() if j not in g]
    if faltantes:
        raise ValueError(f"Candidatos sin fila de costos en {hoja}: {faltantes}")
    return S, {j: g[j] for j in candidatos}, {j: h_t[j] for j in candidatos}, \
        {j: nombres_cedi[j] for j in candidatos}, {"costo_anual": cols_fijo, "costo_por_tonelada": cols_variable}, \
        omitidos


def leer_capacidad(S, archivo=ARCHIVO_DEMANDA, hoja=HOJA_TAMANOS, encabezado=ENCABEZADO_TAMANOS,
                   periodos=PERIODOS_POR_ANO):
    """Capacidad por tamaño a partir de la tabla Tamaño | Mínimo | Máximo (posiciones de estiba por día).

    Devuelve (a, a_min, por_dia, celda) con a[s] = máximo diario × días de operación al año,
    a_min[s] = mínimo diario × días y por_dia[s] = {minimo, maximo}.
    """
    ws = openpyxl.load_workbook(archivo, read_only=True, data_only=True)[hoja]
    filas = [list(f) for f in ws.iter_rows(values_only=True)]
    ubicaciones = [(r, c) for r, f in enumerate(filas) for c, v in enumerate(f)
                   if isinstance(v, str) and v.strip() == encabezado]
    if len(ubicaciones) != 1:
        raise ValueError(f"Se esperaba exactamente una celda {encabezado!r} en {hoja}; hay {len(ubicaciones)}")
    r0, c0 = ubicaciones[0]

    por_dia = {}
    for fila in filas[r0 + 1:]:
        tamano = fila[c0] if c0 < len(fila) else None
        if tamano is None:  # la tabla termina en la primera fila vacía
            break
        minimo, maximo = fila[c0 + 1], fila[c0 + 2]
        if not all(isinstance(v, (int, float)) for v in (minimo, maximo)):
            raise ValueError(f"El tamaño {tamano!r} tiene capacidad vacía o no numérica: {minimo}, {maximo}")
        _registrar(por_dia, str(tamano).strip(), {"minimo": float(minimo), "maximo": float(maximo)}, "El tamaño")

    if list(por_dia) != S:
        raise ValueError(f"Los tamaños de {hoja} {list(por_dia)} no coinciden con los de costos {S}")
    a = {s: v["maximo"] * periodos for s, v in por_dia.items()}
    a_min = {s: v["minimo"] * periodos for s, v in por_dia.items()}
    return a, a_min, por_dia, f"{get_column_letter(c0 + 1)}{r0 + 1}"


def _escribir(nombre, salida):
    DATA_DIR.mkdir(exist_ok=True)
    ruta = DATA_DIR / nombre
    ruta.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta.relative_to(BASE)


def main():
    C, D, d, nombres, zonas = leer_demanda()
    total = sum(v for por_zona in d.values() for v in por_zona.values())
    salida = {
        "descripcion": "d[i][k]: demanda anual en posiciones de estiba de la categoria i en la zona k (codigo DANE) "
                       "(combinaciones sin demanda se omiten; tratarlas como 0)",
        "fuente": {"archivo": ARCHIVO_DEMANDA.name, "hoja": HOJA_DEMANDA,
                   "columnas": {"zona": COL_ZONA, "municipio": COL_MUNICIPIO, "departamento": COL_DEPARTAMENTO,
                                "categoria": COL_CATEGORIA, "nombre_categoria": COL_NOMBRE_CATEGORIA,
                                "pallets": COL_PALLETS, "cedi_actual": COL_CEDI_ACTUAL}},
        "conjuntos": {"C": C, "D": D},
        "nombres_categorias": nombres,
        "zonas": zonas,
        "total_posiciones_estiba": total,
        "d": d,
    }
    ruta = _escribir("demanda.json", salida)
    print(f"{ruta}: |C|={len(C)}, |D|={len(D)}, "
          f"pares con demanda={sum(len(v) for v in d.values())}, total={total:,.0f} posiciones")

    candidatos = codificar_candidatos(zonas)
    W = list(candidatos)
    puntos = {j: {"ciudad": ciudad, "departamento": zonas[j]["departamento"]} for j, ciudad in candidatos.items()}
    fijos = {j: CEDI_FIJOS[ciudad] for j, ciudad in candidatos.items() if ciudad in CEDI_FIJOS}
    desconocidos = set(CEDI_FIJOS) - set(candidatos.values())
    if desconocidos:
        raise ValueError(f"CEDI_FIJOS contiene ciudades que no son candidatas: {sorted(desconocidos)}")
    for j, s in fijos.items():
        puntos[j]["tamano_fijo"] = s
    vehiculos = leer_vehiculos()
    V = list(vehiculos)
    if VEHICULO_CEDI_PLANTA not in vehiculos:
        raise ValueError(f"El vehículo del CEDI de la planta {VEHICULO_CEDI_PLANTA} no está en {HOJA_VEHICULOS}")
    salida = {
        "descripcion": "Tipos de vehiculo v: capacidad q[v] en posiciones de estiba (carga no apilable) y costo "
                       "medio por km (COP/km)",
        "fuente": {"archivo": ARCHIVO_DEMANDA.name, "hoja": HOJA_VEHICULOS,
                   "columnas": {"codigo": COL_VEH_CODIGO, "nombre": COL_VEH_NOMBRE, "costo_km": COL_VEH_COSTO_KM,
                                "capacidad": COL_VEH_CAPACIDAD}},
        "conjuntos": {"V": V},
        "vehiculos": vehiculos,
        "q": {v: info["capacidad"] for v, info in vehiculos.items()},
    }
    ruta = _escribir("vehiculos.json", salida)
    print(f"{ruta}: " + ", ".join(f"{v} {i['nombre']} ({i['capacidad']:g} pos, {i['costo_km']:,.0f} COP/km)"
                                  for v, i in vehiculos.items()))

    cI, cII, km_I, km_II, faltantes, cols_veh = leer_costos_viaje(zonas, candidatos, vehiculos)
    cedi_planta = [j for j, ciudad in candidatos.items() if ciudad == PLANTA]
    if len(cedi_planta) != 1:
        raise ValueError(f"Se esperaba exactamente un candidato en la ciudad de la planta ({PLANTA})")
    j0 = cedi_planta[0]
    phi = vehiculos[VEHICULO_CEDI_PLANTA]["costo_km"]
    W_I = [j for j in W if j != j0]
    salida = {
        "descripcion": "cI[v][j]: costo (COP) de un viaje del vehiculo v desde la planta hasta el punto candidato j "
                       "(codigo DANE), para j distinto del CEDI acoplado a la planta. El CEDI acoplado (j0) se "
                       "abastece en camion sencillo con costo 'phi' por posicion de estiba",
        "fuente": {"archivo": ARCHIVO_DEMANDA.name, "hoja": HOJA_RUTAS, "planta": PLANTA,
                   "columnas": {"origen": COL_RUTA_ORIGEN, "destino": COL_RUTA_DESTINO, "km": COL_RUTA_KM,
                                "costo_viaje": cols_veh},
                   "phi": {"hoja": HOJA_VEHICULOS, "vehiculo": VEHICULO_CEDI_PLANTA, "columna": COL_VEH_COSTO_KM}},
        "conjuntos": {"V": V, "W": W, "W_planta": W_I},
        "candidatos": puntos,
        "cedi_planta": {"codigo": j0, "vehiculo": VEHICULO_CEDI_PLANTA, "phi": phi},
        "km": {j: km_I[j] for j in W_I},
        "cI": {v: {j: cI[v][j] for j in W_I} for v in V},
    }
    ruta = _escribir("costo_fase1.json", salida)
    print(f"{ruta}: |V|={len(V)}, destinos={len(W_I)}, phi({candidatos[j0]})={phi:,.0f} COP/posicion")

    sin_ruta = [k for k in D if all(k in faltantes[j] for j in W)]
    salida = {
        "descripcion": "cII[v][j][k]: costo (COP) de un viaje del vehiculo v desde el punto candidato j (codigo DANE) "
                       "hasta la zona k (codigo DANE). 'rutas_faltantes' lista los pares (j, k) sin costo",
        "fuente": {"archivo": ARCHIVO_DEMANDA.name, "hoja": HOJA_RUTAS,
                   "columnas": {"origen": COL_RUTA_ORIGEN, "destino": COL_RUTA_DESTINO, "km": COL_RUTA_KM,
                                "costo_viaje": cols_veh}},
        "conjuntos": {"V": V, "W": W, "D": D},
        "candidatos": puntos,
        "rutas_faltantes": faltantes,
        "zonas_sin_ninguna_ruta": sin_ruta,
        "km": km_II,
        "cII": cII,
    }
    ruta = _escribir("costo_fase2.json", salida)
    con_ruta = {j: len(v) for j, v in cII[V[0]].items()}
    print(f"{ruta}: rutas con costo por CEDI {con_ruta} "
          f"({sum(con_ruta.values())} de {len(W) * len(D)}); zonas sin ninguna ruta: {sin_ruta}")

    S, g, h_t, nombres_cedi, cols, omitidos = leer_costos_cedi(candidatos)
    if omitidos:
        print(f"AVISO: filas de {HOJA_COSTEO} que no están en CANDIDATOS y no entran al modelo: {omitidos}")
    invalidos = {candidatos[j]: t for j, t in fijos.items() if t not in S}
    if invalidos:
        raise ValueError(f"Tamaños fijos que no están en S={S}: {invalidos}")
    salida = {
        "descripcion": "g[j][s]: costo anual de operacion (COP) del CEDI candidato j (codigo DANE) con tamano s",
        "fuente": {"archivo": ARCHIVO_COSTEO.name, "hoja": HOJA_COSTEO,
                   "columnas": {"cedi": COL_CEDI, "costo_anual": f"{cols['costo_anual'][0]}:{cols['costo_anual'][-1]}"}},
        "conjuntos": {"W": W, "S": S},
        "candidatos": {j: {**puntos[j], "cedi": nombres_cedi[j]} for j in W},
        "g": g,
    }
    ruta = _escribir("costo_fijo.json", salida)
    print(f"{ruta}: |W|={len(W)}, S={S}, costo anual entre {min(min(v.values()) for v in g.values()):,.0f} "
          f"y {max(max(v.values()) for v in g.values()):,.0f} COP")

    factor = TONELADAS_POR_POSICION
    h = {j: {s: v * factor for s, v in por_tamano.items()} for j, por_tamano in h_t.items()}
    salida = {
        "descripcion": "h[j][s]: costo variable de manejo (COP) por posicion de estiba en el CEDI candidato j "
                       "(codigo DANE) con tamano s = costo por tonelada recibida x toneladas por posicion",
        "fuente": {"archivo": ARCHIVO_COSTEO.name, "hoja": HOJA_COSTEO,
                   "columnas": {"cedi": COL_CEDI,
                                "costo_por_tonelada": f"{cols['costo_por_tonelada'][0]}:{cols['costo_por_tonelada'][-1]}"},
                   "factor_conversion": "TONELADAS_POR_POSICION = 2000,1 t / 18.191 posiciones (demanda 2025)"},
        "conjuntos": {"W": W, "S": S},
        "candidatos": {j: {**puntos[j], "cedi": nombres_cedi[j]} for j in W},
        "toneladas_por_posicion": factor,
        "h_por_tonelada": h_t,
        "h": h,
    }
    ruta = _escribir("costo_variable.json", salida)
    print(f"{ruta}: factor={factor:.4f} t/posicion, costo/posicion entre "
          f"{min(min(v.values()) for v in h.values()):,.0f} y {max(max(v.values()) for v in h.values()):,.0f} COP")

    a, a_min, por_dia, celda = leer_capacidad(S)
    salida = {
        "descripcion": "a[s]: capacidad maxima anual de flujo (posiciones de estiba) de un CEDI de tamano s = "
                       "maximo de posiciones por dia x 25 dias x 12 meses. 'a_min' es el minimo del rango "
                       "del tamano en la misma escala (el modelo actual no lo usa)",
        "fuente": {"archivo": ARCHIVO_DEMANDA.name, "hoja": HOJA_TAMANOS, "tabla": celda},
        "dias_operacion": {"por_mes": DIAS_OPERACION_MES, "meses": MESES_ANO, "por_ano": PERIODOS_POR_ANO},
        "conjuntos": {"S": S},
        "cedis_fijos": {"descripcion": "CEDI siempre abiertos con tamano fijo (x[j][s] = 1); no son decision",
                        "tamano": fijos},
        "posiciones_por_dia": por_dia,
        "a_min": a_min,
        "a": a,
    }
    ruta = _escribir("capacidad.json", salida)
    print(f"{ruta}: posiciones/dia x {PERIODOS_POR_ANO} dias, a={ {s: round(v) for s, v in a.items()} }")


if __name__ == "__main__":
    main()
