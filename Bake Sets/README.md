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
| Curvature, Cavity | **GPU** (módulo `gpu`), derivados del normal map tangente. 0.5 = plano; es el detalle del high poly respecto al low poly |
| Dust | **GPU**: `dot(normal en mundo, +Z)` con umbral y suavidad. La normal se reconstruye del normal map con el marco tangente del low poly |
| Paint Base | **GPU**: mezcla de Dust y AO (`Dust in Paint Base`: 0 = solo AO, 1 = solo dust) |
| ORM | **GPU**: AO en R, Roughness en G, Metallic en B |

Los mapas GPU piden por sí solos los que necesitan (por ejemplo Dust hornea el Normal); las dependencias no se guardan si no las marcaste.

Qué usa Cycles y por qué: todo lo que sale de la geometría o del material de los **high poly** necesita proyectar rayos (Blender no tiene otro bake que Cycles). Se hace con 1 sample salvo AO y Thickness. Lo que se puede derivar de lo ya horneado va por GPU.

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
- [ ] Los sets se guardan y recuperan con el .blend.
