# Procedural PBR asset builder for 총합본 북진 작전.
# Run:  blender -b -P asset/src/build_assets.py -- [asset names...]   (no names = build all)
# Every asset is modelled in Blender, its procedural shaders are baked into a texture atlas
# (base color w/ AO, roughness, metallic, normal) and exported as a self-contained GLB.
import bpy, bmesh, math, os, sys, random
import numpy as np
from mathutils import Vector, Matrix, noise as mn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_M, DIR_T, DIR_P = (os.path.join(ROOT, d) for d in ('models', 'textures', 'previews'))
TMP = os.path.join(bpy.app.tempdir or ROOT, 'bake')
for d in (DIR_M, DIR_T, DIR_P, TMP): os.makedirs(d, exist_ok=True)
RES = int(os.environ.get('ASSET_RES', 1024))

# ------------------------------------------------------------------ scene
def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'
    sc.cycles.device = 'CPU'
    sc.render.bake.margin = 8
    return sc

def activate(obj):
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = obj; obj.select_set(True)

def apply_mods(obj):
    activate(obj)
    for m in list(obj.modifiers): bpy.ops.object.modifier_apply(modifier=m.name)

def smooth(obj, angle=35):
    activate(obj); bpy.ops.object.shade_smooth_by_angle(angle=math.radians(angle))

def bevel(obj, w, seg=2, angle=40):
    m = obj.modifiers.new('bev', 'BEVEL'); m.width = w; m.segments = seg
    m.limit_method = 'ANGLE'; m.angle_limit = math.radians(angle); m.harden_normals = False
    apply_mods(obj)

def join(objs, name):
    activate(objs[0])
    for o in objs: o.select_set(True)
    bpy.ops.object.join(); o = bpy.context.view_layer.objects.active; o.name = o.data.name = name
    return o

def mesh_obj(name, bm):
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(o); return o

def prim(kind, mat=None, part=None, **kw):
    getattr(bpy.ops.mesh, 'primitive_' + kind + '_add')(**kw)
    o = bpy.context.view_layer.objects.active
    if mat: o.data.materials.append(mat)
    if part is not None: tag(o, part)
    return o

def tag(o, part):
    a = o.data.attributes.get('part') or o.data.attributes.new('part', 'INT', 'FACE')
    a.data.foreach_set('value', [part] * len(o.data.polygons))

def displace(obj, amp, freq, octaves=5, seed=0, basis='PERLIN_ORIGINAL', ridged=False, axis_scale=(1, 1, 1)):
    off = Vector((seed * 13.7, seed * 7.3, seed * 3.1))
    bm = bmesh.new(); bm.from_mesh(obj.data); bm.normal_update()
    for v in bm.verts:
        p = Vector(c * s for c, s in zip(v.co, axis_scale)) * freq + off
        n = (mn.ridged_multi_fractal(p, 1.0, 2.0, octaves, 1.0, 2.0, noise_basis=basis) - 1.0) if ridged else mn.fractal(p, 1.0, 2.0, octaves, noise_basis=basis)
        v.co += v.normal * n * amp
    bm.to_mesh(obj.data); bm.free(); obj.data.update()

def decimate(obj, faces):
    n = len(obj.data.polygons)
    if n > faces:
        m = obj.modifiers.new('dec', 'DECIMATE'); m.ratio = faces / n; apply_mods(obj)

def loft(name, rings, cap0='ngon', cap1='ngon', mat=None):
    """rings: list of vertex rings (lists of Vector, equal length). cap: 'ngon' | Vector apex | None."""
    bm = bmesh.new(); vs = [[bm.verts.new(p) for p in r] for r in rings]; k = len(rings[0])
    for a, b in zip(vs, vs[1:]):
        for i in range(k): bm.faces.new((a[i], a[(i + 1) % k], b[(i + 1) % k], b[i]))
    for ring, cap, rev in ((vs[0], cap0, True), (vs[-1], cap1, False)):
        if cap == 'ngon': bm.faces.new(ring[::-1] if rev else ring)
        elif cap is not None:
            ap = bm.verts.new(cap)
            for i in range(k):
                f = (ring[i], ring[(i + 1) % k], ap); bm.faces.new(f[::-1] if rev else f)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    o = mesh_obj(name, bm)
    if mat: o.data.materials.append(mat)
    return o

def ring(z, rx, ry=None, n=16, x=0.0, y=0.0, rot=0.0):
    ry = rx if ry is None else ry
    return [Vector((x + rx * math.cos(rot + i / n * math.tau), y + ry * math.sin(rot + i / n * math.tau), z)) for i in range(n)]

# ------------------------------------------------------------------ material DSL
CH = {}  # material name -> {'color','rough','metal'}: socket or constant (for baking via emission)

