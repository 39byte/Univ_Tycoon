# Rigged, realistic characters built on Quaternius "Universal Base Characters" (CC0, asset/source/*.zip).
# Run:  blender -b -P asset/src/build_chars.py -- [names...]
# Outfits are cut from the body mesh by bone weights and pushed outward, so they inherit the skinning and
# deform with the same skeleton the Universal Animation Library uses.
import bpy, bmesh, os, sys, math, zipfile, glob
import numpy as np
from mathutils import Vector, Matrix
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_assets as A

SRC = os.path.join(A.ROOT, 'source')
UBC = os.path.join(SRC, '_extract')
if not os.path.isdir(UBC):
    zipfile.ZipFile(glob.glob(os.path.join(SRC, 'Universal Base Characters*.zip'))[0]).extractall(UBC)
UBC = glob.glob(os.path.join(UBC, 'Universal Base Characters*'))[0]
BODY = os.path.join(UBC, 'Base Characters', 'Godot - UE', 'Superhero_{}_FullBody.gltf')
HAIR = os.path.join(UBC, 'Hairstyles', 'Rigged to Head Bone', 'glTF (Godot -Unreal)', '{}.gltf')
LIGHT = {'Male': 'T_Superhero_Male_Ligh.png', 'Female': 'T_Superhero_Female_Light_BaseColor.png'}

def lin(h):  # 0xRRGGBB -> linear rgb tuple
    return tuple(((h >> s & 255) / 255) ** 2.2 for s in (16, 8, 0))

def imp(path):
    assert os.path.exists(path), path
    before = set(bpy.data.objects); bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in bpy.data.objects if o not in before]

def recolor(img, target, keep=.35):
    """Re-tint a texture to target (linear rgb) while keeping its luminance detail."""
    w, h = img.size; px = np.array(img.pixels[:], np.float32).reshape(h, w, 4)
    L = px[..., :3] @ np.array([.2126, .7152, .0722], np.float32); L = L / max(L.mean(), 1e-4)
    t = np.array(target, np.float32) ** (1 / 2.2)  # pixels are display-space for sRGB images
    px[..., :3] = np.clip(t * (keep + (1 - keep) * L[..., None]), 0, 1)
    img.pixels.foreach_set(px.ravel())

def tint_image_node(mat, target, keep=.35):
    for n in mat.node_tree.nodes:
        if n.type == 'TEX_IMAGE' and n.image and n.image.colorspace_settings.name != 'Non-Color':
            n.image = n.image.copy(); recolor(n.image, target, keep); return

def shrink(img, s=1024):
    if img.size[0] > s: img.scale(s, s)

# ---------------------------------------------------------------- body analysis
def dominant(body):
    names = {g.index: g.name for g in body.vertex_groups}
    out = []
    for v in body.data.vertices:
        g = max(v.groups, key=lambda g: g.weight, default=None)
        out.append(names[g.group] if g else '')
    return out

def bone_pos(arm, name, tail=False):
    b = arm.data.bones[name]; return arm.matrix_world @ (b.tail_local if tail else b.head_local)

def shell(body, keep, off, mat, name, thick=.004, smooth=0):
    """Copy the body faces whose vertices are all in `keep`, push them out by `off`, give them thickness."""
    o = body.copy(); o.data = body.data.copy(); o.name = o.data.name = name; bpy.context.scene.collection.objects.link(o)
    bm = bmesh.new(); bm.from_mesh(o.data); bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if not all(keep[v.index] for v in f.verts)], context='FACES')
    bm.normal_update()
    for v in bm.verts: v.co += v.normal * off
    if smooth:  # relax away the muscle/toe detail so it reads as cloth/leather, then re-inflate what smoothing shrank
        inner = [v for v in bm.verts if not v.is_boundary]
        for _ in range(smooth): bmesh.ops.smooth_vert(bm, verts=inner, factor=.5, use_axis_x=True, use_axis_y=True, use_axis_z=True)
        bm.normal_update()
        for v in inner: v.co += v.normal * off * .6
    bm.to_mesh(o.data); bm.free()
    o.data.materials.clear(); o.data.materials.append(mat)
    if thick:
        s = o.modifiers.new('sol', 'SOLIDIFY'); s.thickness = thick; s.offset = -1
        bpy.context.view_layer.objects.active = o; bpy.ops.object.modifier_move_to_index(modifier='sol', index=0); bpy.ops.object.modifier_apply(modifier='sol')
    return o

