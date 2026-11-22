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
        bounce = s.limit_bounce
        gravity = Vector(s.gravity)

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
                f_local[ax] = -k[ax] * (1.0 + prog * r * r) * off[ax]
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

            # hard limit per axis and side: the velocity component going out bounces back scaled by
            # limit_bounce (0 = it stops at the limit, the original behaviour)
            off = bt @ (self.pos - a_end)
            cl = Vector(off)
            for ax in range(3):
                cl[ax] = min(max(off[ax], -lim_neg[ax]), lim_pos[ax])
            if (cl - off).length_squared > 0.0:
                self.pos = a_end + b @ cl
                vl = bt @ (self.vel - av)
                for ax in range(3):
                    if cl[ax] != off[ax]:
                        if (off[ax] - cl[ax]) * vl[ax] > 0.0:
                            vl[ax] *= -bounce
                        elif bounce == 0.0:
                            vl[ax] = 0.0
                self.vel = av + b @ vl

        self.prev_anchor = anchor.copy()
        self.offset_world = self.pos - anchor
        return self.offset_world


def static_sag_local(s, bt):
    """Static equilibrium of the mass under gravity, in character axes (the offset it falls to).

    Per axis it solves k*(1 + prog*r^2)*|x| = m*|g| with r = |x| / limit of the side it falls toward,
    capped at that limit: the real sag of the simulation, not the linear m*g/omega^2 approximation."""
    m = max(s.mass, 0.001)
    gl = bt @ Vector(s.gravity)
    mo = s.max_offset
    out = Vector()
    for ax in range(3):
        k = max((s.frequency[ax] * math.tau) ** 2, 1e-6)
        side = 1.0 if gl[ax] >= 0.0 else -1.0
        lim = max((s.max_factor_pos[ax] if side > 0.0 else s.max_factor_neg[ax]) * mo, 1e-5)
        f = m * abs(gl[ax])
        if k * lim * (1.0 + s.progressive) <= f:
            out[ax] = side * lim                 # gravity beats the spring: it rests on the limit
            continue
        lo, hi = 0.0, lim
        for _ in range(40):                      # k*x*(1 + p*(x/lim)^2) is increasing: bisection
            x = 0.5 * (lo + hi)
            r = x / lim
            if k * x * (1.0 + s.progressive * r * r) < f:
                lo = x
            else:
                hi = x
        out[ax] = side * 0.5 * (lo + hi)
    return out


def rest_compensation(s, b):
    """World-space offset added when drawing the bone: it moves the bone UP by the sag, like raising the
    vertices by hand. It is NOT a force: the simulation is untouched."""
    if s.gravity_compensation == 0.0:
        return Vector()
    return b @ (static_sag_local(s, b.transposed()) * -s.gravity_compensation)
