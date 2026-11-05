# RustRemesher: contrato addon ↔ engine

El addon (Python) lanza `rustremesher -s RetopoSettings.txt` y espera un archivo de progreso. Cualquier cambio en
estos formatos se hace en el engine (`engine/src/`) y en el addon a la vez.

## Línea de comandos

| Comando | Qué hace |
|---|---|
| `rustremesher -s <settings.txt>` | Remalla según el archivo de ajustes |
| `rustremesher --info <malla>` | Estadísticas de una malla |
| `rustremesher --convert <in> <out>` | Convierte `.obj` ↔ binario (depuración). Hacia binario escribe `RRMI0001` |
| `rustremesher --proxy <in> <out> [quads]` | Escribe la malla proxy (triángulos) para un objetivo de cuads (depuración de la fase 2). `RR_TIMING=1` imprime tiempos por fase |
| `rustremesher --field <in> <out> [quads]` | Escribe el campo cruzado como quads finos sobre el proxy y muestra singularidades y calidad del proxy (depuración de la fase 3) |
| `rustremesher --remesh <in> <out> [quads]` | Remalla de verdad (por defecto 5000 cuads) y muestra piezas, polos (valencia 3/5/6+), vértices de borde y desviación de ángulos (fase 4) |
| `rustremesher --version` | Versión |

Una malla con extensión `.obj` es OBJ; cualquier otra extensión es el binario. El engine no tiene licencias ni
acceso a red.

Códigos de salida (y valor negativo del progreso): `1` ajustes, `2` lectura, `3` malla inválida, `4` escritura,
`5` fallo interno.

## Ajustes (`Clave=valor`, valores opcionalmente entre comillas)

Las claves son las que escribe el puente de Quad Remesher; las desconocidas se ignoran. Booleanos: `0/1`,
`True/False`.

| Clave | Por defecto | Notas |
|---|---|---|
| `FileIn`, `FileOut` | obligatorias | Rutas |
| `ProgressFile` | ninguno | Si falta no se escribe progreso |
| `TargetQuadCount` | 5000 | ≥ 1 |
| `CurvatureAdaptivness` | 50 | 0–100, 0 = cuads uniformes. A 100 los tamaños varían entre ~0,5 y ~1,5 del medio: más pequeños donde la superficie se curva |
| `ExactQuadCount` | 0 | 1 = respetar el objetivo con más exactitud |
| `UseVertexColorMap` | 0 | Colores de vértice como densidad: la luminancia manda, gris medio (0,5) = neutro, blanco = cuads 3 veces más pequeños, negro = 3 veces más grandes |
| `UseMaterialIds` | 0 | Bordes de material guían los loops. Si la entrada trae `MATL`, la salida lo trae también (material de la cara de entrada bajo cada cuad), con o sin este ajuste |
| `UseIndexedNormals` | 0 | Lo resuelve el addon: marca como bordes duros (`SHRP`) las aristas con normales divididas. El engine no lo lee |
| `AutoDetectHardEdges` | 1 | Detecta bordes duros por ángulo |
| `SymAxis` | ninguno | Combinación de `X`, `Y`, `Z`. Se recorta la malla por el plano `coordenada = 0` del espacio local, se remalla el lado positivo (con `TargetQuadCount / 2` por eje) y se refleja soldando los vértices del plano. Se supone la entrada simétrica: lo que haya en el lado negativo se sustituye por el reflejo |
| `SymLocal` | 1 | El engine siempre usa el espacio local (el de las posiciones que recibe); para simetría en espacio global el addon debe enviar las posiciones ya transformadas |
| `GuideStrength` | 50 | 0–100: hasta dónde tira una guía, unas `1 + 3·fuerza/100` aristas de cuad a cada lado. Las guías son suaves: el campo se alinea con su tangente y un loop las sigue, sin quedar forzado exactamente sobre ellas. Con simetría se añade el reflejo de cada guía |
| `Threads` | 0 | 0 = todos los hilos |

## Progreso (`ProgressFile`)

Texto, escrito de forma atómica (temporal + renombrado):

1. Valor: `0..1` trabajando, `2` terminado, negativo = error.
2. Texto (una línea).
3. Segundos que el addon puede esperar antes de volver a mirar (solo mientras trabaja).

## Malla binaria

Little-endian. `RRMI0001` es entrada (addon → engine), `RRMO0001` salida (engine → addon); el diseño es el mismo y
el lector acepta ambas. Para cambiar el formato hay que cambiar la magia.

```
magic[8]
u32 V, u32 F, u32 I        vértices, caras, total de índices
f32 positions[V*3]
u32 face_sizes[F]          vértices por cara (≥ 3: tris, quads, n-gons)
u32 indices[I]
u32 section_count
section*:  id[4], u32 byte_len, payload
```

Secciones (todas opcionales; las desconocidas se saltan):

| Id | Payload |
|---|---|
| `COLR` | `f32[V*3]` color por vértice |
| `MATL` | `u32[F]` material por cara (el engine lo devuelve también en la salida) |
| `SHRP` | pares `u32` de vértices: bordes duros |
| `GUID` | `u32 n`; por guía: `u32 closed`, `u32 stroke`, `u32 npoints`, `f32[npoints*3]` |

Los vértices y caras de la salida son nuevos; no hay correspondencia con los de la entrada.
Las posiciones van en el espacio local del objeto, tal como las guarda Blender.
