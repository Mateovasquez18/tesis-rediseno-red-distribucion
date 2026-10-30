"""Modelo MILP de rediseño de la red de distribución (sección 4 de documento/Rediseno_Resultado.tex).

Lee los parámetros de modelo/data/*.json (generados por excel_a_json.py), resuelve con CBC y escribe
la solución en resultados/ (en la raíz del repositorio).

Conjuntos: C categorías, D zonas, W puntos candidatos, S tamaños, V tipos de vehículo.
j0 ∈ W es el CEDI acoplado a la planta (Cali); W_P = W \\ {j0} son los CEDI abastecidos por carretera.
W_F ⊆ W son los CEDI con apertura y tamaño fijos (j0 en XL).

    min Z = T^I + T^II + A_f + A_v
    T^I  = phi · Σ_i w[i,j0] + Σ_t Σ_{j∈W_P} cI[t,j] · nI[t,j]
    T^II = Σ_t Σ_j Σ_k cII[t,j,k] · nII[t,j,k]
    A_f  = Σ_j Σ_s g[j,s] · x[j,s]
    A_v  = Σ_i Σ_j Σ_s h[j,s] · v[i,j,s]
s.a.
    (demanda)         Σ_j y[i,j,k] = d[i,k]                     ∀ i, k
    (capacidad)       Σ_i v[i,j,s] ≤ a[s] · x[j,s]              ∀ j, s
    (balance 1)       w[i,j] = Σ_k y[i,j,k]                     ∀ i, j
    (balance 2)       Σ_s v[i,j,s] = w[i,j]                     ∀ i, j
    (tamaño)          Σ_s x[j,s] ≤ 1                            ∀ j
    (vehículos I)     Σ_t q[t] · nI[t,j] ≥ Σ_i w[i,j]           ∀ j ∈ W_P
    (vehículos II)    Σ_t q[t] · nII[t,j,k] ≥ Σ_i y[i,j,k]      ∀ j, k
    (CEDI fijos)      x[j,s̄_j] = 1                             ∀ j ∈ W_F
    x binaria; y, v, w ≥ 0; nI, nII enteras ≥ 0
"""
import json
import time
from pathlib import Path

import pulp

DATA_DIR = Path(__file__).resolve().parent / "data"
RESULTADOS_DIR = Path(__file__).resolve().parent.parent / "resultados"
TOLERANCIA = 1e-6
LIMITE_SEGUNDOS = 600     # tiempo máximo del solver
GAP_RELATIVO = 1e-4       # brecha de optimalidad aceptada
HILOS = 8                 # núcleos que usa CBC


def cargar_datos(data_dir=DATA_DIR):
    leer = lambda nombre: json.loads((data_dir / nombre).read_text(encoding="utf-8"))
    dem, veh, f1, f2, fijo, var, cap = (leer(n) for n in (
        "demanda.json", "vehiculos.json", "costo_fase1.json", "costo_fase2.json",
        "costo_fijo.json", "costo_variable.json", "capacidad.json"))
    C, D = dem["conjuntos"]["C"], dem["conjuntos"]["D"]
    W, S, V = fijo["conjuntos"]["W"], fijo["conjuntos"]["S"], veh["conjuntos"]["V"]

    # Los archivos deben describir los mismos conjuntos
    for nombre, conj, esperado in [("costo_fase1 V", f1["conjuntos"]["V"], V), ("costo_fase1 W", f1["conjuntos"]["W"], W),
                                   ("costo_fase2 V", f2["conjuntos"]["V"], V), ("costo_fase2 W", f2["conjuntos"]["W"], W),
                                   ("costo_fase2 D", f2["conjuntos"]["D"], D), ("costo_variable W", var["conjuntos"]["W"], W),
                                   ("costo_variable S", var["conjuntos"]["S"], S), ("capacidad S", cap["conjuntos"]["S"], S)]:
        if conj != esperado:
            raise ValueError(f"El conjunto {nombre} no coincide: {conj} != {esperado}")
    sin_ruta = {j: v for j, v in f2["rutas_faltantes"].items() if v}
    if sin_ruta:
        raise ValueError(f"Hay rutas sin costo en costo_fase2.json: {sin_ruta}")

    return {
        "C": C, "D": D, "W": W, "S": S, "V": V,
        "j0": f1["cedi_planta"]["codigo"], "W_P": f1["conjuntos"]["W_planta"], "phi": f1["cedi_planta"]["phi"],
        "d": dem["d"], "q": veh["q"], "cI": f1["cI"], "cII": f2["cII"], "km_I": f1["km"], "km_II": f2["km"],
        "g": fijo["g"], "h": var["h"], "a": cap["a"], "fijos": cap["cedis_fijos"]["tamano"],
        "vehiculos": veh["vehiculos"], "zonas": dem["zonas"], "candidatos": fijo["candidatos"],
        "nombres_categorias": dem["nombres_categorias"],
    }