class Mat:
    def __init__(s, name):
        s.m = bpy.data.materials.new(name)
        if s.m.node_tree is None: s.m.use_nodes = True
        s.nt = s.m.node_tree; s.nt.nodes.clear()
        s.out = s.n('ShaderNodeOutputMaterial'); s.bsdf = s.n('ShaderNodeBsdfPrincipled')
        s.link(s.bsdf.outputs[0], s.out.inputs['Surface'])
        tc = s.n('ShaderNodeTexCoord'); s.co, s.onrm = tc.outputs['Object'], tc.outputs['Normal']

    def n(s, t, **kw):
        nd = s.nt.nodes.new(t)
        for k, v in kw.items():
            if hasattr(nd, k) and not isinstance(v, bpy.types.NodeSocket): setattr(nd, k, v)
            else: s.set(nd.inputs[k], v)
        return nd

    def link(s, a, b): s.nt.links.new(a, b)

    def set(s, sock, v):
        if isinstance(v, bpy.types.NodeSocket): s.link(v, sock)
        else: sock.default_value = v

    def stretch(s, sc, vec=None):
        m = s.n('ShaderNodeMapping'); s.set(m.inputs['Vector'], vec or s.co); m.inputs['Scale'].default_value = sc
        return m.outputs[0]

    def warp(s, scale, amt, vec=None):
        nd = s.n('ShaderNodeTexNoise', Scale=scale, Detail=4.0); s.set(nd.inputs['Vector'], vec or s.co)
        off = s.n('ShaderNodeVectorMath', operation='MULTIPLY_ADD'); s.link(nd.outputs['Color'], off.inputs[0])
        off.inputs[1].default_value = (amt, amt, amt); off.inputs[2].default_value = (-amt / 2,) * 3
        add = s.n('ShaderNodeVectorMath', operation='ADD'); s.set(add.inputs[0], vec or s.co); s.link(off.outputs[0], add.inputs[1])
        return add.outputs[0]

    def noise(s, scale, detail=6, rough=.55, vec=None, dist=0.0, kind='FBM'):
        nd = s.n('ShaderNodeTexNoise', noise_type=kind, Scale=scale, Detail=detail, Roughness=rough, Distortion=dist)
        s.set(nd.inputs['Vector'], vec or s.co); return nd.outputs['Fac']

    def voronoi(s, scale, feature='F1', vec=None, rand=1.0):
        nd = s.n('ShaderNodeTexVoronoi', feature=feature, Scale=scale, Randomness=rand)
        s.set(nd.inputs['Vector'], vec or s.co); return nd.outputs['Distance']

    def wave(s, scale, axis='Z', dist=2.0, detail=3, vec=None, profile='SIN'):
        nd = s.n('ShaderNodeTexWave', wave_type='BANDS', bands_direction=axis, wave_profile=profile, Scale=scale, Distortion=dist, Detail=detail)
        s.set(nd.inputs['Vector'], vec or s.co); return nd.outputs['Fac']

    def ramp(s, fac, stops):
        nd = s.n('ShaderNodeValToRGB'); el = nd.color_ramp.elements
        while len(el) < len(stops): el.new(0)
        for e, (p, c) in zip(el, stops): e.position = p; e.color = (*c, 1) if len(c) == 3 else (c, c, c, 1)
        s.link(fac, nd.inputs[0]); return nd.outputs['Color']

    def math(s, op, a, b=0.0, clamp=False):
        nd = s.n('ShaderNodeMath', operation=op, use_clamp=clamp); s.set(nd.inputs[0], a); s.set(nd.inputs[1], b); return nd.outputs[0]

    def remap(s, v, a, b, c=0.0, d=1.0):
        nd = s.n('ShaderNodeMapRange'); s.set(nd.inputs['Value'], v)
        for k, x in zip(('From Min', 'From Max', 'To Min', 'To Max'), (a, b, c, d)): nd.inputs[k].default_value = x
        return nd.outputs['Result']

    def mix(s, fac, a, b, blend='MIX'):  # colors (3-tuple or socket)
        nd = s.n('ShaderNodeMix', data_type='RGBA', blend_type=blend); s.set(nd.inputs[0], fac)
        s.set(nd.inputs[6], (*a, 1) if isinstance(a, tuple) else a); s.set(nd.inputs[7], (*b, 1) if isinstance(b, tuple) else b)
        return nd.outputs[2]

    def mul(s, col, f):  # col * f
        nd = s.n('ShaderNodeMix', data_type='RGBA', blend_type='MULTIPLY'); nd.inputs[0].default_value = 1.0
        s.set(nd.inputs[6], (*col, 1) if isinstance(col, tuple) else col); s.set(nd.inputs[7], f); return nd.outputs[2]

    def mixf(s, fac, a, b):
        nd = s.n('ShaderNodeMix', data_type='FLOAT'); s.set(nd.inputs[0], fac); s.set(nd.inputs[2], a); s.set(nd.inputs[3], b); return nd.outputs[0]

    def comp(s, i):  # object-space normal component (0=x,1=y,2=z)
        sep = s.n('ShaderNodeSeparateXYZ'); s.link(s.onrm, sep.inputs[0]); return sep.outputs[i]

    def axis(s, i):
        sep = s.n('ShaderNodeSeparateXYZ'); s.link(s.co, sep.inputs[0]); return sep.outputs[i]

    def pointiness(s):
        return s.n('ShaderNodeNewGeometry').outputs['Pointiness']

    def finish(s, color, rough, metal=0.0, height=None, bump=.5, bump_dist=.01, **extra):
        s.set(s.bsdf.inputs['Base Color'], (*color, 1) if isinstance(color, tuple) else color)
        s.set(s.bsdf.inputs['Roughness'], rough); s.set(s.bsdf.inputs['Metallic'], metal)
        if height is not None:
            b = s.n('ShaderNodeBump', Strength=bump, Distance=bump_dist); s.link(height, b.inputs['Height']); s.link(b.outputs[0], s.bsdf.inputs['Normal'])
        for k, v in extra.items(): s.set(s.bsdf.inputs[k.replace('_', ' ')], v)
        CH[s.m.name] = dict(color=color, rough=rough, metal=metal)
        return s.m

def plain(name, color, rough=.5, metal=0.0, **extra):
    """Non-baked material: exports straight to glTF (glass, glow, flat parts)."""
    m = Mat(name); return m.finish(color, rough, metal, **extra)

def glass(name, tint=(.85, .95, 1.0), rough=.04, ior=1.45, emit=None, emit_k=0.0):
    ex = dict(Transmission_Weight=1.0, IOR=ior)
    if emit: ex.update(Emission_Color=(*emit, 1), Emission_Strength=emit_k)
    return plain(name, tint, rough, **ex)

def glow(name, c, k=4.0):
    return plain(name, c, .4, Emission_Color=(*c, 1), Emission_Strength=k)

# ------------------------------------------------------------------ material library
def m_rock(name='rock', snow=.55, s=1.0, tint=(1, 1, 1), frost=False):
    m = Mat(name)
    big = m.noise(2.2 / s, 8, .62); fine = m.noise(22 / s, 6, .6)
    strata = m.wave(1.4 / s, 'Z', 6.0, 4)
    cr = m.remap(m.voronoi(2.2 / s, 'DISTANCE_TO_EDGE', m.warp(1.5 / s, .9 * s)), 0.0, .035)
    pal = ((0, (.11, .105, .1)), (.45, (.29, .28, .265)), (.7, (.42, .41, .39)), (1, (.6, .59, .56)))
    col = m.ramp(m.math('ADD', m.math('MULTIPLY', big, .8), m.math('MULTIPLY', strata, .2)), pal)
    col = m.mul(col, m.remap(fine, .3, .75, .75, 1.0))
    col = m.mix(1.0, col, tint, 'MULTIPLY')
    col = m.mix(m.remap(cr, 0, 1, .6, 1), (.05, .05, .055), col)
    if frost: col = m.mix(m.math('MULTIPLY', m.math('SUBTRACT', 1.0, cr), m.remap(fine, .4, .6, .1, .55)), col, (.55, .72, .85))
    h = m.math('ADD', m.math('MULTIPLY', big, .6), m.math('ADD', m.math('MULTIPLY', fine, .25), m.math('MULTIPLY', cr, .35)))
    r = m.remap(fine, 0, 1, .62, .92)
    if snow:
        sn = m.remap(m.math('ADD', m.comp(2), m.math('MULTIPLY', m.math('SUBTRACT', big, .5), .7)), snow, snow + .12)
        sc = m.mix(m.remap(m.noise(40 / s, 4), .3, .7), (.66, .74, .85), (.88, .91, .95))
        col = m.mix(sn, col, sc); r = m.mixf(sn, r, .5); h = m.mixf(sn, h, m.math('ADD', .55, m.math('MULTIPLY', big, .15)))
    return m.finish(col, r, 0.0, h, .55, .012 * s)

