// SPDX-License-Identifier: GPL-3.0-or-later
//! Progress file the addon polls. Line 1: value (0..1 working, 2 = done, negative = error code);
//! line 2: text; line 3: seconds the addon may wait before polling again. Written atomically
//! (temp file + rename) so the addon never reads half a file.

use std::path::{Path, PathBuf};

pub struct Progress {
    path: Option<PathBuf>,
}

impl Progress {
    pub fn new(path: Option<PathBuf>) -> Self {
        Progress { path }
    }

    pub fn update(&self, value: f32, text: &str, sleep_advice: f32) {
        self.write(&format!("{:.4}\n{}\n{}\n", value.clamp(0.0, 1.0), one_line(text), sleep_advice));
    }

    pub fn done(&self) {
        self.write("2\n");
    }

    /// `code` is made negative: 1 settings, 2 read, 3 invalid mesh, 4 write, 5 internal failure.
    pub fn error(&self, code: i32, text: &str) {
        self.write(&format!("{}\n{}\n", -code.abs().max(1), one_line(text)));
    }

    fn write(&self, content: &str) {
        let Some(path) = &self.path else { return };
        let _ = write_atomic(path, content);
    }
}

fn one_line(s: &str) -> String {
    s.replace(['\r', '\n'], " ")
}

fn write_atomic(path: &Path, content: &str) -> std::io::Result<()> {
    let mut tmp = path.as_os_str().to_owned();
    tmp.push(".tmp");
    let tmp = PathBuf::from(tmp);
    std::fs::write(&tmp, content)?;
    if std::fs::rename(&tmp, path).is_err() {
        // e.g. the reader has the target open on Windows: fall back to a plain write
        let r = std::fs::write(path, content);
        let _ = std::fs::remove_file(&tmp);
        return r;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("rr_progress_{}_{}", std::process::id(), name))
    }

    #[test]
    fn writes_the_three_line_format() {
        let p = tmp("a");
        let pr = Progress::new(Some(p.clone()));
        pr.update(0.5, "halfway\nsecond line", 0.25);
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "0.5000\nhalfway second line\n0.25\n");
        pr.done();
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "2\n");
        pr.error(3, "bad mesh");
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "-3\nbad mesh\n");
        assert!(!p.with_extension("tmp").exists());
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn no_path_is_a_noop() {
        Progress::new(None).update(0.1, "x", 0.1);
    }

    #[test]
    fn error_code_is_always_negative() {
        let p = tmp("b");
        let pr = Progress::new(Some(p.clone()));
        pr.error(0, "x");
        assert!(std::fs::read_to_string(&p).unwrap().starts_with("-1\n"));
        let _ = std::fs::remove_file(&p);
    }
}
