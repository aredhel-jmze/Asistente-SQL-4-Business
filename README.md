# Asistente SQL4Business

### Asistente de Consultas de Negocio: SQL y Reportes Ejecutivos Fieles a los Datos

> Asistente que traduce preguntas de negocio a SQL y genera reportes ejecutivos fieles a los datos, con modelos abiertos ≤8B.

Generative Artificial Intelligence (580694), Spring 2026, Universidad de Concepción.
Deliverable 1.

## Equipo

- Aredhel Jiménez
- Bryan Riquelme
- Guido Salazar
- Vicente Soñez

## Definición de la tarea

Se propone un asistente que, dada una pregunta de negocio, decide y ejecuta
las consultas SQL necesarias, y redacta un resumen fiel a los resultados
obtenidos, ya sea para una consulta puntual o para una consulta combinada,
que requiere identificar, relacionar y ejecutar varias consultas para
razonar cuál es la respuesta correcta. Una salida se consideraría correcta
cuando las consultas ejecutadas son las necesarias y suficientes para
responder la pregunta, y cuando cada cifra del resumen final es fiel a esos
resultados.

Detalle completo en [`docs/deliverable1.pdf`](docs/deliverable1.pdf).

## Estructura del repositorio

```
.
├── docs/
│   ├── deliverable1.pdf         Documento del Entregable 1 (una página)
│   └── deliverable1.tex         Fuente LaTeX
├── data/
│   ├── build_db.py              Genera business.db (ventas e inventario sintéticos)
│   ├── build_questions.py       Genera questions.json (15 preguntas con resultado de referencia)
│   ├── business.db              Base de datos SQLite generada
│   └── questions.json           Set de evaluación con ground truth
├── notebooks/
│   └── baseline_eval.ipynb      Evaluación baseline en Colab (evidencia de falla)
└── results/
    ├── baseline_comparison.csv  Exactitud por modelo y tipo de pregunta
    └── baseline_results.json    Detalle por pregunta, incluye el SQL generado por cada modelo
```

## Cómo reproducir

1. Generar los datos (ya están generados y versionados, pero se pueden
   reconstruir):
   ```
   cd data
   python build_db.py
   python build_questions.py
   ```
2. Abrir `notebooks/baseline_eval.ipynb` en Google Colab (Runtime > Change
   runtime type > T4 GPU), subir `business.db` y `questions.json`, y correr
   todas las celdas.
3. Abrir notebooks/baseline_eval.ipynb en Google Colab (Runtime > Change runtime type > T4 GPU), subir business.db y questions.json.
Llama-3.1-8B-Instruct requiere cuenta en Hugging Face, aceptar su licencia en huggingface.co/meta-llama/Llama-3.1-8B-Instruct, y un token de acceso (se pide en la celda de `notebook_login()`). Qwen2.5-Coder no requiere esto.
4. Correr todas las celdas. El notebook evalúa los tres modelos candidatos en secuencia y guarda `results/baseline_comparison.csv` y `results/baseline_results.json`.

## Estado actual del trabajo

- [x] Definición de la tarea y de qué cuenta como salida correcta
- [x] Diagnóstico de la falla respaldado con literatura (BIRD, JudgeSQL,
      TinyLLM, inconsistencia factual en data-to-text)
- [x] Tres modelos candidatos con benchmarking publicado
- [x] Base de datos sintética de ventas e inventario (business.db)
- [x] Set de evaluación de 15 preguntas con resultado de referencia
      verificado (10 puntuales, 5 combinadas)
- [x] Notebook de evaluación baseline corrido en Colab sobre los tres candidatos
- [ ] Evidencia propia de falla (ver `results/`.): los tres modelos obtuvieron 0% de exactitud en las preguntas combinadas, frente a 50% y 70% en las puntuales
- [ ] Fine-tuning QLoRA sobre el modelo principal (próximo entregable)
- [ ] Etapa de redacción de reporte ejecutivo con verificación de fidelidad
      numérica (próximo entregable)

## Modelos candidatos

| Modelo | Parámetros | EX en BIRD-dev (publicado) |
|---|---|---|
| Qwen2.5-Coder-7B-Instruct | 7B | 50.9% |
| Qwen2.5-Coder-3B-Instruct | 3B | no reportado en la literatura |
| Llama-3.1-8B-Instruct | 8B | 42.0% |

Evidencia propia, sobre el set de 15 preguntas de negocio del equipo (ver results/baseline_comparison.csv), no comparable directamente con BIRD:

| Modelo | Puntual | Combinada | Global |
|---|---|---|
| Qwen2.5-Coder-7B-Instruct	| 50% |	0% | 33.3% |
| Qwen2.5-Coder-3B-Instruct | 70% |	0% | 46.7% |
| Llama-3.1-8B-Instruct	| 70% |	0% | 46.7% |

## Referencias

1. CogniSQL-R1-Zero: Lightweight Reinforced Reasoning for Efficient SQL Generation. arXiv:2507.06013, 2025.
2. JudgeSQL: Reasoning over SQL Candidates with Weighted Consensus Tournament. arXiv:2510.15560, 2025.
3. TinyLLM: Evaluation and Optimization of Small Language Models for Agentic Tasks on Edge Devices. arXiv:2511.22138, 2025.
4. Mahapatra, Roy, Garain. Factual Inconsistency in Data-to-Text Generation Scales Exponentially with LLM Size. arXiv:2502.12372, 2025.
5. Hui, Yang, et al. Qwen2.5-Coder Technical Report. arXiv:2409.12186, 2024.
6. Li et al. Can LLM Already Serve as A Database Interface? A Big Bench for Large-Scale Database Grounded Text-to-SQLs. NeurIPS 2023.
