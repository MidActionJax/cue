"""Every language the trainer knows: how to highlight it, start a file in it, check its syntax,
build it and run it, and how the test driver ("harness") is wired to your file.

Commands are argv lists with placeholders, filled in by the runner:
    {src}      your solution, copied into the build folder (named `user_file`)
    {harness}  the test driver next to it (function-style problems)
    {main}     what gets built/run: the harness for function-style problems, your file for stdin-style
    {files}    every source file in the build folder (your file + the harness)
    {dir}      the build folder           {exe}  dir/prog.exe

`wiring` says how the harness reaches your code: "include" (the harness includes/imports your
file), "separate" (both files are compiled together), "append" (your code then the harness, one
file; your line numbers don't move).
"""
from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

log = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Toolchain:
    tools: list[str]                         # executables that must all be found
    build: list[str] | None = None
    run: list[str] | None = None
    check: list[str] | None = None           # syntax / type check of your file alone


@dataclass
class Language:
    key: str
    name: str
    lexer: str
    ext: str
    comment: str = "//"
    toolchains: list[Toolchain] = field(default_factory=list)
    wiring: str = "include"
    harness_file: str = "cue_main"           # + ext unless it has a dot
    user_file: str = "solution"              # + ext unless it has a dot
    starter: str = ""
    builtin_harness: bool = False            # Cue writes the driver itself (no model needed)

    def harness_name(self) -> str:
        return self.harness_file if "." in self.harness_file else self.harness_file + self.ext

    def user_name(self, code: str = "") -> str:
        if self.key == "java":   # a public class must live in a file of the same name
            m = re.search(r"\bpublic\s+(?:final\s+)?class\s+(\w+)", code)
            return f"{m.group(1) if m else 'Solution'}.java"
        return self.user_file if "." in self.user_file else self.user_file + self.ext


def _tc(*tools, build=None, run=None, check=None) -> Toolchain:
    return Toolchain(list(tools), build, run, check)


