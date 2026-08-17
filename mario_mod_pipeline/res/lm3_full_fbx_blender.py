"""Blender bridge for full LM3 mesh/skeleton FBX export and validation."""

import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector


LOCAL_REFERENCE_ARMATURE = (
    Path(__file__).resolve().parent.parent
    / "Mario LM3" / "Mario_to_luigi3.armature.json"
)
REPOSITORY_REFERENCE_ARMATURE = (
    Path(__file__).resolve().parents[4]
    / "tmp" / "ml3" / "Mario LM3" / "Mario_to_luigi3.armature.json"
)
REFERENCE_ARMATURE = (
    LOCAL_REFERENCE_ARMATURE
    if LOCAL_REFERENCE_ARMATURE.is_file()
    else REPOSITORY_REFERENCE_ARMATURE
)


def args():
    return sys.argv[sys.argv.index("--") + 1:]


def export(manifest_path, fbx_path):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    armature_data = bpy.data.armatures.new(f"slot_{manifest['slot']}_skeleton")
    armature = bpy.data.objects.new(armature_data.name, armature_data)
    bpy.context.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    expected_names = {item["name"] for item in manifest["bones"]}
    slot_reference = (
        Path(__file__).resolve().parent.parent
        / "armatures" / f"slot_{manifest['slot']}.armature.json"
    )
    reference_path = slot_reference if slot_reference.is_file() else REFERENCE_ARMATURE
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    if set(reference) != expected_names:
        raise RuntimeError(
            f"reference armature has {len(reference)} bones but manifest has "
            f"{len(expected_names)} matching names"
        )
    edit_bones = []
    for index, item in enumerate(manifest["bones"]):
        placement = reference[item["name"]]
        bone = armature_data.edit_bones.new(item["name"])
        bone.head = Vector(placement["head"])
        bone.tail = Vector(placement["tail"])
        parent = item["parent"]
        expected_parent = None if parent == 65535 else f"bone_{parent}"
        if placement["parent"] != expected_parent:
            raise RuntimeError(
                f"{item['name']} parent differs: reference={placement['parent']} "
                f"manifest={expected_parent}"
            )
        if parent != 65535:
            bone.parent = edit_bones[parent]
        edit_bones.append(bone)
    bpy.ops.object.mode_set(mode="OBJECT")

    objects = []
    for item in manifest["meshes"]:
        vertices = item["vertices"]
        mesh = bpy.data.meshes.new(item["name"])
        mesh.from_pydata([v["position"] for v in vertices], [], item["faces"])
        mesh.update()
        for polygon in mesh.polygons:
            polygon.use_smooth = True
        # FBX stores normals per polygon corner. Set every corner explicitly
        # from the LM3 vertex record so Blender cannot replace them with
        # generated face/vertex normals during export.
        mesh.normals_split_custom_set([
            vertices[loop.vertex_index]["normal"] for loop in mesh.loops
        ])
        uv_layer = mesh.uv_layers.new(name="UVMap")
        for loop in mesh.loops:
            uv_layer.data[loop.index].uv = vertices[loop.vertex_index]["uv"]
        obj = bpy.data.objects.new(item["name"], mesh)
        bpy.context.collection.objects.link(obj)
        texture_name = item.get("diffuse_texture")
        if texture_name:
            texture_path = Path(manifest_path).parent / texture_name
            if texture_path.exists():
                material = bpy.data.materials.new(name=f"mat_{item['name']}")
                material.use_nodes = True
                nodes = material.node_tree.nodes
                image_node = nodes.new("ShaderNodeTexImage")
                image_node.image = bpy.data.images.load(str(texture_path), check_existing=True)
                material.node_tree.links.new(
                    image_node.outputs["Color"], nodes.get("Principled BSDF").inputs["Base Color"]
                )
                mesh.materials.append(material)
        obj.parent = armature
        modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
        modifier.object = armature
        groups = {}
        for vertex_index, vertex in enumerate(vertices):
            for bone_index, weight in vertex["weights"]:
                group = groups.get(bone_index)
                if group is None:
                    group = obj.vertex_groups.new(name=f"bone_{bone_index}")
                    groups[bone_index] = group
                group.add([vertex_index], weight, "REPLACE")
        objects.append(obj)
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.export_scene.fbx(
        filepath=str(fbx_path), use_selection=True, object_types={"MESH", "ARMATURE"},
        use_mesh_modifiers=False, add_leaf_bones=False, bake_anim=False,
        apply_unit_scale=False, axis_forward="-Z", axis_up="Y",
        path_mode="COPY", embed_textures=True,
        mesh_smooth_type="OFF", use_tspace=True,
    )


def validate(fbx_path, report_path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(fbx_path), axis_forward="-Z", axis_up="Y")
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    report = {
        "fbx": str(fbx_path), "mesh_count": len(meshes), "armature_count": len(armatures),
        "bone_count": sum(len(obj.data.bones) for obj in armatures),
        "armature_bounds": [{
            "name": obj.name,
            "minimum": [min((obj.matrix_world @ bone.head_local)[axis] for bone in obj.data.bones) for axis in range(3)],
            "maximum": [max((obj.matrix_world @ bone.head_local)[axis] for bone in obj.data.bones) for axis in range(3)],
        } for obj in armatures],
        "meshes": [{
            "name": obj.name, "vertices": len(obj.data.vertices),
            "polygons": len(obj.data.polygons), "uv_layers": len(obj.data.uv_layers),
            "vertex_groups": len(obj.vertex_groups),
            "materials": len(obj.data.materials),
            "normal_count": len(obj.data.vertices),
            "normals_nonzero": all(vertex.normal.length > 1e-6 for vertex in obj.data.vertices),
            "minimum": [min(vertex.co[axis] for vertex in obj.data.vertices) for axis in range(3)],
            "maximum": [max(vertex.co[axis] for vertex in obj.data.vertices) for axis in range(3)],
        } for obj in meshes],
    }
    if not meshes or len(armatures) != 1 or report["bone_count"] == 0:
        raise RuntimeError(f"invalid FBX contents: {report}")
    if any(item["uv_layers"] == 0 for item in report["meshes"]):
        raise RuntimeError("an exported mesh has no UV map")
    if any(item["normal_count"] == 0 for item in report["meshes"]):
        raise RuntimeError("an exported mesh has no normals")
    Path(report_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("mesh_count", "armature_count", "bone_count")}))


command = args()
if command[0] == "export":
    export(command[1], command[2])
elif command[0] == "validate":
    validate(command[1], command[2])
else:
    raise ValueError(command[0])
