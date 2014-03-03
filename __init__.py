# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

bl_info = {
    "name": "Sketchfab Exporter",
    "author": "Bart Crouch",
    "version": (1, 2, 1),
    "blender": (2, 6, 3),
    "location": "Tools > Upload tab",
    "description": "Upload your model to Sketchfab",
    "warning": "",
    "wiki_url": "",
    "tracker_url": "",
    "category": "Import-Export"
}

if "bpy" in locals():
    import imp
    imp.reload(requests)
else:
    # uuid module causes an error messagebox on windows https://developer.blender.org/T38364 and https://developer.blender.org/T27666
    # using a dirty workaround to preload uuid without ctypes, until blender gets compiled with vs2012
    import platform
    if platform.system() == 'Windows':
        import ctypes
        CDLL = ctypes.CDLL
        ctypes.CDLL = None
        import uuid
        ctypes.CDLL = CDLL
        del ctypes, CDLL

    from .packages import requests

import bpy
import os
import threading
import re
import json
import subprocess

from bpy.app.handlers import persistent
from bpy.props import StringProperty, EnumProperty, BoolProperty, PointerProperty

SKETCHFAB_API_URL = 'https://api.sketchfab.com'
SKETCHFAB_API_MODELS_URL = SKETCHFAB_API_URL + '/v1/models'
SKETCHFAB_API_TOKEN_URL = SKETCHFAB_API_URL + '/v1/users/claim-token'
SKETCHFAB_MODEL_URL = 'https://sketchfab.com/show/'
SKETCHFAB_PRESET_FILE = 'sketchfab.txt'
SKETCHFAB_EXPORT_DATA_FILENAME = 'sketchfab-export-data.json'
SKETCHFAB_EXPORT_FILENAME = 'sketchfab-export.blend'

SKETCHFAB_EXPORT_DATA_FILE = os.path.join(
    bpy.utils.user_resource('SCRIPTS'),
    "presets",
    SKETCHFAB_EXPORT_DATA_FILENAME
)

DEBUG_MODE = False     # if True, no contact is made with the webserver


# change a bytes int into a properly formatted string
def format_size(size):
    size /= 1024
    size_suffix = "kB"
    if size > 1024:
        size /= 1024
        size_suffix = "mB"
    if size >= 100:
        size = str(int(size))
    else:
        size = "%.1f"%size
    size += " " + size_suffix

    return size


# attempt to load token from presets
@persistent
def load_token(dummy=False):
    filepath = os.path.join(bpy.utils.user_resource('SCRIPTS'), "presets",
        SKETCHFAB_PRESET_FILE)

    if not os.path.exists(filepath):
        return

    token = ''
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            token = f.readline()
    except:
        import traceback
        traceback.print_exc()

    bpy.context.window_manager.sketchfab.token = token



# save token to file
def update_token(self, context):
    token = context.window_manager.sketchfab.token
    path = os.path.join(bpy.utils.user_resource('SCRIPTS'), "presets")
    if not os.path.exists(path):
        os.makedirs(path)
    filepath = os.path.join(path, SKETCHFAB_PRESET_FILE)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(token)


def show_upload_result(msg, msg_type, result=None):
    props = bpy.context.window_manager.sketchfab
    props.message = msg
    props.message_type = msg_type
    if result:
        props.result = result


# upload the blend-file to sketchfab
def upload(filepath, filename):
    props = bpy.context.window_manager.sketchfab

    title = props.title
    if not title:
        title = os.path.splitext(os.path.basename(bpy.data.filepath))[0]

    data = {
        "title": title,
        "description": props.description,
        "filename": filename,
        "tags": props.tags,
        "private": props.private,
        "token": props.token,
        "source": "blender-exporter"
    }

    if props.private and props.password != "":
        data['password'] = props.password

    files = {
        'fileModel': open(filepath, 'rb')
    }

    try:
        r = requests.post(SKETCHFAB_API_MODELS_URL, data=data, files=files, verify=False)
    except requests.exceptions.RequestException as e:
        return show_upload_result('Upload failed. Error: %s' % str(e), 'WARNING')

    result = r.json()
    if r.status_code != requests.codes.ok:
        return show_upload_result('Upload failed. Error: %s' % result['error'], 'WARNING')

    model_url = SKETCHFAB_MODEL_URL + result['result']['id']
    return show_upload_result('Upload complete. Available on your sketchfab.com dashboard.', 'INFO', model_url)



