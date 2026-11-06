# Godot Level Bridge (Blender 5.2)

Exporta niveles de Blender a un proyecto Godot 4.7 como manifiesto + GLB, que el plugin `addons/level_bridge` del juego reconstruye como escena. Formato: `../FORMAT.md`. Diseño y fases: `../ANALISIS_Level_Bridge.md`.

Estado: **fases B0–B4** — geometría estática (con colores de vértice), placeholders de escenas con props, marcadores, colisores primitivos, áreas, paths, instancias de Geometry Nodes como MultiMesh y assets (colecciones instanciadas) con sus colisiones y luces.

## Instalación

1. `./build_zip.sh` genera `godot_level_bridge-<versión>.zip`.
2. Blender → Edit → Preferences → Get Extensions → menú ⌄ → **Install from Disk** → el ZIP.
3. En las preferencias del addon, **Proyecto Godot**: la carpeta que contiene `project.godot`.

**Actualizar a una versión nueva:** primero desinstalar la anterior (Get Extensions → la extensión → ⌄ → **Uninstall**) y después instalar el ZIP nuevo. Instalar encima puede dejar en uso el código anterior aunque el panel muestre la versión nueva. Para comprobar con qué versión se exportó: en Godot, las mallas de un GLB de 0.2.2 o posterior se llaman `<nivel>_<malla>`, no `gbridge_staging_<malla>`.

## Uso

1. Guarda el `.blend` en `levels/<área>/<sala>/source/<sala>.blend` dentro del proyecto Godot.
2. Pestaña **Godot Level** del sidebar (`N`) → **Nivel**: elige la colección raíz.
3. Organiza con sub-colecciones y dales un **rol por defecto** (panel Colección activa): p. ej. `Colisiones` → Colisor, `Paths` → Path. Las mallas sin rol son geometría estática.
4. Ajusta lo propio de cada objeto en el panel **Objeto** (aparece según su rol):
   - **Placeholder** (un empty): `Escena` = `res://….tscn`. Sus props son las propiedades personalizadas del objeto (Object Properties → Custom Properties); una propiedad que apunta a otro objeto exportado llega a Godot como `NodePath`.
   - **Marcador**: props como metadata del `Marker3D` (p. ej. `entry_id = "default"` para el punto de entrada de la sala).
   - **Geometría estática — Instancias** (scatters de Geometry Nodes): **MultiMesh** (por defecto): solo las instancias, como MultiMesh por trozos de 20 m (ajustable en el panel Nivel); la geometría propia del objeto (el camino guía, etc.) no se exporta. **Realizar**: cada instancia como un nodo de la geometría estática con la malla compartida, más la geometría del objeto. **Ignorar**: solo la geometría del objeto.
   - **Geometría estática — Colores de vértice**: **Datos** (por defecto de la escena): se exportan como `COLOR_0` y el material de Godot no los usa, para máscaras o shaders propios. **Albedo**: el material de Godot los multiplica por el albedo (si otras mallas comparten ese material, esta usa una copia `<material>_vertex_color`). **No exportar**: se quitan.
   - **Colisor / Área**: forma (caja, esfera, cápsula, cilindro, convexa, trimesh) ajustada a la caja del objeto con la escala horneada; capa y máscara con los nombres del `project.godot` (sin marcar = por defecto: colisor en `world`, área que detecta `player`); "Grupo de cuerpo" para que varios colisores compartan cuerpo.
   - **Path**: una curva; Bézier con manijas, poly y NURBS por sus puntos.
   - **Instancia de colección** (rol automático de un empty que instancia una colección): se exporta como instancia del **asset**; ver "Assets" abajo. Sus propiedades personalizadas llegan como metadata de la instancia.
   - **Luz** (rol automático de las luces dentro de un asset; en el nivel hay que ponérselo): punto → `OmniLight3D`, foco → `SpotLight3D`. Energía = W / 100, alcance = "Custom Distance" (o 10 m), sombra = "Shadow". Ajustes finos con propiedades personalizadas con el nombre de la propiedad de Godot (`omni_attenuation`, `light_energy`…).
   - Para copiar ajustes a varios objetos: selecciónalos, clic derecho sobre el campo del activo → **Copy to Selected**.
5. **Mostrar qué se exporta** selecciona todo lo que entra; **Validar** revisa sin exportar; **Exportar nivel** escribe el manifiesto y el GLB.
6. En Godot: selecciona el `LevelBuilder` de la sala → **Rebuild** → **guarda la escena** (Ctrl+S). Los colisores, marcadores y placeholders viven en la escena; la geometría visual en el GLB, que Godot reimporta solo. Si no guardas, el juego usa los colisores anteriores (el builder avisa en la consola).

**Duplicados:** objetos con la misma geometría final comparten la malla también en Godot: Alt+D (aunque tengan modificadores, si dan el mismo resultado) e incluso copias independientes idénticas. Las columnas de la sala de ejemplo son mallas independientes pero idénticas (misma forma y mismo degradado), así que también comparten al exportar con 0.2.2. Esto ahorra memoria pero sigue siendo un `MeshInstance3D` por objeto: para cientos de copias, Geometry Nodes → MultiMesh (fase 3); para un asset con colisiones o luces que se edita una vez, colecciones instanciadas (fase 4).

