#!/usr/bin/env python3
"""Measure warm development rebuilds after a public request-type rename across files."""

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parent
LANGUAGES = ("go", "kotlin", "ocaml", "rust")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(command, project, env):
    return subprocess.check_output(command, cwd=project, env=env, text=True,
                                   stderr=subprocess.STDOUT).strip()


def copy_files(original, project, names):
    for name in names:
        source = original / name
        if source.is_dir():
            shutil.copytree(source, project / name)
        else:
            shutil.copy2(source, project / name)


def prepare(language, temporary):
    original = ROOT / language
    project = temporary / language
    project.mkdir()
    env = dict(os.environ)
    if language == "go":
        names = [p.name for p in original.glob("*.go") if not p.name.endswith("_test.go")]
        copy_files(original, project, names + ["go.mod", "go.sum"])
        env.update(GOCACHE=str(temporary / "go-cache"), GOPROXY="off", GOSUMDB="off",
                   GOTOOLCHAIN="local", GOFLAGS="", CGO_CFLAGS="-O3 -DNDEBUG")
        command = ["go", "build", "-gcflags=all=-N -l", "-o", "bench-debug", "."]
        sources = list(project.glob("*.go"))
        profile = "Debugger build: Go optimizations and inlining disabled, DWARF retained; cached SQLite C keeps -O3 -DNDEBUG"
        versions = {"go": capture(["go", "version"], project, env)}
    elif language == "kotlin":
        copy_files(original, project, ["src", "gradle", "build.gradle", "settings.gradle",
                                       "gradle.properties", "gradlew", "prepare-sqlite.py"])
        shutil.copytree(ROOT / "db", temporary / "db", ignore=shutil.ignore_patterns("*.sqlite*", ".tools"))
        config = json.loads((ROOT / "db/sqlite-config.json").read_text())
        archive = original / ".tools" / (config["release"] + ".zip")
        (project / ".tools").mkdir()
        shutil.copy2(archive, project / ".tools" / archive.name)
        if "JEXTRACT_HOME" not in env and (original / ".tools/jextract-25").is_dir():
            env["JEXTRACT_HOME"] = str(original / ".tools/jextract-25")
        command = ["./gradlew", "--offline", "--no-build-cache", "--console=plain",
                   "-Pkotlin.incremental=true", "classes"]
        sources = list((project / "src/main").rglob("*.kt"))
        profile = "Kotlin/JVM development classes with debug metadata and incremental compilation; warm Gradle/Kotlin daemons; no application launch or JAR packaging"
        java = str(Path(env["JAVA_HOME"]) / "bin/java") if "JAVA_HOME" in env else "java"
        versions = {"java": capture([java, "-version"], project, env)}
    elif language == "ocaml":
        copy_files(original, project, ["lib", "http", "bin", "config", "dune", "dune-project"])
        encoded = capture([str(original / "toolchain.sh"), "exec", "--", "python3", "-c",
                           "import os,json; print(json.dumps(dict(os.environ)))"], original, env)
        env = dict(json.loads(encoded), DUNE_CACHE="disabled")
        command = ["dune", "build", "--profile", "dev", "--display", "verbose", "bin/bench.exe"]
        sources = [p for folder in ("lib", "http", "bin") for p in (project / folder).rglob("*")
                   if p.suffix in (".ml", ".mli", ".atd")]
        profile = "Dune dev profile: native executable with debug information and opaque modules; no release -O3 override; dependencies and SQLite engine prebuilt"
        versions = {"ocaml": capture(["ocamlopt", "-version"], project, env),
                    "dune": capture(["dune", "--version"], project, env),
                    "compiler_config": capture(["ocamlopt", "-config"], project, env)}
    else:
        copy_files(original, project, ["src", ".cargo", "Cargo.toml", "Cargo.lock",
                                       "build.rs", "rust-toolchain.toml"])
        shutil.copytree(ROOT / "db", temporary / "db", ignore=shutil.ignore_patterns("*.sqlite*", ".tools"))
        shutil.copytree(original / ".tools/sqlite", project / ".tools/sqlite")
        env.pop("CARGO_TARGET_DIR", None)
        env["CARGO_INCREMENTAL"] = "1"
        command = ["cargo", "build", "--offline", "--locked"]
        sources = list((project / "src").rglob("*.rs"))
        profile = "Cargo dev profile: unoptimized, full debug information, incremental compilation; cached dependencies and optimized shared SQLite engine"
        versions = {"rustc": capture(["rustc", "--version"], project, env),
                    "cargo": capture(["cargo", "--version"], project, env)}
    return original, project, env, command, sources, profile, versions


def artifact_paths(language, project, name):
    if language == "kotlin":
        classes = project / "build/classes/kotlin/main/bench"
        return [classes / f"{name}.class", classes / "App.class", classes / "JsonCodec.class"]
    return [project / {"go": "bench-debug", "ocaml": "_build/default/bin/bench.exe",
                       "rust": "target/debug/rust-benchmark"}[language]]


