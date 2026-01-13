import bpy
import os
import sys

'''
This script updates Blender files in a specified folder to change the path of a library override
from a relative path (//) to a new absolute path.
Instructions:
'''

# Configuration - UPDATE THESE VALUES
EXISTING_FOLDER = r"C:\git\mesh2motion-assets\rigs\human"  # Folder with animation files
NEW_LIBRARY_PATH = r"C:\git\mesh2motion-assets\rigs\rig-human.blend"  # New rig file location

# ----------------------------------------------------
# Shouldn't have to update anything below this line
# ----------------------------------------------------
def update_library_override():
    for lib in bpy.data.libraries:
        # Check if this library uses the // relative path format
        if lib.filepath.startswith("//"):
            lib.filepath = NEW_LIBRARY_PATH
            return True
    return False

def process_blend_files():
    if not os.path.exists(EXISTING_FOLDER):
        return
    
    blend_files = [f for f in os.listdir(EXISTING_FOLDER) if f.endswith('.blend')]
    
    if not blend_files:
        return

    updated_count = 0
    skipped_count = 0
    
    for blend_file in blend_files:
        file_path = os.path.join(EXISTING_FOLDER, blend_file)
        
        # Open the blend file
        bpy.ops.wm.open_mainfile(filepath=file_path)
        
        # Update the // library override
        if update_library_override():
            # Save the file
            bpy.ops.wm.save_mainfile()
            print(f"  ✓ Saved successfully\n")
            updated_count += 1
        else:
            print(f"  ⊘ Skipped (no // library found)\n")
            skipped_count += 1

process_blend_files()