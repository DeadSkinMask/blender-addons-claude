# RustRemesher

Remallado a cuads para Blender 5.2 con un engine propio en Rust. Sin red, sin licencias, sin descargas: el addon
escribe la malla, lanza el engine como subproceso y lee el resultado.

```
RustRemesher/
├── engine/        engine en Rust (único crate externo: rayon). Linux y Windows (.exe)
├── addon/         extensión de Blender 5.2 (preferencias con la ruta al engine, panel, operador, guías)
├── dist/          binarios compilados y dist/release/ con los ZIP listos para instalar
├── FORMAT.md      contrato addon ↔ engine (ajustes, progreso, formato binario)
├── release.sh     construye, prueba y empaqueta todo
└── LICENSE        GPL-3.0
```

## Instalar

Elige un ZIP de `dist/release/` (Blender → Preferencias → Add-ons → Instalar desde disco):

- `rustremesher-0.1.0-linux-x64.zip` / `rustremesher-0.1.0-windows-x64.zip`: llevan el engine dentro (`bin/`); no hay
  nada más que configurar.
- `rustremesher-0.1.0.zip`: solo el addon. Pon la ruta del engine (`dist/rustremesher` o `.exe`) en las
  preferencias del addon.

Si actualizas, desinstala la versión anterior antes de instalar la nueva. El uso del addon está en
`addon/rustremesher/README.md`.

## Qué hace

Cuads de calidad con el esquema de mapas de rejilla entera: malla proxy → campo cruzado (curvatura, bordes duros)
→ parametrización con rejilla entera → extracción de la rejilla → relajación y proyección. Opciones: número de
cuads, adaptación a la curvatura, densidad por color de vértice, bordes duros (por ángulo y marcados), materiales
que guían los loops (y se devuelven en las caras), simetría X/Y/Z, guías de curva. Las entradas con pocos polígonos se
redondean antes (subdivisión suave que respeta los pliegues). Una pieza que no se puede remallar se devuelve tal cual
y se avisa.

## Construir y probar

```bash
./release.sh /opt/blender-5.2.3-linux-x64/blender   # todo: engine, tests, wine, addon, ZIP, prueba del ZIP con engine
cd engine && cargo test --release                   # solo el engine (115 tests)
cd addon && ./run_tests.sh /opt/blender-5.2.3-linux-x64/blender   # solo el addon (18 comprobaciones headless)
```

Hace falta `cargo-xwin` para el `.exe` (compilación cruzada con CRT estático) y `wine64` para su prueba de humo.

## Origen y licencia

El algoritmo es una reimplementación en Rust del "QRemesh 2" de SculptForge
(github.com/islandmed/SculptForge, GPL-3.0), así que el proyecto es **GPL-3.0-or-later**. Del Quad Remesher comercial
solo se tomó la idea del protocolo del puente (archivo de ajustes, archivo de progreso, engine como subproceso); no se
usó su código ni sus binarios.

## Límites conocidos

- La salida es una malla nueva: UVs, atributos y shape keys no se transfieren.
- Las guías de curva son suaves (orientan el flujo, no fuerzan un loop exacto sobre la curva).
- La simetría supone el objeto simétrico y trabaja en el espacio local del objeto.
- Un cono de 64 lados con más de ~2000 cuads sale cerrado pero con polos de más (169 a 5000 cuads) y tarda; está
  documentado para arreglarlo en `CLAUDE.md` ("Known problem: cone").
- Superficies abiertas muy grandes (≈20 000 cuads) o con objetivos diminutos (decenas de cuads) pueden salir con
  más polos de la cuenta. Detalles y diagnósticos en `CLAUDE.md`.