El export no modifica la malla ni los colores del `.blend` (trabaja sobre copias). Solo asigna el uid de las entidades nuevas.

## Assets (colecciones instanciadas)

Para elementos que se repiten y tienen algo más que malla (una antorcha con su luz, una columna con su colisión):

1. Modela el asset en su propia **colección** (p. ej. `Antorcha`), dentro de una colección `Assets` **fuera de la colección raíz** del nivel; exclúyela del view layer (casilla del outliner) para que no se vea dos veces. Su contenido se exporta igual aunque esté excluida.
2. El **origen** del asset es el "Instance Offset" de la colección (Propiedades de la colección → Instancing). Lo más simple: modelarlo en el origen del mundo.
3. Dentro, los roles funcionan igual que en el nivel: mallas → geometría, un cubo con rol Colisor → colisión, luces → luz, placeholders, marcadores…
4. Colócalo en el nivel con **Add → Collection Instance → Antorcha** (un empty que instancia la colección) tantas veces como quieras, o con **Geometry Nodes** (nodo Collection Info) y la opción de instancias **Escenas** en el objeto que las genera: cada instancia de la colección pasa a ser una instancia de la escena; las demás instancias del objeto siguen como MultiMesh, y la malla propia del objeto (p. ej. el muro) se exporta.
5. **Exportar nivel** escribe el asset en la carpeta de assets (`_kit/bridge_assets/` del área): `Antorcha.asset.json` + `Antorcha.glb`. Para actualizar solo el asset: selecciona una instancia → panel Objeto → **Exportar asset**.
6. En Godot, **Rebuild** de la sala genera `Antorcha.tscn` junto a ellos (solo si el asset cambió) y cada instancia del nivel es una instancia de esa escena: editar el asset en Blender, exportar y hacer Rebuild actualiza todas sus copias, también en las otras salas que lo usan (esas avisan al abrirse que hay que hacer Rebuild). No edites `Antorcha.tscn` a mano: se sobrescribe.

Los objetos con la misma malla (Alt+D) **no** se convierten en assets: siguen en la geometría estática compartiendo la malla.

## Validación

Errores (bloquean el export): instancia sin colección; dos colecciones-asset con el mismo nombre; placeholder sin escena o con una que no existe; escala negativa o no uniforme en un placeholder; escala negativa en un colisor/área; caja sin volumen; forma convexa/trimesh sin malla; área sin capa ni máscara; path que no es curva; prop que apunta a un objeto no exportado.

Avisos: colección-asset dentro de la raíz sin excluir (su contenido se exportaría dos veces); instancia con escala no uniforme; luz de sol o de área (no se exporta); escala no uniforme o negativa en geometría estática; escala ≠ 1 en un placeholder; forma redonda sobre un objeto no redondo; varios atributos de color sin activo. Info: uids duplicados reasignados, splines NURBS.

## Checklist manual (no se puede probar headless)

B0 (verificado en 0.1.1):
- [x] Pestaña "Godot Level", rutas `res://` bajo las carpetas, persistencia de la ruta del proyecto, mensaje al exportar.

B1–B2 (0.2.0–0.3.0):
- [x] Panel Objeto: los campos cambian según el rol y la línea "Se exporta como" es correcta.
- [x] Capa/Máscara muestran los nombres de capa del `project.godot`.
- [x] Panel Validación: lista los problemas y el botón de cada uno selecciona el objeto.
- [x] "Mostrar qué se exporta" selecciona lo esperado y el mensaje cuenta por rol.
- [x] Abrir `levels/catedral/catedral_01/source/catedral_01.blend`, exportar sin cambios: "Nivel sin cambios".
- [x] Mover un colisor, exportar, Rebuild en Godot: el cambio aparece.
- [x] Colores de vértice: llegan a Godot (verificado con la columna en blanco y negro). El damero del suelo de ejemplo no podía verse (caja de 8 vértices) y el degradado original era demasiado sutil. Faltaba poder elegir si el material los usa → modo Datos/Albedo/No exportar (0.3.0).
- [x] Tras Rebuild la pestaña de la escena queda marcada como modificada (asterisco).
- [x] Duplicados con Alt+D: en Godot sus `MeshInstance3D` muestran el mismo recurso Mesh.
- [x] B3 (0.4.0): un scatter de Geometry Nodes aparece en Godot como MultiMesh, sin la geometría guía del objeto; con "Realizar" sale como nodos estáticos.
- [ ] B4 (0.5.0): en la sala de guardado (`catedral_05`) las dos antorchas son instancias de `levels/catedral/_kit/bridge_assets/Antorcha.tscn` y alumbran.
- [ ] B4: crear un asset propio (colección + Collection Instance), exportar, Rebuild: aparece; cambiarlo en Blender (mover una pieza o la luz), exportar, Rebuild: cambian todas las copias.
- [ ] B4: un muro con Geometry Nodes (Collection Info) y opción "Escenas": el muro llega como geometría y cada antorcha como instancia.
- [ ] B4: el botón "Exportar asset" del panel Objeto (con una instancia seleccionada).
- [x] Modo de colores de vértice (0.3.0): un objeto en "Albedo" se ve con sus colores en Godot (material `<material>_vertex_color`); uno en "Datos" (por defecto) conserva su material sin teñir.
