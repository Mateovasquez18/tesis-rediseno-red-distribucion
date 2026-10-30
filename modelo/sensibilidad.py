"""Análisis de sensibilidad de la demanda: ¿cuánto debe cambiar la demanda para que el diseño propuesto deje de
ser óptimo?

La demanda de todas las categorías y zonas se escala por un factor λ (λ = 1 es la demanda actual). Para cada λ:
- se resuelve el modelo libre y se registra el diseño óptimo (CEDI abiertos y su tamaño);
- se resuelve el modelo con el diseño propuesto fijo, para medir el sobrecosto de mantenerlo (arrepentimiento).

Exploración: con los viajes como variables continuas (relajación, segundos por escenario) se recorre una malla de λ
y se localizan por bisección los umbrales inferior y superior en los que el diseño óptimo cambia.
Verificación: los umbrales se comprueban con el modelo entero completo a ambos lados de cada umbral.

Uso:  python3 sensibilidad.py              (exploración + verificación con el modelo entero)
      python3 sensibilidad.py --rapido     (solo exploración relajada)
      python3 sensibilidad.py --excel      (solo regenera el Excel a partir del JSON existente)
"""
import copy
import json
import sys
import time

import pulp

from modelo_pulp import RESULTADOS_DIR, cargar_datos, construir_modelo, resolver

MALLA = [round(0.70 + 0.05 * n, 2) for n in range(12)]   # 0,70 … 1,25
TOLERANCIA_UMBRAL = 0.0025                                # precisión de la bisección en λ
LIMITE_RELAJADO = 120                                     # s por escenario relajado
LIMITE_ENTERO = 300                                       # s por escenario entero de verificación
GAP_RELAJADO = 1e-5
GAP_ENTERO = 5e-4
SALIDA = RESULTADOS_DIR / "sensibilidad_demanda.json"
SALIDA_EXCEL = RESULTADOS_DIR / "sensibilidad_demanda.xlsx"


def escalar(datos, lam):
    nuevos = copy.copy(datos)
    nuevos["d"] = {i: {k: q * lam for k, q in por_zona.items()} for i, por_zona in datos["d"].items()}
    return nuevos


def diseno_propuesto(datos):
    sol = json.loads((RESULTADOS_DIR / "solucion.json").read_text(encoding="utf-8"))
    return {c["codigo"]: c["tamano"] for c in sol["cedis"]}


def texto_diseno(diseno, datos):
    abiertos = [f"{datos['candidatos'][j]['ciudad']} {s}" for j, s in diseno.items() if s]
    return ", ".join(abiertos) if abiertos else "ninguno"


def resolver_escenario(datos, lam, enteros, diseno_fijo=None):
    """Resuelve el escenario λ. Devuelve estado, Z, cota, brecha, diseño y costos por componente."""
    d = escalar(datos, lam)
    m, var, componentes, _ = construir_modelo(d, viajes_enteros=enteros)
    m.name = f"sens_{'E' if enteros else 'R'}_{'fijo' if diseno_fijo else 'libre'}_{lam:.4f}".replace(".", "_")
    if diseno_fijo:
        for j in d["W"]:
            for s in d["S"]:
                m += var["x"][j, s] == (1 if diseno_fijo[j] == s else 0), f"diseno_{j}_{s}"
    estado, segundos, cota = resolver(m, limite_segundos=LIMITE_ENTERO if enteros else LIMITE_RELAJADO,
                                      gap=GAP_ENTERO if enteros else GAP_RELAJADO)
    if not estado.startswith(("Optimal", "Factible")):
        return {"lambda": lam, "estado": estado, "Z": None, "diseno": None, "segundos": segundos}
    z = pulp.value(m.objective)
    diseno = {j: next((s for s in d["S"] if (var["x"][j, s].value() or 0) > 0.5), None) for j in d["W"]}
    return {"lambda": lam, "estado": estado, "Z": z, "cota_inferior": cota,
            "brecha": (z - cota) / z if cota else None, "diseno": diseno,
            "costos": {k: pulp.value(e) for k, e in componentes.items()}, "segundos": segundos}


