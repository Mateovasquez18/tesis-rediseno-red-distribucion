"""Figuras de la sección de experimentos computacionales (SVG vectorial para el .tex, vía \\includesvg).

Lee resultados/solucion.json y resultados/linea_base.json (raíz del repositorio) y escribe en figuras/:
- costos_componentes.svg: costo anual por componente, red actual vs. solución óptima
- uso_cedis.svg: flujo y capacidad de cada CEDI en ambas redes
- mapa_redes.svg: asignación de municipios a CEDI en ambas redes
"""
import json
from pathlib import Path

import matplotlib
import openpyxl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from estimar_km_faltantes import ARCHIVO_COORDENADAS, HOJA_COORDENADAS  # noqa: E402
from modelo_pulp import RESULTADOS_DIR, cargar_datos  # noqa: E402

FIGURAS_DIR = Path(__file__).resolve().parent.parent / "figuras"

# Paleta categórica de referencia (validada: CVD y visión normal). Color fijo por CEDI, nunca por rango.
COLOR_CEDI = {"Cali": "#2a78d6", "Pereira": "#eb6834", "Bogotá": "#1baf7a", "Medellín": "#eda100",
              "Barranquilla": "#e87ba4"}
GRIS = "#9a9890"
# Desplazamiento (puntos) y alineación de la etiqueta de cada CEDI en el mapa, para que no se superpongan
POSICION_ETIQUETA = {"Barranquilla": (8, 5, "left"), "Medellín": (-8, 4, "right"), "Pereira": (-8, -3, "right"),
                     "Bogotá": (8, -3, "left"), "Cali": (-8, -5, "right")}
TINTA, TINTA_2, TINTA_3 = "#0b0b0b", "#52514e", "#8a8984"
CUADRICULA = "#e4e3df"

plt.rcParams.update({
    "font.family": "sans-serif", "font.size": 9, "axes.edgecolor": TINTA_3, "axes.labelcolor": TINTA_2,
    "xtick.color": TINTA_2, "ytick.color": TINTA_2, "axes.spines.top": False, "axes.spines.right": False,
    "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlecolor": TINTA, "legend.frameon": False,
    "svg.fonttype": "path", "pdf.fonttype": 42,  # texto como trazos: la figura se ve igual en cualquier equipo
})


def guardar(fig, nombre):
    """Guarda la figura en SVG (fuente vectorial) y en PDF (para \\includegraphics, sin depender de Inkscape)."""
    for formato in ("svg", "pdf"):
        fig.savefig(FIGURAS_DIR / f"{nombre}.{formato}")


def miles(x, decimales=0):
    """Formato numérico en español: punto de miles y coma decimal."""
    texto = f"{x:,.{decimales}f}"
    return texto.replace(",", "#").replace(".", ",").replace("#", ".")


def figura_costos(base, optimo):
    componentes = [("T_I", "Transporte fase 1"), ("T_II", "Transporte fase 2"),
                   ("A_f", "Almacenamiento fijo"), ("A_v", "Almacenamiento variable")]
    fig, ax = plt.subplots(figsize=(6.3, 3.0))
    alto = 0.36
    for n, (clave, nombre) in enumerate(componentes):
        for desplazamiento, sol, color in ((-alto / 2 - 0.01, base, GRIS), (alto / 2 + 0.01, optimo, COLOR_CEDI["Cali"])):
            valor = sol["costos"][clave] / 1e9
            ax.barh(n + desplazamiento, valor, height=alto, color=color, edgecolor="white", linewidth=1)
            ax.text(valor + 2, n + desplazamiento, miles(valor), va="center", fontsize=8, color=TINTA_2)
    ax.set_yticks(range(len(componentes)), [c[1] for c in componentes])
    ax.invert_yaxis()
    ax.set_xlabel("Costo anual (miles de millones de COP)")
    ax.xaxis.grid(True, color=CUADRICULA, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_xlim(0, max(max(base["costos"].values()), max(optimo["costos"].values())) / 1e9 * 1.15)
    ahorro = base["costo_total"] - optimo["costo_total"]
    ax.set_title(f"Costo total: {miles(base['costo_total'] / 1e9)} → {miles(optimo['costo_total'] / 1e9)} miles de "
                 f"millones de COP (−{miles(100 * ahorro / base['costo_total'], 1)} %)", loc="left", fontsize=9)
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=GRIS), plt.Rectangle((0, 0), 1, 1, color=COLOR_CEDI["Cali"])],
              labels=["Red actual", "Solución óptima"], loc="lower right")
    fig.tight_layout()
    guardar(fig, "costos_componentes")
    plt.close(fig)


