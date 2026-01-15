\# Python scripts

Contains random Python scripts to help me manage the files and animations





\## Library Override

This happened when I wanted to re-arrange the rig and animation files. Moving the rig to a different location broken all the referenced library override files. I needed a way to go into every animation file quickly and update the references.



Open the library-override-fix script file. Change the original and new locations for the file. Make sure that the Blender executable is in your environment variable so you can run the "blender" command from a command prompt. This script was originally designed to move the rig up one folder level. You will need to tweak the code if you want it to do something else. Adding this here for reference.



Navigate to the location.



    blender -b --python library-override-fix.py







\## Combine animations



&nbsp;   blender -b "C:\\git\\mesh2motion-assets\\rigs\\rig-kaiju.blend" --python "C:\\git\\mesh2motion-assets\\python-scripts\\combine-and-export.py"





\## Testing animations



Good website where you can test out bones to make sure only DEF bones are getting exported as well as action names.

https://sandbox.babylonjs.com/

