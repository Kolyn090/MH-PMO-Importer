# -*- coding: utf-8 -*-
"""
Created on Wed Mar  6 14:09:29 2019

@author: AsteriskAmpersand
"""
import array
import bpy
import bmesh
import os
from pathlib import Path
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator

from ..struct.pmo import load_pmo, load_cmo
from ..struct.pmo_parse import MetaLayerNames

"""
============================================
============================================
NORMALS CODE
============================================
============================================
"""

from mathutils import Vector
from fractions import Fraction


def rationalize(value,N):
    frac = Fraction(value).limit_denominator(N)
    return frac.numerator, frac.denominator

def denormalize(vector):
    x,y,z = vector.x, vector.y, vector.z
    maxima = max(abs(x),abs(y),abs(z))
    if maxima == 0: maxima = 1
    x,y,z = round(127*x/maxima), round(127*y/maxima), round(127*z/maxima)
    return [x,y,z,0]

def normalize(vecLike):
    vector = Vector(vecLike)
    vector.normalize()
    return vector


"""
============================================
============================================
MATERIAL CODE
============================================
============================================
"""

def setLocation(node,location):
    x,y = location
    node.location = (x-14)*100,-y*100

def createTexNode(nodeTree,color,texture,name):
    baseType = "ShaderNodeTexImage"
    node = nodeTree.nodes.new(type=baseType)
    #node.color_space = color
    if texture is not None:
        node.image = texture
    node.name = name
    return node

def materialSetup(matName,texture):
    bpy.context.scene.render.engine = 'CYCLES'
    #if matName in bpy.data.materials:
    #    blenderObj.data.materials.append(bpy.data.materials[matName])
    #    return None
    mat = bpy.data.materials.new(name=matName)
    #blenderObj.data.materials.append(mat)
    mat.use_nodes=True

    nodes = mat.node_tree.nodes
    nodes.clear()

    nodeTree = mat.node_tree

    bsdfNode = nodeTree.nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdfNode.inputs["Roughness"].default_value = 1.0
    setLocation(bsdfNode,(6,0))
    bsdfNode.name = "Principled BSDF"
    endNode = bsdfNode

    diffuseNode = createTexNode(nodeTree, "sRGB", texture, "Diffuse Texture")
    setLocation(diffuseNode,(0,0))

    nodeTree.links.new(diffuseNode.outputs["Color"], bsdfNode.inputs["Base Color"])
    nodeTree.links.new(diffuseNode.outputs["Alpha"], bsdfNode.inputs["Alpha"])

    outputNode = nodeTree.nodes.new(type="ShaderNodeOutputMaterial")
    nodeTree.links.new(bsdfNode.outputs["BSDF"], outputNode.inputs["Surface"])

    return mat

"""
============================================
============================================
MESH CODE
============================================
============================================
"""   

