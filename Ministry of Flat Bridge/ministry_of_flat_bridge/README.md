# Ministry of Flat Bridge

Addon (extensión de Blender 5.2) que desenvuelve UVs con **UnWrapConsole3.exe** de [Ministry of Flat](https://www.ministryofflat.com).
Sin red ni licencias: escribe un OBJ temporal, ejecuta la herramienta y lee los `vt` de vuelta (mismo esquema que RustRemesher).
**El ejecutable no se incluye** (licencia propia del autor): descárgalo y pon su ruta en Preferencias del addon.

## Uso
1. Preferencias → Add-ons → Ministry of Flat Bridge → ruta de `UnWrapConsole3.exe` (o su carpeta) → *Check Unwrapper*.
   En Linux/macOS hace falta Wine (`wine64` o `wine` en el PATH, o su ruta en las preferencias).
2. Objeto(s) en Object Mode, `N` → pestaña **MoF** → *Unwrap with Ministry of Flat*. Esc cancela.
3. El resultado va a un UV Map propio (`MoF` por defecto, se crea o se sobrescribe) y queda activo. La geometría no se toca.

## Opciones
- **Fit to 0-1** (activada): la herramienta devuelve el layout en una escala arbitraria (un cubo sale de ~4 unidades de ancho; la GUI hace lo mismo).
  El addon lo escala de forma uniforme (se conserva la densidad de texel) para que quepa en 0-1 (o en N tiles con UDIMs). Desactivada: salida cruda.
  Con *World Scale* nunca se normaliza.
- **Pack Selected Together**: un solo run con todos los seleccionados (en espacio mundo) para que compartan UV. Desactivada: cada objeto por separado.
- **Pack** (casilla del panel): tras el unwrap reempaqueta con el empaquetador nativo de Blender (`uv.pack_islands`, formas cóncavas).
  - *Island Gap (px)*: separación entre islas en píxeles a la *Texture Resolution* (la distancia al borde del tile es la mitad). Medido: es exacta.
  - *Rotation*: None (por defecto; las líneas horizontales/verticales no se dentan), 90 Degrees o Any Angle.
  - *Target Tile*: tile UDIM destino (1001 = 0-1, 1002 = a la derecha, 1011 = primera de la segunda fila). Como la herramienta no soporta UDIMs, aquí sí funcionan.
  - *Keep Overlapping Islands*: las islas superpuestas (p. ej. con Overlap Identical Parts) se empaquetan juntas y siguen superpuestas.
- **% de uso**: tras cada unwrap se muestra en el panel y en el aviso (`73.4% of tile 1001 used, gap 4 px @ 1024`). Se calcula rasterizando las caras
  (hasta 2048 px por tile), así que las islas superpuestas cuentan una sola vez. Mide los tiles que toca el layout.
- **Mark Seams**: sustituye las seams del objeto por los cortes de las islas nuevas.
- Resolution, Separate Hard Edges, Use Normals (envía normales por esquina), Overlap Identical/Mirrored, Pixel Aspect, UDIMs, World Scale + Density: son las opciones de línea de comandos de la herramienta.
- **Timeout** (preferencias, 300 s): si la herramienta se cuelga o se cae bajo Wine (diálogo "Program Error"), se mata y se informa.

## Limitaciones conocidas
- **La densidad de texel no es uniforme entre islas, por diseño**: Ministry of Flat decide automáticamente qué partes necesitan más o menos densidad (dos planos de 1 y 4 unidades salen con áreas ×2,56 en vez de ×16). El empaquetado de Blender solo escala todo por igual y no lo altera.
- La herramienta se cae con geometría degenerada (n-gons no planares, caras de área cero). N-gons válidos, triángulos y quads mezclados funcionan.
- Solo Object Mode; no desenvuelve selecciones parciales.
- **UDIMs no funciona en la herramienta** (v3.7.x): la salida es idéntica con 1, 2, 4 y 8 en una malla de 31 488 caras, con y sin World Scale. No es un fallo del bridge; la opción se deja pero no hace nada.
- Los modificadores no se aplican (se usan los datos del mesh).

## Evaluación: ¿portar la herramienta a Linux?
- El binario es C puro (MSVC), x86-64, un solo hilo, sin AVX, y solo importa `KERNEL32` + la CRT (unas 60 funciones). Por eso funciona bien bajo Wine (probado con Wine 6.0.3; ~0,5 s de arranque, Suzanne subdividida de 31 488 caras en ~9 s).
- **Recompilar es imposible sin el código fuente**, que no es público; solo se licencia comercialmente (license@ministryofflat.com). Descompilar/reimplementar el binario queda descartado.
- Alternativas reales: Wine (lo implementado), pedir al autor un build Linux, o escribir un unwrapper propio (proyecto grande).
- No hay versión Linux nativa conocida.
