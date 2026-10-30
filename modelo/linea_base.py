"""Línea de base: costo de la red actual con los mismos parámetros del modelo, y comparación con el óptimo.

Red actual:
- Los 5 CEDI abiertos con el tamaño de la Tabla 7 del .tex (nivel de servicio 97 %), salvo los CEDI con tamaño
  fijo del modelo (Cali, acoplado a la planta, en XL), que usan el mismo tamaño que en el modelo.
- Cada zona atendida por su CEDI actual (columna AA de Demanda Historica, en demanda.json -> zonas[k].cedi_actual).
- El transporte se paga por viaje con la combinación de vehículos más barata en cada arco (igual que el modelo).

El costo se calcula de dos formas que deben coincidir:
1. Directamente con la función objetivo (T^I + T^II + A_f + A_v) sobre los flujos de la red actual.
2. Resolviendo el mismo modelo de modelo_pulp.py con x e y fijados a la red actual.
"""
import json

import pulp

from modelo_pulp import GAP_RELATIVO, RESULTADOS_DIR, TOLERANCIA, cargar_datos, construir_modelo, extraer_solucion, resolver, validar

# Tabla 7 del .tex: tamaño de cada CEDI al 97 % de nivel de servicio (ciudad -> tamaño)
TAMANOS_TABLA_7 = {"Pereira": "XS", "Medellín": "L", "Cali": "XS", "Bogotá": "XL", "Barranquilla": "XL"}


def red_actual(datos, tamanos=TAMANOS_TABLA_7):
    """Tamaño por CEDI (código) y CEDI actual por zona (código)."""
    codigo = {c["ciudad"]: j for j, c in datos["candidatos"].items()}
    faltan = set(codigo) - set(tamanos)
    if faltan:
        raise ValueError(f"CEDI sin tamaño en la línea de base: {sorted(faltan)}")
    tamano = {codigo[ciudad]: s for ciudad, s in tamanos.items()}
    tamano.update(datos["fijos"])  # mismo tamaño fijo que en el modelo (Cali XL)
    asignacion = {k: codigo[z["cedi_actual"]] for k, z in datos["zonas"].items()}
    return tamano, asignacion


