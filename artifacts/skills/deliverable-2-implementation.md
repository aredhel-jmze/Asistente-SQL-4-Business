# Skill: implementar Deliverable 2

## Proposito

Construir una primera solucion ejecutable que ataque el fallo central del
proyecto: los modelos pequenos no planifican ni resuelven de forma confiable
preguntas combinadas y pueden producir cifras no fieles a SQLite.

## Entradas

- Pregunta de negocio.
- Esquema de `data/business.db`.
- Modelo elegido y su version exacta.
- Contrato de salida y operaciones de `data/questions.json`.

## Procedimiento requerido

1. Cargar el modelo seleccionado de forma reproducible y registrar hardware,
   cuantizacion, tokenizer y parametros de generacion.
2. Pedir una salida estructurada con tipo de pregunta, pasos, SQL por paso y
   operacion de composicion. No aceptar texto libre como contrato interno.
3. Validar que cada paso sea de solo lectura y que use tablas y columnas reales.
4. Ejecutar cada consulta en SQLite y conservar SQL, filas, errores y orden de
   ejecucion.
5. Aplicar la operacion de composicion en codigo determinista. Las operaciones
   `growth_pct`, `compare_equal`, `share_pct` y `filter_then_rank` deben ser
   explicitas y testeables.
6. Generar la respuesta final usando solamente resultados estructurados. Antes
   de mostrarla, verificar que cada cifra citada pueda rastrearse a una fila o
   a una operacion calculada.
7. Guardar trazas suficientes para que el video y los resultados sean auditables
   desde el repositorio.

## Reglas de diseno

- No hardcodear respuestas de `q01` a `q15`.
- No usar una evaluacion distinta para la solucion y el baseline.
- No convertir todas las preguntas en una sola consulta enorme: el objetivo es
  evaluar planificacion y composicion.
- Si el modelo genera una sentencia invalida, tratarla como fallo observable,
  registrar el error y no sustituirla silenciosamente por SQL dorado.
- Mantener la operacion matematica fuera del lenguaje natural cuando sea
  posible; el modelo puede describirla, pero el codigo debe calcularla.

## Criterios de terminado

- Una entrada puntual y una combinada recorren el pipeline completo.
- El sistema funciona en el hardware declarado.
- La seleccion del modelo esta escrita y justificada.
- Hay al menos una traza de exito y una traza de fallo real.
- La solucion se puede ejecutar sin depender de ediciones manuales ocultas.
