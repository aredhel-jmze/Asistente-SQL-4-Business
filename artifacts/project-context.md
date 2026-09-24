# Contexto canonico del proyecto

## Identidad

- Proyecto: SQL4Business, asistente de consultas de negocio.
- Curso: Generative Artificial Intelligence (580694), Universidad de
  Concepcion.
- Equipo: Aredhel Jimenez, Bryan Riquelme, Guido Salazar y Vicente Sonez.
- Repositorio: `Asistente-SQL-4-Business`.
- Objetivo de investigacion: responder preguntas de negocio ejecutando el SQL
  necesario y redactar un resumen ejecutivo cuyas cifras sean fieles a los
  resultados ejecutados.

## Tarea que no se debe simplificar

La tarea incluye dos casos:

1. Preguntas puntuales, resolubles con una consulta.
2. Preguntas combinadas, que requieren planificar y ejecutar varias consultas,
   relacionar sus resultados y realizar una operacion como crecimiento,
   participacion, comparacion o filtrado seguido de ranking.

Una salida correcta requiere consultas necesarias y suficientes, resultados
correctos y un reporte final sin cifras inventadas ni inconsistentes con la
base de datos. Deliverable 2 debe continuar con esta misma definicion, no
convertir el problema en solo text-to-SQL de una consulta.

## Estado al terminar Deliverable 1

- Base SQLite sintetica reproducible en `data/business.db`.
- Datos generados con semilla fija `42`.
- Periodo: 1 de marzo a 31 de agosto de 2026.
- Tablas: `products`, `sales` e `inventory`.
- Set de evaluacion: 15 preguntas con SQL y resultados de referencia en
  `data/questions.json`.
- Distribucion: 10 preguntas puntuales y 5 combinadas.
- Notebook baseline: `notebooks/baseline_eval.ipynb`.
- La evaluacion usa cuantizacion de 4 bits, generacion determinista y una GPU
  T4 de Colab declarada en Deliverable 1.

## Esquema de datos

```sql
CREATE TABLE products (
  product_id INTEGER PRIMARY KEY,
  name TEXT,
  category TEXT,
  price INTEGER
);

CREATE TABLE sales (
  sale_id INTEGER PRIMARY KEY,
  product_id INTEGER,
  sale_date TEXT,
  quantity INTEGER,
  amount INTEGER
);

CREATE TABLE inventory (
  product_id INTEGER PRIMARY KEY,
  stock INTEGER,
  reorder_point INTEGER
);
```

Hay 10 productos en cuatro categorias: Electronica, Hogar, Ropa y Alimentos.
Las ventas tienen fechas ISO, cantidad y monto entero. La segunda parte del
periodo tiene un sesgo de mayor volumen para producir crecimiento observable.

## Preguntas de referencia

Tipos de composicion presentes en `questions.json`:

- `growth_pct`: comparar dos totales y calcular `(nuevo - anterior) /
  anterior * 100`.
- `compare_equal`: comparar lideres entre dos periodos.
- `share_pct`: calcular la participacion de un subconjunto sobre el total.
- `filter_then_rank`: filtrar por inventario y luego seleccionar el mayor
  ingreso.

Las cinco preguntas combinadas son `q09`, `q10`, `q11`, `q12` y `q14`. La
pregunta `q14` es especialmente importante porque exige combinar la condicion
de stock bajo con el ranking de ingresos; el resultado correcto es
`Zapatillas Urbanas`.

## Baseline medido

Fuente: `results/baseline_comparison.csv` y
`results/baseline_results.json`.

| Modelo | Global | Puntual | Combinada |
|---|---:|---:|---:|
| Qwen/Qwen2.5-Coder-7B-Instruct | 33.3% | 50% | 0% |
| Qwen/Qwen2.5-Coder-3B-Instruct | 46.7% | 70% | 0% |
| meta-llama/Llama-3.1-8B-Instruct | 46.7% | 70% | 0% |

El baseline entrega directamente una o mas consultas SQL a partir del esquema
y la pregunta. El notebook extrae sentencias `SELECT`, las ejecuta en SQLite
y compara los resultados con el ground truth. Los errores observados incluyen
fechas inventadas, categorias en ingles, sintaxis de otros motores SQL,
seleccion incorrecta de columnas y tratar una pregunta combinada como una sola
consulta no composicional.

## Modelos candidatos y decision pendiente

Deliverable 1 propone:

- Qwen2.5-Coder-7B-Instruct: mejor exactitud publicada entre los candidatos
  pequenos, pero mayor costo.
- Qwen2.5-Coder-3B-Instruct: menor tamano y misma familia; en el baseline
  propio empata el mejor resultado global.
- Llama-3.1-8B-Instruct: mayor soporte de comunidad, pero requiere acceso
  gated y no mejora el baseline propio.

Hipotesis de trabajo: comenzar con `Qwen/Qwen2.5-Coder-3B-Instruct` por
economia de parametros, velocidad de iteracion y empate en el mejor resultado
propio. Esto no es aun la decision oficial: Deliverable 2 exige declarar y
defender la seleccion final.

## Direccion tecnica recomendada

La primera solucion debe atacar el fallo observado, no solo cambiar el nombre
del modelo. La intervencion recomendada es una descomposicion controlada:

1. El modelo clasifica la pregunta y propone un plan de pasos SQL.
2. Cada paso se ejecuta contra SQLite con validacion de errores.
3. Un compositor determinista calcula operaciones entre resultados cuando la
   pregunta lo requiere.
4. El reporte se genera a partir de resultados estructurados y se valida que
   cada cifra citada exista en esos resultados.

La implementacion concreta puede usar prompts estructurados, formato JSON,
ejecucion de herramientas y composicion determinista. No se debe afirmar que
una tecnica mejora el baseline hasta medirla sobre el mismo set.

## Restricciones de evidencia

- El sistema debe correr end-to-end sobre el hardware declarado en Deliverable
  1, no ser un mockup ni usar respuestas hardcodeadas.
- Baseline y solucion deben recibir las mismas entradas y evaluarse con el
  mismo criterio.
- Debe existir al menos un caso de fallo real de la solucion, con explicacion
  causal.
- La evidencia debe distinguir hechos medidos, decisiones del equipo e
  hipotesis aun no verificadas.
- No usar datos sinteticos nuevos o un subconjunto mas facil sin declararlo.

## Decisiones confirmadas para Deliverable 2

- Modelo: `Qwen/Qwen2.5-Coder-3B-Instruct`.
- Intervencion: prompting estructurado, recuperacion de esquema relevante,
  herramientas SQLite de solo lectura, reintentos limitados y composicion
  determinista.
- Conjunto: las mismas 15 preguntas, sin agregar preguntas nuevas.
- Interfaz: notebook compatible con Colab y ejecutable desde VS Code para
  inspeccion y pruebas locales.
- Idioma visible: ingles.
- Reporte: generado por el modelo y validado contra resultados estructurados.
- Evaluacion: exactitud SQL, respuesta final y fidelidad del reporte; baseline y
  solucion comparten el criterio de equivalencia por resultados ejecutados.
- Metricas: global, puntual, combinada, tiempo y memoria cuando el hardware lo
  permita.
- Reintentos: maximo dos despues del primer intento.
- Presentacion: PDF tecnico LaTeX de una pagina; el video sera preparado fuera
  del repositorio y publicado con acceso abierto.

La corrida completa del modelo queda pendiente de un runtime GPU T4 de Colab;
no se deben inventar sus metricas en el documento tecnico antes de ejecutar esa
corrida.
