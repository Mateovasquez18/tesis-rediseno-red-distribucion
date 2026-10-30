"""Diseño robusto de la red frente a la variación de la demanda (modelo estocástico de dos etapas).

Primera etapa (común a todos los escenarios): apertura y tamaño de los CEDI, x[j,s].
Segunda etapa (por escenario de demanda λ): flujos y viajes, que se adaptan a cada escenario.

Se resuelven dos criterios:
- Costo esperado: minimiza el promedio del costo anual sobre los escenarios (pesos iguales).
- Arrepentimiento máximo: minimiza el mayor sobrecosto relativo frente al óptimo de cada escenario
  (Z*_λ tomado del análisis de sensibilidad).
En ambos, el diseño debe ser factible en todos los escenarios. Para que el problema sea tratable, los viajes son
continuos en la segunda etapa; el diseño elegido se evalúa después con el modelo entero completo.

Uso:  python3 robusto.py
"""
import json
import time

import pulp

from modelo_pulp import RESULTADOS_DIR, cargar_datos, construir_modelo, resolver
from sensibilidad import escalar, texto_diseno

ESCENARIOS = [round(0.70 + 0.05 * n, 2) for n in range(12)]   # mismos escenarios del análisis de sensibilidad
LIMITE_SEGUNDOS = 1800
GAP = 1e-4
SALIDA = RESULTADOS_DIR / "diseno_robusto.json"


def agregar_escenario(m, datos, x, sufijo):
    """Agrega al modelo `m` la segunda etapa de un escenario (viajes continuos). Devuelve su costo operativo."""
    C, D, W, S, V = datos["C"], datos["D"], datos["W"], datos["S"], datos["V"]
    j0, W_P, phi = datos["j0"], datos["W_P"], datos["phi"]
    d, q, cI, cII, h, a = (datos[k] for k in ("d", "q", "cI", "cII", "h", "a"))
    IK = [(i, k) for i in C for k in d[i] if d[i][k] > 0]
    zonas_de = {i: [k for ii, k in IK if ii == i] for i in C}
    categorias_de = {k: [i for i, kk in IK if kk == k] for k in D}
    K = [k for k in D if categorias_de[k]]

    y = pulp.LpVariable.dicts(f"y{sufijo}", [(i, j, k) for i, k in IK for j in W], lowBound=0)
    v = pulp.LpVariable.dicts(f"v{sufijo}", [(i, j, s) for i in C for j in W for s in S], lowBound=0)
    w = pulp.LpVariable.dicts(f"w{sufijo}", [(i, j) for i in C for j in W], lowBound=0)
    nI = pulp.LpVariable.dicts(f"nI{sufijo}", [(t, j) for t in V for j in W_P], lowBound=0)
    nII = pulp.LpVariable.dicts(f"nII{sufijo}", [(t, j, k) for t in V for j in W for k in K], lowBound=0)

    for i, k in IK:
        m += pulp.lpSum(y[i, j, k] for j in W) == d[i][k], f"demanda{sufijo}_{i}_{k}"
    for j in W:
        for s in S:
            m += pulp.lpSum(v[i, j, s] for i in C) <= a[s] * x[j, s], f"capacidad{sufijo}_{j}_{s}"
    for i in C:
        for j in W:
            m += w[i, j] == pulp.lpSum(y[i, j, k] for k in zonas_de[i]), f"balance1{sufijo}_{i}_{j}"
            m += pulp.lpSum(v[i, j, s] for s in S) == w[i, j], f"balance2{sufijo}_{i}_{j}"
    for j in W_P:
        m += pulp.lpSum(q[t] * nI[t, j] for t in V) >= pulp.lpSum(w[i, j] for i in C), f"veh1{sufijo}_{j}"
    for j in W:
        for k in K:
            m += (pulp.lpSum(q[t] * nII[t, j, k] for t in V) >= pulp.lpSum(y[i, j, k] for i in categorias_de[k]),
                  f"veh2{sufijo}_{j}_{k}")

    return (phi * pulp.lpSum(w[i, j0] for i in C)
            + pulp.lpSum(cI[t][j] * nI[t, j] for t in V for j in W_P)
            + pulp.lpSum(cII[t][j][k] * nII[t, j, k] for t in V for j in W for k in K)
            + pulp.lpSum(h[j][s] * v[i, j, s] for i in C for j in W for s in S))