def biseccion(datos, propuesto, dentro, fuera):
    """Último λ (entre `dentro`, donde el diseño es el propuesto, y `fuera`, donde no) con el diseño propuesto."""
    while abs(fuera - dentro) > TOLERANCIA_UMBRAL:
        medio = (dentro + fuera) / 2
        r = resolver_escenario(datos, medio, enteros=False)
        print(f"  bisección λ={medio:.4f}: {texto_diseno(r['diseno'], datos) if r['diseno'] else r['estado']}")
        if r["diseno"] == propuesto:
            dentro = medio
        else:
            fuera = medio
    return dentro, fuera


def exportar_excel(salida):
    """Escribe el resumen, la malla de escenarios y la verificación en un Excel."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Resumen"
    filas = [["Diseño propuesto", ", ".join(f"{c} {s}" for c, s in salida["diseno_propuesto"].items() if s)],
             ["Demanda base (posiciones/año)", salida["demanda_base_posiciones"]],
             ["Capacidad del diseño propuesto", salida["capacidad_diseno_propuesto"]],
             ["Crecimiento máximo factible del diseño", salida["lambda_maximo_factible_diseno_propuesto"] - 1], [],
             ["Umbral", "Último λ con el diseño propuesto", "Primer λ con otro diseño", "Variación de la demanda",
              "Demanda en el umbral (posiciones)", "Diseño óptimo al cruzar el umbral"]]
    for lado, u in salida["umbrales"].items():
        filas.append([lado, u["lambda_ultimo_con_diseno_propuesto"], u["lambda_primero_con_otro_diseno"],
                      u["variacion_demanda_pct"] / 100, u["demanda_umbral_posiciones"], u["diseno_optimo_al_cruzar"]])
    for fila in filas:
        ws.append(fila)
    for celda in ws[6]:
        celda.font = Font(bold=True)
    for fila in ws.iter_rows(min_row=7, min_col=4, max_col=4):
        fila[0].number_format = "0.0%"
    ws["B4"].number_format = "0.0%"

    ws2 = wb.create_sheet("Escenarios")
    ws2.append(["λ", "Variación demanda", "Demanda (posiciones)", "Diseño óptimo", "¿Es el diseño propuesto?",
                "Z óptimo (COP)", "Z con diseño propuesto (COP)", "Sobrecosto del diseño propuesto (COP)",
                "Sobrecosto (%)"])
    verde = PatternFill("solid", fgColor="DDF2E6")
    for e in salida["malla"]:
        ws2.append([e["lambda"], e["lambda"] - 1, e["demanda"], e["diseno_optimo_texto"],
                    "Sí" if e["es_diseno_propuesto"] else "No", e["Z_optimo"],
                    e["Z_diseno_propuesto"] if e["Z_diseno_propuesto"] is not None else "Infactible (capacidad)",
                    e["sobrecosto_diseno_propuesto"], e["sobrecosto_pct"]])
        if e["es_diseno_propuesto"]:
            for celda in ws2[ws2.max_row]:
                celda.fill = verde
    for fila in ws2.iter_rows(min_row=2):
        fila[1].number_format = fila[8].number_format = "0.0%"
        for celda in (fila[2], fila[5], fila[6], fila[7]):
            celda.number_format = "#,##0"

    ws3 = wb.create_sheet("Verificacion_entera")
    ws3.append(["Umbral", "λ", "Estado", "Z (COP)", "Brecha", "Diseño óptimo", "¿Es el diseño propuesto?"])
    for v in salida["verificacion_entera"]:
        ws3.append([v["umbral"], v["lambda"], v["estado"], v["Z"], v["brecha"], v["diseno"],
                    "Sí" if v["es_diseno_propuesto"] else "No"])
    for fila in ws3.iter_rows(min_row=2):
        fila[3].number_format = "#,##0"
        fila[4].number_format = "0.000%"

    for hoja in wb.worksheets:
        if hoja.title != "Resumen":
            for celda in hoja[1]:
                celda.font = Font(bold=True)
            hoja.freeze_panes = "A2"
        for col in "ABCDEFGHI":
            hoja.column_dimensions[col].width = 24
    wb.save(SALIDA_EXCEL)


def main():
    if "--excel" in sys.argv:
        exportar_excel(json.loads(SALIDA.read_text(encoding="utf-8")))
        print(f"Excel en {SALIDA_EXCEL.relative_to(RESULTADOS_DIR.parent)}")
        return
    rapido = "--rapido" in sys.argv
    inicio = time.time()
    datos = cargar_datos()
    propuesto = diseno_propuesto(datos)
    demanda = sum(q for por_zona in datos["d"].values() for q in por_zona.values())
    capacidad_propuesta = sum(datos["a"][s] for s in propuesto.values() if s)
    print(f"Diseño propuesto: {texto_diseno(propuesto, datos)}; demanda {demanda:,.0f}; "
          f"capacidad del diseño {capacidad_propuesta:,.0f} (λ máx. factible {capacidad_propuesta / demanda:.4f})")

    # 1. Malla relajada: diseño óptimo libre y costo del diseño propuesto fijo
    malla = []
    for lam in MALLA:
        libre = resolver_escenario(datos, lam, enteros=False)
        fijo = resolver_escenario(datos, lam, enteros=False, diseno_fijo=propuesto)
        sobrecosto = (fijo["Z"] - libre["Z"]) if fijo["Z"] is not None and libre["Z"] is not None else None
        malla.append({"lambda": lam, "demanda": demanda * lam, "diseno_optimo": libre["diseno"],
                      "diseno_optimo_texto": texto_diseno(libre["diseno"], datos) if libre["diseno"] else libre["estado"],
                      "es_diseno_propuesto": libre["diseno"] == propuesto, "Z_optimo": libre["Z"],
                      "Z_diseno_propuesto": fijo["Z"], "estado_diseno_propuesto": fijo["estado"],
                      "sobrecosto_diseno_propuesto": sobrecosto,
                      "sobrecosto_pct": sobrecosto / libre["Z"] if sobrecosto is not None else None})
        z_fijo = f"{fijo['Z'] / 1e9:,.2f}" if fijo["Z"] is not None else fijo["estado"]
        print(f"λ={lam:.2f}  óptimo: {malla[-1]['diseno_optimo_texto']:<45} Z*={libre['Z'] / 1e9:,.2f} mm  "
              f"Z(propuesto)={z_fijo}  {'= propuesto' if malla[-1]['es_diseno_propuesto'] else ''}")

    if not any(e["lambda"] == 1.0 and e["es_diseno_propuesto"] for e in malla):
        raise RuntimeError("Con λ = 1 la relajación no reproduce el diseño propuesto; no se pueden estimar umbrales")

    # 2. Bisección de los umbrales alrededor de λ = 1
    indice = next(n for n, e in enumerate(malla) if e["lambda"] == 1.0)
    umbrales = {}
    abajo = next((malla[n] for n in range(indice - 1, -1, -1) if not malla[n]["es_diseno_propuesto"]), None)
    if abajo:
        dentro = next(malla[n]["lambda"] for n in range(indice, -1, -1) if not malla[n - 1]["es_diseno_propuesto"])
        print(f"Umbral inferior entre λ={abajo['lambda']:.2f} y {dentro:.2f}")
        umbrales["inferior"] = biseccion(datos, propuesto, dentro, abajo["lambda"])
    arriba = next((malla[n] for n in range(indice + 1, len(malla)) if not malla[n]["es_diseno_propuesto"]), None)
    if arriba:
        dentro = next(malla[n]["lambda"] for n in range(indice, len(malla)) if not malla[n + 1]["es_diseno_propuesto"])
        print(f"Umbral superior entre λ={dentro:.2f} y {arriba['lambda']:.2f}")
        umbrales["superior"] = biseccion(datos, propuesto, dentro, arriba["lambda"])

    resumen_umbrales = {}
    for lado, (dentro, fuera) in umbrales.items():
        r_fuera = resolver_escenario(datos, fuera, enteros=False)
        resumen_umbrales[lado] = {
            "lambda_ultimo_con_diseno_propuesto": dentro, "lambda_primero_con_otro_diseno": fuera,
            "variacion_demanda_pct": (fuera - 1) * 100,
            "demanda_umbral_posiciones": demanda * fuera,
            "diseno_optimo_al_cruzar": texto_diseno(r_fuera["diseno"], datos) if r_fuera["diseno"] else r_fuera["estado"],
        }

    # 3. Verificación con el modelo entero completo a ambos lados de cada umbral
    verificacion = []
    if not rapido:
        for lado, u in resumen_umbrales.items():
            hacia_fuera = -1 if lado == "inferior" else 1   # dirección en que λ se aleja de 1
            for lam in (u["lambda_ultimo_con_diseno_propuesto"] - 0.01 * hacia_fuera,   # dentro del rango
                        u["lambda_primero_con_otro_diseno"] + 0.01 * hacia_fuera):     # fuera del rango
                r = resolver_escenario(datos, round(lam, 4), enteros=True)
                verificacion.append({"umbral": lado, "lambda": r["lambda"], "estado": r["estado"], "Z": r["Z"],
                                     "brecha": r.get("brecha"),
                                     "diseno": texto_diseno(r["diseno"], datos) if r["diseno"] else None,
                                     "es_diseno_propuesto": r["diseno"] == propuesto})
                print(f"Verificación entera λ={r['lambda']:.4f}: {verificacion[-1]['diseno']} "
                      f"({'= propuesto' if verificacion[-1]['es_diseno_propuesto'] else 'otro diseño'}), "
                      f"brecha {r.get('brecha') or 0:.3%}")

    salida = {
        "descripcion": "Sensibilidad del diseño propuesto a un cambio uniforme de la demanda (factor lambda sobre "
                       "todas las categorías y zonas). Un diseño deja de ser óptimo cuando el modelo libre elige otro "
                       "conjunto de CEDI abiertos o tamaños.",
        "metodo": {"exploracion": "modelo con viajes continuos (relajación), malla de lambda y bisección",
                   "verificacion": "modelo entero completo a 1 punto porcentual de cada umbral",
                   "tolerancia_lambda": TOLERANCIA_UMBRAL},
        "diseno_propuesto": {datos["candidatos"][j]["ciudad"]: s for j, s in propuesto.items()},
        "demanda_base_posiciones": demanda,
        "capacidad_diseno_propuesto": capacidad_propuesta,
        "lambda_maximo_factible_diseno_propuesto": capacidad_propuesta / demanda,
        "umbrales": resumen_umbrales,
        "rango_de_validez": {
            "lambda_min": resumen_umbrales.get("inferior", {}).get("lambda_ultimo_con_diseno_propuesto"),
            "lambda_max": resumen_umbrales.get("superior", {}).get("lambda_ultimo_con_diseno_propuesto"),
        },
        "malla": [{**e, "diseno_optimo": {datos["candidatos"][j]["ciudad"]: s for j, s in e["diseno_optimo"].items()}
                   if e["diseno_optimo"] else None} for e in malla],
        "verificacion_entera": verificacion,
        "tiempo_total_segundos": time.time() - inicio,
    }
    SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    exportar_excel(salida)
    print(f"\nResultados en {SALIDA.relative_to(RESULTADOS_DIR.parent)} ({salida['tiempo_total_segundos'] / 60:.1f} min)")


if __name__ == "__main__":
    main()
