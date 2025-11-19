# 3D modeling considerations when using Mesh2Motion

## Try to have one material per mesh
There is a quirk with how the web technology (three.js) deals with materials and meshes. The 3d library only supports one material per mesh. If you import a mesh with more than one material, the mesh will be broken apart into multiple meshes when you export it. This may, or may not, be an issue for the end animations. 

We will just need to keep that in mind for future reference when building out samples or instructions.

Reference:
https://discourse.threejs.org/t/some-issues-when-export-a-single-mesh-with-multiple-materials-using-gltf-exporter/50395/4