def m_steel(name='steel', c=(.55, .56, .57), rough=.3, dark=False):
    m = Mat(name)
    big = m.noise(4, 4, .5); scr = m.noise(60, 2, .5, m.stretch((1, 1, 30)))
    scr2 = m.noise(60, 2, .5, m.stretch((30, 1, 1)))
    s = m.remap(m.math('MAXIMUM', scr, scr2), .62, .7)
    edge = m.remap(m.pointiness(), .5, .56)
    col = m.mul(c, m.remap(big, .3, .7, .82, 1.0))
    col = m.mix(m.math('MAXIMUM', edge, m.math('MULTIPLY', s, .5)), col, tuple(min(1, x * 1.35 + .05) for x in c))
    if dark: col = m.mix(m.remap(big, .5, .7, 0, .45), col, (.08, .075, .07))  # grime
    r = m.math('ADD', m.math('ADD', rough, m.math('MULTIPLY', big, .2)), m.math('MULTIPLY', s, -.12))
    return m.finish(col, r, 1.0, m.math('MULTIPLY', s, -1.0), .15, .002)

def m_brass(name='brass'):
    return m_steel(name, (.78, .52, .22), .32)

def m_wood(name='wood', c0=(.10, .05, .025), c1=(.30, .16, .07)):
    m = Mat(name)
    v = m.stretch((14, 14, .7))
    g = m.noise(3, 10, .6, v, 2.0); knots = m.remap(m.noise(1.5, 2, .5), .62, .75)
    col = m.ramp(g, ((0, c0), (.55, tuple((a + b) / 2 for a, b in zip(c0, c1))), (1, c1)))
    col = m.mix(knots, col, c0)
    return m.finish(col, m.remap(g, 0, 1, .7, .5), 0.0, g, .25, .004)

def m_leather(name='leather', c=(.07, .04, .025), wrap=40.0):
    m = Mat(name)
    pores = m.voronoi(260, 'F1'); n = m.noise(30, 5, .6)
    bands = m.wave(wrap, 'Z', 0.0, 0, profile='SAW')
    col = m.mul(c, m.remap(n, .3, .7, .7, 1.2))
    col = m.mix(m.remap(bands, .85, 1.0), col, tuple(x * .45 for x in c))
    h = m.math('ADD', m.math('MULTIPLY', bands, .6), m.math('MULTIPLY', pores, .15))
    return m.finish(col, m.remap(n, 0, 1, .5, .72), 0.0, h, .4, .003)

def m_cloth(name='cloth', c=(.42, .03, .03)):
    m = Mat(name); w = m.noise(400, 2, .5, m.stretch((1, 1, 6))); n = m.noise(12, 4)
    col = m.mul(c, m.remap(n, .3, .7, .75, 1.1))
    return m.finish(col, .92, 0.0, w, .2, .002)

def m_bone(name='bone', c=(.70, .64, .52)):
    m = Mat(name); g = m.noise(6, 8, .6, m.stretch((10, 10, 1)), 1.0); p = m.noise(80, 3)
    col = m.ramp(g, ((0, tuple(x * .8 for x in c)), (1, c)))
    col = m.mix(m.remap(p, .6, .8), col, tuple(x * .55 for x in c))
    return m.finish(col, m.remap(g, 0, 1, .55, .38), 0.0, m.math('ADD', g, m.math('MULTIPLY', p, .3)), .15, .003)

def m_stone(name='stone', snow=.62):
    m = Mat(name)
    big = m.noise(2.5, 7, .6); fine = m.noise(35, 5, .6)
    cr = m.remap(m.voronoi(3.0, 'DISTANCE_TO_EDGE', m.warp(1.2, .8, m.stretch((1, 1, .4)))), 0.0, .025)
    col = m.ramp(big, ((0, (.2, .2, .2)), (.6, (.36, .36, .35)), (1, (.5, .5, .48))))
    col = m.mul(col, m.remap(fine, .3, .75, .8, 1.05))
    col = m.mix(m.remap(cr, 0, 1, .55, 1), (.06, .06, .065), col)
    cav = m.remap(m.pointiness(), .47, .5, 1.0, 0.0)  # frost settles in crevices
    col = m.mix(m.math('MULTIPLY', cav, .3), col, (.7, .8, .9))
    sn = m.remap(m.math('ADD', m.comp(2), m.math('MULTIPLY', m.math('SUBTRACT', big, .5), .5)), snow, snow + .1)
    col = m.mix(sn, col, (.86, .9, .95))
    h = m.mixf(sn, m.math('ADD', m.math('MULTIPLY', big, .4), m.math('ADD', m.math('MULTIPLY', fine, .2), m.math('MULTIPLY', cr, .4))), .6)
    return m.finish(col, m.mixf(sn, m.remap(fine, 0, 1, .65, .88), .5), 0.0, h, .5, .006)

# ------------------------------------------------------------------ baking
def uv_unwrap(obj, margin=.004):
    activate(obj); bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(55), island_margin=margin, area_weight=0.0, correct_aspect=True, scale_to_bounds=True)
    bpy.ops.object.mode_set(mode='OBJECT')

def bake_atlas(obj, name, res=RES, ao=.4):
    """Bake all procedural materials on obj into one PBR texture set; replace them with a single image material."""
    sc = bpy.context.scene; uv_unwrap(obj); activate(obj)
    mats = [s.material for s in obj.material_slots]
    ms = [CH[m.name]['metal'] for m in mats]
    const_metal = ms[0] if all(not isinstance(x, bpy.types.NodeSocket) and x == ms[0] for x in ms) else None
    chans = ['color', 'rough', 'normal'] + ([] if const_metal is not None else ['metal'])
    imgs = {}
    for ch in chans:
        img = imgs[ch] = bpy.data.images.new(f'{name}_{ch}', res, res)
        if ch != 'color': img.colorspace_settings.name = 'Non-Color'
        tmp_nodes = []
        for m in mats:
            nt = m.node_tree; tn = nt.nodes.new('ShaderNodeTexImage'); tn.image = img; nt.nodes.active = tn; tmp_nodes.append((m, tn))
            if ch == 'normal': continue
            em = nt.nodes.new('ShaderNodeEmission'); tmp_nodes.append((m, em)); v = CH[m.name][ch]
            if ch == 'color' and ao:
                aon = nt.nodes.new('ShaderNodeAmbientOcclusion'); aon.samples = 24; aon.inputs['Distance'].default_value = .25
                k = nt.nodes.new('ShaderNodeMapRange'); k.inputs['To Min'].default_value = 1 - ao
                nt.links.new(aon.outputs['AO'], k.inputs['Value'])
                mul = nt.nodes.new('ShaderNodeMix'); mul.data_type = 'RGBA'; mul.blend_type = 'MULTIPLY'; mul.inputs[0].default_value = 1
                if isinstance(v, bpy.types.NodeSocket): nt.links.new(v, mul.inputs[6])
                else: mul.inputs[6].default_value = (*v, 1)
                nt.links.new(k.outputs[0], mul.inputs[7]); v = mul.outputs[2]; tmp_nodes += [(m, aon), (m, k), (m, mul)]
            if isinstance(v, bpy.types.NodeSocket): nt.links.new(v, em.inputs['Color'])
            else: em.inputs['Color'].default_value = (*v, 1) if isinstance(v, tuple) else (v, v, v, 1)
            nt.links.new(em.outputs[0], nt.nodes['Material Output'].inputs['Surface'])
        sc.cycles.samples = {'color': 6, 'normal': 4}.get(ch, 1)
        bpy.ops.object.bake(type='NORMAL' if ch == 'normal' else 'EMIT', normal_space='TANGENT', margin=8, use_clear=True)
        for m, nd in tmp_nodes: m.node_tree.nodes.remove(nd)
        for m in mats:
            nt = m.node_tree; nt.links.new(nt.nodes['Principled BSDF'].outputs[0], nt.nodes['Material Output'].inputs['Surface'])
        img.filepath_raw = os.path.join(TMP, img.name + '.png'); img.file_format = 'PNG'; img.save(); img.pack()
    # final material
    fm = bpy.data.materials.new(name)
    if fm.node_tree is None: fm.use_nodes = True
    nt = fm.node_tree; b = nt.nodes['Principled BSDF']
    def tex(ch):
        t = nt.nodes.new('ShaderNodeTexImage'); t.image = imgs[ch]; return t.outputs['Color']
    nt.links.new(tex('color'), b.inputs['Base Color'])
    sep = nt.nodes.new('ShaderNodeSeparateColor'); nt.links.new(tex('rough'), sep.inputs[0]); nt.links.new(sep.outputs[0], b.inputs['Roughness'])
    if const_metal is None:
        sep2 = nt.nodes.new('ShaderNodeSeparateColor'); nt.links.new(tex('metal'), sep2.inputs[0]); nt.links.new(sep2.outputs[0], b.inputs['Metallic'])
    else: b.inputs['Metallic'].default_value = const_metal
    nm = nt.nodes.new('ShaderNodeNormalMap'); nt.links.new(tex('normal'), nm.inputs['Color']); nt.links.new(nm.outputs[0], b.inputs['Normal'])
    obj.data.materials.clear(); obj.data.materials.append(fm)
    return fm