EXE = "{exe}"
LANGUAGES: list[Language] = [
    Language("python", "Python", "python", ".py", "#", [
        _tc("python", run=["python", "-X", "utf8", "{main}"]),
        _tc("py", run=["py", "-3", "-X", "utf8", "{main}"]),
        _tc("python3", run=["python3", "-X", "utf8", "{main}"]),
    ], builtin_harness=True),
    Language("javascript", "JavaScript", "javascript", ".js", "//", [
        _tc("node", run=["node", "{main}"], check=["node", "--check", "{src}"]),
    ], builtin_harness=True),
    Language("typescript", "TypeScript", "typescript", ".ts", "//", [
        _tc("tsx", run=["tsx", "{main}"]),
        _tc("deno", run=["deno", "run", "--quiet", "--allow-read", "{main}"], check=["deno", "check", "{src}"]),
        _tc("ts-node", run=["ts-node", "--transpile-only", "{main}"]),
    ], wiring="append", harness_file="cue_main"),
    Language("java", "Java", "java", ".java", "//", [
        _tc("javac", "java", build=["javac", "-encoding", "UTF-8", "-d", "{dir}", "{files}"],
            run=["java", "-Xss64m", "-cp", "{dir}", "Main"],
            check=["javac", "-encoding", "UTF-8", "-d", "{dir}/check", "{src}"]),
    ], wiring="separate", harness_file="Main.java", starter="class Solution {\n    \n}\n"),
    Language("cpp", "C++", "cpp", ".cpp", "//", [
        # -static: the program runs without the compiler's DLLs on PATH (winget installs don't add them)
        _tc("g++", build=["g++", "-O2", "-std=gnu++17", "-static", "-o", EXE, "{main}"], run=[EXE],
            check=["g++", "-fsyntax-only", "-std=gnu++17", "{src}"]),
        _tc("clang++", build=["clang++", "-O2", "-std=c++17", "-o", EXE, "{main}"], run=[EXE],
            check=["clang++", "-fsyntax-only", "-std=c++17", "{src}"]),
        _tc("cl", build=["cl", "/nologo", "/EHsc", "/O2", "/std:c++17", "/Fe:{exe}", "/Fo:{dir}\\", "{main}"],
            run=[EXE]),
    ], starter="#include <bits/stdc++.h>\nusing namespace std;\n\n"),
    Language("c", "C", "c", ".c", "//", [
        _tc("gcc", build=["gcc", "-O2", "-std=gnu17", "-static", "-o", EXE, "{main}", "-lm"], run=[EXE],
            check=["gcc", "-fsyntax-only", "-std=gnu17", "{src}"]),
        _tc("clang", build=["clang", "-O2", "-o", EXE, "{main}"], run=[EXE],
            check=["clang", "-fsyntax-only", "{src}"]),
        _tc("cl", build=["cl", "/nologo", "/O2", "/Fe:{exe}", "/Fo:{dir}\\", "{main}"], run=[EXE]),
    ], starter="#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <stdbool.h>\n\n"),
    Language("csharp", "C#", "csharp", ".cs", "//", [
        _tc("dotnet", build=["dotnet", "build", "{dir}", "-o", "{dir}/bin", "-nologo", "-v", "q", "-clp:NoSummary"],
            run=["dotnet", "{dir}/bin/cue.dll"]),
    ], wiring="separate", harness_file="CueMain.cs",
        starter="using System;\nusing System.Collections.Generic;\nusing System.Linq;\n\npublic class Solution {\n    \n}\n"),
    Language("go", "Go", "go", ".go", "//", [
        _tc("go", build=["go", "build", "-o", EXE, "{files}"], run=[EXE], check=["gofmt", "-e", "-l", "{src}"]),
    ], wiring="separate", starter="package main\n\n"),
    Language("rust", "Rust", "rust", ".rs", "//", [
        _tc("rustc", build=["rustc", "-O", "--edition", "2021", "-A", "warnings", "-o", EXE, "{main}"], run=[EXE],
            check=["rustc", "--edition", "2021", "--crate-type", "lib", "--emit=metadata", "-A", "warnings",
                   "-o", "{dir}/check.rmeta", "{src}"]),
    ], starter="#![allow(dead_code)]\n\n"),
    Language("kotlin", "Kotlin", "kotlin", ".kt", "//", [
        _tc("kotlinc", "java", build=["kotlinc", "{files}", "-include-runtime", "-d", "{dir}/prog.jar"],
            run=["java", "-jar", "{dir}/prog.jar"]),
    ], wiring="separate", harness_file="CueMain.kt"),
    Language("swift", "Swift", "swift", ".swift", "//", [
        _tc("swiftc", build=["swiftc", "-O", "-o", EXE, "{files}"], run=[EXE]),
    ], wiring="separate", harness_file="main.swift", starter="import Foundation\n\n"),
    Language("scala", "Scala", "scala", ".scala", "//", [
        _tc("scala-cli", run=["scala-cli", "run", "-q", "{files}"]),
    ], wiring="separate"),
    Language("ruby", "Ruby", "ruby", ".rb", "#", [
        _tc("ruby", run=["ruby", "{main}"], check=["ruby", "-c", "{src}"]),
    ]),
    Language("php", "PHP", "php", ".php", "//", [
        _tc("php", run=["php", "{main}"], check=["php", "-l", "{src}"]),
    ], starter="<?php\n\n"),
    Language("dart", "Dart", "dart", ".dart", "//", [
        _tc("dart", run=["dart", "run", "{main}"]),
    ]),
    Language("lua", "Lua", "lua", ".lua", "--", [
        _tc("lua", run=["lua", "{main}"]),
        _tc("luajit", run=["luajit", "{main}"]),
    ]),
    Language("r", "R", "r", ".R", "#", [
        _tc("Rscript", run=["Rscript", "{main}"]),
    ]),
    Language("julia", "Julia", "julia", ".jl", "#", [
        _tc("julia", run=["julia", "{main}"]),
    ]),
    Language("haskell", "Haskell", "haskell", ".hs", "--", [
        _tc("runghc", run=["runghc", "-i{dir}", "{main}"]),
    ], harness_file="Main.hs", user_file="Solution.hs", starter="module Solution where\n\n"),
    Language("perl", "Perl", "perl", ".pl", "#", [
        _tc("perl", run=["perl", "{main}"], check=["perl", "-c", "{src}"]),
    ]),
    Language("bash", "Bash", "bash", ".sh", "#", [
        _tc("bash", run=["bash", "{main}"], check=["bash", "-n", "{src}"]),
    ]),
    Language("powershell", "PowerShell", "powershell", ".ps1", "#", [
        _tc("pwsh", run=["pwsh", "-NoProfile", "-File", "{main}"]),
        _tc("powershell", run=["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "{main}"]),
    ]),
]

# Reference only: highlighted, reviewed and debugged by reasoning, but not run.
REFERENCE_ONLY = [
    ("elixir", "Elixir", "elixir", ".ex", "#"), ("erlang", "Erlang", "erlang", ".erl", "%"),
    ("ocaml", "OCaml", "ocaml", ".ml", "(*"), ("fsharp", "F#", "fsharp", ".fs", "//"),
    ("clojure", "Clojure", "clojure", ".clj", ";"), ("racket", "Racket", "racket", ".rkt", ";"),
    ("scheme", "Scheme", "scheme", ".scm", ";"), ("commonlisp", "Common Lisp", "common-lisp", ".lisp", ";"),
    ("zig", "Zig", "zig", ".zig", "//"), ("nim", "Nim", "nim", ".nim", "#"),
    ("crystal", "Crystal", "crystal", ".cr", "#"), ("d", "D", "d", ".d", "//"),
    ("fortran", "Fortran", "fortran", ".f90", "!"), ("objc", "Objective-C", "objective-c", ".m", "//"),
    ("matlab", "MATLAB", "matlab", ".m", "%"), ("groovy", "Groovy", "groovy", ".groovy", "//"),
    ("vbnet", "Visual Basic .NET", "vbnet", ".vb", "'"), ("pascal", "Pascal", "delphi", ".pas", "//"),
    ("cobol", "COBOL", "cobol", ".cob", "*>"), ("prolog", "Prolog", "prolog", ".pl", "%"),
    ("sql", "SQL", "sql", ".sql", "--"), ("solidity", "Solidity", "solidity", ".sol", "//"),
    ("assembly", "Assembly (x86)", "nasm", ".asm", ";"), ("vlang", "V", "v", ".v", "//"),
    ("elm", "Elm", "elm", ".elm", "--"), ("purescript", "PureScript", "purescript", ".purs", "--"),
    ("ada", "Ada", "ada", ".adb", "--"), ("apl", "APL", "apl", ".apl", "⍝"),
    ("smalltalk", "Smalltalk", "smalltalk", ".st", '"'), ("tcl", "Tcl", "tcl", ".tcl", "#"),
]
for _key, _name, _lexer, _ext, _comment in REFERENCE_ONLY:
    LANGUAGES.append(Language(_key, _name, _lexer, _ext, _comment, [], wiring="none"))

BY_KEY = {lang.key: lang for lang in LANGUAGES}


def get(key: str) -> Language:
    return BY_KEY.get((key or "").lower()) or BY_KEY["python"]


def find(name: str) -> Language | None:
    """A language by key or display name ("C++", "c#", "golang"...)."""
    n = (name or "").strip().lower()
    alias = {"c++": "cpp", "cplusplus": "cpp", "c#": "csharp", "cs": "csharp", "golang": "go", "js": "javascript",
             "node": "javascript", "ts": "typescript", "py": "python", "python3": "python", "rb": "ruby",
             "kt": "kotlin", "rs": "rust", "sh": "bash", "shell": "bash", "ps1": "powershell", "f#": "fsharp",
             "obj-c": "objc", "objective-c": "objc", "lisp": "commonlisp", "vb": "vbnet", "vb.net": "vbnet"}
    n = alias.get(n, n)
    return BY_KEY.get(n) or next((lang for lang in LANGUAGES if lang.name.lower() == n), None)


# ---------------------------------------------------------------------------- toolchain detection

EXTRA_DIRS = [r"C:\Program Files\Go\bin", r"C:\msys64\ucrt64\bin", r"C:\msys64\mingw64\bin",
              r"C:\MinGW\bin", r"C:\Program Files\LLVM\bin", r"C:\Strawberry\c\bin", r"C:\Strawberry\perl\bin",
              r"C:\Ruby33-x64\bin", r"C:\Ruby32-x64\bin", r"C:\tools\php", r"C:\php",
              os.path.expandvars(r"%USERPROFILE%\.cargo\bin"), os.path.expandvars(r"%USERPROFILE%\go\bin"),
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Julia\bin"), os.path.expandvars(r"%USERPROFILE%\.deno\bin"),
              os.path.expandvars(r"%APPDATA%\npm"), os.path.expandvars(r"%JAVA_HOME%\bin"),
              # winget: command links, and zip packages like WinLibs (GCC) unpacked under Packages
              os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links")]
WINGET_GCC = r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\BrechtSanders.WinLibs*\mingw64\bin"
_found: dict[str, str | None] = {}


def which(tool: str) -> str | None:
    """Full path of a tool, or None. Skips the Microsoft Store "python" stub, which opens the Store."""
    if tool in _found:
        return _found[tool]
    path = shutil.which(tool)
    if path and "windowsapps" in path.lower() and tool.startswith("python"):
        path = None
    if not path:
        for d in EXTRA_DIRS:
            if "%" in d or not os.path.isdir(d):
                continue
            for ext in ("", ".exe", ".cmd", ".bat"):
                p = os.path.join(d, tool + ext)
                if os.path.isfile(p):
                    path = p
                    break
            if path:
                break
    if path and path.lower().endswith(".exe"):
        # winget's Links are symlinks; GCC finds its own parts relative to where the real exe lives
        path = os.path.realpath(path)
    if path and tool == "dotnet" and not _dotnet_sdk(path):
        path = None   # the runtime alone can't build
    if path and tool == "javac" and not os.path.isfile(path):
        path = None
    _found[tool] = path
    return path


def _dotnet_sdk(exe: str) -> bool:
    try:
        out = subprocess.run([exe, "--list-sdks"], capture_output=True, text=True, timeout=10,
                             creationflags=_NO_WINDOW).stdout
        return bool(out.strip())
    except Exception:
        return False


def toolchain(lang: Language) -> Toolchain | None:
    """The first toolchain whose tools are all installed, with tool names replaced by full paths."""
    for tc in lang.toolchains:
        paths = {t: which(t) for t in tc.tools}
        if all(paths.values()):
            def fill(cmd):
                return [paths.get(c, c) for c in cmd] if cmd else None
            # a command's first word may be a tool that isn't in `tools` (e.g. java for kotlin's jar)
            return Toolchain(tc.tools, fill(tc.build), fill(tc.run), fill(tc.check))
    if lang.key == "python":   # no Python installed: Cue's own interpreter runs it
        from ..cli import self_cmd
        return Toolchain([], run=self_cmd("trainer-exec", "{main}"))
    return None


def runnable(lang: Language) -> bool:
    return toolchain(lang) is not None


def refresh() -> None:
    """Look for toolchains again (one may have been installed while Cue was running)."""
    _found.clear()
    for d in glob.glob(os.path.expandvars(WINGET_GCC)):
        if d not in EXTRA_DIRS:
            EXTRA_DIRS.append(d)


def starter(lang: Language, problem_title: str = "") -> str:
    c = lang.comment
    end = " *)" if c == "(*" else ""
    head = (f"{c} Cue Trainer{' — ' + problem_title if problem_title else ''}{end}\n"
            f"{c} Write your solution here. Cue's test driver calls your function, so no main is needed{end}\n"
            f"{c} (read-input-and-print problems: write the whole program as usual).{end}\n\n")
    return head + (lang.starter or "")


refresh()   # pick up winget-installed GCC from the start
