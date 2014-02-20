import os
import bpy
import json
import time


SKETCHFAB_EXPORT_DATA_FILENAME = 'sketchfab-export-data.json'

SKETCHFAB_EXPORT_DATA_FILE = os.path.join(
    bpy.utils.user_resource('SCRIPTS'),
    "presets",
    SKETCHFAB_EXPORT_DATA_FILENAME
)

# save a copy of the current blendfile
def save_blend_copy():
    filepath = os.path.dirname(bpy.data.filepath)
    filename = time.strftime("Sketchfab_%Y_%m_%d_%H_%M_%S.blend",
        time.localtime(time.time()))
    filepath = os.path.join(filepath, filename)
    bpy.ops.wm.save_as_mainfile(filepath=filepath,
                                compress=True,
                                copy=True)
    size = os.path.getsize(filepath)

    return (filepath, filename, size)


# change visibility statuses and pack images
def prepare_assets(export_settings):
    hidden = set()
    images = set()
    if export_settings['models'] == 'SELECTION' or export_settings['lamps'] != 'ALL':
        for ob in bpy.data.objects:
            if ob.type == 'MESH':
                for mat_slot in ob.material_slots:
                    if not mat_slot.material:
                        continue
                    for tex_slot in mat_slot.material.texture_slots:
                        if not tex_slot:
                            continue
                        if tex_slot.texture.type == 'IMAGE':
                            images.add(tex_slot.texture.image)
            if (export_settings['models'] == 'SELECTION' and ob.type == 'MESH') or \
            (export_settings['lamps'] == 'SELECTION' and ob.type == 'LAMP'):
                if not ob.select and not ob.hide:
                    ob.hide = True
                    hidden.add(ob)
            elif export_settings['lamps'] == 'NONE' and ob.type == 'LAMP':
                if not ob.hide:
                    ob.hide = True
                    hidden.add(ob)

    packed = set()
    for img in images:
        if not img.packed_file:
            img.pack()
            packed.add(img)


def prepare_file(export_settings):
    prepare_assets(export_settings)
    return save_blend_copy()


def read_settings():
    with open(SKETCHFAB_EXPORT_DATA_FILE, 'r') as s:
        return json.load(s)


def write_result(filepath, filename, size):
    with open(SKETCHFAB_EXPORT_DATA_FILE, 'w') as s:
        json.dump({'filepath': filepath,
                   'filename': filename,
                   'size': size}, s)

export_settings = read_settings()
filepath, filename, size = prepare_file(export_settings)
write_result(filepath, filename, size)
