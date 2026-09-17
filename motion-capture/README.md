# Motion capture animations

The motion capture animations are very similar, but there are some differences which is why
they are in a different folder. 

1. Library overrides cannot do "edit mode". The Rokoko retargeter plugin I am using needs access to that
For that reason the the rig is a copy of the human rig, not a library override like the other rigs


# Retargeting Workflow

1. Duplicate the setup blender file. This will already have the human IK rig in it. It is the appended rig in the normal rig folder
2. Import the BVH file in the raw capture. Set the scale to 0.01
3. Run the python script "retarget-master.py.". This will move bones around to align with Mesh2motion. It will also create pole IK bones for retargeting
4. Load the Rokoko plugin if it isn't. Open that panel. It should have the mappings for the bones. There is a JSON mapping file if that doesn't happen.
5. Press the retarget button