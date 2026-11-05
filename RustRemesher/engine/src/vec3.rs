// SPDX-License-Identifier: GPL-3.0-or-later
//! Minimal 3D vector (f32). No external math crate: the engine stays dependency-light for cross-compiling.

use std::ops::{Add, AddAssign, Div, DivAssign, Mul, MulAssign, Neg, Sub, SubAssign};

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct V3 {
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

pub const fn v3(x: f32, y: f32, z: f32) -> V3 {
    V3 { x, y, z }
}

impl V3 {
    pub const ZERO: V3 = v3(0.0, 0.0, 0.0);

    pub const fn splat(s: f32) -> V3 {
        v3(s, s, s)
    }
    pub fn from_array(a: [f32; 3]) -> V3 {
        v3(a[0], a[1], a[2])
    }
    pub fn to_array(self) -> [f32; 3] {
        [self.x, self.y, self.z]
    }
    pub fn dot(self, o: V3) -> f32 {
        self.x * o.x + self.y * o.y + self.z * o.z
    }
    pub fn cross(self, o: V3) -> V3 {
        v3(self.y * o.z - self.z * o.y, self.z * o.x - self.x * o.z, self.x * o.y - self.y * o.x)
    }
    pub fn length2(self) -> f32 {
        self.dot(self)
    }
    pub fn length(self) -> f32 {
        self.length2().sqrt()
    }
    /// Unit vector, or `fallback` when the length is ~0.
    pub fn safe_normalize(self, fallback: V3) -> V3 {
        let l = self.length();
        if l > 1e-20 {
            self / l
        } else {
            fallback
        }
    }
    /// Unit vector, or +Z when the length is ~0.
    pub fn normalized(self) -> V3 {
        self.safe_normalize(v3(0.0, 0.0, 1.0))
    }
    pub fn min(self, o: V3) -> V3 {
        v3(self.x.min(o.x), self.y.min(o.y), self.z.min(o.z))
    }
    pub fn max(self, o: V3) -> V3 {
        v3(self.x.max(o.x), self.y.max(o.y), self.z.max(o.z))
    }
    /// Component by axis index (0, 1, 2).
    pub fn get(self, i: usize) -> f32 {
        match i {
            0 => self.x,
            1 => self.y,
            _ => self.z,
        }
    }
}

impl Add for V3 {
    type Output = V3;
    fn add(self, o: V3) -> V3 {
        v3(self.x + o.x, self.y + o.y, self.z + o.z)
    }
}
impl Sub for V3 {
    type Output = V3;
    fn sub(self, o: V3) -> V3 {
        v3(self.x - o.x, self.y - o.y, self.z - o.z)
    }
}
impl Neg for V3 {
    type Output = V3;
    fn neg(self) -> V3 {
        v3(-self.x, -self.y, -self.z)
    }
}
impl Mul<f32> for V3 {
    type Output = V3;
    fn mul(self, s: f32) -> V3 {
        v3(self.x * s, self.y * s, self.z * s)
    }
}
impl Mul<V3> for f32 {
    type Output = V3;
    fn mul(self, v: V3) -> V3 {
        v * self
    }
}
impl Div<f32> for V3 {
    type Output = V3;
    fn div(self, s: f32) -> V3 {
        v3(self.x / s, self.y / s, self.z / s)
    }
}
impl AddAssign for V3 {
    fn add_assign(&mut self, o: V3) {
        *self = *self + o;
    }
}
impl SubAssign for V3 {
    fn sub_assign(&mut self, o: V3) {
        *self = *self - o;
    }
}
impl MulAssign<f32> for V3 {
    fn mul_assign(&mut self, s: f32) {
        *self = *self * s;
    }
}
impl DivAssign<f32> for V3 {
    fn div_assign(&mut self, s: f32) {
        *self = *self / s;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn basic_algebra() {
        let a = v3(1.0, 2.0, 3.0);
        let b = v3(4.0, 5.0, 6.0);
        assert_eq!(a + b, v3(5.0, 7.0, 9.0));
        assert_eq!(b - a, v3(3.0, 3.0, 3.0));
        assert_eq!(a.dot(b), 32.0);
        assert_eq!(v3(1.0, 0.0, 0.0).cross(v3(0.0, 1.0, 0.0)), v3(0.0, 0.0, 1.0));
        assert_eq!(2.0 * a, a * 2.0);
        assert_eq!(a.get(1), 2.0);
        assert!((v3(3.0, 4.0, 0.0).length() - 5.0).abs() < 1e-6);
    }

    #[test]
    fn normalize_handles_zero() {
        assert_eq!(V3::ZERO.normalized(), v3(0.0, 0.0, 1.0));
        assert_eq!(V3::ZERO.safe_normalize(v3(1.0, 0.0, 0.0)), v3(1.0, 0.0, 0.0));
        assert!((v3(0.0, 3.0, 0.0).normalized().y - 1.0).abs() < 1e-6);
    }
}