def construir_modelo(datos, viajes_enteros=True):
    """Construye el MILP. Con viajes_enteros=False los viajes nI, nII son continuos (relajación usada para
    explorar escenarios rápidamente; el modelo de la tesis es siempre el entero)."""
    C, D, W, S, V = datos["C"], datos["D"], datos["W"], datos["S"], datos["V"]
    j0, W_P, phi = datos["j0"], datos["W_P"], datos["phi"]
    d, q, cI, cII, g, h, a = (datos[k] for k in ("d", "q", "cI", "cII", "g", "h", "a"))
    # Solo se crean flujos y_ijk para los pares (i, k) con demanda; los demás serían 0 por (demanda)
    IK = [(i, k) for i in C for k in d[i] if d[i][k] > 0]
    zonas_de = {i: [k for ii, k in IK if ii == i] for i in C}
    categorias_de = {k: [i for i, kk in IK if kk == k] for k in D}
    K = [k for k in D if categorias_de[k]]

    m = pulp.LpProblem("Rediseno_Red_Distribucion", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", [(j, s) for j in W for s in S], cat=pulp.LpBinary)
    y = pulp.LpVariable.dicts("y", [(i, j, k) for i, k in IK for j in W], lowBound=0)
    v = pulp.LpVariable.dicts("v", [(i, j, s) for i in C for j in W for s in S], lowBound=0)
    w = pulp.LpVariable.dicts("w", [(i, j) for i in C for j in W], lowBound=0)
    tipo_viaje = pulp.LpInteger if viajes_enteros else pulp.LpContinuous
    nI = pulp.LpVariable.dicts("nI", [(t, j) for t in V for j in W_P], lowBound=0, cat=tipo_viaje)
    nII = pulp.LpVariable.dicts("nII", [(t, j, k) for t in V for j in W for k in K], lowBound=0, cat=tipo_viaje)

    componentes = {
        "T_I": phi * pulp.lpSum(w[i, j0] for i in C)
               + pulp.lpSum(cI[t][j] * nI[t, j] for t in V for j in W_P),
        "T_II": pulp.lpSum(cII[t][j][k] * nII[t, j, k] for t in V for j in W for k in K),
        "A_f": pulp.lpSum(g[j][s] * x[j, s] for j in W for s in S),
        "A_v": pulp.lpSum(h[j][s] * v[i, j, s] for i in C for j in W for s in S),
    }
    m += pulp.lpSum(componentes.values()), "Costo_Logistico_Anual"

    for i, k in IK:
        m += pulp.lpSum(y[i, j, k] for j in W) == d[i][k], f"demanda_{i}_{k}"
    for j in W:
        for s in S:
            m += pulp.lpSum(v[i, j, s] for i in C) <= a[s] * x[j, s], f"capacidad_{j}_{s}"
        m += pulp.lpSum(x[j, s] for s in S) <= 1, f"tamano_{j}"
    for i in C:
        for j in W:
            m += w[i, j] == pulp.lpSum(y[i, j, k] for k in zonas_de[i]), f"balance1_{i}_{j}"
            m += pulp.lpSum(v[i, j, s] for s in S) == w[i, j], f"balance2_{i}_{j}"
    for j in W_P:
        m += pulp.lpSum(q[t] * nI[t, j] for t in V) >= pulp.lpSum(w[i, j] for i in C), f"vehiculos1_{j}"
    for j in W:
        for k in K:
            m += (pulp.lpSum(q[t] * nII[t, j, k] for t in V) >= pulp.lpSum(y[i, j, k] for i in categorias_de[k]),
                  f"vehiculos2_{j}_{k}")
    for j, s_fijo in datos["fijos"].items():
        m += x[j, s_fijo] == 1, f"fijo_{j}"

    return m, {"x": x, "y": y, "v": v, "w": w, "nI": nI, "nII": nII}, componentes, IK


def resolver(m, mensajes=False, limite_segundos=LIMITE_SEGUNDOS, gap=GAP_RELATIVO, hilos=HILOS):
    """Resuelve con CBC. Devuelve (estado, segundos, cota_inferior); la cota sale del registro de CBC."""
    RESULTADOS_DIR.mkdir(exist_ok=True)
    registro = RESULTADOS_DIR / f"cbc_{m.name}.log"
    solver = pulp.PULP_CBC_CMD(msg=mensajes, timeLimit=limite_segundos, gapRel=gap, threads=hilos,
                               logPath=str(registro))
    inicio = time.time()
    m.solve(solver)
    segundos = time.time() - inicio
    estado = pulp.LpStatus[m.status]
    cota = _cota_cbc(registro)
    # Con límite de tiempo CBC puede devolver una solución factible no probada como óptima
    if estado == "Optimal" and segundos >= limite_segundos * 0.98:
        estado = "Factible (límite de tiempo)"
    return estado, segundos, cota


def _cota_cbc(registro):
    """Mejor cota inferior reportada por CBC ("best possible"), o None si no aparece en el registro."""
    import re
    try:
        texto = registro.read_text(errors="ignore")
    except OSError:
        return None
    valores = re.findall(r"best possible ([-\d.e+]+)", texto) or re.findall(r"Lower bound:\s+([-\d.e+]+)", texto)
    return float(valores[-1]) if valores else None


def extraer_solucion(datos, variables, componentes, IK, estado, segundos, m, cota=None):
    C, W, S, D, V = datos["C"], datos["W"], datos["S"], datos["D"], datos["V"]
    x, y, w, nI, nII = (variables[k] for k in ("x", "y", "w", "nI", "nII"))
    val = lambda var: var.value() or 0.0

    abiertos = {j: s for j in W for s in S if val(x[j, s]) > 0.5}
    flujo_cedi = {j: sum(val(w[i, j]) for i in C) for j in W}
    cedis = []
    for j in W:
        s = abiertos.get(j)
        cedis.append({
            "codigo": j, "ciudad": datos["candidatos"][j]["ciudad"], "abierto": s is not None, "tamano": s,
            "fijo": j in datos["fijos"], "flujo_posiciones": flujo_cedi[j],
            "capacidad": datos["a"][s] if s else 0.0,
            "utilizacion": flujo_cedi[j] / datos["a"][s] if s else None,
            "costo_fijo": datos["g"][j][s] if s else 0.0,
        })

    flujos = [{"categoria": i, "cedi": j, "zona": k, "posiciones": val(y[i, j, k])}
              for i, k in IK for j in W if val(y[i, j, k]) > TOLERANCIA]

    viajes_I = [{"cedi": j, "vehiculo": t, "viajes": round(val(nI[t, j])), "capacidad": datos["q"][t] * round(val(nI[t, j])),
                 "posiciones": flujo_cedi[j], "costo": datos["cI"][t][j] * round(val(nI[t, j]))}
                for j in datos["W_P"] for t in V if val(nI[t, j]) > 0.5]
    carga_II = {}
    for f in flujos:
        carga_II[(f["cedi"], f["zona"])] = carga_II.get((f["cedi"], f["zona"]), 0.0) + f["posiciones"]
    viajes_II = [{"cedi": j, "zona": k, "vehiculo": t, "viajes": round(val(nII[t, j, k])),
                  "capacidad": datos["q"][t] * round(val(nII[t, j, k])), "posiciones": carga_II.get((j, k), 0.0),
                  "costo": datos["cII"][t][j][k] * round(val(nII[t, j, k]))}
                 for (t, j, k), var in nII.items() if val(var) > 0.5]

    # Asignación de cada zona: CEDI(s) que la atienden y participación
    asignacion = {}
    for k in D:
        por_cedi = {j: q for (j, kk), q in carga_II.items() if kk == k}
        total = sum(por_cedi.values())
        asignacion[k] = {"municipio": datos["zonas"][k]["municipio"], "departamento": datos["zonas"][k]["departamento"],
                         "demanda": total,
                         "cedis": {j: q / total for j, q in sorted(por_cedi.items(), key=lambda t: -t[1])}}

    costos = {nombre: pulp.value(expr) for nombre, expr in componentes.items()}
    return {
        "estado": estado, "tiempo_segundos": segundos,
        "costo_total": pulp.value(m.objective), "cota_inferior": cota,
        "brecha": (pulp.value(m.objective) - cota) / pulp.value(m.objective) if cota else None,
        "costos": costos,
        "cedis": cedis, "asignacion_zonas": asignacion, "flujos": flujos,
        "viajes_fase1": viajes_I, "viajes_fase2": viajes_II,
        "posiciones_planta_cedi_acoplado": flujo_cedi[datos["j0"]],
        "zonas_divididas": [k for k, z in asignacion.items() if len(z["cedis"]) > 1],
    }


def validar(datos, sol, IK):
    """Comprueba en la solución las restricciones del modelo (independiente del solver)."""
    errores = []
    servido = {}
    for f in sol["flujos"]:
        servido[(f["categoria"], f["zona"])] = servido.get((f["categoria"], f["zona"]), 0.0) + f["posiciones"]
    for i, k in IK:
        if abs(servido.get((i, k), 0.0) - datos["d"][i][k]) > 1e-4:
            errores.append(f"demanda {i},{k}: servido {servido.get((i, k), 0.0)} != {datos['d'][i][k]}")
    for c in sol["cedis"]:
        if c["flujo_posiciones"] > c["capacidad"] + 1e-4:
            errores.append(f"capacidad {c['ciudad']}: {c['flujo_posiciones']} > {c['capacidad']}")
    for j, s in datos["fijos"].items():
        c = next(c for c in sol["cedis"] if c["codigo"] == j)
        if c["tamano"] != s:
            errores.append(f"CEDI fijo {c['ciudad']} quedó con tamaño {c['tamano']} y no {s}")

    # Capacidad de los vehículos (carga no apilable): posiciones transportadas ≤ q · viajes en cada arco
    cap_I, cap_II = {}, {}
    for r in sol["viajes_fase1"]:
        cap_I[r["cedi"]] = cap_I.get(r["cedi"], 0.0) + r["capacidad"]
    for r in sol["viajes_fase2"]:
        cap_II[(r["cedi"], r["zona"])] = cap_II.get((r["cedi"], r["zona"]), 0.0) + r["capacidad"]
    for c in sol["cedis"]:
        if c["codigo"] in datos["W_P"] and c["flujo_posiciones"] > cap_I.get(c["codigo"], 0.0) + 1e-4:
            errores.append(f"vehículos fase 1 hacia {c['ciudad']}: {c['flujo_posiciones']} > {cap_I.get(c['codigo'], 0)}")
    carga = {}
    for f in sol["flujos"]:
        carga[(f["cedi"], f["zona"])] = carga.get((f["cedi"], f["zona"]), 0.0) + f["posiciones"]
    for arco, q in carga.items():
        if q > cap_II.get(arco, 0.0) + 1e-4:
            errores.append(f"vehículos fase 2 en {arco}: {q} > {cap_II.get(arco, 0)}")
    return errores


def imprimir_resumen(sol, datos):
    print(f"Estado: {sol['estado']}  ({sol['tiempo_segundos']:.1f} s)")
    print(f"Costo logístico anual Z = {sol['costo_total']:,.0f} COP")
    if sol["cota_inferior"] is not None:
        print(f"Cota inferior {sol['cota_inferior']:,.0f} COP; brecha de optimalidad {sol['brecha']:.3%}")
    nombres = {"T_I": "Transporte fase 1", "T_II": "Transporte fase 2", "A_f": "Almacenamiento fijo",
               "A_v": "Almacenamiento variable"}
    for clave, valor in sol["costos"].items():
        print(f"  {nombres[clave]:<26}{valor:>20,.0f}  ({valor / sol['costo_total']:.1%})")
    print("\nCEDI:")
    for c in sol["cedis"]:
        if c["abierto"]:
            print(f"  {c['ciudad']:<14}{c['tamano']:<4}{'(fijo)' if c['fijo'] else '':<8}"
                  f"flujo {c['flujo_posiciones']:>8,.0f} / {c['capacidad']:>7,.0f}  ({c['utilizacion']:.0%})")
        else:
            print(f"  {c['ciudad']:<14}cerrado")
    for fase, filas in (("fase 1", sol["viajes_fase1"]), ("fase 2", sol["viajes_fase2"])):
        por_tipo = {}
        for r in filas:
            por_tipo[r["vehiculo"]] = por_tipo.get(r["vehiculo"], 0) + r["viajes"]
        print(f"Viajes {fase}: " + (", ".join(f"{t} {datos['vehiculos'][t]['nombre']}: {n:,}"
                                             for t, n in sorted(por_tipo.items())) or "ninguno"))
    print(f"Zonas atendidas por más de un CEDI: {len(sol['zonas_divididas'])}")


def exportar(sol, datos, carpeta=RESULTADOS_DIR):
    carpeta.mkdir(exist_ok=True)
    (carpeta / "solucion.json").write_text(json.dumps(sol, ensure_ascii=False, indent=2), encoding="utf-8")

    import openpyxl
    from openpyxl.styles import Font
    ciudad = lambda j: datos["candidatos"][j]["ciudad"]
    vehiculo = lambda t: f"{t} {datos['vehiculos'][t]['nombre']}"
    wb = openpyxl.Workbook()
    hojas = {
        "Resumen": (["Concepto", "COP/año"],
                    [["Costo total Z", sol["costo_total"]]] + [[k, v] for k, v in sol["costos"].items()]),
        "CEDIs": (["Código DANE", "Ciudad", "Abierto", "Tamaño", "Fijo", "Flujo (posiciones/año)", "Capacidad",
                   "Utilización", "Costo fijo (COP/año)"],
                  [[c["codigo"], c["ciudad"], c["abierto"], c["tamano"], c["fijo"], c["flujo_posiciones"],
                    c["capacidad"], c["utilizacion"], c["costo_fijo"]] for c in sol["cedis"]]),
        "Asignacion_Zonas": (["Código DANE", "Municipio", "Departamento", "Demanda (posiciones)", "CEDI principal",
                              "Participación CEDI principal", "Atendida por"],
                             [[k, z["municipio"], z["departamento"], z["demanda"],
                               ciudad(next(iter(z["cedis"]))), next(iter(z["cedis"].values())),
                               ", ".join(f"{ciudad(j)} {p:.0%}" for j, p in z["cedis"].items())]
                              for k, z in sol["asignacion_zonas"].items()]),
        "Viajes_Fase1": (["CEDI", "Vehículo", "Viajes/año", "Capacidad (posiciones)", "Posiciones al CEDI", "Costo (COP)"],
                         [[ciudad(r["cedi"]), vehiculo(r["vehiculo"]), r["viajes"], r["capacidad"], r["posiciones"],
                           r["costo"]] for r in sol["viajes_fase1"]]
                         + [[ciudad(datos["j0"]), f"{vehiculo('V002')} (phi por posición)", None, None,
                             sol["posiciones_planta_cedi_acoplado"],
                             datos["phi"] * sol["posiciones_planta_cedi_acoplado"]]]),
        "Viajes_Fase2": (["CEDI", "Código zona", "Municipio", "Vehículo", "Viajes/año", "Capacidad (posiciones)",
                          "Posiciones en el arco", "Costo (COP)"],
                         [[ciudad(r["cedi"]), r["zona"], datos["zonas"][r["zona"]]["municipio"], vehiculo(r["vehiculo"]),
                           r["viajes"], r["capacidad"], r["posiciones"], r["costo"]] for r in sol["viajes_fase2"]]),
        "Flujos": (["Categoría", "Nombre categoría", "CEDI", "Código zona", "Municipio", "Posiciones"],
                   [[f["categoria"], datos["nombres_categorias"][f["categoria"]], ciudad(f["cedi"]),
                     f["zona"], datos["zonas"][f["zona"]]["municipio"], f["posiciones"]] for f in sol["flujos"]]),
    }
    wb.remove(wb.active)
    for nombre, (encabezado, filas) in hojas.items():
        ws = wb.create_sheet(nombre)
        ws.append(encabezado)
        for celda in ws[1]:
            celda.font = Font(bold=True)
        for fila in filas:
            ws.append(fila)
        ws.freeze_panes = "A2"
        for n, _ in enumerate(encabezado, start=1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(n)].width = 22
    wb.save(carpeta / "solucion.xlsx")


def main():
    datos = cargar_datos()
    m, variables, componentes, IK = construir_modelo(datos)
    print(f"Modelo: {len(m.variables())} variables ({sum(v.isBinary() for v in m.variables())} binarias, "
          f"{sum(v.cat == pulp.LpInteger and not v.isBinary() for v in m.variables())} enteras), "
          f"{len(m.constraints)} restricciones")
    estado, segundos, cota = resolver(m)
    if not estado.startswith(("Optimal", "Factible")):
        raise RuntimeError(f"El solver terminó con estado {estado}")
    sol = extraer_solucion(datos, variables, componentes, IK, estado, segundos, m, cota)
    errores = validar(datos, sol, IK)
    if errores:
        raise RuntimeError("La solución no cumple las restricciones:\n" + "\n".join(errores[:20]))
    imprimir_resumen(sol, datos)
    exportar(sol, datos)
    print(f"\nResultados en {RESULTADOS_DIR.name}/solucion.json y solucion.xlsx")


if __name__ == "__main__":
    main()
