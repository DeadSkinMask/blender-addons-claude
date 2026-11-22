"""Point-mass spring simulation of an inertial bone.

Pure maths (mathutils only, no bpy), a port of ``InertialBone3D`` from the Godot addon so both give the
same feel with the same numbers. The mass lives in world space, is pulled to the bone's anchor by a spring,
and damping acts on the velocity *relative to the anchor*, so a constant-speed walk leaves no offset and
only accelerations excite it.

Parameters are expressed in character axes: X = right, Y = up, Z = back (the Godot convention).
``b`` is the matrix whose columns are those three directions in world space.
"""
import math

from mathutils import Vector

MAX_SUBSTEP = 1.0 / 120.0
TELEPORT_DISTANCE = 1.0


class Sim:
    __slots__ = ("pos", "vel", "prev_anchor", "offset_world", "ready")

    def __init__(self):
        self.pos = Vector()
        self.vel = Vector()
        self.prev_anchor = Vector()
        self.offset_world = Vector()
        self.ready = False

    def reset(self, anchor):
        self.pos = anchor.copy()
        self.vel = Vector()
        self.prev_anchor = anchor.copy()
        self.offset_world = Vector()
        self.ready = True

    def step(self, s, dt, anchor, b, partner_off=None):
        """Advance by ``dt`` seconds. ``s`` is any object with the InertialBoneSettings attributes.
        Returns the world-space offset of the mass from the anchor."""
        if (not self.ready or dt <= 0.0
                or (anchor - self.prev_anchor).length > TELEPORT_DISTANCE):
            self.reset(anchor)
            return self.offset_world

        dt = min(dt, 0.1)
        bt = b.transposed()
        av = (anchor - self.prev_anchor) / dt
        n = max(1, math.ceil(dt / MAX_SUBSTEP))
        h = dt / n
        m = max(s.mass, 0.001)
        zeta = s.damping_ratio
        w = [f * math.tau for f in s.frequency]
        k = [x * x for x in w]
        c = [2.0 * zeta * x for x in w]
        mo = s.max_offset
        lim_pos = [max(f * mo, 1e-5) for f in s.max_factor_pos]
        lim_neg = [max(f * mo, 1e-5) for f in s.max_factor_neg]
        dpos, dneg = s.damping_pos, s.damping_neg
        prog, coupling = s.progressive, s.coupling
        gravity = Vector(s.gravity)
        # spring centre shifted against gravity: at rest k*center = -m*g_local cancels the static sag
        # (sag = m*g/omega^2), so the mass rests exactly at the modelled pose when compensation = 1
        comp = s.gravity_compensation
        center = [0.0, 0.0, 0.0]
        if comp != 0.0:
            gl = bt @ gravity
            center = [-comp * m * gl[ax] / max(k[ax], 1e-6) for ax in range(3)]

        for i in range(n):
            # forces use the anchor at the START of the substep (same snapshot as self.pos); the hard
            # limit uses the anchor at its end. Mixing them left a constant v*h offset at constant speed.
            a = self.prev_anchor.lerp(anchor, i / n)
            a_end = self.prev_anchor.lerp(anchor, (i + 1) / n)
            off = bt @ (self.pos - a)

            # spring with progressive stiffness near the limit
            f_local = Vector()
            for ax in range(3):
                lim = lim_pos[ax] if off[ax] >= 0.0 else lim_neg[ax]
                r = abs(off[ax]) / lim
                f_local[ax] = -k[ax] * (1.0 + prog * r * r) * (off[ax] - center[ax])
            if partner_off is not None:
                f_local += (bt @ (partner_off - (self.pos - a))) * coupling

            self.vel += ((b @ f_local) / m + gravity) * h

            # directional damping on the velocity relative to the anchor
            vl = bt @ (self.vel - av)
            for ax in range(3):
                mult = dpos[ax] if vl[ax] >= 0.0 else dneg[ax]
                vl[ax] *= math.exp(-c[ax] * mult / m * h)
            self.vel = av + b @ vl
            self.pos += self.vel * h

            # hard limit per axis and side (zeroes the velocity component that hits it)
            off = bt @ (self.pos - a_end)
            cl = Vector(off)
            for ax in range(3):
                cl[ax] = min(max(off[ax], -lim_neg[ax]), lim_pos[ax])
            if (cl - off).length_squared > 0.0:
                self.pos = a_end + b @ cl
                vl = bt @ (self.vel - av)
                for ax in range(3):
                    if cl[ax] != off[ax]:
                        vl[ax] = 0.0
                self.vel = av + b @ vl

        self.prev_anchor = anchor.copy()
        self.offset_world = self.pos - anchor
        return self.offset_world
