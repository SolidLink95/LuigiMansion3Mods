"""Extract replacement meshes, normals, UVs, topology, and weights from FBX."""

import json
import sys
from pathlib import Path

import bpy


source = Path(sys.argv[sys.argv.index("--") + 1])
output = Path(sys.argv[sys.argv.index("--") + 2])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=str(source), axis_forward="-Z", axis_up="Y")
result = {}
result["_bone_parents"] = {
    bone.name: bone.parent.name if bone.parent else None
    for armature in bpy.data.armatures
    for bone in armature.bones
}
arguments = sys.argv[sys.argv.index("--") + 1:]
configured_meshes = arguments[2:]
mesh_names = configured_meshes or sorted(
    (
        obj.name
        for obj in bpy.data.objects
        if obj.type == "MESH" and obj.name.startswith("submesh_")
    ),
    key=lambda name: int(name.removeprefix("submesh_")),
)
if not mesh_names:
    raise RuntimeError("FBX has no mesh objects named submesh_<number>")
for object_name in mesh_names:
    obj = bpy.data.objects.get(object_name)
    if obj is None or obj.type != "MESH":
        raise RuntimeError(f"FBX has no mesh object named {object_name}")
    mesh = obj.data
    mesh.calc_loop_triangles()
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        raise RuntimeError(f"{object_name} has no active UV layer")
    uvs = [None] * len(mesh.vertices)
    for loop in mesh.loops:
        uv = list(uv_layer.data[loop.index].uv)
        previous = uvs[loop.vertex_index]
        if previous is not None and any(abs(a - b) > 1e-5 for a, b in zip(previous, uv)):
            raise RuntimeError(f"{object_name} vertex {loop.vertex_index} has split UVs")
        uvs[loop.vertex_index] = uv
    names = [group.name for group in obj.vertex_groups]
    result[object_name] = {
        "positions": [list(vertex.co) for vertex in mesh.vertices],
        "normals": [list(vertex.normal) for vertex in mesh.vertices],
        "uvs": uvs,
        "faces": [list(triangle.vertices) for triangle in mesh.loop_triangles],
        "weights": [
            [[names[item.group], item.weight] for item in vertex.groups if item.weight > 1e-6]
            for vertex in mesh.vertices
        ],
    }
    print(f"extracted {object_name}: {len(mesh.vertices)} vertices, {len(mesh.loop_triangles)} triangles")
output.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
