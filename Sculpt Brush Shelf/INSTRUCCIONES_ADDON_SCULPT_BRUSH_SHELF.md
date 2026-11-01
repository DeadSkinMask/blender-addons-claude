# Blender 5.2 — Addon de paleta filtrada de pinceles de Sculpt Mode

## Objetivo

Crear un addon para Blender 5.2 que permita acceder rápidamente a una lista pequeña y configurable de pinceles de escultura, sin tener que recorrer el catálogo completo ni usar el menú de favoritos.

El addon debe trabajar con los pinceles originales de Blender siempre que sea posible. No debe sobrescribir, renombrar ni modificar destructivamente los assets originales.

## Entorno objetivo

- Blender 5.2.x.
- Python incluido con Blender; no instalar dependencias externas.
- Debe funcionar en modo gráfico y permitir pruebas automatizadas en modo `--background`.
- Código y comentarios en inglés; interfaz visible en inglés o español, preferiblemente español.
- Mantener compatibilidad estricta con la API real de Blender 5.2: verificarla ejecutando pruebas en la instalación objetivo, no asumir nombres de API a partir de documentación de otras versiones.

## Resultado esperado

Un addon instalable, por ejemplo:

```text
sculpt_brush_shelf/
├── __init__.py
├── README.md
└── tests/
    └── test_headless.py
```

Entregar también un ZIP instalable si es posible.

## Requisito funcional principal

Añadir un panel llamado **Sculpt Brush Shelf** visible únicamente en el contexto de Sculpt Mode, preferiblemente en la región de herramientas de la vista 3D o en el panel lateral `N` si eso resulta más estable en Blender 5.2.

El panel muestra exclusivamente la lista configurada por el usuario. Cada elemento debe permitir activar el pincel correspondiente con un clic. La interfaz no debe listar los demás pinceles de Blender.

No asumir que la estantería de assets estándar puede filtrarse mediante una API pública. Primero investigar y probar la API de Blender 5.2. Si no es posible filtrar la estantería nativa de manera fiable, implementar una paleta propia con botones/miniaturas y selección directa del pincel. La paleta propia es un resultado válido y preferible a una solución frágil.

## Lista inicial de pinceles

Preconfigurar nombres o identificadores iniciales aproximados para estos pinceles:

- Clay Strips
- Inflate
- Crease / Hard Stroke (verificar el nombre exacto en Blender 5.2)
- Pinch
- Smooth
- Flatten
- Elastic Grab
- Grab
- Relax Slide / Relax (verificar el nombre exacto)
- Scene Project (o el pincel equivalente que proyecta la superficie sobre geometría de escena; verificar nombre y comportamiento exactos)

IMPORTANTE: no asumir que los nombres mostrados en la interfaz son iguales a los identificadores internos. Descubrir los pinceles disponibles en tiempo de ejecución y construir un mapa de identificadores reales. Algunos nombres de la lista del usuario son descripciones aproximadas, no nombres exactos.

El usuario también usa un pincel parecido a Hard Stroke y Pinch simultáneamente. No adivinar cuál es: permitir añadir cualquier pincel existente desde una lista de búsqueda/autocompletado, y documentar cómo seleccionarlo.

## Selección y resolución de pinceles

Investigar la API real de Blender 5.2 para:

1. Enumerar pinceles disponibles y sus nombres internos.
2. Determinar cómo se selecciona un pincel de Sculpt Mode para la herramienta activa.
3. Comprobar si los pinceles integrados están disponibles como `bpy.data.brushes`, assets o mediante otro mecanismo.
4. Comprobar si es posible reutilizar directamente los pinceles integrados sin duplicarlos.
5. Evitar cambiar accidentalmente de herramienta, modo o tipo de objeto.
6. Manejar con claridad pinceles que no estén disponibles o cuyos nombres hayan cambiado.

El cambio de pincel debe ocurrir desde una acción del usuario. No cambiar el pincel automáticamente al registrar el addon.

## Configuración

La lista debe ser editable desde las preferencias del addon o desde un panel de configuración:

- Añadir pincel por nombre/identificador.
- Eliminar pincel de la lista.
- Reordenar elementos, si es viable.
- Restaurar la lista predeterminada.
- Guardar la configuración entre sesiones.
- Mostrar un aviso si un pincel configurado no existe en esta versión de Blender.

No exigir que el usuario cree una biblioteca de assets ni duplique manualmente cada pincel.

## Interfaz

