# Cue Trainer for VS Code

Connects VS Code to [Cue](https://github.com/MidActionJax/cue)'s Trainer mode (coding-interview
practice):

- Cue sees your solution file **as you type**, before you save, along with VS Code's own errors.
- Cue's line marks show up **in the editor**: red = broken (proven by a failing test or an error),
  yellow = suspect, green = verified. Hover a line for the reason.
- The status bar shows whether a Trainer session is watching the file; click it to run the tests.
- Commands: *Cue Trainer: Run tests*, *Cue Trainer: Debug*, *Cue Trainer: Clear marks*.

It only talks to Cue on `127.0.0.1`, using the port and token Cue writes to
`%LOCALAPPDATA%\Cue\trainer_bridge.json` while a session is open. It does nothing when no session
is running.

Install it from Cue's Trainer setup window (**Install VS Code helper**).