def hide_covered(body, covered):
    bm = bmesh.new(); bm.from_mesh(body.data); bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if all(covered[v.index] for v in f.verts)], context='FACES')
    bm.to_mesh(body.data); bm.free()

def bone_attach(obj, arm, bone):
    mw = obj.matrix_world.copy(); obj.parent = arm; obj.parent_type = 'BONE'; obj.parent_bone = bone
    bpy.context.view_layer.update(); obj.matrix_world = mw

# ---------------------------------------------------------------- materials
def m_fabric(name, c, rough=.85, scale=90.0, worn=.12):
    m = A.Mat(name)
    tw = m.wave(scale, 'DIAGONAL', 0.0, 0)          # twill
    n = m.noise(6, 4, .6); fine = m.noise(60, 3, .5)
    col = m.mul(c, m.remap(n, .3, .7, 1 - worn, 1 + worn * .6))
    col = m.mul(col, m.remap(tw, 0, 1, .92, 1.04))
    return m.finish(col, m.remap(fine, 0, 1, rough - .08, rough + .06), 0.0, m.math('ADD', tw, m.math('MULTIPLY', fine, .3)), .25, .0015)

def m_armor(name, c=(.12, .15, .2)):
    return A.m_steel(name, c, .42, dark=True)

# ---------------------------------------------------------------- characters
def build(name, sex, hair, top, pants, boots, accent, hair_col, sleeves='long', acc=None, enemy=False):
    A.reset(); top, pants, boots, accent = top(), pants(), boots(), accent()  # materials must outlive the reset
    objs = imp(BODY.format(sex))
    for o in [o for o in objs if o.type == 'MESH' and o.name.startswith(('Icosphere',))]: bpy.data.objects.remove(o)
    arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE'); arm.name = 'rig'
    body = next(o for o in bpy.data.objects if o.type == 'MESH' and o.name.lower().startswith('superhero')); body.name = 'body'
    eyes = bpy.data.objects.get('Eyes'); brows = bpy.data.objects.get('Eyebrows')
    # skin: light texture, smaller; enemies get a frost-bitten pallor
    for n in body.active_material.node_tree.nodes:
        if n.type == 'TEX_IMAGE' and n.image:
            if n.image.colorspace_settings.name != 'Non-Color':
                n.image = bpy.data.images.load(os.path.join(UBC, 'Base Characters', 'Textures', LIGHT[sex]))
                if enemy: n.image = n.image.copy(); recolor(n.image, (.42, .5, .62), .1)
            shrink(n.image)
    # hair (separate rigged meshes) -> rebind to our skeleton
    for hname in hair:
        for o in imp(HAIR.format(hname)):
            if o.type == 'MESH' and not o.name.startswith('Icosphere'):
                for md in o.modifiers:
                    if md.type == 'ARMATURE': md.object = arm
                mw = o.matrix_world.copy(); o.parent = arm; o.matrix_world = mw
                tint_image_node(o.active_material, hair_col, .3)
                for n in o.active_material.node_tree.nodes:
                    if n.type == 'TEX_IMAGE' and n.image: shrink(n.image, 512)
            elif o.type != 'MESH' or o.name.startswith('Icosphere'):
                if o.name in bpy.data.objects: bpy.data.objects.remove(o)
    if brows: tint_image_node(brows.active_material, hair_col, .3)

    dom = dominant(body); co = [v.co for v in body.data.vertices]  # rest pose, armature at identity
    waist = bone_pos(arm, 'spine_01').z + .03; ankle = bone_pos(arm, 'foot_l').z
    sh_x = abs(bone_pos(arm, 'upperarm_l').x); el_x = abs(bone_pos(arm, 'lowerarm_l').x)
    TOP = ('spine_0', 'clavicle_', 'upperarm_') + (('lowerarm_',) if sleeves == 'long' else ())
    top_k = [d.startswith(TOP) or (d == 'pelvis' and c.z > waist) for d, c in zip(dom, co)]
    if sleeves != 'long': top_k = [k and abs(c.x) < sh_x + (el_x - sh_x) * .45 for k, c in zip(top_k, co)]
    leg_k = [d.startswith(('thigh_', 'calf_')) or (d == 'pelvis' and c.z <= waist + .04) for d, c in zip(dom, co)]
    boot_k = [d.startswith(('foot_', 'ball_')) or (d.startswith('calf_') and c.z < ankle + .2) for d, c in zip(dom, co)]
    belt_k = [abs(c.z - waist) < .04 and d in ('pelvis', 'spine_01') for d, c in zip(dom, co)]
    parts = [shell(body, top_k, .014, top, 'top', smooth=8), shell(body, leg_k, .01, pants, 'pants', smooth=6),
             shell(body, boot_k, .018, boots, 'boots', .005, 30), shell(body, belt_k, .018, accent, 'belt', .006, 1)]
    if enemy:  # helmet over the skull, leaving the face open
        eye_z = (eyes.matrix_world @ Vector(eyes.bound_box[0])).z if eyes else bone_pos(arm, 'Head').z + .08
        helm_k = [d in ('Head', 'neck_01') and (c.z > eye_z + .025 or c.y > bone_pos(arm, 'Head').y + .02) for d, c in zip(dom, co)]
        parts.append(shell(body, helm_k, .025, accent, 'helm', .008))
    hide_covered(body, [a or b or c for a, b, c in zip(top_k, leg_k, boot_k)])
    outfit = A.join(parts, 'outfit')
    A.bake_atlas(outfit, name + '_outfit', 1024, ao=.45)
    if acc: acc(arm, body, eyes)
    out = [arm]
    A.export(name, out)
    A.preview(name, out, view=(.55, -1.4, .25), zoom=1.0)
    print(f'[char] {name}: ok')