- Botones compactos y legibles.
- Si se dispone de iconos/miniaturas de forma fiable, mostrarlos; si no, usar botones con nombre e icono estándar.
- La paleta no debe abrir un menú gigante con todos los pinceles.
- Añadir búsqueda únicamente al selector de configuración, no a la paleta de uso diario.
- No crear operadores que pretendan seleccionar un pincel pero no lo hagan.
- Registrar y desregistrar correctamente clases, propiedades y keymaps.
- Evitar añadir hotkeys globales por defecto.

## Plan de investigación antes de implementar

1. Consultar la documentación/API local de Blender 5.2 o ejecutar introspección desde Blender Python.
2. Crear una prueba mínima que enumere los pinceles disponibles y sus propiedades relevantes.
3. Verificar cómo cambia el pincel activo en Sculpt Mode mediante Python.
4. Probar la viabilidad de una estantería nativa filtrada.
5. Elegir la implementación más estable y documentar por qué.

No detenerse en una investigación teórica: ejecutar las pruebas en el Blender instalado.

## Pruebas headless obligatorias

Ejecutar Blender con `--background --python tests/test_headless.py` (adaptar la ruta y los argumentos al entorno real).

Las pruebas deben:

1. Imprimir versión exacta de Blender y versión de Python.
2. Registrar los pinceles disponibles y buscar coincidencias con la lista predeterminada.
3. Crear una escena de prueba con una malla y un objeto activo.
4. Verificar registro del addon y ausencia de excepciones.
5. Verificar que la configuración predeterminada se construye correctamente.
6. Probar que cada identificador resuelto apunta a un pincel válido.
7. Probar el operador de selección cuando la API permita hacerlo en background.
8. Si la selección depende de un contexto de UI que no existe en background, marcar esa prueba explícitamente como `SKIP` y preparar una prueba manual mínima para modo gráfico; no declarar que pasó.
9. Ejecutar `unregister()` y comprobar que no quedan propiedades/clases registradas por el addon.
10. Devolver un código de salida distinto de cero si falla cualquier prueba obligatoria.

No simular ni falsear pruebas que no puedan ejecutarse en modo headless.

## Validación gráfica

Preparar una lista manual breve para Claude Code o el usuario:

- Instalar el ZIP en Blender 5.2.
- Activar el addon.
- Entrar en Sculpt Mode con una malla activa.
- Confirmar que aparecen únicamente los pinceles configurados en la paleta.
- Pulsar varios pinceles y comprobar que el pincel activo cambia realmente.
- Probar un pincel de tipo grab, uno de tipo deformación/superficie y Smooth.
- Añadir y quitar un pincel personalizado.
- Reiniciar Blender y verificar persistencia.
- Confirmar que Object Mode y otros modos no muestran el panel.

## Entregables

- Código completo del addon.
- `README.md` con instalación, uso, limitaciones de la API y resolución de problemas.
- Pruebas headless ejecutables.
- Log de pruebas con versión de Blender y resultados PASS/FAIL/SKIP.
- ZIP instalable.
- Informe final que distinga claramente entre:
  - funciones verificadas en headless,
  - funciones verificadas en interfaz gráfica,
  - limitaciones aún no verificadas.

## Restricciones de calidad

- No inventar funciones de la API.
- No asumir que `bpy.data.brushes` contiene todos los assets integrados sin comprobarlo.
- No alterar archivos internos de Blender.
- No usar temporizadores, llamadas repetidas por frame ni hacks de contexto como solución permanente.
- Usar `context.temp_override(...)` solo si la API real y el caso de uso lo justifican.
- Manejar `poll()` y contexto de forma correcta en operadores.
- Evitar registrar clases repetidamente sin limpiarlas durante desarrollo.
- Mantener el addon pequeño, legible y fácil de depurar.
- Si la API no permite una función concreta, explicar la limitación y entregar la alternativa funcional más cercana.

## Instrucción de ejecución para Claude Code

Trabaja directamente en el directorio del proyecto. Inspecciona la instalación de Blender 5.2 disponible en el equipo, identifica el ejecutable real y ejecuta las pruebas con él. Implementa, prueba, corrige y vuelve a ejecutar hasta obtener un resultado estable. No te limites a escribir el código: verifica el comportamiento real y entrega los artefactos y logs solicitados.

En Windows, localiza `blender.exe` según la instalación existente. En Linux, localiza `blender` con las herramientas del sistema. No hardcodees una ruta específica si puedes detectarla o recibirla como argumento.

Al finalizar, informa con precisión qué funciona, qué pruebas pasaron y qué partes requieren validación gráfica.
