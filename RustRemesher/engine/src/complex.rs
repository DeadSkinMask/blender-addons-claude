// SPDX-License-Identifier: GPL-3.0-or-later
//! Complex numbers (f64) and the `Scalar` trait the sparse solver is generic over (real or complex).

use std::ops::{Add, AddAssign, Mul, Neg, Sub, SubAssign};

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct C64 {
    pub re: f64,
    pub im: f64,
}

pub const fn c64(re: f64, im: f64) -> C64 {
    C64 { re, im }
}

impl C64 {
    pub const ZERO: C64 = c64(0.0, 0.0);
    pub const ONE: C64 = c64(1.0, 0.0);

    /// r e^{i theta}
    pub fn polar(r: f64, theta: f64) -> C64 {
        c64(r * theta.cos(), r * theta.sin())
    }
    pub fn arg(self) -> f64 {
        self.im.atan2(self.re)
    }
    pub fn abs(self) -> f64 {
        self.re.hypot(self.im)
    }
    /// Squared magnitude.
    pub fn norm(self) -> f64 {
        self.re * self.re + self.im * self.im
    }
    pub fn conj(self) -> C64 {
        c64(self.re, -self.im)
    }
}

impl Add for C64 {
    type Output = C64;
    fn add(self, o: C64) -> C64 {
        c64(self.re + o.re, self.im + o.im)
    }
}
impl Sub for C64 {
    type Output = C64;
    fn sub(self, o: C64) -> C64 {
        c64(self.re - o.re, self.im - o.im)
    }
}
impl Neg for C64 {
    type Output = C64;
    fn neg(self) -> C64 {
        c64(-self.re, -self.im)
    }
}
impl Mul for C64 {
    type Output = C64;
    fn mul(self, o: C64) -> C64 {
        c64(self.re * o.re - self.im * o.im, self.re * o.im + self.im * o.re)
    }
}
impl Mul<f64> for C64 {
    type Output = C64;
    fn mul(self, s: f64) -> C64 {
        c64(self.re * s, self.im * s)
    }
}
impl Mul<C64> for f64 {
    type Output = C64;
    fn mul(self, c: C64) -> C64 {
        c * self
    }
}
impl AddAssign for C64 {
    fn add_assign(&mut self, o: C64) {
        *self = *self + o;
    }
}
impl SubAssign for C64 {
    fn sub_assign(&mut self, o: C64) {
        *self = *self - o;
    }
}

/// What the solver needs from its scalar: arithmetic, complex conjugate, and scaling by a real number.
pub trait Scalar:
    Copy + Default + Send + Sync + Add<Output = Self> + Sub<Output = Self> + Mul<Output = Self> + Neg<Output = Self> + AddAssign + SubAssign
{
    fn real(x: f64) -> Self;
    fn conj(self) -> Self;
    fn re(self) -> f64;
    /// Squared magnitude.
    fn abs2(self) -> f64;
    fn scale(self, s: f64) -> Self;
}

impl Scalar for f64 {
    fn real(x: f64) -> f64 {
        x
    }
    fn conj(self) -> f64 {
        self
    }
    fn re(self) -> f64 {
        self
    }
    fn abs2(self) -> f64 {
        self * self
    }
    fn scale(self, s: f64) -> f64 {
        self * s
    }
}

impl Scalar for C64 {
    fn real(x: f64) -> C64 {
        c64(x, 0.0)
    }
    fn conj(self) -> C64 {
        C64::conj(self)
    }
    fn re(self) -> f64 {
        self.re
    }
    fn abs2(self) -> f64 {
        self.norm()
    }
    fn scale(self, s: f64) -> C64 {
        self * s
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn arithmetic() {
        let a = c64(1.0, 2.0);
        let b = c64(3.0, -1.0);
        assert_eq!(a + b, c64(4.0, 1.0));
        assert_eq!(a - b, c64(-2.0, 3.0));
        assert_eq!(a * b, c64(5.0, 5.0));
        assert_eq!(a.conj(), c64(1.0, -2.0));
        assert_eq!(a.norm(), 5.0);
        assert_eq!(2.0 * a, c64(2.0, 4.0));
    }

    #[test]
    fn polar_and_arg_roundtrip() {
        for k in -7..8 {
            let t = k as f64 * 0.4;
            let z = C64::polar(2.0, t);
            assert!((z.abs() - 2.0).abs() < 1e-12);
            assert!((z.arg() - t).abs() < 1e-12, "{} vs {}", z.arg(), t);
        }
    }

    #[test]
    fn scalar_trait_agrees_for_real_and_complex() {
        assert_eq!(<f64 as Scalar>::real(2.0).scale(3.0), 6.0);
        assert_eq!(<C64 as Scalar>::real(2.0).scale(3.0), c64(6.0, 0.0));
        assert_eq!(c64(3.0, 4.0).abs2(), 25.0);
        assert_eq!((-2.0f64).abs2(), 4.0);
    }
}
