# Asistente SQL4Business

### Asistente de Consultas de Negocio: SQL y Reportes Ejecutivos Fieles a los Datos

Generative Artificial Intelligence (580694), Universidad de Concepción.

**Equipo:** Aredhel Jiménez, Bryan Riquelme, Guido Salazar y Vicente Soñez.

| Entregable | Documento | Reproducción |
|---|---|---|
| Deliverable 1 | [`docs/deliverable1.pdf`](docs/deliverable1.pdf) | [`notebooks/baseline_eval.ipynb`](notebooks/baseline_eval.ipynb) |
| Deliverable 2 | [`docs/deliverable2.pdf`](docs/deliverable2.pdf) · [video](https://drive.google.com/file/d/1ovemshyIkbVYasXr7zaFDXIrdrHN7OH5/view) | [`notebooks/deliverable2_demo.ipynb`](notebooks/deliverable2_demo.ipynb) |

## Definición de la tarea

Se propone un asistente que, dada una pregunta de negocio, decide y ejecuta las consultas
SQL necesarias y redacta un resumen fiel a los resultados obtenidos. La pregunta puede ser
puntual, o combinada: en ese caso hay que identificar, relacionar y ejecutar varias
consultas para razonar cuál es la respuesta correcta. Una salida se considera correcta
cuando las consultas ejecutadas son las necesarias y suficientes para responder la pregunta,
y cuando cada cifra del resumen final es fiel a esos resultados.

Text-to-SQL es una tarea muy estudiada, pero la literatura casi siempre se detiene en la
exactitud de una única consulta. Este proyecto la extiende en dos direcciones poco
exploradas en modelos abiertos pequeños: planificar qué consultas se necesitan para
preguntas que combinan varios datos, y la fidelidad numérica del reporte.

## Datos

- `data/business.db`: base SQLite sintética de ventas e inventario (10 productos, 549
  ventas entre el 1 de marzo y el 31 de agosto de 2026), generada con semilla fija por
  `data/build_db.py`.
- `data/questions.json`: las 15 preguntas oficiales de Deliverable 1 (10 puntuales y 5
  combinadas), con el SQL y el resultado de referencia. No cambiaron. Solo se agregó
  `answer_fields` a q11, porque esa pregunta pide dos conteos y no un porcentaje.
- `data/paraphrases.json`: 12 paráfrasis de preguntas oficiales, 5 puntuales y 7
  combinadas (2 en inglés), con el mismo SQL y la misma respuesta de referencia. Es el
  conjunto externo (held-out): se escribió durante la auditoría y no se usa para diseñar
  el sistema.

Ambos JSON los genera `data/build_questions.py`, que ejecuta el SQL de referencia sobre
`business.db`.

## Sistema (Deliverable 2)

**Modelo:** `Qwen/Qwen2.5-Coder-3B-Instruct`, cuantizado en NF4 de 4 bits con cómputo
bf16 y decodificación greedy, en una GPU T4 de Colab (el hardware declarado en
Deliverable 1). Es el menor de los tres candidatos y empató el mejor baseline propio
(46,7 %). Qwen-7B cuesta más parámetros sin ventaja local, y Llama-3.1-8B es mayor,
requiere acceso restringido y obtuvo el mismo 46,7 %.

**Intervención contra el fallo diagnosticado.** En Deliverable 1, el prompting directo
obtuvo 0/5 en preguntas combinadas. Los errores eran fechas inventadas, categorías
traducidas, sintaxis de otros motores y tratar una pregunta combinada como una sola
consulta. La solución separa planificar de calcular:

```
pregunta ─► esquema enriquecido ─► plan JSON (Qwen 3B) ─► SQL de solo lectura ─► composición ─► reporte (Qwen 3B) ─► verificación
            valores exactos,        pasos + operación      validación; un paso   determinista    idioma de la       cada cifra debe
            rango de fechas, joins  elegida por el modelo  puede leer otro;      (crecimiento,   pregunta           salir de la
                                         ▲                 reparación ≤ 2        participación,                     respuesta
                                         └──── error ◄─────┘                     comparación, filtro+ranking)
```

- `src/sql4business/sql_tools.py`: esquema enriquecido y ejecución de solo lectura
  (conexión `mode=ro`, validación que ignora literales, rechazo de años sin datos).
- `src/sql4business/pipeline.py`: plan, ejecución con referencias entre pasos (como
  CTE), composición, reporte y hasta dos reparaciones con el mensaje de error.
- `src/sql4business/composition.py`: operaciones deterministas; los errores de forma
  activan la reparación.
- `src/sql4business/fidelity.py`: verificación del reporte. Toda cifra debe coincidir con
  la respuesta verificada o con la evidencia (pregunta, SQL, filas) a la precisión en que
  está escrita, y se controla la dirección del cambio y la afirmación de si "cambió".
  Si el modelo no entrega un reporte válido, se muestra un resumen determinista y cuenta
  como fallo.
- `src/sql4business/evaluation.py`: evaluación compartida por el script y el notebook.

## Evaluación

Se comparan tres sistemas sobre las mismas entradas y con los mismos criterios:

| Sistema | Qué es |
|---|---|
| Baseline | Prompt directo de Deliverable 1, literal, con el esquema simple |
| Alternativa | El mismo prompt directo con el esquema enriquecido |
| Solución | El pipeline estructurado |

| Métrica | Criterio |
|---|---|
| `sql` | Criterio de Deliverable 1: cada conjunto de filas de referencia es producido exactamente por alguna consulta ejecutada |
| `answer` | La respuesta final es correcta, sin importar cómo se llegó a ella. Por ejemplo, una sola consulta que devuelve el producto ganador cuenta |
| `report` | (solo solución) el reporte escrito por el modelo pasa la verificación de fidelidad |
| `e2e` | (solo solución) `answer` y `report` correctos a la vez |

### Resultados

Corrida v3: 24-09-2026, Colab T4, commit `d0c33b6`. Detalle por pregunta, trazas y
versiones en [`results/deliverable2_v3.json`](results/deliverable2_v3.json); la tabla del
PDF se genera desde ese archivo.

| Conjunto | Sistema | `sql` | `answer` | `report` | `e2e` |
|---|---|---|---|---|---|
| Oficial (15, in-sample) | Baseline (D1) | 7/15 | 8/15 | — | — |
| | Alternativa (esquema enriquecido) | 8/15 | 10/15 | — | — |
| | **Solución** | **11/15** | **11/15** | 12/15 | **10/15** |
| Paráfrasis (12, held-out) | Baseline (D1) | 3/12 | 5/12 | — | — |
| | Alternativa (esquema enriquecido) | 4/12 | 8/12 | — | — |
| | **Solución** | **8/12** | **10/12** | 11/12 | **10/12** |

- **Preguntas combinadas (`answer`):** en el set oficial, baseline 1/5, alternativa 1/5 y
  solución 2/5; en paráfrasis, 2/7, 4/7 y 5/7.
- **De dónde viene la mejora:** el esquema enriquecido explica casi toda la mejora en
  puntuales (9/10 respuestas oficiales, contra 7/10 de la baseline). La descomposición con
  composición determinista suma las combinadas.
- **Costo:** 16,3 s por pregunta, contra 6,2 s de la baseline, en T4.

### Casos de fallo y límites conocidos

- **q09 (caso de fallo principal):** el planificador filtra con
  `strftime('%Y-%m', sale_date) BETWEEN '2026-03-01' AND '2026-05-31'`. Comparar `'2026-03'`
  con una fecha completa deja fuera marzo y junio, así que el crecimiento calculado es 70,06 %
  en vez de 60,13 %. El SQL es válido, el resultado es un solo número y el reporte es fiel a
  él: ni la validación, ni la reparación, ni el verificador pueden detectarlo, porque
  revisan forma y cifras, no el significado de un predicado.
- **q10:** el modelo vuelve a usar `strftime('%Q')` en los tres intentos pese al mensaje de
  error; los reintentos no corrigen sesgos fuertes del modelo.
- **q03 y q11:** el plan no se limita al primer producto (q03) u omite el total (q11). En
  q09-p el planificador responde con un solo paso y el reportero inventa porcentajes, que el
  verificador rechaza.
- **Verificador:** controla cifras, no su atribución. Acepta "488" presentado como total
  (q11) y un id de producto sacado de un paso (q14-p). Además es conservador: rechaza
  "paso 1" en q13.
- **Validez:** los resultados oficiales son in-sample, y ambos conjuntos son chicos (27
  preguntas sobre datos sintéticos).

## Cómo reproducir

1. **Todo lo del PDF y el video, en Colab:** abrir
   [`notebooks/deliverable2_demo.ipynb`](https://colab.research.google.com/github/aredhel-jmze/Asistente-SQL-4-Business/blob/main/notebooks/deliverable2_demo.ipynb),
   elegir `Runtime > Change runtime type > T4 GPU` y luego `Run all`. El notebook clona este
   repositorio en `REF`, instala `requirements.txt` (versiones fijadas), corre la demo y la
   evaluación completa (unos 20–25 minutos) y escribe `results/deliverable2_v3.json` y
   `docs/deliverable2_results.tex`. No hay que subir archivos ni tener cuenta en Hugging Face.
   La versión versionada del notebook ya incluye las salidas de la corrida v3.
2. **Tests sin GPU** (Python 3.8 o superior, sin dependencias):
   `python -m unittest discover -s tests`.
3. **Scripts con GPU:** `pip install -r requirements.txt` y luego:
   - `python scripts/run_question.py --id q09-p`: una pregunta, baseline y solución sobre
     la misma entrada; también acepta el texto de la pregunta.
   - `python scripts/evaluate.py --tex docs/deliverable2_results.tex`: la evaluación
     completa.
4. **PDF:** `cd docs && pdflatex deliverable2.tex`. La tabla de resultados la incluye
   desde `deliverable2_results.tex`.
5. **Datos:** `cd data && python build_db.py && python build_questions.py` regenera
   exactamente los archivos versionados.

Qué muestra el video: la celda de demo del notebook, que corre la baseline y la solución
sobre la misma pregunta (por defecto `q09-p`, la primera paráfrasis, sin elegirla por su
resultado), y el resumen de la evaluación.

## Estructura del repositorio

```
.
├── data/                 Base sintética, generadores y los dos conjuntos de preguntas
├── docs/                 PDF y fuentes LaTeX de D1 y D2, tabla generada, enunciado de D2
├── notebooks/            baseline_eval.ipynb (D1) y deliverable2_demo.ipynb (D2, Colab)
├── results/              Resultados de D1 y de D2 v1, v2 y v3 (v3 es la versión final)
├── scripts/              evaluate.py (evaluación completa) y run_question.py (una pregunta)
├── src/sql4business/     Código del asistente y de la evaluación
├── tests/                Tests por módulo, sin GPU
└── artifacts/            Contexto de trabajo con IA y auditoría de D2
```

## Historial y desviaciones declaradas

- **Deliverable 1** (`results/baseline_*`): prompting directo con los tres candidatos,
  sobre las 15 preguntas oficiales.

  | Modelo | Puntual | Combinada | Global |
  |---|---|---|---|
  | Qwen2.5-Coder-7B-Instruct | 50 % | 0 % | 33,3 % |
  | Qwen2.5-Coder-3B-Instruct | 70 % | 0 % | 46,7 % |
  | Llama-3.1-8B-Instruct | 70 % | 0 % | 46,7 % |

- **Deliverable 2 v1** (17-09, `results/deliverable2_v1.json`): 12/15 con el criterio SQL.
  La auditoría ([`artifacts/auditoria-deliverable-2.md`](artifacts/auditoria-deliverable-2.md))
  encontró cuatro problemas:
  - el tipo de operación lo fijaba un clasificador con palabras copiadas de las 5 preguntas
    combinadas (0/7 en paráfrasis);
  - la baseline no era la de D1;
  - el criterio penalizaba respuestas correctas de una sola consulta;
  - el validador de reportes no medía fidelidad.
- **Deliverable 2 v2** (24-09, commit `2d99f37`, `results/deliverable2_v2.json`): corrige
  lo anterior. Elimina las reglas específicas de las preguntas, vuelve a la baseline de D1,
  agrega una métrica de respuesta común y un nuevo validador. En esa corrida el reportero
  descartó 5 reportes en texto plano por no ser JSON. En ese archivo el texto del reporte
  está en `raw_report`, porque la clave `report` guarda la métrica.
- **Deliverable 2 v3** (24-09, commit `d0c33b6`, `results/deliverable2_v3.json`, versión
  final): solo cambia el formato de salida del reportero, que acepta texto plano. El SQL y
  las respuestas son idénticos a v2 en las 27 preguntas.
- Las 15 preguntas oficiales se usaron para desarrollar el sistema (in-sample); la
  estimación de generalización es el conjunto de paráfrasis.
- **Cambio de plan respecto de D1:** D1 anunciaba fine-tuning QLoRA para este entregable,
  pero no se aplicó. Se optó por una intervención sin entrenamiento (planificación,
  herramientas y composición determinista), porque el fallo diagnosticado es de
  planificación y cálculo más que de conocimiento de SQL.
- **Idioma:** el documento técnico está en inglés; el reporte del asistente se escribe en
  el idioma de la pregunta.

## Referencias

1. CogniSQL-R1-Zero: Lightweight Reinforced Reasoning for Efficient SQL Generation. arXiv:2507.06013, 2025.
2. JudgeSQL: Reasoning over SQL Candidates with Weighted Consensus Tournament. arXiv:2510.15560, 2025.
3. TinyLLM: Evaluation and Optimization of Small Language Models for Agentic Tasks on Edge Devices. arXiv:2511.22138, 2025.
4. Mahapatra, Roy, Garain. Factual Inconsistency in Data-to-Text Generation Scales Exponentially with LLM Size. arXiv:2502.12372, 2025.
5. Hui, Yang, et al. Qwen2.5-Coder Technical Report. arXiv:2409.12186, 2024.
6. Li et al. Can LLM Already Serve as A Database Interface? A BIg Bench for Large-Scale Database Grounded Text-to-SQLs. NeurIPS 2023 (arXiv:2305.03111).