def figura_cedis(base, optimo, capacidad):
    orden = ["Bogotá", "Medellín", "Barranquilla", "Cali", "Pereira"]
    fig, ejes = plt.subplots(1, 2, figsize=(6.3, 3.0), sharey=True, sharex=True)
    for ax, sol, titulo in ((ejes[0], base, "Red actual"), (ejes[1], optimo, "Solución óptima")):
        cedis = {c["ciudad"]: c for c in sol["cedis"]}
        for n, ciudad in enumerate(orden):
            c = cedis[ciudad]
            if c["tamano"]:
                ax.barh(n, capacidad[c["tamano"]] / 1e3, height=0.62, color="white", edgecolor=TINTA_3,
                        linewidth=0.8, linestyle=(0, (2, 2)))
                ax.barh(n, c["flujo_posiciones"] / 1e3, height=0.62, color=COLOR_CEDI[ciudad], edgecolor="white",
                        linewidth=1)
                ax.text(capacidad[c["tamano"]] / 1e3 + 12, n,
                        f"{c['tamano']} · {miles(100 * c['utilizacion'])} %", va="center", fontsize=8, color=TINTA_2)
            else:
                ax.text(8, n, "cerrado", va="center", fontsize=8, color=TINTA_3, style="italic")
        ax.set_title(titulo, loc="left")
        ax.xaxis.grid(True, color=CUADRICULA, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.set_xlim(0, 720)
        ax.set_xlabel("Miles de posiciones de estiba al año")
    ejes[0].set_yticks(range(len(orden)), orden)
    ejes[0].invert_yaxis()
    fig.legend(handles=[plt.Rectangle((0, 0), 1, 1, facecolor="white", edgecolor=TINTA_3, linestyle=(0, (2, 2))),
                        plt.Rectangle((0, 0), 1, 1, color=GRIS)],
               labels=["Capacidad anual del tamaño", "Flujo anual (color del CEDI)"], loc="lower center", ncol=2,
               fontsize=7.5)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    guardar(fig, "uso_cedis")
    plt.close(fig)


def figura_mapa(base, optimo, datos):
    ws = openpyxl.load_workbook(ARCHIVO_COORDENADAS, read_only=True, data_only=True)[HOJA_COORDENADAS]
    coord = {str(f[0]): (f[7], f[6]) for f in ws.iter_rows(min_row=2, values_only=True) if f[0]}  # (lon, lat)
    ciudad = {j: c["ciudad"] for j, c in datos["candidatos"].items()}
    demanda = {k: sum(datos["d"][i].get(k, 0) for i in datos["C"]) for k in datos["D"]}
    escala = 260 / max(demanda.values())

    fig, ejes = plt.subplots(1, 2, figsize=(6.3, 4.6))
    for ax, sol, titulo in ((ejes[0], base, "Red actual"), (ejes[1], optimo, "Solución óptima")):
        abiertos = {c["codigo"] for c in sol["cedis"] if c["abierto"]}
        for k, z in sol["asignacion_zonas"].items():
            for j, participacion in z["cedis"].items():
                (x0, y0), (x1, y1) = coord[j], coord[k]
                ax.plot([x0, x1], [y0, y1], color=COLOR_CEDI[ciudad[j]], linewidth=0.5 + 0.7 * participacion,
                        alpha=0.55, zorder=1)
        for k, z in sol["asignacion_zonas"].items():
            j = next(iter(z["cedis"]))
            ax.scatter(*coord[k], s=8 + escala * demanda[k], color=COLOR_CEDI[ciudad[j]], edgecolor="white",
                       linewidth=0.5, zorder=2)
        for j in datos["W"]:
            x, y = coord[j]
            abierto = j in abiertos
            ax.scatter(x, y, s=90, marker="s", color=COLOR_CEDI[ciudad[j]] if abierto else "white",
                       edgecolor=TINTA if abierto else TINTA_3, linewidth=1.2, zorder=3)
            dx, dy, ha = POSICION_ETIQUETA.get(ciudad[j], (7, 4, "left"))
            ax.annotate(ciudad[j] + ("" if abierto else " (cerrado)"), (x, y), xytext=(dx, dy), ha=ha, va="center",
                        textcoords="offset points", fontsize=7.5, color=TINTA if abierto else TINTA_3, zorder=4,
                        bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.8))
        ax.set_title(titulo, loc="left")
        ax.set_aspect("equal")
        ax.set_xlim(-80, -66.5)
        ax.set_ylim(-4.8, 12.8)
        ax.tick_params(labelsize=7)
        ax.grid(True, color=CUADRICULA, linewidth=0.5)
        ax.set_axisbelow(True)
        ax.set_xlabel("Longitud", fontsize=7.5)
    ejes[0].set_ylabel("Latitud", fontsize=7.5)
    fig.text(0.01, 0.01, "Cuadrados: CEDI. Círculos: municipios, con área proporcional a la demanda y color del CEDI "
             "que los atiende.", fontsize=7, color=TINTA_2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    guardar(fig, "mapa_redes")
    plt.close(fig)


def figura_sensibilidad(sens):
    """Sobrecosto de mantener el diseño propuesto frente al óptimo de cada escenario de demanda."""
    malla = sens["malla"]
    u = sens["umbrales"]
    lim_inf = (u["inferior"]["lambda_ultimo_con_diseno_propuesto"] - 1) * 100
    lim_sup = (u["superior"]["lambda_ultimo_con_diseno_propuesto"] - 1) * 100
    lim_cap = (sens["lambda_maximo_factible_diseno_propuesto"] - 1) * 100
    puntos = [((e["lambda"] - 1) * 100, 100 * e["sobrecosto_pct"]) for e in malla if e["sobrecosto_pct"] is not None]
    # Los umbrales son puntos con sobrecosto 0 (el diseño propuesto sigue siendo óptimo)
    puntos = sorted(puntos + [(lim_inf, 0.0), (lim_sup, 0.0)])

    fig, ax = plt.subplots(figsize=(6.3, 3.1))
    ax.axvspan(lim_inf, lim_sup, color=COLOR_CEDI["Bogotá"], alpha=0.15, linewidth=0)
    ax.text((lim_inf + lim_sup) / 2, 2.45, "Diseño propuesto\nóptimo", ha="center", va="top", fontsize=7.5,
            color=TINTA_2)
    ax.axvspan(lim_cap, 27, color=TINTA_3, alpha=0.12, linewidth=0)
    ax.text((lim_cap + 27) / 2, 2.45, "Excede la\ncapacidad\ndel diseño", ha="center", va="top", fontsize=7.5,
            color=TINTA_2)
    x, y = zip(*puntos)
    ax.plot(x, y, color=COLOR_CEDI["Cali"], linewidth=2, zorder=3)
    ax.scatter(x, y, s=28, color=COLOR_CEDI["Cali"], edgecolor="white", linewidth=1, zorder=4)
    for xi, yi in puntos:
        if yi > 0.05:
            ax.annotate(f"{miles(yi, 1)} %", (xi, yi), xytext=(0, 6), textcoords="offset points", ha="center",
                        fontsize=7, color=TINTA_2)
    ax.axvline(0, color=TINTA_3, linewidth=0.8, linestyle=(0, (2, 2)))
    ax.set_xlim(-32, 27)
    ax.set_ylim(-0.1, 2.6)
    ax.set_xlabel("Variación de la demanda frente a 2025 (%)")
    ax.set_ylabel("Sobrecosto del diseño propuesto (%)")
    ax.yaxis.grid(True, color=CUADRICULA, linewidth=0.6)
    ax.set_axisbelow(True)
    fig.tight_layout()
    guardar(fig, "sensibilidad_demanda")
    plt.close(fig)


def figura_robusto(sens, robusto):
    """Sobrecosto frente al óptimo de cada escenario: diseño propuesto vs. diseño robusto."""
    lim_cap = (sens["lambda_maximo_factible_diseno_propuesto"] - 1) * 100
    propuesto = [((e["lambda"] - 1) * 100, 100 * e["sobrecosto_pct"]) for e in sens["malla"]
                 if e["sobrecosto_pct"] is not None]
    rob = [((e["lambda"] - 1) * 100, max(0.0, 100 * e["sobrecosto_pct"]))
           for e in robusto["criterios"][robusto["criterio_elegido"]]["escenarios"]]

    fig, ax = plt.subplots(figsize=(6.3, 3.1))
    ax.axvspan(lim_cap, 27, color=TINTA_3, alpha=0.12, linewidth=0)
    ax.text((lim_cap + 27) / 2, 3.25, "El diseño\npropuesto\nexcede su\ncapacidad", ha="center", va="top",
            fontsize=7.5, color=TINTA_2)
    for puntos, color, nombre in ((propuesto, COLOR_CEDI["Cali"], "Diseño propuesto (Cali XL, Pereira XL, Bogotá M)"),
                                  (rob, COLOR_CEDI["Pereira"], "Diseño robusto (Cali XL, Medellín XL, Bogotá L)")):
        x, y = zip(*puntos)
        ax.plot(x, y, color=color, linewidth=2, zorder=3, label=nombre)
        ax.scatter(x, y, s=26, color=color, edgecolor="white", linewidth=1, zorder=4)
    ax.axvline(0, color=TINTA_3, linewidth=0.8, linestyle=(0, (2, 2)))
    ax.set_xlim(-32, 27)
    ax.set_ylim(-0.1, 3.4)
    ax.set_xlabel("Variación de la demanda frente a 2025 (%)")
    ax.set_ylabel("Sobrecosto frente al óptimo (%)")
    ax.yaxis.grid(True, color=CUADRICULA, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(loc="upper center", fontsize=7.5, bbox_to_anchor=(0.42, 1.0))
    fig.tight_layout()
    guardar(fig, "sensibilidad_robusto")
    plt.close(fig)


# Rampa secuencial (un solo tono, de claro a oscuro) para el tamaño de los CEDI
COLOR_TAMANO = {"XS": "#dce8f7", "S": "#b4cdee", "M": "#82a9e0", "L": "#4f84cf", "XL": "#2a5fa8"}


def figura_mapa_disenos(sens):
    """Mapa de diseños: CEDI abiertos y su tamaño en cada escenario de demanda, con el sobrecosto del propuesto."""
    import matplotlib.patches as mpatches
    malla = sens["malla"]
    cedis = ["Cali", "Pereira", "Bogotá", "Medellín", "Barranquilla"]
    x = list(range(len(malla)))

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(6.3, 4.4), sharex=True,
                                  gridspec_kw={"height_ratios": [2.3, 1], "hspace": 0.08})
    for col, e in enumerate(malla):
        if e["es_diseno_propuesto"]:
            for eje in (ax, ax2):
                eje.axvspan(col - 0.5, col + 0.5, color=COLOR_CEDI["Bogotá"], alpha=0.14, linewidth=0, zorder=0)
        for fila, ciudad in enumerate(cedis):
            tamano = e["diseno_optimo"].get(ciudad)
            if tamano:
                ax.add_patch(mpatches.FancyBboxPatch((col - 0.42, fila - 0.38), 0.84, 0.76,
                             boxstyle="round,pad=0,rounding_size=0.08", facecolor=COLOR_TAMANO[tamano],
                             edgecolor="white", linewidth=1.2, zorder=2))
                ax.text(col, fila, tamano, ha="center", va="center", fontsize=7.5, zorder=3,
                        color="white" if tamano in ("L", "XL") else TINTA, fontweight="bold")
            else:
                ax.text(col, fila, "·", ha="center", va="center", fontsize=9, color=CUADRICULA, zorder=3)
    ax.set_xlim(-0.6, len(malla) - 0.4)
    ax.set_ylim(len(cedis) - 0.5, -0.5)
    ax.set_yticks(range(len(cedis)), cedis)
    ax.tick_params(axis="both", length=0)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_visible(False)
    ax.legend(handles=[mpatches.Patch(color=COLOR_TAMANO[t], label=t) for t in COLOR_TAMANO],
              loc="lower right", bbox_to_anchor=(1.0, 1.02), ncol=5, fontsize=7, handlelength=1.2,
              columnspacing=0.8, title="Tamaño del CEDI:", title_fontsize=7, alignment="left")
    ax.set_title("Diseño óptimo en cada escenario de demanda", loc="left", fontsize=9, pad=8)
    propuestos = [c for c, e in enumerate(malla) if e["es_diseno_propuesto"]]
    if propuestos:
        ax.text(sum(propuestos) / len(propuestos), len(cedis) - 0.45, "Diseño propuesto", ha="center",
                va="bottom", fontsize=7, color=TINTA_2, style="italic")

    for col, e in enumerate(malla):
        if e["sobrecosto_pct"] is None:
            ax2.text(col, 0.15, "No\nfactible", ha="center", va="bottom", fontsize=6.5, color=TINTA_3)
        elif e["sobrecosto_pct"] > 0.0005:
            v = 100 * e["sobrecosto_pct"]
            ax2.bar(col, v, width=0.6, color=COLOR_CEDI["Cali"], zorder=2)
            ax2.text(col, v + 0.08, f"{miles(v, 1)}", ha="center", va="bottom", fontsize=6.5, color=TINTA_2)
    if propuestos:
        ax2.text(sum(propuestos) / len(propuestos), 0.15, "0 % (óptimo)", ha="center", va="bottom", fontsize=6.5,
                 color=TINTA, fontweight="bold")
    ax2.set_ylim(0, 3.0)
    ax2.set_ylabel("Sobrecosto del\ndiseño propuesto (%)", fontsize=7.5)
    ax2.yaxis.grid(True, color=CUADRICULA, linewidth=0.6)
    ax2.set_axisbelow(True)
    ax2.set_xticks(x, [f"{(e['lambda'] - 1) * 100:+.0f} %".replace("+0 %", "0 %") for e in malla], fontsize=7.5)
    ax2.set_xlabel("Variación de la demanda frente a 2025")
    fig.subplots_adjust(left=0.17, right=0.98, top=0.86, bottom=0.12)
    guardar(fig, "mapa_disenos_sensibilidad")
    plt.close(fig)


def main():
    FIGURAS_DIR.mkdir(exist_ok=True)
    datos = cargar_datos()
    optimo = json.loads((RESULTADOS_DIR / "solucion.json").read_text(encoding="utf-8"))
    base = json.loads((RESULTADOS_DIR / "linea_base.json").read_text(encoding="utf-8"))
    figura_costos(base, optimo)
    figura_cedis(base, optimo, datos["a"])
    figura_mapa(base, optimo, datos)
    archivo_sens = RESULTADOS_DIR / "sensibilidad_demanda.json"
    if archivo_sens.exists():
        sens = json.loads(archivo_sens.read_text(encoding="utf-8"))
        figura_sensibilidad(sens)
        figura_mapa_disenos(sens)
        archivo_rob = RESULTADOS_DIR / "diseno_robusto.json"
        if archivo_rob.exists():
            figura_robusto(sens, json.loads(archivo_rob.read_text(encoding="utf-8")))
    print(f"Figuras en {FIGURAS_DIR}: " + ", ".join(sorted(p.name for p in FIGURAS_DIR.glob("*.*"))))


if __name__ == "__main__":
    main()
