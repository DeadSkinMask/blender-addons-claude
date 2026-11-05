// SPDX-License-Identifier: GPL-3.0-or-later
//! Read-only closest-point queries on a fixed surface (triangles) and on line segments, safe to call from many
//! threads. A BVH (bounding volume hierarchy) is a binary tree of boxes over the primitives: a query skips whole
//! subtrees whose box is farther than the best hit so far, so it costs about log(n) instead of n.
//! Median split on the longest axis of the primitive centres, 4 primitives per leaf (same design as
//! SculptForge's `SurfaceQuery`, which this module follows; SculptForge is GPL-3.0-or-later).

use crate::vec3::{v3, V3};
use rayon::prelude::*;

const LEAF_SIZE: u32 = 4;
const STACK: usize = 64;

#[derive(Clone, Copy, Debug)]
pub struct Aabb {
    pub min: V3,
    pub max: V3,
}

impl Aabb {
    pub fn empty() -> Aabb {
        Aabb { min: V3::splat(f32::INFINITY), max: V3::splat(f32::NEG_INFINITY) }
    }
    pub fn expand_point(&mut self, p: V3) {
        self.min = self.min.min(p);
        self.max = self.max.max(p);
    }
    pub fn expand_box(&mut self, b: &Aabb) {
        self.min = self.min.min(b.min);
        self.max = self.max.max(b.max);
    }
    pub fn center(&self) -> V3 {
        (self.min + self.max) * 0.5
    }
    pub fn extent(&self) -> V3 {
        self.max - self.min
    }
    /// Squared distance from `p` to the box (0 inside).
    pub fn distance2(&self, p: V3) -> f32 {
        let d = (self.min - p).max(p - self.max).max(V3::ZERO);
        d.length2()
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct SurfaceHit {
    /// Triangle (or segment) index.
    pub prim: u32,
    pub position: V3,
    /// Barycentric weights of the triangle's corners (segments: x = 1 - u, y = u).
    pub bary: V3,
    pub distance: f32,
}

#[derive(Clone, Copy, Debug)]
struct Node {
    bbox: Aabb,
    /// Leaf: first index into `order`; inner: left child (right = first + 1).
    first: u32,
    /// > 0 for leaves.
    count: u32,
}

#[derive(Default)]
struct FlatBvh {
    nodes: Vec<Node>,
    order: Vec<u32>,
}

fn cmp_f32(a: f32, b: f32) -> std::cmp::Ordering {
    a.total_cmp(&b)
}

impl FlatBvh {
    fn build(boxes: &[Aabb]) -> FlatBvh {
        let n = boxes.len();
        let mut order: Vec<u32> = (0..n as u32).collect();
        let mut nodes: Vec<Node> = Vec::new();
        if n == 0 {
            return FlatBvh { nodes, order };
        }
        let centers: Vec<V3> = boxes.iter().map(|b| b.center()).collect();
        let blank = Node { bbox: Aabb::empty(), first: 0, count: 0 };
        nodes.reserve(2 * n / LEAF_SIZE as usize + 2);
        nodes.push(blank);
        let mut stack = vec![(0u32, 0u32, n as u32)];
        while let Some((node, begin, end)) = stack.pop() {
            let mut bbox = Aabb::empty();
            let mut cbox = Aabb::empty();
            for i in begin..end {
                let id = order[i as usize] as usize;
                bbox.expand_box(&boxes[id]);
                cbox.expand_point(centers[id]);
            }
            nodes[node as usize].bbox = bbox;
            let count = end - begin;
            let ext = cbox.extent();
            if count <= LEAF_SIZE || ext.x.max(ext.y).max(ext.z) <= 0.0 {
                nodes[node as usize].first = begin;
                nodes[node as usize].count = count;
                continue;
            }
            let axis = if ext.x >= ext.y && ext.x >= ext.z {
                0
            } else if ext.y >= ext.z {
                1
            } else {
                2
            };
            let mid = begin + count / 2;
            order[begin as usize..end as usize].select_nth_unstable_by((mid - begin) as usize, |&a, &b| {
                cmp_f32(centers[a as usize].get(axis), centers[b as usize].get(axis))
            });
            let left = nodes.len() as u32;
            nodes.push(blank);
            nodes.push(blank);
            nodes[node as usize].first = left;
            nodes[node as usize].count = 0;
            stack.push((left, begin, mid));
            stack.push((left + 1, mid, end));
        }
        FlatBvh { nodes, order }
    }

    fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    /// Branch-and-bound nearest search: `visit(prim)` returns the squared distance of that primitive when it beats
    /// the current best (`best2`), which it must then record itself.
    fn nearest(&self, p: V3, max_distance: f32, mut visit: impl FnMut(u32, f32) -> Option<f32>) -> Option<f32> {
        if self.is_empty() {
            return None;
        }
        let mut best2 = max_distance * max_distance;
        let mut found = false;
        let mut stack = [0u32; STACK];
        let mut sp = 1usize;
        while sp > 0 {
            sp -= 1;
            let n = &self.nodes[stack[sp] as usize];
            if n.bbox.distance2(p) > best2 {
                continue;
            }
            if n.count > 0 {
                for i in n.first..n.first + n.count {
                    if let Some(d2) = visit(self.order[i as usize], best2) {
                        best2 = d2;
                        found = true;
                    }
                }
                continue;
            }
            if sp + 2 > STACK {
                continue; // cannot happen with a median split of < 2^60 primitives
            }
            let l = n.first;
            let dl = self.nodes[l as usize].bbox.distance2(p);
            let dr = self.nodes[l as usize + 1].bbox.distance2(p);
            // push the farther child first so the nearer one is visited next
            if dl < dr {
                stack[sp] = l + 1;
                stack[sp + 1] = l;
            } else {
                stack[sp] = l;
                stack[sp + 1] = l + 1;
            }
            sp += 2;
        }
        found.then_some(best2)
    }
}

/// Closest point to `p` on triangle abc, with its barycentric weights.
pub fn closest_point_on_triangle(p: V3, a: V3, b: V3, c: V3) -> (V3, V3) {
    let ab = b - a;
    let ac = c - a;
    let ap = p - a;
    let d1 = ab.dot(ap);
    let d2 = ac.dot(ap);
    if d1 <= 0.0 && d2 <= 0.0 {
        return (a, v3(1.0, 0.0, 0.0));
    }
    let bp = p - b;
    let d3 = ab.dot(bp);
    let d4 = ac.dot(bp);
    if d3 >= 0.0 && d4 <= d3 {
        return (b, v3(0.0, 1.0, 0.0));
    }
    let vc = d1 * d4 - d3 * d2;
    if vc <= 0.0 && d1 >= 0.0 && d3 <= 0.0 && d1 - d3 > 0.0 {
        let v = d1 / (d1 - d3);
        return (a + ab * v, v3(1.0 - v, v, 0.0));
    }
    let cp = p - c;
    let d5 = ab.dot(cp);
    let d6 = ac.dot(cp);
    if d6 >= 0.0 && d5 <= d6 {
        return (c, v3(0.0, 0.0, 1.0));
    }
    let vb = d5 * d2 - d1 * d6;
    if vb <= 0.0 && d2 >= 0.0 && d6 <= 0.0 && d2 - d6 > 0.0 {
        let w = d2 / (d2 - d6);
        return (a + ac * w, v3(1.0 - w, 0.0, w));
    }
    let va = d3 * d6 - d5 * d4;
    if va <= 0.0 && d4 - d3 >= 0.0 && d5 - d6 >= 0.0 && (d4 - d3) + (d5 - d6) > 0.0 {
        let w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
        return (b + (c - b) * w, v3(0.0, 1.0 - w, w));
    }
    let sum = va + vb + vc;
    if sum.is_nan() || sum.abs() == 0.0 {
        // degenerate triangle: nearest of its edges
        let (q0, u0) = closest_point_on_segment(p, a, b);
        let mut best = (q0, v3(1.0 - u0, u0, 0.0));
        let mut best_d = (q0 - p).length2();
        let (q1, u1) = closest_point_on_segment(p, b, c);
        if (q1 - p).length2() < best_d {
            best_d = (q1 - p).length2();
            best = (q1, v3(0.0, 1.0 - u1, u1));
        }
        let (q2, u2) = closest_point_on_segment(p, a, c);
        if (q2 - p).length2() < best_d {
            best = (q2, v3(1.0 - u2, 0.0, u2));
        }
        return best;
    }
    let denom = 1.0 / sum;
    let v = vb * denom;
    let w = vc * denom;
    (a + ab * v + ac * w, v3(1.0 - v - w, v, w))
}

/// Closest point on segment ab and its parameter u in [0, 1].
pub fn closest_point_on_segment(p: V3, a: V3, b: V3) -> (V3, f32) {
    let ab = b - a;
    let len2 = ab.length2();
    let u = if len2 > 0.0 { ((p - a).dot(ab) / len2).clamp(0.0, 1.0) } else { 0.0 };
    (a + ab * u, u)
}

pub struct TriangleBvh {
    positions: Vec<V3>,
    triangles: Vec<[u32; 3]>,
    bvh: FlatBvh,
}

impl TriangleBvh {
    pub fn build(positions: Vec<V3>, triangles: Vec<[u32; 3]>) -> TriangleBvh {
        let boxes: Vec<Aabb> = triangles
            .par_iter()
            .map(|t| {
                let mut b = Aabb::empty();
                for &i in t {
                    b.expand_point(positions[i as usize]);
                }
                b
            })
            .collect();
        let bvh = FlatBvh::build(&boxes);
        TriangleBvh { positions, triangles, bvh }
    }

    pub fn is_empty(&self) -> bool {
        self.bvh.is_empty()
    }

    pub fn triangles(&self) -> &[[u32; 3]] {
        &self.triangles
    }

    pub fn positions(&self) -> &[V3] {
        &self.positions
    }

    /// Closest surface point within `max_distance` of `p`.
    pub fn closest_point(&self, p: V3, max_distance: f32) -> Option<SurfaceHit> {
        let mut hit = SurfaceHit::default();
        let found = self.bvh.nearest(p, max_distance, |t, best2| {
            let tri = self.triangles[t as usize];
            let (q, bary) = closest_point_on_triangle(
                p,
                self.positions[tri[0] as usize],
                self.positions[tri[1] as usize],
                self.positions[tri[2] as usize],
            );
            let d2 = (q - p).length2();
            if d2 <= best2 {
                hit = SurfaceHit { prim: t, position: q, bary, distance: 0.0 };
                Some(d2)
            } else {
                None
            }
        })?;
        hit.distance = found.sqrt();
        Some(hit)
    }
}

/// Closest points on a set of line segments (mesh borders, group borders, hard edges).
pub struct SegmentBvh {
    a: Vec<V3>,
    b: Vec<V3>,
    bvh: FlatBvh,
}

impl SegmentBvh {
    pub fn build(a: Vec<V3>, b: Vec<V3>) -> SegmentBvh {
        assert_eq!(a.len(), b.len());
        let boxes: Vec<Aabb> = a
            .iter()
            .zip(&b)
            .map(|(&p, &q)| {
                let mut bb = Aabb::empty();
                bb.expand_point(p);
                bb.expand_point(q);
                bb
            })
            .collect();
        let bvh = FlatBvh::build(&boxes);
        SegmentBvh { a, b, bvh }
    }

    pub fn is_empty(&self) -> bool {
        self.bvh.is_empty()
    }

    pub fn len(&self) -> usize {
        self.a.len()
    }

    pub fn closest_point(&self, p: V3, max_distance: f32) -> Option<SurfaceHit> {
        let mut hit = SurfaceHit::default();
        let found = self.bvh.nearest(p, max_distance, |s, best2| {
            let (q, u) = closest_point_on_segment(p, self.a[s as usize], self.b[s as usize]);
            let d2 = (q - p).length2();
            if d2 <= best2 {
                hit = SurfaceHit { prim: s, position: q, bary: v3(1.0 - u, u, 0.0), distance: 0.0 };
                Some(d2)
            } else {
                None
            }
        })?;
        hit.distance = found.sqrt();
        Some(hit)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Deterministic pseudo-random numbers in [0, 1).
    struct Lcg(u64);
    impl Lcg {
        fn next(&mut self) -> f32 {
            self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            ((self.0 >> 40) as f32) / (1u64 << 24) as f32
        }
        fn point(&mut self, s: f32) -> V3 {
            v3(self.next() * s, self.next() * s, self.next() * s)
        }
    }

    fn soup(n: usize, seed: u64) -> (Vec<V3>, Vec<[u32; 3]>) {
        let mut r = Lcg(seed);
        let mut p = Vec::new();
        let mut t = Vec::new();
        for i in 0..n {
            let c = r.point(10.0);
            for _ in 0..3 {
                p.push(c + r.point(1.0));
            }
            let b = (3 * i) as u32;
            t.push([b, b + 1, b + 2]);
        }
        (p, t)
    }

    #[test]
    fn triangle_regions() {
        let (a, b, c) = (v3(0.0, 0.0, 0.0), v3(1.0, 0.0, 0.0), v3(0.0, 1.0, 0.0));
        // above the interior
        let (q, w) = closest_point_on_triangle(v3(0.25, 0.25, 2.0), a, b, c);
        assert_eq!(q, v3(0.25, 0.25, 0.0));
        assert!((w.x + w.y + w.z - 1.0).abs() < 1e-6);
        // vertex regions
        assert_eq!(closest_point_on_triangle(v3(-1.0, -1.0, 0.0), a, b, c).0, a);
        assert_eq!(closest_point_on_triangle(v3(3.0, -1.0, 0.0), a, b, c).0, b);
        assert_eq!(closest_point_on_triangle(v3(-1.0, 3.0, 0.0), a, b, c).0, c);
        // edge region (ab) and hypotenuse (bc)
        assert_eq!(closest_point_on_triangle(v3(0.5, -1.0, 0.0), a, b, c).0, v3(0.5, 0.0, 0.0));
        let (q, _) = closest_point_on_triangle(v3(1.0, 1.0, 0.0), a, b, c);
        assert!((q - v3(0.5, 0.5, 0.0)).length() < 1e-6);
    }

    #[test]
    fn degenerate_triangle_does_not_blow_up() {
        let p = v3(0.5, 1.0, 0.0);
        let (q, _) = closest_point_on_triangle(p, v3(0.0, 0.0, 0.0), v3(1.0, 0.0, 0.0), v3(2.0, 0.0, 0.0));
        assert!(q.x.is_finite() && (q - v3(0.5, 0.0, 0.0)).length() < 1e-6);
    }

    #[test]
    fn matches_brute_force_on_triangle_soup() {
        let (p, t) = soup(500, 7);
        let bvh = TriangleBvh::build(p.clone(), t.clone());
        let mut r = Lcg(99);
        for _ in 0..300 {
            let q = r.point(12.0) - v3(1.0, 1.0, 1.0);
            let mut best = f32::MAX;
            for tri in &t {
                let (c, _) = closest_point_on_triangle(q, p[tri[0] as usize], p[tri[1] as usize], p[tri[2] as usize]);
                best = best.min((c - q).length2());
            }
            let hit = bvh.closest_point(q, 1e9).unwrap();
            assert!((hit.distance * hit.distance - best).abs() <= 1e-4 * best.max(1.0), "{} vs {}", hit.distance, best.sqrt());
            assert!(((hit.position - q).length() - hit.distance).abs() < 1e-3);
        }
    }

    #[test]
    fn respects_max_distance_and_empty() {
        let (p, t) = soup(50, 3);
        let bvh = TriangleBvh::build(p, t);
        assert!(bvh.closest_point(v3(1000.0, 0.0, 0.0), 1.0).is_none());
        let empty = TriangleBvh::build(vec![], vec![]);
        assert!(empty.is_empty() && empty.closest_point(V3::ZERO, 1e9).is_none());
    }

    #[test]
    fn segments_match_brute_force() {
        let mut r = Lcg(5);
        let a: Vec<V3> = (0..400).map(|_| r.point(10.0)).collect();
        let b: Vec<V3> = a.iter().map(|&p| p + r.point(1.0)).collect();
        let bvh = SegmentBvh::build(a.clone(), b.clone());
        assert_eq!(bvh.len(), 400);
        for _ in 0..200 {
            let q = r.point(10.0);
            let best = a
                .iter()
                .zip(&b)
                .map(|(&x, &y)| (closest_point_on_segment(q, x, y).0 - q).length2())
                .fold(f32::MAX, f32::min);
            let hit = bvh.closest_point(q, 1e9).unwrap();
            assert!((hit.distance * hit.distance - best).abs() <= 1e-4 * best.max(1.0));
        }
    }

    #[test]
    fn identical_centres_do_not_recurse_forever() {
        // many coincident triangles: the splitter must stop at a leaf
        let p = vec![v3(0.0, 0.0, 0.0), v3(1.0, 0.0, 0.0), v3(0.0, 1.0, 0.0)];
        let t = vec![[0, 1, 2]; 100];
        let bvh = TriangleBvh::build(p, t);
        assert!(bvh.closest_point(v3(0.2, 0.2, 1.0), 10.0).is_some());
    }
}