def origin_base(o):
    """Put the object origin at the centre of its footprint, on its lowest point."""
    vs = [o.matrix_world @ v.co for v in o.data.vertices]
    p = Vector(((min(v.x for v in vs) + max(v.x for v in vs)) / 2, (min(v.y for v in vs) + max(v.y for v in vs)) / 2, min(v.z for v in vs)))
    o.data.transform(Matrix.Translation(-(p - o.location))); o.location = p

def split_parts(obj, names):
    """Split a baked mesh by its 'part' face attribute -> {name: obj} (all share the atlas)."""
    out = {}
    for i, nm in enumerate(names):
        o = obj.copy(); o.data = obj.data.copy(); o.name = o.data.name = nm; bpy.context.scene.collection.objects.link(o)
        bm = bmesh.new(); bm.from_mesh(o.data); lay = bm.faces.layers.int.get('part')
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f[lay] != i], context='FACES'); bm.to_mesh(o.data); bm.free()
        o.data.attributes.remove(o.data.attributes['part']); out[nm] = o
    bpy.data.objects.remove(obj); return out

# ------------------------------------------------------------------ export & preview
def export(name, objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
        for ch in o.children_recursive: ch.select_set(True)
    bpy.ops.export_scene.gltf(filepath=os.path.join(DIR_M, name + '.glb'), export_format='GLB', use_selection=True,
                              export_apply=True, export_image_format='JPEG', export_yup=True)

def preview(name, objs, res=640, view=(1.0, -1.25, .55), samples=48, **kw):
    sc = bpy.context.scene
    pts = [o.matrix_world @ Vector(c) for o in bpy.context.scene.objects if o.type == 'MESH' for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    ctr, size = (lo + hi) / 2, (hi - lo).length
    gm = plain('pv_ground', (.72, .76, .82), .85)
    g = prim('plane', gm, size=size * 40, location=(ctr.x, ctr.y, lo.z))
    w = bpy.data.worlds.new('pv'); sc.world = w
    if w.node_tree is None: w.use_nodes = True
    bg = w.node_tree.nodes['Background']; bg.inputs['Color'].default_value = (.42, .52, .68, 1); bg.inputs['Strength'].default_value = .8
    sun = bpy.data.objects.new('sun', bpy.data.lights.new('sun', 'SUN')); sc.collection.objects.link(sun)
    sun.data.energy = 3.5; sun.data.angle = math.radians(4); sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); sc.collection.objects.link(cam); sc.camera = cam
    cam.data.lens = 50; d = Vector(view).normalized()
    cam.location = ctr + d * (size * .5 / math.tan(cam.data.angle / 2) * kw.get('zoom', 1.0))
    cam.rotation_euler = (ctr - cam.location).to_track_quat('-Z', 'Y').to_euler()
    sc.render.resolution_x = sc.render.resolution_y = res; sc.cycles.samples = samples; sc.cycles.use_denoising = True
    sc.view_settings.view_transform = 'AgX'; sc.view_settings.look = 'AgX - Medium High Contrast' if 'AgX - Medium High Contrast' in [i.identifier for i in sc.view_settings.bl_rna.properties['look'].enum_items] else 'None'
    sc.render.filepath = os.path.join(DIR_P, name + '.png'); sc.render.image_settings.file_format = 'PNG'
    bpy.ops.render.render(write_still=True)

def finish(name, objs, **pv):
    export(name, objs); preview(name, objs, **pv)
    print(f'[asset] {name}: ok')

# ================================================================== assets
ASSETS = {}
def asset(f): ASSETS[f.__name__] = f; return f

@asset
def snow_rocks():
    """Three snow-capped boulders sharing one atlas (nodes rock_a, rock_b, rock_c)."""
    mat = m_rock('rock_snow'); parts = []
    for i, (sx, sy, sz, x) in enumerate(((1.6, 1.2, .95, -2.2), (1.0, .9, .8, 0.6), (.7, .6, .55, 2.4))):
        o = prim('ico_sphere', mat, i, subdivisions=5, radius=1, location=(0, 0, 0))
        o.scale = (sx, sy, sz); bpy.ops.object.transform_apply(scale=True)
        displace(o, .32 * sx, .9 / sx, 6, seed=i * 3 + 1)
        displace(o, .06 * sx, 4.0 / sx, 4, seed=i + 9, ridged=True)
        bm = bmesh.new(); bm.from_mesh(o.data)
        zmin = -.35 * sz
        for v in bm.verts: v.co.z = max(v.co.z, zmin) - zmin
        bm.to_mesh(o.data); bm.free()
        decimate(o, 2400); o.location.x = x; bpy.ops.object.transform_apply(location=True)
        tag(o, i); smooth(o, 60); parts.append(o)
    whole = join(parts, 'rocks'); bake_atlas(whole, 'rock_snow')
    rocks = split_parts(whole, ['rock_a', 'rock_b', 'rock_c'])
    for o in rocks.values(): origin_base(o)
    finish('snow_rocks', list(rocks.values()))

def crystal(name, h, r, mat, tip=1.8, seed=0, n=6):
    rnd = random.Random(seed); j = [rnd.uniform(.85, 1.12) for _ in range(n)]
    rings = [[Vector((p.x * j[i], p.y * j[i], p.z)) for i, p in enumerate(ring(z, r * k, n=n))] for z, k in ((0, 1), (h, .96))]
    o = loft(name, rings, 'ngon', Vector((rnd.uniform(-.1, .1) * r, rnd.uniform(-.1, .1) * r, h + r * tip)), mat)
    bevel(o, r * .04, 1, 30); smooth(o, 20); return o

def place(o, loc=(0, 0, 0), rot=(0, 0, 0)):
    o.rotation_euler = rot; o.location = loc
    activate(o); bpy.ops.object.transform_apply(location=True, rotation=True, scale=True); return o

def cyl(r1, r2, z0, z1, mat, n=24, part=None, x=0.0, y=0.0):
    return prim('cone', mat, part, vertices=n, radius1=r1, radius2=r2, depth=z1 - z0, location=(x, y, (z0 + z1) / 2))

def box(sx, sy, sz, loc, mat, part=None, bev=0.0):
    o = prim('cube', mat, part, size=1, location=loc); o.scale = (sx, sy, sz)
    activate(o); bpy.ops.object.transform_apply(scale=True)
    if bev: bevel(o, bev, 2, 60)
    return o

@asset
def ice_crystals():
    """Ice crystal cluster on a frosted rock base (nodes: crystal_base, crystal_ice)."""
    ice = glass('ice', (.72, .9, 1.0), .05, 1.31, (.35, .75, 1.0), .35)
    base = prim('ico_sphere', m_rock('crystal_rock', snow=.5, s=.6, frost=True), subdivisions=4, radius=1)
    base.scale = (.85, .75, .35); bpy.ops.object.transform_apply(scale=True)
    displace(base, .18, 1.6, 5, seed=4); displace(base, .04, 6, 3, seed=5, ridged=True); decimate(base, 1500); smooth(base, 60)
    rnd = random.Random(7); shards = []
    for i in range(11):
        a = rnd.uniform(0, math.tau); d = 0 if i == 0 else rnd.uniform(.12, .55)
        h = 2.1 if i == 0 else rnd.uniform(.35, 1.5) * (1 - d * .6)
        c = crystal(f's{i}', h, h * rnd.uniform(.13, .18), ice, seed=i)
        place(c, (math.cos(a) * d, math.sin(a) * d, .1), (0, d * 1.1 + rnd.uniform(-.1, .15), a))  # lean outward along a
        shards.append(c)
    ice_o = join(shards, 'crystal_ice'); bake_atlas(base, 'crystal_rock', 512); base.name = 'crystal_base'
    finish('ice_crystals', [base, ice_o], view=(1, -1.2, .45))

@asset
def frost_pillar():
    """Weathered hex stone obelisk + floating crystal (nodes: pillar, pillar_crystal; crystal spins in-game)."""
    st = m_stone('pillar_stone'); parts = [cyl(.86, .82, 0, .26, st, 6), cyl(.74, .7, .26, .44, st, 6)]
    rings = []
    for z in (.44, .62, .66, .7, 1.5, 1.54, 1.58, 2.7, 2.74, 2.78, 3.0):
        r = .6 - (z - .44) * .045; k = .93 if z in (.66, 1.54, 2.74) else 1.0  # carved grooves
        rings.append(ring(z, r * k, n=6))
    parts += [loft('shaft', rings, 'ngon', 'ngon', st), cyl(.6, .62, 3.0, 3.16, st, 6), cyl(.4, .56, 3.16, 3.34, st, 6)]
    p = join(parts, 'pillar'); bevel(p, .018, 2, 35)
    # subdivide + light noise so edges read as weathered rather than CG-sharp
    bm = bmesh.new(); bm.from_mesh(p.data); bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=3, use_grid_fill=True); bm.to_mesh(p.data); bm.free()
    displace(p, .014, 7, 4, seed=2); decimate(p, 6000); smooth(p, 38); bake_atlas(p, 'pillar_stone')
    c = loft('pillar_crystal', [ring(-.06, .3, n=6), ring(.06, .3, n=6)], Vector((0, 0, -.55)), Vector((0, 0, .7)),
             glass('pillar_ice', (.7, .9, 1.0), .03, 1.31, (.45, .85, 1.0), 2.5))
    bevel(c, .01, 1, 30); c.location.z = 3.75
    finish('frost_pillar', [p, c], view=(1, -1.4, .35))