# operator to export model to sketchfab
class ExportSketchfab(bpy.types.Operator):
    '''Upload your model to Sketchfab'''
    bl_idname = "export.sketchfab"
    bl_label = "Upload"

    _timer = None
    _thread = None

    def modal(self, context, event):
        if event.type == 'TIMER':
            if not self._thread.is_alive():
                props = context.window_manager.sketchfab
                terminate(props.filepath)
                if context.area:
                    context.area.tag_redraw()
                if not props.message_type:
                    props.message_type = 'ERROR'
                self.report({props.message_type}, props.message)
                context.window_manager.event_timer_remove(self._timer)
                self._thread.join()
                props.uploading = False
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def execute(self, context):
        context.window_manager.sketchfab.result = ''
        props = context.window_manager.sketchfab
        if not props.token:
            self.report({'ERROR'}, "Token is missing")
            return {'CANCELLED'}
        props.uploading = True

        try:
            # save settings to access them in the subprocess call
            with open(SKETCHFAB_EXPORT_DATA_FILE, 'w') as s:
                json.dump({'models': props.models,
                           'lamps': props.lamps}, s)

            binary_path = bpy.app.binary_path
            script_path = os.path.dirname(os.path.realpath(__file__))
            (basename, ext) = os.path.splitext(bpy.data.filepath)
            filepath = basename + "-export-sketchfab" + ext

            # save a copy of actual scene but don't interfere with the users models
            bpy.ops.wm.save_as_mainfile(filepath=filepath,
                                compress=True, copy=True)

            with open(SKETCHFAB_EXPORT_DATA_FILE, 'w') as s:
                json.dump({'models': props.models, 'lamps': props.lamps}, s)

            subprocess.check_call([binary_path, '-b', filepath,
                                   '--python', script_path + '/pack_for_export.py'])
            os.remove(filepath)

            # read subprocess call results
            with open(SKETCHFAB_EXPORT_DATA_FILE, 'r') as s:
                r = json.load(s)
                size = r['size']
                props.filepath = r['filepath']
                filename = r['filename']

        except Exception as e:
            self.report({'WARNING'}, 'Error occured while preparing your file: %s' % str(e))
            return {'FINISHED'}

        props.size = format_size(size)
        self._thread = threading.Thread(
            target=upload,
            args=(props.filepath, filename)
        )
        self._thread.start()

        context.window_manager.modal_handler_add(self)
        self._timer = context.window_manager.event_timer_add(1.0,
            context.window)

        return {'RUNNING_MODAL'}

    def cancel(self, context):
        context.window_manager.event_timer_remove(self._timer)
        self._thread.join()

        return {'CANCELLED'}


# popup to say that something is already being uploaded
class ExportSketchfabBusy(bpy.types.Operator):
    '''Upload your model to Sketchfab'''
    bl_idname = "export.sketchfab_busy"
    bl_label = "Uploading"

    def execute(self, context):
        self.report({'WARNING'}, "Please wait till current upload is finished")

        return {'FINISHED'}


