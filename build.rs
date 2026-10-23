use sha2::{Digest, Sha256};
use std::{fs, path::Path};

fn collect(path: &Path, files: &mut Vec<std::path::PathBuf>) {
    for entry in fs::read_dir(path).expect("read source directory") {
        let path = entry.expect("source entry").path();
        if path.is_dir() {
            collect(&path, files);
        } else if path.extension().is_some_and(|v| v == "rs") {
            files.push(path);
        }
    }
}

fn main() {
    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=Cargo.toml");
    println!("cargo:rerun-if-changed=Cargo.lock");
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=tools/execution_runtime.py");
    let mut files = vec![
        "Cargo.toml".into(),
        "Cargo.lock".into(),
        "build.rs".into(),
        "tools/execution_runtime.py".into(),
    ];
    collect(Path::new("src"), &mut files);
    files.sort();
    let mut hash = Sha256::new();
    let mut build_environment: Vec<_> = std::env::vars()
        .filter(|(key, _)| {
            key.starts_with("CARGO_CFG_")
                || key.starts_with("CARGO_FEATURE_")
                || ["CARGO_ENCODED_RUSTFLAGS", "OPT_LEVEL", "PROFILE"].contains(&key.as_str())
        })
        .collect();
    build_environment.sort();
    for (key, value) in build_environment {
        hash.update(key.as_bytes());
        hash.update([0]);
        hash.update(value.as_bytes());
        hash.update([0]);
    }
    let compiler = std::process::Command::new(std::env::var("RUSTC").expect("Rust compiler"))
        .arg("-V")
        .output()
        .expect("compiler identity");
    assert!(compiler.status.success());
    hash.update(compiler.stdout);
    for path in files {
        let bytes = fs::read(&path).expect("read compilation source");
        hash.update(path.to_string_lossy().as_bytes());
        hash.update([0]);
        hash.update((bytes.len() as u64).to_le_bytes());
        hash.update(bytes);
    }
    println!(
        "cargo:rustc-env=VISN_EXECUTION_CODE_SHA256={:x}",
        hash.finalize()
    );
}
