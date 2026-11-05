# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import BoolProperty, BoolVectorProperty, FloatProperty, IntProperty, PointerProperty, StringProperty


class RustRemesherSettings(bpy.types.PropertyGroup):
    target_quads: IntProperty(name="Cuads objetivo", description="Número de cuads que se quiere en total",
                              default=5000, min=4, soft_max=100000)
    adaptivness: FloatProperty(name="Adaptación a la curvatura",
                               description="0: cuads uniformes. Más alto: cuads más pequeños donde la superficie se curva",
                               default=50.0, min=0.0, max=100.0, subtype='PERCENTAGE')
    auto_hard_edges: BoolProperty(name="Detectar bordes duros",
                                  description="Las aristas con caras a más de 60° reciben un loop", default=True)
    use_sharp_edges: BoolProperty(name="Respetar aristas marcadas",
                                  description="Las aristas marcadas como agudas (Mark Sharp) reciben un loop", default=True)
    use_materials: BoolProperty(name="Materiales guían los loops",
                                description="Los bordes entre materiales distintos reciben un loop", default=False)
    use_vertex_colors: BoolProperty(name="Densidad por color",
                                    description="El brillo del atributo de color fija la densidad: blanco = cuads más pequeños, "
                                                "gris medio = neutro, negro = más grandes", default=False)
    color_attribute: StringProperty(name="Atributo de color", description="Atributo de color del mesh con la densidad")
    symmetry: BoolVectorProperty(name="Simetría", description="Ejes de simetría en el espacio local del objeto (se asume "
                                 "el objeto simétrico: se remalla el lado positivo y se refleja)", size=3, default=(False, False, False))
    guide_strength: FloatProperty(name="Fuerza de las guías", description="Hasta dónde tira una curva guía del flujo de aristas",
                                  default=50.0, min=0.0, max=100.0, subtype='PERCENTAGE')
    use_modifiers: BoolProperty(name="Aplicar modificadores", description="Remallar el resultado de los modificadores en vez de los datos del mesh",
                                default=False)
    shade_smooth: BoolProperty(name="Sombreado suave", description="El resultado se crea con sombreado suave", default=True)
    hide_original: BoolProperty(name="Ocultar original", description="Oculta el objeto original al terminar", default=False)


def register():
    bpy.utils.register_class(RustRemesherSettings)
    bpy.types.Scene.rustremesher = PointerProperty(type=RustRemesherSettings)


def unregister():
    del bpy.types.Scene.rustremesher
    bpy.utils.unregister_class(RustRemesherSettings)
