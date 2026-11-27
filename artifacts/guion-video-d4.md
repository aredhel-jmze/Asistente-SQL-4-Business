# Video del Deliverable 4 (máximo 3 minutos, sin narración)

Lo que pide el enunciado: una grabación de pantalla del sistema con el agente ajustado corriendo sobre
entradas reales, con los mensajes intermedios entre agentes visibles, y el agente base y el ajustado
sobre la misma entrada. Link con acceso abierto. No pide narración.

`scripts/run_agents.py --compare` cumple eso por sí solo. Para cada pregunta corre el sistema con el
Planificador base y después con el Planificador ajustado (adaptador de Hugging Face), e imprime cada
mensaje entre agentes y herramientas.

## Pasos

1. Abrir PowerShell, maximizar la ventana y agrandar la letra (Ctrl + rueda).
2. Antes de grabar:

```
cd C:\Users\56965\Documents\GitHub\Asistente-SQL-4-Business
chcp 65001; $env:PYTHONIOENCODING = "utf-8"
```

3. Empezar a grabar la ventana de PowerShell con la Xbox Game Bar (Win + Alt + R).
4. Ejecutar (H100 en Modal, ~4–5 min, ~US$0,5):

```
modal run scripts/modal_d4.py::demo --ids q12,q11-p
```

5. Cuando termine (aparece `App completed`), detener la grabación con Win + Alt + R. El video queda en
   `Videos\Capturas`.
6. Recortar la espera inicial (carga del contenedor y del modelo) en la app Fotos de Windows para quedar
   en 3 minutos o menos.
7. Subirlo con acceso abierto: YouTube "No listado" o Drive "Cualquier persona con el enlace".
8. Poner el link en el README y en `\VideoURL` de `docs/deliverable4.tex`.

## Qué se ve

- `=== Sistema de agentes — Planificador base ===` y `=== Sistema de agentes — Planificador ajustado ===`,
  primero para q12 y después para q11-p.
- q12: el base pone agosto antes que marzo, el Verificador lo devuelve y el base repite el plan; el
  ajustado ordena los períodos de entrada.
- q11-p: paráfrasis externa; el ajustado pone el total antes que la parte, pero filtra un "semestre" sin
  datos (el caso de falla documentado en el README).
- Las trazas quedan en `results/deliverable4/demo/<id>_base.json` y `<id>_ajustado.json`.
