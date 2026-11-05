# RustRemesher (addon de Blender 5.2)

Remalla el objeto activo a cuads con el engine local `rustremesher` (proyecto hermano `../engine`). Sin red, sin
licencias: el addon solo escribe la malla, lanza el engine y lee el resultado. El contrato entre ambos está en
`../../FORMAT.md`.

## Instalación

1. Compila el engine (`RustRemesher/engine/build.sh`) o usa los binarios de `RustRemesher/dist/`
   (`rustremesher` en Linux, `rustremesher.exe` en Windows).
2. Blender → Preferencias → Add-ons → Instalar desde disco → `rustremesher-0.1.0.zip`.
3. En las preferencias del addon pon la **ruta del engine** (el ejecutable o su carpeta) y pulsa *Comprobar engine*.
   Si no la pones, busca `bin/rustremesher` dentro del addon y luego en el `PATH`.

Si actualizas el addon: desinstala el anterior antes de instalar el nuevo (Blender puede seguir con el código viejo).

## Uso

Panel `N` → pestaña **RustRemesher** del 3D View, con un objeto malla activo en modo objeto:

- **Cuads objetivo** y **Adaptación a la curvatura** (0 = uniforme; más alto, cuads más pequeños donde la superficie se curva).
- **Remallar**: trabaja en segundo plano con barra de progreso y texto en la barra de estado; **Esc** cancela. El
  resultado es un objeto nuevo `<nombre>_remesh` con la misma transformación, los mismos huecos de material y
  sombreado suave. El original no se toca (se puede ocultar con *Ocultar original*).
- **Opciones**: detectar bordes duros (caras a más de 60°), respetar aristas marcadas como agudas, materiales que
  guían los loops, aplicar modificadores, simetría X/Y/Z, densidad por color.
- **Simetría**: en el espacio local del objeto. Se asume el objeto simétrico: se remalla el lado positivo y se
  refleja (lo que haya en el lado negativo se descarta).
- **Densidad por color**: elige un atributo de color del mesh (vértice o esquina). Blanco = cuads 3 veces más
  pequeños, gris medio = neutro, negro = 3 veces más grandes.
- **Guías**: *Nueva guía* crea una curva en la colección `RR_Guides` y activa el dibujo sobre la superficie (o
  edita la curva a mano). El flujo de aristas sigue las guías de forma suave; *Fuerza de las guías* fija hasta
  dónde tiran. Con simetría también se usa su reflejo. *Quitar guías* borra la colección.

Normales divididas: el addon no las lee; marca esas aristas como agudas (Mark Sharp) y se respetan como bordes duros.

## Qué se envía y qué no

Posiciones, caras (tris, quads, n-gons), material por cara, aristas agudas, el atributo de color elegido y las
guías (en espacio local del objeto). Los datos del usuario nunca se modifican. La salida no tiene correspondencia
con la entrada: UVs, atributos y shape keys no se transfieren.

## Pruebas

```bash
cd RustRemesher/addon
./run_tests.sh /opt/blender-5.2.3-linux-x64/blender      # headless, contra el engine real de ../dist
./run_gui_test.sh /opt/blender-5.2.3-linux-x64/blender   # opcional: abre una ventana unos segundos (modo modal y Esc)
blender --command extension validate rustremesher
```

Lista manual (no automatizable): el panel se dibuja bien, *Nueva guía* deja el dibujo sobre la superficie activo,
las preferencias conservan la ruta tras reiniciar, la barra de progreso y el texto de la barra de estado se ven.
