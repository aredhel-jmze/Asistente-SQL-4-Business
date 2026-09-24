# Skill: evaluar baseline y solucion

## Proposito

Producir evidencia comparable para Deliverable 2 y evitar que una mejora
aparente provenga de cambiar entradas, criterio o normalizacion.

## Conjunto minimo

Usar las mismas 15 preguntas de `data/questions.json`:

- 10 `puntual`.
- 5 `combinada`.
- Reportar global, puntual y combinada.

Si se agrega un conjunto, conservar los 15 casos originales y documentar su
origen, tamano y razon de inclusion.

## Protocolo

1. Fijar modelo, revision/version, hardware, cuantizacion y parametros de
   generacion para cada corrida.
2. Ejecutar el baseline de prompting directo sin cambiar su contrato.
3. Ejecutar la solucion con las mismas preguntas y la misma base.
4. Comparar la salida ejecutada contra `gold_result`, no contra similitud
   textual ni contra el SQL exacto cuando dos SQL equivalentes producen las
   mismas filas.
5. Registrar por caso: id, tipo, salida cruda, SQL, error, resultado,
   correccion y numero de pasos.
6. Calcular exactitud global, puntual y combinada. Mantener los conteos ademas
   de los porcentajes.
7. Inspeccionar manualmente todos los fallos de la solucion y seleccionar al
   menos uno que pueda explicarse causalmente.

## Controles

- Verificar que el periodo de fechas sea marzo-agosto de 2026; las respuestas
  del baseline muestran que fechas inventadas son un fallo recurrente.
- Verificar que las categorias coincidan con el esquema en espanol sin
  traducirlas a `Electronics`, `Home`, etc.
- Verificar que SQLite sea el motor usado para ejecutar ambos sistemas.
- Verificar que una pregunta combinada no se marque correcta por responder solo
  una parte.
- Ejecutar una comprobacion independiente de los resultados dorados antes de
  publicar cifras.

## Salidas esperadas

- CSV comparativo con una fila por modelo o sistema y columnas de exactitud.
- JSON por pregunta con trazas auditables.
- Tabla de errores agrupados por causa: fechas, dialecto SQL, plan incompleto,
  composicion incorrecta, formato o reporte no fiel.

## Criterio de mejora

La afirmacion valida es especifica, por ejemplo: "la solucion subio de X/Y a
Z/W en las 5 preguntas combinadas". No afirmar que el sistema generaliza mas
alla del conjunto medido. Reportar tambien cualquier regresion en las
preguntas puntuales.
