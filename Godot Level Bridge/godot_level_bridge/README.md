# Godot Level Bridge (Blender 5.2)

Exporta niveles de Blender a un proyecto Godot 4.7 como manifiesto + GLB, que el plugin `addons/level_bridge` del juego reconstruye como escena. Formato: `../FORMAT.md`. Diseño y fases: `../ANALISIS_Level_Bridge.md`.

Estado: **fases B0–B2** — geometría estática (con colores de vértice), placeholders de escenas con props, marcadores, colisores primitivos, áreas y paths.

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
   - **Colisor / Área**: forma (caja, esfera, cápsula, cilindro, convexa, trimesh) ajustada a la caja del objeto con la escala horneada; capa y máscara con los nombres del `project.godot` (sin marcar = por defecto: colisor en `world`, área que detecta `player`); "Grupo de cuerpo" para que varios colisores compartan cuerpo.
   - **Path**: una curva; Bézier con manijas, poly y NURBS por sus puntos.
   - Para copiar ajustes a varios objetos: selecciónalos, clic derecho sobre el campo del activo → **Copy to Selected**.
5. **Mostrar qué se exporta** selecciona todo lo que entra; **Validar** revisa sin exportar; **Exportar nivel** escribe el manifiesto y el GLB.
6. En Godot: selecciona el `LevelBuilder` de la sala → **Rebuild** → **guarda la escena** (Ctrl+S). Los colisores, marcadores y placeholders viven en la escena; la geometría visual en el GLB, que Godot reimporta solo. Si no guardas, el juego usa los colisores anteriores (el builder avisa en la consola).

**Duplicados:** objetos con la misma geometría final comparten la malla también en Godot: Alt+D (aunque tengan modificadores, si dan el mismo resultado) e incluso copias independientes idénticas. Las columnas de la sala de ejemplo son mallas independientes pero idénticas (misma forma y mismo degradado), así que también comparten al exportar con 0.2.2. Esto ahorra memoria pero sigue siendo un `MeshInstance3D` por objeto: para cientos de copias, Geometry Nodes → MultiMesh (fase 3); para un asset con colisiones o luces que se edita una vez, colecciones instanciadas (fase 4).

El export no modifica la malla ni los colores del `.blend` (trabaja sobre copias). Solo asigna el uid de las entidades nuevas.

## Validación

Errores (bloquean el export): placeholder sin escena o con una que no existe; escala negativa o no uniforme en un placeholder; escala negativa en un colisor/área; caja sin volumen; forma convexa/trimesh sin malla; área sin capa ni máscara; path que no es curva; prop que apunta a un objeto no exportado.

Avisos: escala no uniforme o negativa en geometría estática; escala ≠ 1 en un placeholder; forma redonda sobre un objeto no redondo; varios atributos de color sin activo. Info: uids duplicados reasignados, splines NURBS.

## Checklist manual (no se puede probar headless)

B0 (verificado en 0.1.1):
- [x] Pestaña "Godot Level", rutas `res://` bajo las carpetas, persistencia de la ruta del proyecto, mensaje al exportar.

B1–B2 (0.2.0–0.2.2):
- [ ] Panel Objeto: los campos cambian según el rol y la línea "Se exporta como" es correcta.
- [ ] Capa/Máscara muestran los nombres de capa del `project.godot`.
- [ ] Panel Validación: lista los problemas y el botón de cada uno selecciona el objeto.
- [ ] "Mostrar qué se exporta" selecciona lo esperado y el mensaje cuenta por rol.
- [ ] Abrir `levels/catedral/catedral_01/source/catedral_01.blend`, exportar sin cambios: "Nivel sin cambios".
- [ ] Mover un colisor, exportar, Rebuild en Godot: el cambio aparece.
- [ ] Colores de vértice visibles en Godot (suelo de la catedral en damero, columnas con degradado).
- [ ] Tras Rebuild la pestaña de la escena queda marcada como modificada (asterisco).
- [ ] Duplicados con Alt+D: en Godot sus `MeshInstance3D` muestran el mismo recurso Mesh.