def m_mountain(name, H):
    m = Mat(name)
    big = m.noise(.12, 9, .6); fine = m.noise(1.6, 6, .6)
    strata = m.wave(.12, 'Z', 12.0, 4)
    col = m.ramp(m.math('ADD', m.math('MULTIPLY', big, .92), m.math('MULTIPLY', strata, .08)),
                 ((0, (.04, .04, .045)), (.5, (.11, .105, .11)), (1, (.26, .25, .26))))
    col = m.mul(col, m.remap(fine, .3, .75, .7, 1.05))
    slope = m.remap(m.math('ADD', m.comp(2), m.math('MULTIPLY', m.math('SUBTRACT', fine, .5), .5)), .42, .55)
    height = m.remap(m.math('ADD', m.math('DIVIDE', m.axis(2), H), m.math('MULTIPLY', m.math('SUBTRACT', big, .5), .4)), .02, .2)
    sn = m.math('MULTIPLY', slope, height, True)
    col = m.mix(sn, col, m.mix(m.remap(fine, .3, .7), (.62, .7, .82), (.9, .93, .97)))
    h = m.mixf(sn, m.math('ADD', m.math('MULTIPLY', fine, .5), m.math('MULTIPLY', strata, .3)), .5)
    return m.finish(col, m.mixf(sn, .82, .55), 0.0, h, .6, .08)

@asset
def snow_mountain():
    """Backdrop mountain, 22 m tall / ~30 m wide (node: mountain). Scale freely in-game."""
    H, R, N = 22.0, 16.0, 170
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=N, y_subdivisions=N, size=R * 2.3); o = bpy.context.view_layer.objects.active
    for v in o.data.vertices:
        x, y = v.co.x, v.co.y; d = math.hypot(x, y * 1.25) / R
        d2 = math.hypot(x - 5.5, y + 2) / (R * .55)
        f = max(0.0, 1 - d) ** 1.5 + .45 * max(0.0, 1 - d2) ** 1.6
        rn = mn.ridged_multi_fractal(Vector((x, y, 0)) * .09, .9, 2.1, 7, 1.0, 2.2) / 2.2
        v.co.z = H * (f * .72 + f * rn * .42) + mn.fractal(Vector((x, y, 3)) * .5, 1, 2, 3) * .25 if f > 0 else -1.0
    bm = bmesh.new(); bm.from_mesh(o.data)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if all(v.co.z < -.5 for v in f.verts)], context='FACES')
    bm.to_mesh(o.data); bm.free(); o.name = o.data.name = 'mountain'
    o.data.materials.append(m_mountain('mountain_rock', H)); decimate(o, 9000); smooth(o, 70)
    bake_atlas(o, 'mountain', 2048, ao=.7)
    finish('snow_mountain', [o], view=(1, -1.4, .3), zoom=.85)