# user interface
class VIEW3D_PT_sketchfab(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'TOOLS'
    bl_category = 'Upload'
    bl_label = "Sketchfab"

    def draw(self, context):
        props = context.window_manager.sketchfab
        if props.token_reload:
            props.token_reload = False
            if not props.token:
                load_token()
        layout = self.layout

        layout.label('Export:')
        col = layout.box().column(align=True)
        col.prop(props, "models")
        col.prop(props, "lamps")

        layout.label('Model info:')
        col = layout.box().column(align=True)
        col.prop(props, "title")
        col.prop(props, "description")
        col.prop(props, "tags")
        col.prop(props, "private")
        if props.private:
            col.prop(props, "password")

        layout.label('Sketchfab account:')
        col = layout.box().column(align=True)
        col.prop(props, "token")
        row = col.row()
        row.alignment = 'RIGHT'
        row.operator('object.dialog_operator', text="Claim your token")
        if props.uploading:
            layout.operator("export.sketchfab_busy",
                text="Uploading " + props.size)
        else:
            layout.operator("export.sketchfab")
        if context.window_manager.sketchfab.result:
            layout.operator('wm.url_open', text='View online model', icon='URL').url = context.window_manager.sketchfab.result


# property group containing all properties for the user interface
class SketchfabProps(bpy.types.PropertyGroup):
    description = StringProperty(name="Description",
        description = "Description of the model (optional)",
        default = "")
    filepath = StringProperty(name="Filepath",
        description = "internal use",
        default = "")
    lamps = EnumProperty(name="Lamps",
        items = (('ALL', "All", "Export all lamps in the file"),
                ('NONE', "None", "Don't export any lamps"),
                ('SELECTION', "Selection", "Only export selected lamps")),
        description = "Determines which lamps are exported",
        default = 'ALL')
    message = StringProperty(name="Message",
        description = "internal use",
        default = "")
    message_type = StringProperty(name="Message type",
        description = "internal use",
        default = "")
    models = EnumProperty(name="Models",
        items = (('ALL', "All", "Export all meshes in the file"),
                 ('SELECTION', "Selection", "Only export selected meshes")),
        description = "Determines which meshes are exported",
        default = 'SELECTION')
    result = StringProperty(name="Result",
        description = "internal use, stores the url of the uploaded model",
        default = "")
    size = StringProperty(name="Size",
        description = "Current filesize being uploaded",
        default = "")
    private = BoolProperty(name="Private",
        description = "Upload as private (requires a pro account)",
        default = False)
    password = StringProperty(name="Password",
        description = "Password-protect your model (requires a pro account)",
        default = "")
    tags = StringProperty(name="Tags",
        description = "List of tags, separated by spaces (optional)",
        default = "")
    title = StringProperty(name="Title",
        description = "Title of the model (determined automatically if \
left empty)",
        default = "")
    token = StringProperty(name="Api Key",
        description = "You can find this on your dashboard at the Sketchfab \
website",
        default = "",
        update = update_token)
    token_reload = BoolProperty(name="Reload of token necessary?",
        description = "internal use",
        default = True)
    token_reload = BoolProperty(name="Reload of token necessary?",
        description = "internal use",
        default = True)
    uploading = BoolProperty(name="Busy uploading",
        description = "internal use",
        default = False)


class DialogOperator(bpy.types.Operator):
    bl_idname = "object.dialog_operator"
    bl_label = "Enter your email to get a sketchfab token"

    email = StringProperty(name="Email",
                                     default="you@example.com")

    def execute(self, context):
        EMAIL_RE = re.compile(r'[^@]+@[^@]+\.[^@]+')
        if not EMAIL_RE.match(self.email):
            self.report({'ERROR'}, 'Wrong email format')
        try:
            r = requests.get(SKETCHFAB_API_TOKEN_URL + '?source=blender-exporter&email=' + self.email, verify=False)
        except requests.exceptions.RequestException as e:
            self.report({'ERROR'}, str(e))
            return {'FINISHED'}

        if r.status_code != requests.codes.ok:
            self.report({'ERROR'}, 'An error occured. Check the format of your email')
        else:
            self.report({'INFO'}, "Your email was sent at your email address")

        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=550)

# remove file copy
def terminate(filepath):
    os.remove(filepath)

# registration
classes = [ExportSketchfab,
           ExportSketchfabBusy,
           SketchfabProps,
           DialogOperator,
           VIEW3D_PT_sketchfab]


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.sketchfab = PointerProperty(
        type = SketchfabProps)
    load_token()
    bpy.app.handlers.load_post.append(load_token)


def unregister():
    for c in classes:
        bpy.utils.unregister_class(c)
    try:
        del bpy.types.WindowManager.sketchfab
    except:
        pass

if __name__ == "__main__":
    register()
