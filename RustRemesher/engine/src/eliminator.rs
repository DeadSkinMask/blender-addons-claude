// SPDX-License-Identifier: GPL-3.0-or-later
//! Linear constraints sum a_i x_i + c = 0, eliminated as they come: each picks one unknown (the pivot) and expresses
//! it by the others; expressions only ever mention unknowns still free. Real unknowns are eliminated first; integer
//! unknowns (seam shifts, feature lines, singularity positions) only through coefficients +-1, so they stay integer
//! combinations of the integers left. Port of the `Eliminator` in SculptForge's `QRemesh2Param.cpp`
//! (GPL-3.0-or-later).

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Term {
    pub var: u32,
    pub a: f64,
}

pub const fn term(var: u32, a: f64) -> Term {
    Term { var, a }
}

#[derive(Default)]
pub struct Eliminator {
    kind: Vec<u8>,
    elim: Vec<bool>,
    expr: Vec<Vec<Term>>,
    cst: Vec<f64>,
    users: Vec<Vec<u32>>,
    lost: usize,
}

fn gcd(a: i64, b: i64) -> i64 {
    let (mut a, mut b) = (a.abs(), b.abs());
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

/// Sorts by variable and sums equal ones.
fn merge_terms(mut v: Vec<Term>) -> Vec<Term> {
    v.sort_by_key(|t| t.var);
    let mut m: Vec<Term> = Vec::with_capacity(v.len());
    for t in v {
        match m.last_mut() {
            Some(last) if last.var == t.var => last.a += t.a,
            _ => m.push(t),
        }
    }
    m
}

impl Eliminator {
    /// kind: 0 = real, 1..3 = integer (1 eliminated first: seam shifts, 2: feature lines, 3: singularity positions).
    pub fn add(&mut self, kind: u8) -> u32 {
        self.kind.push(kind);
        self.elim.push(false);
        self.expr.push(Vec::new());
        self.cst.push(0.0);
        self.users.push(Vec::new());
        (self.kind.len() - 1) as u32
    }

    pub fn size(&self) -> usize {
        self.kind.len()
    }
    pub fn eliminated(&self, v: u32) -> bool {
        self.elim[v as usize]
    }
    pub fn kind(&self, v: u32) -> u8 {
        self.kind[v as usize]
    }
    pub fn expr(&self, v: u32) -> &[Term] {
        &self.expr[v as usize]
    }
    pub fn constant(&self, v: u32) -> f64 {
        self.cst[v as usize]
    }
    /// Integer constraints that could not keep an integer pivot (a real pivot was used instead).
    pub fn lost_integers(&self) -> usize {
        self.lost
    }

    pub fn constrain(&mut self, row: &[Term], c: f64) {
        // Substitute.
        let mut r: Vec<Term> = Vec::with_capacity(row.len());
        let mut k = c;
        for t in row {
            if t.a.abs() < 1e-14 {
                continue;
            }
            if self.elim[t.var as usize] {
                for u in &self.expr[t.var as usize] {
                    r.push(term(u.var, t.a * u.a));
                }
                k += t.a * self.cst[t.var as usize];
            } else {
                r.push(*t);
            }
        }
        let mut m = merge_terms(r);
        m.retain(|t| t.a.abs() >= 1e-9);
        if m.is_empty() {
            return; // redundant (or inconsistent, which the least squares then can't satisfy)
        }
        // An all-integer row with integer coefficients: divide by their common factor (a parity row such as
        // 2a - 2b + 2c = 0 then keeps a pivot with coefficient 1, so integers stay integer).
        {
            let mut all_int = (k - k.round()).abs() < 1e-9;
            let mut g = 0i64;
            for t in &m {
                if self.kind[t.var as usize] == 0 || (t.a - t.a.round()).abs() > 1e-9 {
                    all_int = false;
                } else {
                    g = gcd(g, t.a.round() as i64);
                }
            }
            if all_int && g > 1 && (k / g as f64 - (k / g as f64).round()).abs() < 1e-9 {
                for t in m.iter_mut() {
                    t.a /= g as f64;
                }
                k /= g as f64;
            }
        }
        // Pivot: a real unknown with the largest coefficient; else an integer one with coefficient +-1, lowest kind.
        let mut best: Option<usize> = None;
        for (i, t) in m.iter().enumerate() {
            if self.kind[t.var as usize] == 0 && best.is_none_or(|b| t.a.abs() > m[b].a.abs() + 1e-12) {
                best = Some(i);
            }
        }
        if best.is_none() {
            for (i, t) in m.iter().enumerate() {
                if (t.a.abs() - 1.0).abs() < 1e-9 && best.is_none_or(|b| self.kind[t.var as usize] < self.kind[m[b].var as usize]) {
                    best = Some(i);
                }
            }
        }
        if best.is_none() {
            self.lost += 1;
            for (i, t) in m.iter().enumerate() {
                if best.is_none_or(|b| t.a.abs() > m[b].a.abs()) {
                    best = Some(i);
                }
            }
        }
        let best = best.expect("row is not empty");
        let (p, ap) = (m[best].var, m[best].a);
        let e: Vec<Term> = m.iter().filter(|t| t.var != p).map(|t| term(t.var, -t.a / ap)).collect();
        let ec = -k / ap;
        // Replace p in every expression using it.
        let users = std::mem::take(&mut self.users[p as usize]);
        for q in users {
            if !self.elim[q as usize] {
                continue;
            }
            let ap2: f64 = self.expr[q as usize].iter().filter(|t| t.var == p).map(|t| t.a).sum();
            if ap2 == 0.0 {
                continue;
            }
            let mut merged: Vec<Term> = Vec::with_capacity(self.expr[q as usize].len() + e.len());
            merged.extend(self.expr[q as usize].iter().filter(|t| t.var != p).copied());
            merged.extend(e.iter().map(|t| term(t.var, ap2 * t.a)));
            let mut ex = merge_terms(merged);
            ex.retain(|t| t.a.abs() >= 1e-12);
            self.expr[q as usize] = ex;
            self.cst[q as usize] += ap2 * ec;
            for t in &e {
                self.add_user(t.var, q);
            }
        }
        self.elim[p as usize] = true;
        // The expression must be in place before `p` is registered as a user: the stale-user sweep looks at it.
        self.cst[p as usize] = ec;
        self.expr[p as usize] = e.clone();
        for t in &e {
            self.add_user(t.var, p);
        }
    }

    fn add_user(&mut self, var: u32, q: u32) {
        let u = &mut self.users[var as usize];
        if u.last() == Some(&q) {
            return;
        }
        u.push(q);
        if u.len() >= 32 && u.len().is_power_of_two() {
            // at powers of two: drop duplicates and stale ones
            u.sort_unstable();
            u.dedup();
            let (elim, expr) = (&self.elim, &self.expr);
            self.users[var as usize].retain(|&x| elim[x as usize] && expr[x as usize].iter().any(|t| t.var == var));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn value(el: &Eliminator, v: u32, free: &[f64]) -> f64 {
        if !el.eliminated(v) {
            return free[v as usize];
        }
        el.constant(v) + el.expr(v).iter().map(|t| t.a * value(el, t.var, free)).sum::<f64>()
    }

    /// Whichever unknowns end up free, the constraints must hold for any values of them.
    fn free_values(n: usize) -> Vec<f64> {
        (0..n).map(|i| 1.3 + 0.7 * i as f64).collect()
    }

    #[test]
    fn solves_a_chain_of_equalities() {
        let mut el = Eliminator::default();
        let (a, b, c) = (el.add(0), el.add(0), el.add(0));
        el.constrain(&[term(b, 1.0), term(a, -1.0)], 0.0); // b = a
        el.constrain(&[term(c, 1.0), term(b, -2.0)], 1.0); // c = 2b - 1
        let free = free_values(3);
        let (va, vb, vc) = (value(&el, a, &free), value(&el, b, &free), value(&el, c, &free));
        assert!((vb - va).abs() < 1e-12, "{va} {vb}");
        assert!((vc - (2.0 * vb - 1.0)).abs() < 1e-12, "{vb} {vc}");
        assert_eq!(el.lost_integers(), 0);
        assert_eq!((0..3).filter(|&v| !el.eliminated(v)).count(), 1, "one unknown stays free");
    }

    #[test]
    fn later_constraints_see_earlier_eliminations() {
        let mut el = Eliminator::default();
        let v: Vec<u32> = (0..4).map(|_| el.add(0)).collect();
        el.constrain(&[term(v[1], 1.0), term(v[0], -1.0)], 0.0);
        el.constrain(&[term(v[2], 1.0), term(v[1], -1.0)], 0.0);
        el.constrain(&[term(v[3], 1.0), term(v[2], -1.0)], 2.0); // v3 - v2 + 2 = 0
        let free = free_values(4);
        let x: Vec<f64> = v.iter().map(|&i| value(&el, i, &free)).collect();
        assert!((x[1] - x[0]).abs() < 1e-12 && (x[2] - x[1]).abs() < 1e-12, "{x:?}");
        // the row is v3 - v2 + 2 = 0
        assert!((x[3] - x[2] + 2.0).abs() < 1e-12, "{x:?}");
        // every expression only mentions free unknowns
        for &i in &v {
            if el.eliminated(i) {
                assert!(el.expr(i).iter().all(|t| !el.eliminated(t.var)));
            }
        }
    }

    #[test]
    fn integers_stay_integer_combinations() {
        // x = round-trip quarter turn: b = -a + t (t integer shift): the integer t is a free unknown or a +-1 pivot
        let mut el = Eliminator::default();
        let a = el.add(0);
        let b = el.add(0);
        let t = el.add(1);
        el.constrain(&[term(b, 1.0), term(a, 1.0), term(t, -1.0)], 0.0);
        assert!(el.eliminated(b) || el.eliminated(a), "a real unknown must be the pivot");
        assert!(!el.eliminated(t), "the integer must stay free");
        assert_eq!(el.lost_integers(), 0);
    }

    #[test]
    fn parity_rows_are_divided_by_their_common_factor() {
        // 2s - 2t + 2w = 0 over integers has gcd 2: dividing keeps a pivot with coefficient 1
        let mut el = Eliminator::default();
        let (s, t, w) = (el.add(1), el.add(1), el.add(2));
        el.constrain(&[term(s, 2.0), term(t, -2.0), term(w, 2.0)], 0.0);
        assert_eq!(el.lost_integers(), 0);
        assert!([s, t, w].iter().any(|&v| el.eliminated(v)));
    }

    #[test]
    fn redundant_rows_are_ignored() {
        let mut el = Eliminator::default();
        let (a, b) = (el.add(0), el.add(0));
        el.constrain(&[term(b, 1.0), term(a, -1.0)], 0.0);
        el.constrain(&[term(b, 1.0), term(a, -1.0)], 0.0); // the same again
        assert_eq!(el.lost_integers(), 0);
        assert!(el.eliminated(a) != el.eliminated(b));
    }
}
