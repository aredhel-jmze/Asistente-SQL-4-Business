---
base_model: Qwen/Qwen2.5-Coder-3B-Instruct
library_name: peft
license: apache-2.0
language: [es, en]
tags: [text-to-sql, qlora, lora, agents, sql4business]
---

# SQL4Business — Planificador (adaptador QLoRA)

Adaptador LoRA del agente **Planificador** del sistema de agentes SQL4Business (Generative Artificial
Intelligence 580694, Universidad de Concepción; Deliverable 4). Convierte una pregunta de negocio en un
plan JSON (pasos SQL + operación de composición) sobre una base SQLite sintética de ventas.

- Modelo base: `Qwen/Qwen2.5-Coder-3B-Instruct` (revisión `488639f`), cargado en NF4 de 4 bits.
- Datos: 1155 ejemplos de entrenamiento y 127 de validación, generados por
  `scripts/build_planner_data.py` del repositorio (planes construidos por código y verificados contra un
  valor de referencia; sin preguntas ni cálculos de los conjuntos de evaluación).
- Método: QLoRA (NF4 4-bit base, LoRA adapters); r=16, alpha=32, dropout=0.05,
  módulos q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj; AdamW lr=0.0002, linear warmup 5% + cosine; 2.0 épocas,
  lote efectivo 8, 290 pasos; precisión bf16 autocast, bf16 4-bit compute.
- Hardware: NVIDIA H100 80GB HBM3; tiempo de entrenamiento 16.7 min.
- Pérdida de validación: 0.1581 antes de entrenar, 0.0002 al final.

Código, prompt exacto, datos y evaluación: https://github.com/aredhel-jmze/Asistente-SQL-4-Business

```python
from peft import PeftModel
from transformers import AutoModelForCausalLM
base = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-Coder-3B-Instruct")
model = PeftModel.from_pretrained(base, "vicentesonez/sql4business-planificador-qlora")
```
