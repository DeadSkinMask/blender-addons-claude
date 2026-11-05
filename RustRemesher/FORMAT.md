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
| `CurvatureAdaptivness` | 50 | 0–100, 0 = cuads uniformes |
| `ExactQuadCount` | 0 | 1 = respetar el objetivo con más exactitud |
| `UseVertexColorMap` | 0 | Colores de vértice como densidad |
| `UseMaterialIds` | 0 | Bordes de material guían los loops |
| `UseIndexedNormals` | 0 | Normales divididas guían los loops |
| `AutoDetectHardEdges` | 1 | Detecta bordes duros por ángulo |
| `SymAxis` | ninguno | Combinación de `X`, `Y`, `Z` |
| `SymLocal` | 1 | Simetría en espacio local |
| `GuideStrength` | 50 | 0–100, alcance de las guías de curva |
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