class ImportPMO(Operator, ImportHelper):
    bl_idname = "custom_import.import_mhfu_pmo"
    bl_label = "Load MHFU PMO file (.pmo)"
    bl_options = {'REGISTER', 'PRESET', 'UNDO'}

    # ImportHelper mixin class uses this
    filename_ext = ".pmo"
    filter_glob = StringProperty(default="*.pmo", options={'HIDDEN'}, maxlen=255)

    loadTexture : BoolProperty(
        name = "Import Textures",
        description = "Attempts to import textures.",
        default = True)
    flipUV : BoolProperty(
        name = "Flip UV Map",
        description = "Flips UV Map Vertically.",
        default = False)
    enforceNormals : BoolProperty(
        name = "Enforce Normals",
        description = "Forces Face Normals to Follow Edge Normals.",
        default = False)
    importMetalayers : BoolProperty(
        name = "Import Additional Meta Layers",
        description = "Import additional GPU per Face Command Options",
        default = False
        )
    texturePath : StringProperty(
        name = "Texture Folder",
        description = "Folder were suspect textures are, leave empty to do Pray to God Search.",
        default = ""
        )


    def execute(self,context):
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except:
            pass

        meshes,pmo = load_pmo(self.properties.filepath)
        materials = self.createTexMaterials(pmo)
        self.setClip(pmo.header.clippingDistance)
        for metadata,mesh in zip(pmo.meshHeaders,meshes):
            obj = self.loadMesh(materials,*mesh)
            self.writeMetadata(obj,metadata)
        return {'FINISHED'}

    def writeMetadata(self,obj,data):
        for ix,var in enumerate(data.unkn1):
            obj.data["unkn01-%02d"%ix] = var

    def fetchTexture(self,filepath):
        if os.path.exists(filepath):
            return bpy.data.images.load(filepath)
        else:
            return None

    def findTexture(self,tindex):
        #print(tindex)
        #print("Starting Texture Load")
        pattern = f"material{tindex:02d}.png"
        if str(self.texturePath) != "":
            #print(self.texturePath)
            for p in Path(self.texturePath).glob(pattern):
                tex = self.fetchTexture(str(p))
                if tex is not None:
                    return tex
        else:
            #print(self.properties.filepath)
            for p in Path(self.properties.filepath).parent.rglob(pattern):
                tex = self.fetchTexture(str(p))
                if tex is not None:
                    return tex
        return None

    def createTexMaterials(self,pmo):
        mapping = {}
        texturemap = {}
        textureIDs = {}
        for mat in pmo.materialData:
            if mat.index in mapping:
                continue
            tindex = textureIDs[mat.textureID] if mat.textureID in textureIDs else len(textureIDs)
            print(f"Texture Index: {tindex}")
            textureIDs[mat.textureID] = tindex
            if tindex in texturemap:
                texture = texturemap[tindex]
            else:
                if self.loadTexture:
                    texture = self.findTexture(mat.textureID)
                else:
                    texture = None
                texturemap[tindex] = texture
            # matname = "PMO_Material_%03d"%(mat.index)
            # material = materialSetup(matname,texture)
            base_name = f"material_{mat.textureID:02d}"
            material = bpy.data.materials.get(base_name)
            if material is None:
                material = materialSetup(base_name, texture)

            for f,fn in zip(["rgba","shadow_rgba","textureID"],
                        ["diffuse","ambient","texture_index"]):
                material["pmo_"+fn] = mat[f]
            mapping[mat.index] = material
        return mapping

    def parseVerts(self,mesh,scale,uvModifier):
        #print("Verts Parsed")
        #print(len(mesh))
        verts = []
        nors = []
        uv = []
        col = []
        wts = []
        uvScale = uvModifier[0]
        uvOffset = uvModifier[1]
        vs = [1 - vtx.uv.v * uvScale[1] for vtx in mesh if vtx.uv]
        min_vs = min(vs)
        print(min_vs)
        for v in mesh:
            if v.position:
                # verts.append((v.position.x*scale[0],v.position.y*scale[1],v.position.z*scale[2]))
                verts.append((-v.position.x*scale[0],v.position.z*scale[2],v.position.y*scale[1]))
            if v.normal:
                # nors.append((v.normal.x,v.normal.y,v.normal.z))
                nors.append((-v.normal.x, v.normal.z, v.normal.y))
            if v.uv:
                # if self.flipUV:
                #     uv.append((v.uv.u*uvScale[0],1 - v.uv.v*uvScale[1]))
                # else:
                #     uv.append((v.uv.u*uvScale[0],v.uv.v*uvScale[1]))
                
                uv_v = 1 - v.uv.v * uvScale[1] - min_vs
                print(uv_v)
                uv.append((
                    v.uv.u * uvScale[0],
                    uv_v
                ))
            if v.colour:
                col.append((v.colour.r,v.colour.g,v.colour.b,v.colour.a))
            if any(map(lambda x: x is not None, v.weight)):
                wts.append([(bid,w.weight) for bid,w in v["weightData"]])
        return verts,nors,uv,col,wts

    def parseFaces(self,faces):
        #print(faces)
        #raise
        return faces
        #print("Face Parsed")
        #return [f for f,m in faces],[m for f,m in faces]

    def setMaterials(self, mesh, faceMats, masterMats):
        # Step 1: map global index → actual material object
        face_material_objs = [masterMats[m] for m in faceMats]

        # Step 2: build unique material list (by object identity)
        unique_materials = []
        mat_to_index = {}

        for mat in face_material_objs:
            if mat not in mat_to_index:
                mat_to_index[mat] = len(unique_materials)
                unique_materials.append(mat)

        # Step 3: clear and assign unique materials
        mesh.materials.clear()
        for mat in unique_materials:
            mesh.materials.append(mat)

        # Step 4: assign indices per face
        blenderBMesh = bmesh.new()
        blenderBMesh.from_mesh(mesh)
        blenderBMesh.faces.ensure_lookup_table()

        for face, mat_obj in zip(blenderBMesh.faces, face_material_objs):
            face.material_index = mat_to_index[mat_obj]

        blenderBMesh.to_mesh(mesh)
        blenderBMesh.free()
        mesh.update()

    def decomposeMetaLayers(self,metalayers):
        fields = MetaLayerNames
        layers = []
        for field in fields:
            f = [getattr(m,field) for m in metalayers]
            if any(f):
                fieldn = "PMO " + field.replace("_"," ").title()
                layers.append((fieldn,f))
        return layers

    def parseMetaLayers(self,obj,metalayers):
        layers = self.decomposeMetaLayers(metalayers)
        for fieldn,layer in layers:
            dal = obj.data.attributes.new(name = fieldn, type = "INT", domain = "FACE")
            dal.data.foreach_set('value',layer)

    def loadMesh(self,materials,meshdata,faces,metalayers,mat,scale,uvModifier):
        #print("Mesh Started")
        mesh = bpy.data.meshes.new(name="PMO_Mesh")
        verts,normals,uvs,color,weights = self.parseVerts(meshdata,scale,uvModifier)
        bfaces = self.parseFaces(faces)
        try:
            mesh.from_pydata(verts, [], bfaces)
        except Exception as e:
            print(e)
            return
        #print("Mesh Created")
        if normals:
            self.setNormals(mesh,normals)
        if self.enforceNormals and normals:
            self.setFaceNormals(mesh,normals)
            self.setNormals(mesh,normals)
        if uvs:
            self.setUVs(mesh,uvs)
        if color:
            self.setColor(mesh,color)
        if materials:
            self.setMaterials(mesh,mat,materials)
        #if weights:
        #    self.setWeights(mesh,weights)
        #mesh.validate(verbose=True)
        mesh.update()
        obj = bpy.data.objects.new('PMO_Mesh',mesh)
        bpy.context.collection.objects.link(obj)
        if self.importMetalayers: 
            self.parseMetaLayers(obj,metalayers)
        if weights:
            self.setWeights(obj,weights)
        return obj
        #print("Mesh End")
        #object_data_add(context, mesh, operator=self)

    def setNormals(self,meshpart,normals):
        meshpart.update(calc_edges=True)
        #meshpart.normals_split_custom_set_from_vertices(normals)

        clnors = array.array('f', [0.0] * (len(meshpart.loops) * 3))
        meshpart.loops.foreach_get("normal", clnors)
        meshpart.polygons.foreach_set("use_smooth", [True] * len(meshpart.polygons))

        #meshpart.normals_split_custom_set(tuple(zip(*(iter(clnors),) * 3)))
        meshpart.normals_split_custom_set_from_vertices([normalize(v) for v in normals])#normalize
        #meshpart.normals_split_custom_set([normals[loop.vertex_index] for loop in meshpart.loops])
        if(hasattr(meshpart,"use_auto_smooth")): meshpart.use_auto_smooth = True
        #bpy.context.space_data.overlay.show_edge_sharp  = True

    def setFaceNormals(self,blenderMesh,normals):
        bpy.context.view_layer.update()
        blenderBMesh = bmesh.new()
        blenderBMesh.from_mesh(blenderMesh)
        blenderBMesh.faces.ensure_lookup_table()
        for face in blenderBMesh.faces:
            faceNormals = [normalize(normals[v.vert.index]) for v in face.loops]
            netNormal = (sum(faceNormals,Vector([0,0,0]))/len(face.loops)).normalized()
            if netNormal.dot(face.normal) < 0:
                face.normal_flip()
        blenderBMesh.to_mesh(blenderMesh)
        blenderMesh.update()


    def setColor(self,mesphart,color):
        vcol_layer = mesphart.vertex_colors.new()
        for l,col in zip(mesphart.loops, vcol_layer.data):
            col.color = color[l.vertex_index]

   #UVs
    def setUVs(self, blenderMesh, uv):
        name = "UV_Layer"

        # Create UV layer (new API)
        uv_layer = blenderMesh.uv_layers.new(name=name)

        blenderMesh.update()

        blenderBMesh = bmesh.new()
        blenderBMesh.from_mesh(blenderMesh)

        # Access UV layer in bmesh
        uv_layer_bm = blenderBMesh.loops.layers.uv[uv_layer.name]

        blenderBMesh.faces.ensure_lookup_table()

        for face in blenderBMesh.faces:
            for loop in face.loops:
                loop[uv_layer_bm].uv = uv[loop.vert.index]

        blenderBMesh.to_mesh(blenderMesh)
        blenderMesh.update()
        return

    def setWeights(self, blenderObj, wts):
        for ix,wtgroup in enumerate(wts):
            for bid,wt in wtgroup:
                if wt != 0:
                    groupName = "Bone.%03d"%bid
                    if groupName not in blenderObj.vertex_groups:
                        blenderObj.vertex_groups.new(name=groupName)
                    blenderObj.vertex_groups[groupName].add([ix],wt,'ADD')

    def setClip(self,clippingDistance):
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            space.clip_start = 0.5
                            space.clip_end = clippingDistance * 10
    
class ImportCMO(ImportPMO, Operator, ImportHelper):
    bl_idname = "custom_import.import_mhfu_cmo"
    bl_label = "Load MHFU CMO file (.cmo)"
    bl_options = {'REGISTER', 'PRESET', 'UNDO'}
 
    # ImportHelper mixin class uses this
    filename_ext = ".cmo"
    filter_glob : StringProperty(default="*.cmo", options={'HIDDEN'}, maxlen=255)

    def execute(self,context):
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except:
            pass        
        meshes,pmo = load_cmo(self.properties.filepath)
        for mesh in meshes:
            self.loadMesh([],*mesh)
        return {'FINISHED'}

def menu_func_import(self, context):
    self.layout.operator(ImportPMO.bl_idname, text="MHFU PMO (.pmo)")
    self.layout.operator(ImportCMO.bl_idname, text="MHFU CMO (.cmo)")