def resolver_robusto(datos, criterio, z_optimo):
    """criterio: 'esperado' o 'arrepentimiento'. Devuelve (diseño, costos por escenario, estado, segundos, cota)."""
    W, S = datos["W"], datos["S"]
    m = pulp.LpProblem(f"Robusto_{criterio}", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", [(j, s) for j in W for s in S], cat=pulp.LpBinary)
    for j in W:
        m += pulp.lpSum(x[j, s] for s in S) <= 1, f"tamano_{j}"
    for j, s_fijo in datos["fijos"].items():
        m += x[j, s_fijo] == 1, f"fijo_{j}"
    fijo = pulp.lpSum(datos["g"][j][s] * x[j, s] for j in W for s in S)

    costo = {}
    for n, lam in enumerate(ESCENARIOS):
        costo[lam] = fijo + agregar_escenario(m, escalar(datos, lam), x, f"_e{n}")

    if criterio == "esperado":
        m += pulp.lpSum(costo.values()) * (1 / len(ESCENARIOS)), "Costo_esperado"
    else:
        r = pulp.LpVariable("arrepentimiento_max", lowBound=0)
        for lam in ESCENARIOS:
            m += costo[lam] - z_optimo[lam] <= r * z_optimo[lam], f"arrepentimiento_{lam}"
        m += r, "Arrepentimiento_maximo"

    estado, segundos, cota = resolver(m, limite_segundos=LIMITE_SEGUNDOS, gap=GAP)
    diseno = {j: next((s for s in S if (x[j, s].value() or 0) > 0.5), None) for j in W}
    return diseno, {lam: pulp.value(c) for lam, c in costo.items()}, estado, segundos, cota


def evaluar(datos, diseno, z_optimo):
    """Costo de un diseño fijo en cada escenario (viajes continuos) y su sobrecosto frente al óptimo."""
    filas = []
    for lam in ESCENARIOS:
        d = escalar(datos, lam)
        m, var, _, _ = construir_modelo(d, viajes_enteros=False)
        m.name = f"eval_{lam}".replace(".", "_")
        for j in d["W"]:
            for s in d["S"]:
                m += var["x"][j, s] == (1 if diseno[j] == s else 0), f"d_{j}_{s}"
        estado, _, _ = resolver(m, limite_segundos=300, gap=1e-5)
        z = pulp.value(m.objective) if estado.startswith("Optimal") else None
        filas.append({"lambda": lam, "estado": estado, "Z": z,
                      "sobrecosto_pct": (z - z_optimo[lam]) / z_optimo[lam] if z else None})
    validos = [f["Z"] for f in filas if f["Z"] is not None]
    return {"escenarios": filas, "factible_en_todos": len(validos) == len(filas),
            "costo_esperado": sum(validos) / len(validos) if len(validos) == len(filas) else None,
            "arrepentimiento_max_pct": max(f["sobrecosto_pct"] for f in filas) if len(validos) == len(filas) else None}


def resolver_entero(datos, diseno, lam=1.0):
    """Costo del diseño con la demanda actual en el modelo entero completo (viajes enteros)."""
    d = escalar(datos, lam)
    m, var, componentes, _ = construir_modelo(d)
    m.name = "Robusto_entero"
    for j in d["W"]:
        for s in d["S"]:
            m += var["x"][j, s] == (1 if diseno[j] == s else 0), f"d_{j}_{s}"
    estado, segundos, cota = resolver(m)
    z = pulp.value(m.objective)
    flujo = {j: sum(var["w"][i, j].value() or 0 for i in d["C"]) for j in d["W"]}
    return {"estado": estado, "Z": z, "cota_inferior": cota, "brecha": (z - cota) / z if cota else None,
            "costos": {k: pulp.value(e) for k, e in componentes.items()}, "flujo": flujo, "segundos": segundos}


def main():
    inicio = time.time()
    datos = cargar_datos()
    sens = json.loads((RESULTADOS_DIR / "sensibilidad_demanda.json").read_text(encoding="utf-8"))
    z_optimo = {e["lambda"]: e["Z_optimo"] for e in sens["malla"]}
    ciudad = {j: c["ciudad"] for j, c in datos["candidatos"].items()}
    propuesto = {j: sens["diseno_propuesto"][ciudad[j]] for j in datos["W"]}

    resultados = {}
    for criterio in ("esperado", "arrepentimiento"):
        diseno, _, estado, segundos, cota = resolver_robusto(datos, criterio, z_optimo)
        evaluacion = evaluar(datos, diseno, z_optimo)
        resultados[criterio] = {"diseno": diseno, "estado": estado, "segundos": segundos, **evaluacion}
        print(f"[{criterio}] {texto_diseno(diseno, datos)} ({estado}, {segundos:.0f} s): costo esperado "
              f"{evaluacion['costo_esperado'] / 1e9:,.2f} mm, arrepentimiento máx. "
              f"{100 * evaluacion['arrepentimiento_max_pct']:.2f} %")

    ref = evaluar(datos, propuesto, z_optimo)
    print(f"[propuesto] {texto_diseno(propuesto, datos)}: factible en todos = {ref['factible_en_todos']}")

    elegido = resultados["arrepentimiento"]["diseno"]
    entero = resolver_entero(datos, elegido)
    print(f"Diseño robusto con demanda actual (modelo entero): Z = {entero['Z'] / 1e9:,.2f} mm ({entero['estado']}, "
          f"brecha {entero['brecha'] or 0:.3%})")

    nombrar = lambda dis: {ciudad[j]: s for j, s in dis.items()}
    salida = {
        "descripcion": "Diseño robusto: una sola configuración de CEDI factible en todos los escenarios de demanda "
                       "(lambda de 0,70 a 1,25), con flujos y viajes que se adaptan a cada escenario.",
        "escenarios_lambda": ESCENARIOS,
        "criterios": {c: {**r, "diseno": nombrar(r["diseno"])} for c, r in resultados.items()},
        "diseno_propuesto_base": {"diseno": nombrar(propuesto), **ref},
        "diseno_robusto_elegido": nombrar(elegido),
        "criterio_elegido": "arrepentimiento",
        "diseno_robusto_demanda_actual_entero": {**entero, "flujo": {ciudad[j]: f for j, f in entero["flujo"].items()}},
        "tiempo_total_segundos": time.time() - inicio,
    }
    SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Resultados en {SALIDA.relative_to(RESULTADOS_DIR.parent)} ({salida['tiempo_total_segundos'] / 60:.1f} min)")


if __name__ == "__main__":
    main()