# ---------------------------------------------------------------- weapons (Z-up, grip at origin, business end +Z -> +Y in glTF)
@asset
def wpn_spear():
    """창기병 장창 3.4 m. origin = grip, tip +Y."""
    wood, steel, lea = m_wood('spear_wood', (.16, .1, .05), (.4, .27, .14)), m_steel('spear_steel'), m_leather('spear_leather')
    brass, cloth = m_brass('spear_brass'), m_cloth('spear_cloth', (.2, .04, .3))
    P = [loft('shaft', [ring(z, r, n=14) for z, r in ((-.8, .026), (0, .031), (1.0, .03), (1.75, .027))], 'ngon', 'ngon', wood)]
    P += [cyl(.034, .034, -.12, .38, lea, 16), cyl(.028, .018, -.9, -.8, steel, 14), cyl(.036, .036, 1.70, 1.74, brass, 16),
          cyl(.033, .022, 1.74, 1.98, steel, 14), cyl(.026, .026, 1.98, 2.0, brass, 14)]
    sec = [(2.0, .028, .014), (2.08, .062, .017), (2.17, .074, .018), (2.28, .062, .015), (2.4, .034, .01), (2.5, .012, .005)]
    P.append(loft('blade', [[Vector((w, 0, z)), Vector((0, t, z)), Vector((-w, 0, z)), Vector((0, -t, z))] for z, w, t in sec], 'ngon', Vector((0, 0, 2.58)), steel))
    rnd = random.Random(3)
    for i in range(14):  # tassel strands hanging below the socket
        a = i / 14 * math.tau; r = .03; L = rnd.uniform(.2, .28)
        P.append(loft(f'tas{i}', [ring(1.69, .006, n=5, x=math.cos(a) * r, y=math.sin(a) * r),
                                  ring(1.69 - L, .004, n=5, x=math.cos(a) * r * 1.9, y=math.sin(a) * r * 1.9)], 'ngon', 'ngon', cloth))
    o = join(P, 'spear'); smooth(o, 30); bake_atlas(o, 'spear', 1024, ao=.4)
    finish('wpn_spear', [o], view=(1, -1, .2), zoom=1.15)

@asset
def wpn_hammer():
    """공병 대형 해머 1.5 m. origin = grip, head +Y."""
    wood, lea, brass = m_wood('hm_wood', (.08, .045, .02), (.26, .14, .06)), m_leather('hm_leather'), m_brass('hm_brass')
    steel = m_steel('hm_steel', (.36, .37, .39), .38, dark=True)
    P = [cyl(.042, .038, -.22, 1.0, wood, 16), cyl(.047, .047, -.16, .42, lea, 16), cyl(.05, .03, -.3, -.22, steel, 16),
         box(.62, .36, .36, (0, 0, 1.12), steel, bev=.035), box(.07, .37, .37, (0, 0, 1.12), brass, bev=.006)]
    for sx in (-1, 1):
        f = cyl(.2, .2, -.05, .05, steel, 8); place(f, (sx * .34, 0, 1.12), (0, math.pi / 2, 0)); bevel(f, .015, 2, 40); P.append(f)
        P.append(box(.035, .385, .385, (sx * .2, 0, 1.12), brass, bev=.006))
        P.append(box(.07, .022, .32, (0, sx * .045, .92), steel, bev=.006))  # langets down the handle
        for k in range(3): P.append(prim('uv_sphere', brass, segments=10, ring_count=6, radius=.012, location=(0, sx * .058, .82 + k * .08)))
    o = join(P, 'hammer'); smooth(o, 35); bake_atlas(o, 'hammer', 1024, ao=.5)
    finish('wpn_hammer', [o], view=(1, -1.3, .35), zoom=1.1)

@asset
def wpn_staff():
    """언어 마법사 지팡이 1.9 m. origin = grip; orb node 'staff_orb' (emissive glass)."""
    bone, silver = m_bone('staff_bone'), m_steel('staff_silver', (.8, .8, .82), .22)
    rings = []
    for i in range(90):  # gently twisted, fluted shaft
        z = -.95 + i / 89 * 1.65; r0 = .03 + .006 * math.sin(z * 2.2)
        rings.append([Vector((math.cos(a) * r0 * (1 + .1 * math.sin(3 * a + z * 22)), math.sin(a) * r0 * (1 + .1 * math.sin(3 * a + z * 22)), z))
                      for a in (j / 16 * math.tau for j in range(16))])
    P = [loft('shaft', rings, 'ngon', 'ngon', bone), cyl(.026, .036, -1.02, -.95, silver, 16), cyl(.04, .036, .66, .74, silver, 16)]
    t = prim('torus', silver, major_radius=.16, minor_radius=.018, major_segments=48, minor_segments=10); place(t, (0, 0, .92), (math.pi / 2, 0, 0)); P.append(t)
    for sx in (-1, 1):  # two prongs from the collar up to the ring
        P.append(loft('pr', [ring(.72 + k * .016, .012 - k * .0015, n=8, x=sx * (.02 + .13 * math.sin(k / 4 * math.pi / 2))) for k in range(5)], 'ngon', 'ngon', silver))
    o = join(P, 'staff'); smooth(o, 40); bake_atlas(o, 'staff', 1024, ao=.4)
    orb = prim('uv_sphere', glass('staff_orb', (.55, .8, 1.0), .02, 1.5, (.44, .76, 1.0), 6.0), segments=32, ring_count=16, radius=.105, location=(0, 0, .92))
    activate(orb); bpy.ops.object.shade_smooth(); orb.name = 'staff_orb'
    finish('wpn_staff', [o, orb], view=(1, -1.2, .25), zoom=1.1)