# ---- head props (parented to the Head bone)
def head_frame(arm, eyes):
    e = [eyes.matrix_world @ Vector(c) for c in eyes.bound_box]
    ec = sum(e, Vector()) / 8; head = bone_pos(arm, 'Head')
    return ec, head

def ring_around_head(arm, eyes, z_up, r_scale, minor, mat, name):
    ec, head = head_frame(arm, eyes); c = Vector((head.x, head.y + (ec.y - head.y) * .15, ec.z + z_up))
    o = A.prim('torus', mat, major_radius=.105 * r_scale, minor_radius=minor, major_segments=40, minor_segments=8, location=c)
    o.scale.y = 1.12; o.rotation_euler.x = math.radians(-8); A.activate(o); bpy.ops.object.transform_apply(scale=True, rotation=True)
    o.name = name; bone_attach(o, arm, 'Head'); return o

def acc_headband(c):
    return lambda arm, body, eyes: ring_around_head(arm, eyes, .055, 1.0, .012, m_fabric('band', c, .8), 'headband')

def acc_glasses(arm, body, eyes):
    ec, _ = head_frame(arm, eyes); fr = A.m_steel('frame', (.08, .08, .09), .35); parts = []
    front = min(Vector(c).y for c in eyes.bound_box) * eyes.scale.y + eyes.location.y - .012
    xs = sorted({round((eyes.matrix_world @ Vector(c)).x, 3) for c in eyes.bound_box}); sep = (xs[-1] - xs[0]) / 4
    for s in (-1, 1):
        r = A.prim('torus', fr, major_radius=.017, minor_radius=.0022, major_segments=24, minor_segments=6, location=(ec.x + s * (sep + .006), front, ec.z), rotation=(math.pi / 2, 0, 0))
        parts.append(r)
    parts.append(A.cyl(.0018, .0018, -.008, .008, fr, 6)); A.place(parts[-1], (ec.x, front, ec.z + .004), (0, math.pi / 2, 0))
    g = A.join(parts, 'glasses'); bone_attach(g, arm, 'Head')

def acc_goggles(arm, body, eyes):
    st = A.m_steel('gog', (.25, .25, .27), .35); band = ring_around_head(arm, eyes, .06, 1.0, .011, A.m_leather('gogband', (.05, .03, .02)), 'goggle_band')
    ec, _ = head_frame(arm, eyes); front = min((eyes.matrix_world @ Vector(c)).y for c in eyes.bound_box) - .015
    lens = A.glass('gog_lens', (1.0, .75, .25), .05, 1.5, (1.0, .7, .15), 1.5); parts = []
    for s in (-1, 1):
        parts += [A.prim('cylinder', st, vertices=20, radius=.022, depth=.022, location=(ec.x + s * .033, front - .004, ec.z + .068), rotation=(math.pi / 2 - .3, 0, 0))]
    g = A.join(parts, 'goggles'); bone_attach(g, arm, 'Head')
    for s in (-1, 1):
        l = A.prim('cylinder', lens, vertices=20, radius=.017, depth=.004, location=(ec.x + s * .033, front - .016, ec.z + .072), rotation=(math.pi / 2 - .3, 0, 0)); bone_attach(l, arm, 'Head')

