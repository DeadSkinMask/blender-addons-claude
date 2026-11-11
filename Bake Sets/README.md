# Bake Sets

Addon (extensión de Blender 5.2) para hornear mapas PBR desde **varios objetos high poly hacia un solo low poly**, sin depender de sufijos ni prefijos en los nombres. Reemplaza la parte de bake de TexTools.

Panel: vista 3D → `N` → pestaña **Bake**.

## Uso

1. Selecciona el low poly y pulsa **+** en la lista de sets (el nombre del set es el prefijo de los archivos).
2. Selecciona los high poly y pulsa **+** en "High poly objects". Cada set tiene su propia lista; un objeto puede estar en varios sets. Si la lista está vacía se hornea el low poly contra sí mismo.
3. Marca los mapas, ajusta resolución y margen, elige la carpeta de salida y pulsa **Bake Set** (o **Bake All**).

Se escribe `<set>_<mapa>.png` y la imagen queda cargada en el .blend (volver a hornear reemplaza el datablock).

## Mapas

| Mapa | Cómo se calcula |
|---|---|
| Tangent Normal | Cycles, proyectado desde los high poly (Y+ OpenGL o Y- DirectX, opción global) |
| Ambient Occlusion | Cycles (`AO Samples`) |
| Thickness | Cycles con el nodo AO "Inside" (blanco = grueso, negro = delgado) |
| Albedo, Roughness, Metallic | Cycles **EMIT** de 1 sample leyendo directo el Base Color / Roughness / Metallic del Principled (sigue las texturas conectadas). No usa el bake DIFFUSE de Blender, que excluye la componente glossy y deja negros los metales blancos |
| Emission | Cycles EMIT de 1 sample: Emission Color × Emission Strength del Principled (o lo conectado); sin emisión sale negro |
| Material ID, Element ID | Cycles EMIT de 1 sample con un color plano por material / por objeto fuente |
| Bent Normal | Cycles (4 pasadas `DIFFUSE` solo luz directa con un mundo temporal: cielo blanco uniforme y 3 degradados en X, Y, Z; `AO Samples` controla el ruido) + GPU que reconstruye `2·E_eje/E0 − 1`, lo normaliza y lo pasa a espacio tangente del low poly. Es la dirección media del cielo abierto ponderada por coseno, como pide Godot. Cuesta como 4 AO. Usa la misma opción Normal Y que el normal map |
| Curvature, Cavity | **GPU** (módulo `gpu`), derivados del normal map tangente. 0.5 = plano; es el detalle del high poly respecto al low poly |
| Dust | **GPU**: `dot(normal en mundo, +Z)` con umbral y suavidad. La normal se reconstruye del normal map con el marco tangente del low poly |
| Paint Base | **GPU**: base neutra estilo Blizzard/Dota: degradado vertical suave (luz ambiental desde arriba: `mezcla(suelo, 1, 0.5 + 0.5·normal.z)`) multiplicado por la oclusión ambiental. Arriba queda blanco, abajo oscuro (`Paint Ground Level`) y las cavidades se oscurecen con `Paint AO Strength` |
| ORM | **GPU**: AO en R, Roughness en G, Metallic en B |

Los mapas GPU piden por sí solos los que necesitan (por ejemplo Dust hornea el Normal); las dependencias no se guardan si no las marcaste.

Qué usa Cycles y por qué: todo lo que sale de la geometría o del material de los **high poly** necesita proyectar rayos (Blender no tiene otro bake que Cycles). Se hace con 1 sample salvo AO y Thickness. Lo que se puede derivar de lo ya horneado va por GPU.

Bent normal en Godot: asígnalo en `StandardMaterial3D` → Bent Normal (la malla necesita normales y tangentes). Está orientado alrededor de la normal de la malla, no de la del normal map.

## Fondo transparente y limpiar / sobrescribir

- **Transparent Background** (aparece con Albedo): el albedo se guarda con canal alpha, opaco en las islas y su margen y transparente en el resto.
- **Clear Before Bake** (activado por defecto): cada bake parte de una textura vacía. Desactivado, si ya existe `<set>_<mapa>.png` con la misma resolución, se hornea **encima**: solo se reemplazan las islas del low poly (más su margen) y el resto se conserva. Sirve para hornear elementos distintos por separado en una misma textura: crea un set por elemento con el **mismo nombre** (misma textura de salida) y UV en zonas distintas, y desactiva Clear en los siguientes. Aplica a todos los mapas, también a los calculados en GPU. Si el archivo no existe o tiene otra resolución, parte de cero.
- Si activas alpha pero sigues sin limpiar sobre un archivo sin alpha, el fondo anterior se queda opaco.

## UV y normal maps en espacio tangente

El bake usa la **UV activa** (la resaltada) del low poly; el panel muestra su nombre cuando eliges Tangent Normal o Bent Normal. Un normal map tangente solo se ve bien si se decodifica con las tangentes de **esa misma UV**: en el nodo **Normal Map** escribe su nombre en el campo "UV Map" (o marca esa UV como UV de render, el icono de cámara). El nodo UV Map que alimenta la imagen NO basta: solo decide de dónde se leen los píxeles. Si el campo queda vacío con otra UV de render, aparecen parches rectangulares con el sombreado cambiado en las islas giradas. Albedo, ORM y los demás mapas no tienen este problema porque solo leen por coordenada UV.

## Progreso

Al pulsar Bake desde el panel, el bake va por pasos (un mapa por vez): la barra de estado muestra el mapa en curso, el porcentaje y una estimación del tiempo restante, y **Esc** cancela entre pasos limpiando todo lo temporal. La interfaz no responde a otras acciones mientras corre, y cada paso individual bloquea Blender mientras Cycles trabaja, así que con AO/Bent Normal a alta resolución la barra avanza a saltos. Llamado desde Python (`bpy.ops.bakesets.bake()`) es síncrono.

## Garantías

- No modifica tus datos: trabaja sobre copias en una escena temporal (copias de objetos y de materiales, el nodo de imagen y la emisión se añaden a las copias) y lo borra todo al terminar.
- Los objetos pueden estar ocultos o ser hijos: se copian con su matriz de mundo, con modificadores aplicados como en render.

## Límites conocidos

- Solo el tile UV 0-1 (sin UDIM). PNG de 8 bits.
- Element ID distingue objetos, no partes sueltas de una misma malla.
- Los mapas GPU (curvature, cavity, dust, paint base) y el panel no se pueden probar en headless con la GUI; los tests cubren el cálculo, no el dibujo del panel.
- Curvature y Cavity salen del normal map: con un low y un high casi iguales son casi planos (0.5 y 1.0).

## Desarrollo

```bash
cd "Bake Sets"
./run_tests.sh /opt/blender-5.2.3-linux-x64/blender   # construye el ZIP y corre los tests headless
blender --command extension validate bake_sets
```

## Checklist manual (GUI)

- [ ] El panel aparece en `N` → Bake; agregar/quitar sets y high poly funciona.
- [ ] "Add selected as high poly" ignora el low poly y los no-mesh.
- [ ] Las opciones por mapa se muestran solo cuando el mapa está marcado.
- [ ] Hornear con GPU como dispositivo en tu máquina (los tests usan CPU).
- [ ] Durante el bake la barra de estado muestra mapa, % y tiempo restante; Esc cancela.
- [ ] Los sets se guardan y recuperan con el .blend.