@asset
def wpn_bow():
    """저격 궁수 리커브 보우 1.15 m. origin = grip, limbs along ±Y, string on the -X side."""
    wood, lea = m_wood('bow_wood', (.05, .025, .012), (.2, .1, .045)), m_leather('bow_leather', (.05, .03, .02), 70)
    brass, cord = m_brass('bow_brass'), m_cloth('bow_string', (.55, .5, .4))
    def path(t):
        s = max(0.0, (abs(t) - .78) / .22)
        return Vector((.12 * (1 - t * t) + .07 * s * s - .12, 0, .56 * t))
    def limb(t0, t1, n, w, th, name, mat):
        rs = []
        for i in range(n):
            t = t0 + (t1 - t0) * i / (n - 1); p = path(t); tg = (path(t + .01) - path(t - .01)).normalized()
            nx = Vector((tg.z, 0, -tg.x)); k = 1 - .55 * abs(t) ** 1.5
            rs.append([p + Vector((0, math.cos(a) * w(t) * k, 0)) + nx * math.sin(a) * th(t) * k for a in (j / 12 * math.tau for j in range(12))])
        return loft(name, rs, 'ngon', 'ngon', mat)
    P = [limb(-1, 1, 81, lambda t: .03, lambda t: .022 + (.016 if abs(t) < .13 else 0), 'limbs', wood),
         limb(-.12, .12, 9, lambda t: .036, lambda t: .042, 'grip', lea)]
    for sg in (-1, 1): P.append(prim('uv_sphere', brass, segments=10, ring_count=6, radius=.012, location=path(sg * .995)))
    a, b = path(-.985), path(.985)
    P.append(loft('string', [ring(a.z, .0028, n=6, x=a.x - .004), ring(b.z, .0028, n=6, x=b.x - .004)], 'ngon', 'ngon', cord))
    o = join(P, 'bow'); smooth(o, 40); bake_atlas(o, 'bow', 1024, ao=.35)
    finish('wpn_bow', [o], view=(1.2, -1, .2), zoom=1.1)

@asset
def wpn_flask():
    """연금술사 폭발 플라스크. origin = body centre; nodes flask (cork+wire), flask_glass, flask_liquid (emissive)."""
    g = glass('flask_glass', (.92, .97, .95), .03, 1.5)
    body = prim('uv_sphere', g, segments=40, ring_count=20, radius=.13)
    bm = bmesh.new(); bm.from_mesh(body.data)  # open the top so the neck joins cleanly
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if all(v.co.z > .118 for v in f.verts)], context='FACES'); bm.to_mesh(body.data); bm.free()
    neck = loft('neck', [ring(z, r, n=40) for z, r in ((.118, .052), (.14, .036), (.24, .034), (.25, .042), (.265, .042), (.27, .036))], None, None, g)
    gl = join([body, neck], 'flask_glass')
    sol = gl.modifiers.new('th', 'SOLIDIFY'); sol.thickness = .004; apply_mods(gl); bpy.ops.object.shade_smooth()
    liq = prim('uv_sphere', glow('flask_liquid', (.49, 1.0, .7), 2.2), segments=32, ring_count=16, radius=.12)
    bm = bmesh.new(); bm.from_mesh(liq.data)
    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=(0, 0, .03), plane_no=(0, 0, 1), clear_outer=True)
    bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary]); bm.to_mesh(liq.data); bm.free()
    activate(liq); bpy.ops.object.shade_smooth(); liq.name = 'flask_liquid'
    cork, brass = m_wood('cork', (.32, .2, .1), (.55, .38, .2)), m_brass('flask_brass')
    P = [cyl(.031, .036, .22, .3, cork, 20), prim('torus', brass, major_radius=.037, minor_radius=.005, location=(0, 0, .2)),
         prim('torus', brass, major_radius=.044, minor_radius=.005, location=(0, 0, .245))]
    o = join(P, 'flask'); smooth(o, 40); bake_atlas(o, 'flask', 512, ao=.3)
    finish('wpn_flask', [o, gl, liq], view=(1, -1.2, .4), zoom=.9)

@asset
def wpn_gauntlet():
    """격투가 건틀릿 (right hand; mirror X for left). origin = fist centre; glTF: knuckles +Z, wrist -Z, back of hand +Y."""
    lea, brass = m_leather('gt_leather', (.06, .035, .025), 0), m_brass('gt_brass')
    steel = m_steel('gt_steel', (.42, .43, .45), .3, dark=True)
    core = box(.17, .19, .2, (0, 0, 0), lea); core.modifiers.new('ss', 'SUBSURF').levels = 2; apply_mods(core)
    cuff = [cyl(.088, .1, .1, .24, steel, 20), cyl(.103, .103, .11, .13, brass, 20)]
    for c in cuff:  # wrist cuff runs along +Y, opposite the knuckles
        activate(c); bpy.ops.object.transform_apply(location=True); c.data.transform(Matrix.Rotation(-math.pi / 2, 4, 'X'))
    P = [core] + cuff
    for i in range(4):  # finger plates curled over the front
        x = -.06 + i * .04
        P += [box(.036, .05, .06, (x, -.1, .03), steel, bev=.008), box(.036, .045, .045, (x, -.08, -.04), steel, bev=.008)]
        P.append(prim('cone', steel, vertices=8, radius1=.014, radius2=0, depth=.035, location=(x, -.155, .055), rotation=(math.pi / 2, 0, 0)))
    P += [box(.18, .03, .05, (0, -.125, .055), brass, bev=.01), box(.15, .14, .025, (0, 0, .1), steel, bev=.01)]
    o = join(P, 'gauntlet'); smooth(o, 35); bake_atlas(o, 'gauntlet', 1024, ao=.45)
    strip = box(.15, .006, .012, (0, -.141, .055), glow('gt_glow', (1.0, .42, .24), 4.0)); strip.name = 'gauntlet_glow'
    finish('wpn_gauntlet', [o, strip], view=(.6, -1.4, .5), zoom=1.2)

