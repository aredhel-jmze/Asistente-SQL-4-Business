# Deliverable 2: lectura verificada

Fuente leida directamente: `docs/Deliverable 2.pdf`, archivo de 2 paginas.
Este documento conserva los requisitos que gobiernan el trabajo siguiente.

## Pagina 1

### Datos administrativos

- Curso: Generative Artificial Intelligence (580694), Spring 2026.
- Fecha limite: 30 de septiembre de 2026 a las 23:59.
- Peso: 20% del proyecto semestral.

### Objetivo

Deliverable 1 definio la tarea y diagnostico por que un modelo pequeno de
pesos abiertos falla cuando recibe prompting directo. Deliverable 2 exige
convertir ese diagnostico en un sistema funcional: comprometerse con uno de
los tres modelos, implementar una primera solucion contra el fallo y mostrarla
funcionando con entradas reales.

### Requisitos tecnicos

1. **Compromiso de modelo.** Elegir uno de los tres candidatos y explicar por
   que se selecciona sobre los otros dos. Si se cambia a otro modelo, el cambio
   debe declararse y defenderse por razones ligadas a la tarea.
2. **Primera solucion.** Implementar una intervencion contra el fallo
   diagnosticado. Las opciones permitidas incluyen estructura de prompt,
   descomposicion, retrieval, uso de herramientas, decoding restringido,
   fine-tuning o combinaciones. El enlace entre intervencion y fallo debe ser
   explicito.
3. **Comparacion con baseline.** Mostrar la solucion frente al prompting
   directo sobre las mismas entradas. Sin baseline no se puede observar una
   mejora.
4. **Sistema funcional.** El pipeline debe correr de extremo a extremo en el
   hardware declarado en Deliverable 1. Mockups, wireframes y salidas
   hardcodeadas no cuentan.
5. **Caso de fallo.** Identificar al menos una entrada donde la solucion aun
   falla o degrada, y mostrarla.

## Pagina 2

### Entregables de forma

- Video de maximo tres minutos con una grabacion de pantalla del sistema
  corriendo. Solo se miran los primeros 3:00 y el enlace debe tener acceso
  abierto.
- Documento tecnico: PDF vertical de una sola pagina, compilado desde LaTeX.
  Debe contener un diagrama del pipeline, modelo y version, estrategias
  alternativas evaluadas, resultados medidos contra el baseline sobre un
  conjunto cuyo tamano se indique y limites conocidos. Un PDF de varias paginas
  o no producido en LaTeX es rechazado.
- Repositorio: el enlace debe funcionar, el codigo debe correr desde el
  repositorio y el README debe explicar como reproducir lo que aparece en el
  video.

### Rubrica

- **Continuidad con Deliverable 1, 10 puntos:** misma tarea y mismo fallo,
  bajo la misma definicion de salida correcta; toda desviacion debe declararse
  y argumentarse.
- **Ejecucion demostrada, 10 puntos:** el video debe mostrar el pipeline
  completo en una entrada no escogida para favorecerlo, con el baseline visible
  en la misma entrada. No vale ocultar la ejecucion ni mostrar resultados no
  trazables al repositorio.
- **Evidencia de mejora, 10 puntos:** resultados sobre un conjunto declarado,
  con el mismo criterio de correccion para baseline y solucion. Un solo ejemplo
  solo ilustra, no demuestra.
- **Lectura de limites, 10 puntos:** el caso de fallo debe ser real y explicar
  por que ocurre; "mas datos" o "mas tiempo" no es una explicacion suficiente.
- **Economia de modelo, 10 puntos:** se favorece el modelo con menos
  parametros, pero solo si el sistema cumple los demas criterios.
- **Repositorio y reproducibilidad, 10 puntos:** el codigo debe correr y el
  README debe permitir entender y reproducir lo visto en el video.

## Implicaciones para este repositorio

- Mantener las 15 preguntas actuales como conjunto minimo comparable mientras
  no se documente otra decision.
- Evaluar por separado las 10 puntuales y las 5 combinadas, ademas del total.
- Conservar el baseline ya medido en `results/` y crear resultados de la
  solucion en archivos separados o claramente versionados.
- Seleccionar el caso de video sin editar la ejecucion y mostrando pregunta,
  baseline, plan, SQL ejecutado, resultados y respuesta final.
- Preparar el documento LaTeX como una pagina vertical, no adaptar el PDF del
  Deliverable 1 sin incluir los elementos nuevos exigidos.
