# blender-addons-claude

Addons de Blender 5.2, un proyecto por carpeta (independientes entre sí).

| Carpeta | Qué es |
|---|---|
| `Godot Level Bridge/` | Extensión que exporta niveles de Blender a Godot 4.7 (geometría, placeholders, colisores primitivos, áreas, paths). Formato en `FORMAT.md`; el lado Godot vive en el juego Elegía (`addons/level_bridge/`). |
| `Godot MultiMesh Scatter/` | Addon + loader de Godot para exportar instancias de Geometry Nodes como MultiMesh. |
| `Sculpt Brush Shelf/` | Extensión con una lista corta y configurable de pinceles de Sculpt en el sidebar. |
| `Inertial Bone/` | Extensión con la vista previa en vivo del hueso inercial de Godot (`InertialBone3D`): masa con muelle e inercia que se desplaza (no rota), mismos controles y valores que en Godot, con interruptor para apagarla. |

Cada proyecto incluye su ZIP instalable, generado con su `build_zip.sh`. Guía para agentes: `CLAUDE.md`.