# ---------------------------------------------------------------- enemy
@asset
def frost_golem():
    """서리 골렘(brute/boss). Joint nodes mirror makeEnemy: hips > shoulderL/R (armL/armR), hipL/R (legL/legR), head.
    Rest height ~2.3 m with hips at y=1.0 like the game rig; scale 1.65 (brute) / 2.05 (boss). Faces +Z in glTF."""
    rock = m_rock('golem_rock', snow=.72, s=.5, tint=(.75, .85, 1.0), frost=True)
    names = ['torso', 'head', 'armL', 'armR', 'legL', 'legR']; pid = {n: i for i, n in enumerate(names)}
    rnd = random.Random(11); chunks = []
    def chunk(part, sc, loc, amp=.22):
        o = prim('ico_sphere', rock, pid[part], subdivisions=3, radius=1); o.scale = sc; activate(o); bpy.ops.object.transform_apply(scale=True)
        m = min(sc); displace(o, amp * m * 1.6, 1.4 / m, 4, seed=rnd.randint(0, 99)); displace(o, .05 * m, 5 / m, 3, seed=rnd.randint(0, 99), ridged=True)
        decimate(o, 500); o.location = loc; activate(o); bpy.ops.object.transform_apply(location=True); chunks.append(o)
    chunk('torso', (.5, .36, .42), (0, 0, 1.55)); chunk('torso', (.36, .28, .22), (0, 0, 1.06)); chunk('torso', (.3, .22, .2), (0, .05, 1.25))
    chunk('head', (.24, .22, .21), (0, -.08, 2.05))
    for s, a, l in ((-1, 'armL', 'legL'), (1, 'armR', 'legR')):
        chunk(a, (.24, .22, .2), (s * .48, 0, 1.78)); chunk(a, (.13, .13, .24), (s * .46, 0, 1.4))
        chunk(a, (.15, .15, .22), (s * .46, -.02, 1.05)); chunk(a, (.2, .2, .2), (s * .46, -.03, .76))
        chunk(l, (.17, .17, .28), (s * .17, 0, .72)); chunk(l, (.15, .15, .24), (s * .17, 0, .3)); chunk(l, (.17, .22, .09), (s * .17, -.05, .06))
    for o in chunks: smooth(o, 50)
    whole = join(chunks, 'golem'); bake_atlas(whole, 'golem', 1024, ao=.6)
    parts = split_parts(whole, names)
    ice = glass('golem_ice', (.7, .9, 1.0), .05, 1.31, (.4, .8, 1.0), .8)
    def spikes(part, base, n, spread, tilt):
        out = []
        for i in range(n):
            h = rnd.uniform(.25, .5); c = crystal('k', h, h * .17, ice, seed=rnd.randint(0, 999))
            place(c, base + Vector((rnd.uniform(-spread, spread), rnd.uniform(-spread * .4, spread * .4), 0)),
                  (tilt[0] + rnd.uniform(-.25, .25), tilt[1] + rnd.uniform(-.25, .25), 0)); out.append(c)
        k = join(out, part + '_ice'); k.parent = parts[part]
    spikes('torso', Vector((0, .25, 1.75)), 6, .3, (-.6, 0)); spikes('armL', Vector((-.52, 0, 1.92)), 3, .08, (0, -.5)); spikes('armR', Vector((.52, 0, 1.92)), 3, .08, (0, .5))
    ey = join([box(.07, .02, .03, (s * .085, -.29, 2.08), glow('golem_eye', (.56, .89, 1.0), 6.0)) for s in (-1, 1)], 'head_eyes'); ey.parent = parts['head']
    def emp(name, loc, parent):
        e = bpy.data.objects.new(name, None); bpy.context.scene.collection.objects.link(e); e.location = loc; e.parent = parent; return e
    root = emp('frost_golem', (0, 0, 0), None); hips = emp('hips', (0, 0, 1.0), root)
    joints = {'torso': hips, 'head': hips, 'armL': emp('shoulderL', (-.42, 0, .72), hips), 'armR': emp('shoulderR', (.42, 0, .72), hips),
              'legL': emp('hipL', (-.16, 0, 0), hips), 'legR': emp('hipR', (.16, 0, 0), hips)}
    bpy.context.view_layer.update()
    for nm, o in parts.items():
        mw = o.matrix_world.copy(); o.parent = joints[nm]; o.matrix_world = mw
    finish('frost_golem', [root], view=(.8, -1.5, .3), zoom=.75)

# ---------------------------------------------------------------- tileable ground textures (numpy, periodic by construction)
def pnoise(n, beta, seed, fmin=1):
    rng = np.random.default_rng(seed); f = np.fft.fftfreq(n) * n
    fr = np.sqrt(f[:, None] ** 2 + f[None, :] ** 2); amp = np.where(fr >= fmin, 1 / np.maximum(fr, 1) ** beta, 0)
    x = np.real(np.fft.ifft2(np.fft.fft2(rng.normal(size=(n, n))) * amp)); return (x - x.min()) / np.ptp(x)

def pvoronoi_edge(n, k, seed):
    rng = np.random.default_rng(seed); pts = rng.random((k, 2)) * n
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32); d1 = np.full((n, n), 1e9, np.float32); d2 = d1.copy()
    for px, py in pts:
        dx = np.abs(xx - px); dx = np.minimum(dx, n - dx); dy = np.abs(yy - py); dy = np.minimum(dy, n - dy); d = np.hypot(dx, dy)
        d2 = np.minimum(d2, np.maximum(d1, d)); d1 = np.minimum(d1, d)
    return d2 - d1

def normal_from(h, k):
    dx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * k; dy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * k
    nrm = np.dstack([-dx, -dy, np.ones_like(h)]); nrm /= np.linalg.norm(nrm, axis=2, keepdims=True); return nrm * .5 + .5

def save_tex(name, rgb, srgb=True):
    n = rgb.shape[0]; img = bpy.data.images.new(name, n, n)
    if not srgb: img.colorspace_settings.name = 'Non-Color'
    if rgb.ndim == 2: rgb = np.dstack([rgb] * 3)
    img.pixels.foreach_set(np.dstack([np.clip(rgb, 0, 1), np.ones(rgb.shape[:2])]).astype(np.float32).ravel())
    img.filepath_raw = os.path.join(DIR_T, name + '.jpg'); img.file_format = 'JPEG'; img.save(quality=90)

@asset
def ground_textures():
    """1024² seamless PBR sets: snow_* (outer field) and ice_* (arena floor). Use RepeatWrapping."""
    n = 1024; lin = lambda c: np.array(c) ** 2.2
    big, mid, fine = pnoise(n, 2.4, 1), pnoise(n, 1.6, 2, 8), pnoise(n, 1.0, 3, 60)
    yy, xx = np.mgrid[0:n, 0:n] / n
    rip = .5 + .5 * np.sin(math.tau * (9 * xx + 4 * yy) + 6 * big)  # wind ripples; integer freq keeps it periodic
    h = big * .5 + rip * mid * .2 + fine * .15
    col = lin((.74, .8, .9)) + (lin((.95, .97, 1.0)) - lin((.74, .8, .9))) * np.clip((big * .7 + fine * .3) * 1.3 - .1, 0, 1)[..., None]
    save_tex('snow_color', col ** (1 / 2.2)); save_tex('snow_normal', normal_from(h, 14), False); save_tex('snow_rough', .62 + .25 * fine - .15 * rip * mid, False)
    cr = np.clip(1 - pvoronoi_edge(n, 26, 4) / 3.5, 0, 1) * (pnoise(n, 1.8, 5) > .38)
    cr2 = np.clip(1 - pvoronoi_edge(n, 90, 6) / 2.0, 0, 1) * (pnoise(n, 1.8, 7) > .55) * .6
    crack = np.maximum(cr, cr2); frost = np.clip((pnoise(n, 2.0, 8) - .5) * 1.8 + fine * .2, 0, 1) * .7
    deep, light, white = lin((.06, .2, .3)), lin((.36, .56, .68)), lin((.82, .9, .96))
    col = deep + (light - deep) * (big * .8 + .2)[..., None]
    col = col + (white - col) * np.clip(frost * .85 + crack * .9, 0, 1)[..., None]
    save_tex('ice_color', col ** (1 / 2.2)); save_tex('ice_rough', .06 + frost * .5 + crack * .3, False)
    save_tex('ice_normal', normal_from(-crack * .6 + frost * .15 + fine * .05, 6), False)
    print('[asset] ground_textures: ok')

# ================================================================== main
if __name__ == '__main__':
    want = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else list(ASSETS)
    for nm in want:
        reset(); random.seed(nm); ASSETS[nm]()