def costo_minimo_vehiculos(posiciones, costos_viaje, q):
    """Costo mínimo de cubrir `posiciones` con viajes enteros (programación dinámica, capacidades en medias posiciones)."""
    if posiciones <= TOLERANCIA:
        return 0.0
    unidades = {t: round(2 * q[t]) for t in costos_viaje}
    necesidad = int(-(-2 * posiciones // 1))  # techo de 2 × posiciones
    costo = [0.0] + [float("inf")] * necesidad
    for u in range(1, necesidad + 1):
        costo[u] = min(c + costo[max(0, u - unidades[t])] for t, c in costos_viaje.items())
    return costo[necesidad]


def costo_directo(datos, tamano, asignacion):
    """Evalúa la función objetivo sobre la red actual sin solver (vehículos óptimos por arco)."""
    C, d, V, q, j0 = datos["C"], datos["d"], datos["V"], datos["q"], datos["j0"]
    flujo = {j: 0.0 for j in datos["W"]}
    arco = {}
    for i in C:
        for k, cantidad in d[i].items():
            j = asignacion[k]
            flujo[j] += cantidad
            arco[(j, k)] = arco.get((j, k), 0.0) + cantidad
    T_I = datos["phi"] * flujo[j0] + sum(
        costo_minimo_vehiculos(flujo[j], {t: datos["cI"][t][j] for t in V}, q) for j in datos["W_P"])
    T_II = sum(costo_minimo_vehiculos(cant, {t: datos["cII"][t][j][k] for t in V}, q) for (j, k), cant in arco.items())
    A_v = sum(datos["h"][j][tamano[j]] * f for j, f in flujo.items())
    A_f = sum(datos["g"][j][s] for j, s in tamano.items())
    excedidos = {j: (f, datos["a"][tamano[j]]) for j, f in flujo.items() if f > datos["a"][tamano[j]] + 1e-6}
    return {"T_I": T_I, "T_II": T_II, "A_f": A_f, "A_v": A_v}, flujo, excedidos


def resolver_red_fija(datos, tamano, asignacion):
    """Mismo modelo de modelo_pulp.py con apertura, tamaño y asignación fijados a la red actual."""
    datos_fijos = {**datos, "fijos": tamano}
    m, variables, componentes, IK = construir_modelo(datos_fijos)
    y = variables["y"]
    for i, k in IK:
        for j in datos["W"]:
            if j != asignacion[k]:
                m += y[i, j, k] == 0, f"actual_{i}_{j}_{k}"
    m.name = "Linea_Base"
    estado, segundos, cota = resolver(m)
    if estado != "Optimal":
        raise RuntimeError(f"La red actual no es factible en el modelo: {estado}")
    sol = extraer_solucion(datos_fijos, variables, componentes, IK, estado, segundos, m, cota)
    errores = validar(datos_fijos, sol, IK)
    if errores:
        raise RuntimeError("La línea de base no cumple las restricciones:\n" + "\n".join(errores[:20]))
    return sol


def comparar(base, optimo, datos):
    nombres = {"T_I": "Transporte fase 1", "T_II": "Transporte fase 2", "A_f": "Almacenamiento fijo",
               "A_v": "Almacenamiento variable"}
    filas = [(nombres[c], base["costos"][c], optimo["costos"][c]) for c in nombres]
    filas.append(("Costo total Z", base["costo_total"], optimo["costo_total"]))

    print(f"\n{'Componente':<26}{'Red actual':>18}{'Óptimo':>18}{'Ahorro':>18}{'%':>8}")
    for nombre, b, o in filas:
        print(f"{nombre:<26}{b:>18,.0f}{o:>18,.0f}{b - o:>18,.0f}{(b - o) / b:>8.1%}")

    print(f"\n{'CEDI':<14}{'Actual':>8}{'Flujo actual':>14}{'Óptimo':>8}{'Flujo óptimo':>14}")
    cedis_o = {c["codigo"]: c for c in optimo["cedis"]}
    for c in base["cedis"]:
        o = cedis_o[c["codigo"]]
        print(f"{c['ciudad']:<14}{c['tamano'] or '-':>8}{c['flujo_posiciones']:>14,.0f}"
              f"{o['tamano'] or 'cerrado':>8}{o['flujo_posiciones']:>14,.0f}")

    reasignadas = [k for k, z in optimo["asignacion_zonas"].items()
                   if next(iter(z["cedis"])) != next(iter(base["asignacion_zonas"][k]["cedis"]))]
    print(f"\nZonas cuyo CEDI principal cambia: {len(reasignadas)} de {len(datos['D'])}")
    return filas, reasignadas


def exportar(base, optimo, filas, reasignadas, datos, variante):
    RESULTADOS_DIR.mkdir(exist_ok=True)
    (RESULTADOS_DIR / "linea_base.json").write_text(
        json.dumps({"tamanos": {datos["candidatos"][c["codigo"]]["ciudad"]: c["tamano"] for c in base["cedis"]},
                    **base, "referencia_tabla_7_cali_xs": variante},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Costos"
    ws.append(["Componente", "Red actual (COP/año)", "Óptimo (COP/año)", "Ahorro (COP/año)", "Ahorro %"])
    for nombre, b, o in filas:
        ws.append([nombre, b, o, b - o, (b - o) / b])
    ws.append([])
    ws.append(["Referencia: Tabla 7 sin ajuste (Cali XS)", variante["costo_total"], optimo["costo_total"],
               variante["costo_total"] - optimo["costo_total"],
               (variante["costo_total"] - optimo["costo_total"]) / variante["costo_total"]])

    ws2 = wb.create_sheet("CEDIs")
    ws2.append(["Código DANE", "Ciudad", "Tamaño actual", "Flujo actual", "Utilización actual",
                "Tamaño óptimo", "Flujo óptimo", "Utilización óptima"])
    cedis_o = {c["codigo"]: c for c in optimo["cedis"]}
    for c in base["cedis"]:
        o = cedis_o[c["codigo"]]
        ws2.append([c["codigo"], c["ciudad"], c["tamano"], c["flujo_posiciones"], c["utilizacion"],
                    o["tamano"] or "cerrado", o["flujo_posiciones"], o["utilizacion"]])

    ws3 = wb.create_sheet("Zonas")
    ws3.append(["Código DANE", "Municipio", "Departamento", "Demanda", "CEDI actual", "CEDI óptimo", "Cambia"])
    nombre = lambda j: datos["candidatos"][j]["ciudad"]
    for k, z in base["asignacion_zonas"].items():
        actual, opt = next(iter(z["cedis"])), optimo["asignacion_zonas"][k]["cedis"]
        ws3.append([k, z["municipio"], z["departamento"], z["demanda"], nombre(actual),
                    ", ".join(f"{nombre(j)} {p:.0%}" for j, p in opt.items()), k in reasignadas])

    for hoja in wb.worksheets:
        for celda in hoja[1]:
            celda.font = Font(bold=True)
        hoja.freeze_panes = "A2"
        for col in "ABCDEFGH":
            hoja.column_dimensions[col].width = 22
    wb.save(RESULTADOS_DIR / "comparacion_linea_base.xlsx")


def main():
    datos = cargar_datos()
    tamano, asignacion = red_actual(datos)

    directo, flujo, excedidos = costo_directo(datos, tamano, asignacion)
    if excedidos:
        raise RuntimeError(f"La red actual excede la capacidad a[s] en: {excedidos}")
    base = resolver_red_fija(datos, tamano, asignacion)
    # El cálculo directo es exacto (vehículos óptimos por arco); el modelo fijo se detiene dentro de su brecha,
    # así que debe quedar igual o por encima del directo y a lo sumo una brecha por encima.
    z_directo, z_modelo = sum(directo.values()), base["costo_total"]
    if not (z_directo - 1.0 <= z_modelo <= z_directo * (1 + GAP_RELATIVO) + 1.0):
        raise RuntimeError(f"La línea de base no coincide: directo {z_directo:,.2f} vs modelo {z_modelo:,.2f}")
    base["costos"], base["costo_total"] = directo, z_directo
    print(f"Línea de base: Z = {z_directo:,.0f} COP (cálculo directo exacto; modelo fijo {z_modelo:,.0f}, "
          f"diferencia {(z_modelo - z_directo) / z_directo:.4%})")

    # Referencia: Tabla 7 sin ajuste (Cali XS)
    codigo = {c["ciudad"]: j for j, c in datos["candidatos"].items()}
    tamano_t7 = {codigo[ciudad]: s for ciudad, s in TAMANOS_TABLA_7.items()}
    variante_costos, _, _ = costo_directo(datos, tamano_t7, asignacion)
    variante = {"tamanos": dict(TAMANOS_TABLA_7), "costos": variante_costos,
                "costo_total": sum(variante_costos.values())}

    optimo = json.loads((RESULTADOS_DIR / "solucion.json").read_text(encoding="utf-8"))
    filas, reasignadas = comparar(base, optimo, datos)
    ahorro_v = variante["costo_total"] - optimo["costo_total"]
    print(f"\nReferencia Tabla 7 sin ajuste (Cali XS): Z = {variante['costo_total']:,.0f} COP; "
          f"ahorro del óptimo = {ahorro_v:,.0f} ({ahorro_v / variante['costo_total']:.1%})")
    exportar(base, optimo, filas, reasignadas, datos, variante)
    print(f"\nResultados en {RESULTADOS_DIR.name}/linea_base.json y comparacion_linea_base.xlsx")


if __name__ == "__main__":
    main()