def measure(language, output, rounds):
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix=f"{language}-debug-rebuild-") as directory:
        original, project, env, command, sources, profile, versions = prepare(language, Path(directory))
        token = "new_post" if language == "ocaml" else "NewPost"
        baseline = {p: p.read_text() for p in sorted(sources) if token in p.read_text()}
        if len(baseline) < 2:
            raise RuntimeError(f"{language}: expected the public request type in multiple compiled source files")
        original_hashes = {str(p.relative_to(project)): digest(original / p.relative_to(project)) for p in baseline}
        (output / "configuration.json").write_text(json.dumps({
            "command": command, "profile": profile, "versions": versions,
            "source_sha256": original_hashes,
        }, indent=2) + "\n")

        def build(label):
            with (output / f"{label}.log").open("w") as log:
                start = time.perf_counter()
                subprocess.run(command, cwd=project, env=env, stdout=log,
                               stderr=subprocess.STDOUT, check=True)
                elapsed = time.perf_counter() - start
            print(f"{language} {label}: {elapsed:.3f}s", flush=True)
            return elapsed

        warmup = build("warmup")
        no_change = build("warm-no-change")
        previous_name = token
        previous = dict(baseline)
        previous_hashes = [digest(p) for p in artifact_paths(language, project, token)]
        samples = []
        for index in range(1, rounds + 1):
            name = f"new_post_build_{index}" if language == "ocaml" else f"NewPostBuild{index}"
            patch = []
            for path, initial in baseline.items():
                # Replacing the OCaml token also updates generated codec references
                # (read_new_post/write_new_post) and the ATD schema declaration.
                changed = initial.replace(token, name)
                path.write_text(changed)
                relative = str(path.relative_to(project))
                patch.extend(difflib.unified_diff(previous[path].splitlines(True), changed.splitlines(True),
                                                 fromfile=relative, tofile=relative))
                previous[path] = changed
            (output / f"incremental-{index}.patch").write_text("".join(patch))
            elapsed = build(f"incremental-{index}")
            artifacts = artifact_paths(language, project, name)
            hashes = [digest(p) for p in artifacts]
            if hashes == previous_hashes or any(a == b for a, b in zip(hashes, previous_hashes)):
                raise RuntimeError(f"{language}: rename did not change the compiled definition and caller outputs")
            if language == "kotlin" and (artifacts[0].parent / f"{previous_name}.class").exists():
                raise RuntimeError("Kotlin left the old request class in its incremental output")
            samples.append({"seconds": elapsed, "renamed_from": previous_name, "renamed_to": name,
                            "changed_files": list(original_hashes),
                            "artifact_sha256": {str(p.relative_to(project)): digest(p) for p in artifacts}})
            previous_name, previous_hashes = name, hashes

        if language == "kotlin":
            # Verify classes is on application's run task graph, without running it.
            graph = capture(command[:-1] + ["run", "--dry-run"], project, env)
            (output / "application-run-tasks.log").write_text(graph + "\n")
            if ":classes SKIPPED" not in graph or ":run SKIPPED" not in graph:
                raise RuntimeError("cannot verify that application run depends on classes")
            javap = str(Path(env["JAVA_HOME"]) / "bin/javap") if "JAVA_HOME" in env else "javap"
            debug = capture([javap, "-c", "-l", str(artifact_paths(language, project, previous_name)[0])], project, env)
            (output / "debug-metadata.log").write_text(debug + "\n")
            if "LineNumberTable:" not in debug or "LocalVariableTable:" not in debug:
                raise RuntimeError("Kotlin debug metadata is missing")
            versions["gradle"] = capture(["./gradlew", "--offline", "--version"], project, env)
        for relative, expected in original_hashes.items():
            if digest(original / relative) != expected:
                raise RuntimeError(f"original source changed during measurement: {language}/{relative}")
        values = [sample["seconds"] for sample in samples]
        result = {"language": language, "unit": "seconds", "command": command, "profile": profile,
                  "versions": versions, "rounds": rounds, "samples": samples,
                  "incremental": values, "incremental_median": statistics.median(values),
                  "warmup_seconds_excluded": warmup, "no_change_seconds_excluded": no_change,
                  "edit": "Rename the public request type and all consumers to a fresh name each round; OCaml also regenerates its ATD codecs",
                  "source_sha256": original_hashes,
                  "includes": "Build command startup/configuration, changed application compilation and native executable linking (or JVM classes/resources)",
                  "excludes": "Toolchain/dependency setup, warm-up, source copying/edits, output verification, tests, application startup; no release packaging or LTO"}
        (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Fresh output directory for logs, patches and timings")
    parser.add_argument("--language", choices=LANGUAGES, nargs="+", default=list(LANGUAGES))
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if args.rounds < 1 or len(set(args.language)) != len(args.language):
        parser.error("positive rounds and unique languages required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    reports = {language: measure(language, output / language, args.rounds) for language in args.language}
    report = {"platform": platform.platform(), "method": "Sequential warm debug/development rebuilds after cross-file public API renames; no server launches", "languages": reports}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({language: data["incremental_median"] for language, data in reports.items()}, indent=2))


if __name__ == "__main__":
    main()
