// Cue Trainer for VS Code: sends your solution file's live buffer (and VS Code's own diagnostics)
// to Cue while a trainer session runs, and draws Cue's line marks in the editor.
// Cue writes %LOCALAPPDATA%\Cue\trainer_bridge.json (port + token) while a session is open.
const vscode = require("vscode");
const fs = require("fs");
const path = require("path");
const http = require("http");

const BRIDGE = path.join(process.env.LOCALAPPDATA || require("os").homedir(), "Cue", "trainer_bridge.json");
let bridge = null;          // {port, token}
let session = null;         // {path, marks, version, summary}
let decorations = {};
let status;
let sendTimer = null;
let lastSent = "";

function readBridge() {
  try {
    const b = JSON.parse(fs.readFileSync(BRIDGE, "utf8"));
    bridge = b && b.port && b.token ? b : null;
  } catch (e) {
    bridge = null;
  }
}

function request(method, route, body) {
  return new Promise((resolve) => {
    if (!bridge) return resolve(null);
    const data = body ? Buffer.from(JSON.stringify(body)) : null;
    const req = http.request({ host: "127.0.0.1", port: bridge.port, path: route, method, timeout: 2000,
      headers: Object.assign({ "X-Cue-Token": bridge.token }, data ? { "Content-Type": "application/json", "Content-Length": data.length } : {}) },
      (res) => {
        let buf = "";
        res.on("data", (c) => (buf += c));
        res.on("end", () => { try { resolve(res.statusCode === 200 ? JSON.parse(buf) : null); } catch (e) { resolve(null); } });
      });
    req.on("error", () => { bridge = null; resolve(null); });
    req.on("timeout", () => { req.destroy(); resolve(null); });
    if (data) req.write(data);
    req.end();
  });
}

const same = (a, b) => a && b && path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase();

function isSessionDoc(doc) {
  return session && doc && doc.uri.scheme === "file" && same(doc.uri.fsPath, session.path);
}

function diagnosticsFor(doc) {
  return vscode.languages.getDiagnostics(doc.uri).map((d) => ({
    line: d.range.start.line + 1,
    message: d.message,
    severity: ["error", "warning", "info", "hint"][d.severity] || "error",
  }));
}

function scheduleSend(doc) {
  if (!isSessionDoc(doc)) return;
  clearTimeout(sendTimer);
  sendTimer = setTimeout(() => {
    const text = doc.getText();
    const key = text + JSON.stringify(diagnosticsFor(doc));
    if (key === lastSent) return;
    lastSent = key;
    request("POST", "/buffer", { path: doc.uri.fsPath, text, diagnostics: diagnosticsFor(doc) });
  }, 300);
}

function makeDecorations() {
  const mk = (bg, ruler) => vscode.window.createTextEditorDecorationType({
    isWholeLine: true, backgroundColor: bg, overviewRulerColor: ruler, overviewRulerLane: vscode.OverviewRulerLane.Left,
  });
  return { red: mk("rgba(255,80,80,0.18)", "#ff5f57"), yellow: mk("rgba(254,188,46,0.14)", "#febc2e"),
           green: mk("rgba(76,217,123,0.12)", "#4cd97b") };
}

function draw() {
  for (const editor of vscode.window.visibleTextEditors) {
    const byKind = { red: [], yellow: [], green: [] };
    if (isSessionDoc(editor.document)) {
      for (const m of session.marks || []) {
        const line = (m.line || 0) - 1;
        if (line < 0 || line >= editor.document.lineCount || !byKind[m.mark]) continue;
        const range = editor.document.lineAt(line).range;
        const why = [m.why, m.evidence].filter(Boolean).join(" · ");
        byKind[m.mark].push({ range, hoverMessage: new vscode.MarkdownString(`**Cue:** ${why || m.mark}`),
          renderOptions: why ? { after: { contentText: `  ← ${m.why || ""}`.slice(0, 90), color: "rgba(160,170,190,0.8)",
                                          fontStyle: "italic" } } : undefined });
      }
    }
    for (const k of Object.keys(byKind)) editor.setDecorations(decorations[k], byKind[k]);
  }
}

async function poll() {
  if (!bridge) readBridge();
  const s = bridge ? await request("GET", "/session") : null;
  if (!s) {
    if (session) { session = null; draw(); }
    status.text = "$(circle-slash) Cue";
    status.tooltip = "Cue Trainer isn't running a session";
    return;
  }
  const first = !session || !same(session.path, s.path);
  const changed = !session || session.version !== s.version || JSON.stringify(session.marks) !== JSON.stringify(s.marks);
  session = s;
  const reds = (s.marks || []).filter((m) => m.mark === "red").length;
  status.text = reds ? `$(error) Cue: ${reds}` : "$(pass) Cue";
  status.tooltip = s.summary || "Cue Trainer is watching this file";
  if (first) {
    lastSent = "";
    const doc = vscode.workspace.textDocuments.find(isSessionDoc);
    if (doc) scheduleSend(doc);
  }
  if (changed || first) draw();
}

function activate(context) {
  decorations = makeDecorations();
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.command = "cueTrainer.runTests";
  status.show();
  context.subscriptions.push(status, ...Object.values(decorations));
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument((e) => scheduleSend(e.document)),
    vscode.workspace.onDidOpenTextDocument((d) => scheduleSend(d)),
    vscode.languages.onDidChangeDiagnostics((e) => {
      const doc = vscode.workspace.textDocuments.find(isSessionDoc);
      if (doc && e.uris.some((u) => same(u.fsPath, doc.uri.fsPath))) scheduleSend(doc);
    }),
    vscode.window.onDidChangeVisibleTextEditors(() => draw()),
    vscode.commands.registerCommand("cueTrainer.runTests", () => request("POST", "/command", { cmd: "run_tests" })),
    vscode.commands.registerCommand("cueTrainer.debug", () => request("POST", "/command", { cmd: "debug" })),
    vscode.commands.registerCommand("cueTrainer.clearMarks", () => { if (session) session.marks = []; draw(); }),
  );
  const timer = setInterval(poll, 1000);
  context.subscriptions.push({ dispose: () => clearInterval(timer) });
  poll();
}

function deactivate() {}

module.exports = { activate, deactivate };