CHARS = {
    'hero_sports': dict(sex='Male',   hair=['Hair_Buzzed', 'Eyebrows_Regular'], c1=0xECE7DC, c2=0xE2483D, hc=0x1D1B22, pants=0x22252E, sleeves='short', acc=lambda: acc_headband(lin(0xE2483D))),
    'hero_strat':  dict(sex='Female', hair=['Hair_Long', 'Eyebrows_Female'],   c1=0x2F4B3A, c2=0xD9C27A, hc=0xC9D3DA, pants=0x1E2A24),
    'hero_eeu':    dict(sex='Female', hair=['Hair_Long', 'Eyebrows_Female'],   c1=0x4A2F6B, c2=0xE6C35C, hc=0xB5452F, pants=0x241A33),
    'hero_interp': dict(sex='Female', hair=['Hair_Buns', 'Eyebrows_Female'],   c1=0x203A6E, c2=0xEDE6D6, hc=0x2A2140, pants=0x182440),
    'hero_sci':    dict(sex='Male',   hair=['Hair_SimpleParted', 'Eyebrows_Regular'], c1=0xE9EEF0, c2=0x2A9D8F, hc=0x5B3A29, pants=0x2B3440, acc=lambda: acc_glasses),
    'hero_eng':    dict(sex='Male',   hair=['Hair_Buzzed', 'Hair_Beard', 'Eyebrows_Regular'], c1=0x8A6A3B, c2=0xF2B33D, hc=0x222222, pants=0x2A2622, acc=lambda: acc_goggles),
}

UAL = os.path.join(SRC, '_extract')
KEEP = ['Idle_Loop', 'Walk_Loop', 'Jog_Fwd_Loop', 'Sprint_Loop', 'Roll', 'Punch_Jab', 'Punch_Cross', 'Sword_Attack', 'Sword_Idle',
        'Spell_Simple_Enter', 'Spell_Simple_Idle_Loop', 'Spell_Simple_Shoot', 'Pistol_Idle_Loop', 'Pistol_Shoot', 'Hit_Chest', 'Hit_Head',
        'Death01', 'Jump_Start', 'Jump_Land']

def anims():
    """Skeleton + the clips the game uses, cut from Quaternius' Universal Animation Library (CC0)."""
    A.reset()
    if not glob.glob(os.path.join(UAL, 'Universal Animation Library*')):
        zipfile.ZipFile(glob.glob(os.path.join(SRC, 'Universal Animation Library*.zip'))[0]).extractall(UAL)
    imp(glob.glob(os.path.join(UAL, 'Universal Animation Library*', 'Unreal-Godot', 'UAL1_Standard.glb'))[0])
    for o in [o for o in bpy.data.objects if o.type != 'ARMATURE']: bpy.data.objects.remove(o)
    arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE'); arm.name = 'rig'
    for a in list(bpy.data.actions):
        if a.name.split('|')[-1] not in KEEP: bpy.data.actions.remove(a)
    bpy.ops.object.select_all(action='DESELECT'); arm.select_set(True)
    bpy.ops.export_scene.gltf(filepath=os.path.join(A.DIR_M, 'anims.glb'), export_format='GLB', use_selection=True, export_animation_mode='ACTIONS')
    print('[char] anims:', len(bpy.data.actions), 'clips')

def run(name):
    if name == 'anims': return anims()
    if name == 'frost_soldier':
        build(name, 'Male', ['Eyebrows_Regular'], lambda: m_armor('sol_top'), lambda: m_armor('sol_legs', (.1, .12, .16)), lambda: A.m_leather('sol_boots', (.03, .03, .035)),
              lambda: A.m_steel('sol_trim', (.55, .75, .9), .25), lin(0xC9D3DA), enemy=True)
        return
    d = CHARS[name]
    build(name, d['sex'], d['hair'], lambda: m_fabric(name + '_top', lin(d['c1'])), lambda: m_fabric(name + '_pants', lin(d['pants']), .9),
          lambda: A.m_leather(name + '_boots', (.035, .025, .02), 0), lambda: A.m_leather(name + '_belt', lin(d['c2']), 0), lin(d['hc']),
          d.get('sleeves', 'long'), d['acc']() if 'acc' in d else None)

if __name__ == '__main__':
    want = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else ['anims'] + list(CHARS) + ['frost_soldier']
    for n in want: run(n)
