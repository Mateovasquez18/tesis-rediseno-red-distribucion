# Rediseño de la red de distribución de una empresa de artículos para el hogar

Caso de estudio de la Maestría en Ingeniería de la Universidad EAFIT (Mateo Vásquez Jaramillo).

Este repositorio contiene los datos, el código y los resultados necesarios para replicar el caso. El problema consiste en elegir qué centros de distribución (CEDI) operar, de qué tamaño y cómo asignar los flujos desde una planta única en Cali hacia 79 municipios. El objetivo es minimizar el costo logístico anual, que incluye el transporte primario y secundario con cuatro tipos de vehículo, el costo fijo de los CEDI y el manejo de materiales.

> Los datos están **normalizados y homologados**: no representan la operación real de la compañía, sino una versión funcional de su red. Los nombres de las categorías y de los artículos no corresponden a productos reales.

## Estructura

```
tesis-rediseno-red-distribucion/
├── datos/                         Archivos Excel de entrada
│   ├── Datos_Demanda_Normalizados.xlsx     demanda 2025, rutas (KM_Zonas), vehículos (Ref_Veh), tamaños de CEDI
│   ├── Costeo_anual_CEDIS.xlsx             costo anual y costo por tonelada de cada CEDI por tamaño
│   └── Datos_Rediseno_Electrodomesticos.xlsx  coordenadas de los municipios
├── modelo/
│   ├── excel_a_json.py            1. lee los Excel y genera los parámetros en modelo/data/*.json
│   ├── modelo_pulp.py             2. modelo MILP (PuLP + CBC): solución óptima
│   ├── linea_base.py              3. costo de la red actual con los mismos parámetros
│   ├── sensibilidad.py            4. sensibilidad del diseño a cambios de la demanda
│   ├── robusto.py                 5. diseño robusto (modelo de dos etapas por escenarios)
│   ├── figuras.py                 6. figuras SVG/PDF del documento
│   ├── estimar_km_faltantes.py    (auxiliar) estimación de km de rutas sin dato
│   └── data/                      parámetros en JSON (d, q, cI, cII, phi, g, h, a)
├── resultados/                    soluciones en JSON y Excel
├── figuras/                       figuras del documento (SVG y PDF)
└── documento/Rediseno_Resultado.tex   documento del caso de estudio (LaTeX)
```

## Requisitos

- Python 3.10 o superior.
- Las dependencias de `requirements.txt`:

```bash
pip install -r requirements.txt
```

Se usa **PuLP 2.9.0**, que incluye el solver CBC. PuLP 4.x cambió su API (`LpVariable.dicts`, `LpStatus`) y no es compatible con este código.

## Cómo replicar el caso

```bash
git clone https://github.com/Mateovasquez18/tesis-rediseno-red-distribucion.git
cd tesis-rediseno-red-distribucion
```

Ejecutar desde la raíz del repositorio, en este orden:

| Paso | Comando | Qué hace | Tiempo aprox. |
|---|---|---|---|
| 1 | `python modelo/excel_a_json.py` | Genera los JSON de parámetros desde los Excel | segundos |
| 2 | `python modelo/modelo_pulp.py` | Resuelve el modelo y escribe `resultados/solucion.*` | 10 min |
| 3 | `python modelo/linea_base.py` | Evalúa la red actual y la compara con el óptimo | 2 min |
| 4 | `python modelo/sensibilidad.py` | Umbrales de demanda en los que cambia el diseño óptimo | 15 min |
| 5 | `python modelo/robusto.py` | Diseño robusto para demandas entre −30 % y +25 % | 11 min |
| 6 | `python modelo/figuras.py` | Regenera las figuras en `figuras/` | segundos |

`modelo_pulp.py` tiene un límite de 600 s por corrida (`LIMITE_SEGUNDOS`) y reporta la brecha de optimalidad que obtiene el solver. Los tiempos de la tabla se midieron en un Apple M2 con 8 hilos.

## Modelo

El modelo se formula en la sección 4 del documento. En resumen:

- **Conjuntos:** categorías $C$ (20), zonas de demanda $D$ (79 municipios, por código DANE), CEDI candidatos $W$ (Barranquilla, Bogotá, Cali, Medellín, Pereira), tamaños $S$ (XS a XL) y tipos de vehículo $V$ (4).
- **Variables:** apertura y tamaño de cada CEDI (binarias), flujos en posiciones de estiba y número de viajes por tipo de vehículo (enteros) en cada fase.
- **Supuestos principales:**
  - La unidad de flujo es la posición de estiba, y la carga no es apilable.
  - El CEDI de Cali está acoplado a la planta: siempre opera con tamaño XL y recibe el producto a un costo de φ = 4.300 COP por posición.
  - La capacidad anual de cada tamaño es el máximo de posiciones diarias × 300 días.

## Resultados principales (demanda 2025)

| Configuración | CEDI | Costo anual (millones de COP) |
|---|---|---:|
| Red actual | Bogotá XL, Medellín L, Barranquilla XL, Cali XL, Pereira XS | 269.758 |
| Diseño de menor costo | Cali XL, Pereira XL, Bogotá M | 241.452 (−10,5 %) |
| Diseño robusto (−30 % a +25 % de demanda) | Cali XL, Medellín XL, Bogotá L | 241.679 (−10,4 %) |

El diseño de menor costo sigue siendo óptimo mientras la demanda varíe entre −2,2 % y +5,0 %. El diseño robusto soporta aumentos de demanda de hasta +28,5 %.

## Notas para la replicación

- **Datos de los resultados incluidos:** los archivos de `resultados/` y `modelo/data/` se generaron con una demanda de **1.176.918 posiciones de estiba**. El Excel de demanda se actualizó después y hoy suma 1.164.287 posiciones, que está dentro del rango en el que el diseño propuesto sigue siendo óptimo. Al ejecutar el paso 1, los JSON se regeneran con los datos vigentes del Excel.
- **Factor de conversión:** el costo de manejo `h` se convierte de COP/t a COP/posición con 0,11 t/posición (`TONELADAS_POR_POSICION` en `excel_a_json.py`).
- **Compilación del documento:** `documento/Rediseno_Resultado.tex` usa `\includesvg` con las figuras en la misma carpeta, que es como se compila en Overleaf. Para compilarlo, copia las figuras `.svg` de `figuras/` junto al `.tex` y compila con `--shell-escape` (requiere Inkscape). El archivo de bibliografía `referencias.bib` no está incluido en el repositorio.
