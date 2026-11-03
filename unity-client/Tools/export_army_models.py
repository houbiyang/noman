"""Run with Blender --background <source.blend> --python this-file -- <output-dir>."""
import bpy
import json
import sys
from pathlib import Path

output = Path(sys.argv[sys.argv.index('--') + 1])
output.mkdir(parents=True, exist_ok=True)
materials = []
parts = []
for material in bpy.data.materials:
    node = next((n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if node:
        materials.append({'name': material.name, 'color': list(node.inputs['Base Color'].default_value),
                          'metallic': node.inputs['Metallic'].default_value,
                          'roughness': node.inputs['Roughness'].default_value})
for key in ['recon', 'infantry', 'support']:
    for part in bpy.data.objects[key].children_recursive:
        if part.type == 'MESH' and part.active_material:
            parts.append({'name': part.name, 'material': part.active_material.name})
    bpy.ops.object.select_all(action='DESELECT')
    root = bpy.data.objects[key]
    original = root.location.copy()
    root.location = (0, 0, 0)
    root.select_set(True)
    for obj in root.children_recursive:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = root
    bpy.context.view_layer.update()
    bpy.ops.export_scene.fbx(filepath=str(output / (key + '.fbx')), use_selection=True,
                            object_types={'EMPTY', 'MESH'}, axis_forward='-Z', axis_up='Y',
                            apply_unit_scale=True, use_mesh_modifiers=True, bake_anim=False,
                            add_leaf_bones=False, path_mode='AUTO')
    root.location = original
(output / 'materials.json').write_text(json.dumps({'materials': materials, 'parts': parts}, indent=2))
print('ARMY_EXPORT_OK', output)
