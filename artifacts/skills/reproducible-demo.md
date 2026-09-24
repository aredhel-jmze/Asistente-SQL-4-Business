# Skill: preparar evidencia y reproduccion

## Proposito

Convertir la implementacion y la evaluacion en los tres artefactos exigidos:
repositorio reproducible, PDF tecnico de una pagina y video de maximo tres
minutos.

## Guion minimo del video

Mostrar en una sola ejecucion no editada:

1. La pregunta de negocio y el conjunto de datos usado.
2. El baseline directo sobre esa misma pregunta.
3. La solucion elegida, su plan y los pasos SQL ejecutados.
4. Los resultados intermedios y la composicion.
5. La respuesta final y el criterio de correccion.
6. Un caso de fallo real, preferiblemente con la causa visible.

La entrada mostrada no debe elegirse solo porque produce una respuesta
favorable. La traza debe poder encontrarse en el repositorio.

## Documento tecnico LaTeX

El PDF debe ser vertical, de una sola pagina y compilado desde LaTeX. Reservar
espacio para:

- Diagrama: pregunta -> plan -> SQL/SQLite -> composicion -> verificador ->
  reporte.
- Modelo y version exacta.
- Intervencion y relacion con el fallo del Deliverable 1.
- Estrategias alternativas evaluadas o descartadas.
- Tamano del conjunto y tabla baseline versus solucion.
- Un limite conocido y su caso de fallo.

No reciclar la tabla del Deliverable 1 como si fuera el resultado nuevo. La
tabla nueva debe incluir la solucion ejecutada.

## README y comandos

El README final debe indicar:

- Dependencias y version de Python.
- Como regenerar `business.db` y `questions.json`.
- Como ejecutar baseline y solucion.
- Como reproducir la evaluacion y donde quedan los resultados.
- Hardware requerido, memoria, acceso a Hugging Face si aplica y tiempo
  aproximado.
- Que parte del video corresponde a cada comando o salida.

## Lista de verificacion

- [ ] El enlace del repositorio funciona.
- [ ] Una persona externa puede seguir el README sin adivinar rutas.
- [ ] El video dura 3:00 o menos y tiene acceso abierto.
- [ ] El PDF tiene exactamente una pagina vertical y proviene de LaTeX.
- [ ] El modelo y version aparecen en video, PDF y resultados.
- [ ] Baseline y solucion usan las mismas entradas.
- [ ] Hay un fallo real explicado por una causa concreta.
