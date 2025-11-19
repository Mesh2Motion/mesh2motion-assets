# Mesh2Motion Source Art Assets

This contains all the source files for art assets. This includes the following
- 3d model files
- 3d control rig files for each skeleton type
- Animations for each rig type

This was separated as this repository will grow the fastest in terms of how large it is. The mesh2motion-app repository will have the final GLB files needed to run the app. This will also make running the main Mesh2Motion app locally much smaller since it doesn't need all the original soure Blender files.


## Creating Animations from rig file

The skeleton rigs need to be compatible with the the web application. There are pre-rigged meshes ready to animate using Blender 4.5. These will make it very easy to add the animations to the application.

1. Download the 3d Blender rig file you want to help with. Go in the rigs folder and find the "rig" file that you want to help with.
2. Create a new Blender file and delete all the objects (meshes, camera, lights) that exist in the scene
3. Go to the main menu option File > Link
4. Find the rig file you just downloaded. Double click the blend file. This will allow us to link/import the rig.  
5. Go into the "Collection" folder. Select all the files in there (will have two collections) and click the Link button to import.
6. Hide the custom bone shapes collection from the scene outline
7. Select the rig and go to the 3d viewport option Object > Library Override > Make
8. Select the skeleton and go into Pose mode.

From here you can create the animation. I usually use the action editor. The project currently contains one animation per Blender file.

## Sharing final animation file

Once your animation is done, you can create a new Github ticket and attach your Blender file with a note with what you have done.