# Inertial Bone (Blender 5.2)

Vista previa **en vivo** del hueso inercial de Godot (`InertialBone3D`, ver el addon `inertial_bone` del repo
`DeadSkinMask/inertial_bone_test`) mientras animas en Blender. Misma simulación, mismos controles y mismos
valores por defecto, así que lo que ajustas aquí se traslada tal cual a Godot.

Un hueso inercial **no rota: se desplaza** en 3 ejes, unido por un muelle a su posición de reposo, y reacciona
con inercia al movimiento del personaje en el espacio global (pensado para pecho y otras masas blandas).

```bash
./run_tests.sh /opt/blender-5.2.3-linux-x64/blender   # construye el ZIP y corre los tests headless (test_log.txt)
./build_zip.sh                                        # solo reconstruye inertial_bone-1.0.0.zip
blender --command extension validate inertial_bone
```

Instalar: *Edit > Preferences > Get Extensions > ▼ > Install from Disk…* y elegir `inertial_bone-1.0.0.zip`.

## Uso

1. Modelo con **un solo hueso por pecho** (`Breast_L`, `Breast_R`), con pesos de skin y sin constraints/IK.
2. Entra en *Pose Mode*, selecciona los huesos y en la barra lateral (`N`) pestaña **Inertial**:
   `Enable` (activa el hueso), `Couple Selected Pair` (los acopla entre sí) y `Copy Settings to Selected`
   (copia los ajustes del hueso activo a los demás seleccionados, útil para igualar izquierdo y derecho).
3. Activa **Preview ON**. Al reproducir la línea de tiempo, los huesos activos se simulan y se ven moverse.
4. Los controles de cada hueso están en *Properties > Bone > Inertial Bone* (activar con la casilla del encabezado).

### Controles (idénticos a Godot)

| Control | Qué hace |
|---|---|
| Frequency (Hz, por eje) | Rigidez/velocidad de oscilación para masa 1. |
| Damping Ratio | 0 rebota siempre, 1 crítico. Actúa sobre la velocidad relativa al ancla. |
| Mass | Frecuencia efectiva = frecuencia / √masa. |
| Damping + / Damping - | Multiplicador de damping hacia +X/+Y/+Z y -X/-Y/-Z. |
| Gravity | Aceleración en mundo (ejes de Blender, Z arriba). Caída estática = g/ω². |
| Max Offset | Offset máximo **maestro** (metros). |
| Factor + / Factor - | Límite de cada dirección = maestro × factor. |
| Progressive | Endurece el muelle cerca del límite. |
| Tilt From Offset | Giro leve (°/cm) según el offset, con tope `Max Tilt`. |
| Coupled With / Coupling | Acopla el hueso con otro del mismo armature. |
| Axes Reference / Character Front | Objeto cuyos ejes definen X/Y/Z, y hacia dónde mira (-Y en Blender por defecto). |

**Ejes de personaje:** X = derecha, Y = arriba, Z = atrás (convención de Godot), para que los vectores se copien
sin convertir. Para un personaje que mira a -Y, la derecha del personaje es -X de Blender. Caminando hacia delante
el pecho se queda atrás y se hunde hacia **+Z**: ahí conviene un factor bajo.

### Apagarlo

**Preview OFF** quita el handler de `frame_change_post` (cero procesamiento), restaura la pose original de los
huesos y descarta el estado. Con el preview apagado el addon no hace nada aunque haya huesos activados. Medido en
el rig de prueba: 0,16 ms por frame con dos huesos; el resto del coste al reproducir es la evaluación de la escena.

## Cómo se comporta

- La simulación avanza con la reproducción **hacia delante** (también con *Frame Step* y unos pocos frames
  saltados). Al **saltar de frame, arrastrar la línea de tiempo o ir hacia atrás** las masas vuelven al reposo
  (no se puede simular hacia atrás). `Reset Simulation` hace lo mismo a mano.
- Solo se escribe `location` y la rotación del hueso; se sobreescribe lo que ese hueso tuviera animado.
- Es una **vista previa**: no hornea keyframes. Se ve en la línea de tiempo, no queda guardado en la acción.
- Las unidades se asumen en metros (se respeta `Unit Scale` para los grados por cm del giro).
- El preview se guarda con el archivo: al abrirlo se vuelve a activar solo si estaba encendido.
