# Sculpt Brush Shelf

Paleta corta y configurable de pinceles de Sculpt Mode para Blender 5.2+.

## Instalación

Preferences → Get Extensions → flecha de la esquina superior derecha → *Install from Disk…* → `sculpt_brush_shelf-1.0.0.zip`.

## Uso

1. Entra en Sculpt Mode con una malla activa.
2. En la barra lateral (`N`), pestaña **Pinceles**, panel **Sculpt Brush Shelf**: un clic en un botón activa ese pincel (el activo aparece resaltado).
3. Configuración en Preferences → Add-ons → Sculpt Brush Shelf: añadir (con búsqueda), quitar, subir/bajar y restaurar la lista predeterminada. Se guarda con las preferencias (activa *Auto-Save Preferences* o guárdalas manualmente).

`Crease Sharp`, `Draw Sharp` y `Relax Pinch` (los parecidos a Hard Stroke + Pinch) están en la lista predeterminada. Cualquier otro se añade con *Añadir pincel* escribiendo parte del nombre.

## Cómo funciona / limitaciones de la API

- En Blender 5.x los pinceles integrados son *assets* (`bpy.data.brushes` está vacío al arrancar). Se activan con `bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS', ...)`, que enlaza el asset sin duplicarlo ni modificar archivos de Blender.
- No hay API pública para filtrar el asset shelf nativo, por eso la paleta es propia.
- Solo se listan los pinceles de la biblioteca Essentials de escultura de malla. Pinceles de bibliotecas de usuario no están soportados.
- Sin miniaturas: botones con nombre e icono estándar.
- Los nombres internos difieren de los habituales: `Inflate/Deflate`, `Pinch/Magnify`, `Flatten/Contrast`, `Crease Sharp`.
- Un pincel configurado que no existe se muestra con aviso (icono de error) y no se puede activar.

## Pruebas

```bash
./run_tests.sh /ruta/a/blender   # genera test_log.txt
```

## Checklist manual (GUI)

- [ ] Instalar el ZIP y activarlo.
- [ ] En Sculpt Mode aparecen solo los 12 pinceles configurados; en Object/Edit Mode no hay panel.
- [ ] Pulsar varios (Grab, Crease Sharp, Smooth) y comprobar que el pincel activo cambia.
- [ ] Añadir y quitar un pincel; reordenar.
- [ ] Reiniciar Blender y comprobar que la lista persiste.
