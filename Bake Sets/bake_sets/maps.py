# SPDX-License-Identifier: GPL-3.0-or-later
"""Map catalogue: what each map is, how it is produced and what it needs."""

# key -> info. engine: 'CYCLES' = baked from the sources with Cycles (1 sample for the
# flat ones), 'GL' = derived on the GPU from other baked maps (module gpu, no Cycles).
MAP_INFO = {
    'NORMAL':      dict(label="Tangent Normal", suffix="normal", engine='CYCLES', deps=(), srgb=False,
                        desc="Tangent space normal map projected from the high-poly objects"),
    'AO':          dict(label="Ambient Occlusion", suffix="ao", engine='CYCLES', deps=(), srgb=False,
                        desc="Ambient occlusion"),
    'THICKNESS':   dict(label="Thickness", suffix="thickness", engine='CYCLES', deps=(), srgb=False,
                        desc="Thickness seen from inside the surface (white = thick, black = thin)"),
    'ALBEDO':      dict(label="Albedo", suffix="albedo", engine='CYCLES', deps=(), srgb=True,
                        desc="Base Color read directly, so white metals do not come out black"),
    'EMISSION':    dict(label="Emission", suffix="emission", engine='CYCLES', deps=(), srgb=True,
                        desc="Emission Color x Emission Strength read directly (clipped to 0-1 in 8 bit)"),
    'ROUGHNESS':   dict(label="Roughness", suffix="roughness", engine='CYCLES', deps=(), srgb=False,
                        desc="Principled Roughness read directly"),
    'METALLIC':    dict(label="Metallic", suffix="metallic", engine='CYCLES', deps=(), srgb=False,
                        desc="Principled Metallic read directly"),
    'MATERIAL_ID': dict(label="Material ID", suffix="matid", engine='CYCLES', deps=(), srgb=False,
                        desc="One flat colour per material"),
    'ELEMENT_ID':  dict(label="Element ID", suffix="elementid", engine='CYCLES', deps=(), srgb=False,
                        desc="One flat colour per source object"),
    'CURVATURE':   dict(label="Curvature", suffix="curvature", engine='GL', deps=('NORMAL',), srgb=False,
                        desc="Convex/concave from the normal map (0.5 = flat), computed on the GPU"),
    'CAVITY':      dict(label="Cavity", suffix="cavity", engine='GL', deps=('NORMAL',), srgb=False,
                        desc="Darkens only the concave detail of the normal map, computed on the GPU"),
    'DUST':        dict(label="Dust", suffix="dust", engine='GL', deps=('NORMAL',), srgb=False,
                        desc="Mask of surfaces facing up (dot of the world normal with Z), on the GPU"),
    'PAINT_BASE':  dict(label="Paint Base", suffix="paintbase", engine='GL', deps=('NORMAL', 'AO'), srgb=False,
                        desc="Hand-painted style base: mix of the Dust mask and Ambient Occlusion"),
    'ORM':         dict(label="ORM", suffix="orm", engine='GL', deps=('AO', 'ROUGHNESS', 'METALLIC'), srgb=False,
                        desc="AO in R, Roughness in G, Metallic in B"),
}

MAP_ORDER = list(MAP_INFO)  # Cycles maps are listed first, GL maps need them
MAP_ITEMS = [(k, v['label'], v['desc']) for k, v in MAP_INFO.items()]


def resolve(requested):
    """Return (keys to compute in order, keys to save). Dependencies are computed but not saved."""
    needed = set(requested)
    for k in list(needed):
        needed.update(MAP_INFO[k]['deps'])
    return [k for k in MAP_ORDER if k in needed], [k for k in MAP_ORDER if k in requested]
