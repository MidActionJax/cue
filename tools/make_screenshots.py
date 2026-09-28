"""Render README screenshots of the real panel with made-up demo content (no personal data,
nothing from your desktop — the widget is rendered off-screen onto a drawn backdrop).

    python tools/make_screenshots.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QPoint, QPointF, QRectF  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QRadialGradient, QLinearGradient  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from cue import config  # noqa: E402
from cue.app import md_html  # noqa: E402
from cue.overlay import Overlay, grouped_html  # noqa: E402

OUT = ROOT / "assets" / "screenshots"


def backdrop(w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#1a2236"))
    g.setColorAt(1, QColor("#0b0e15"))
    p.fillRect(0, 0, w, h, g)
    for cx, cy, r, color in ((w * 0.85, h * 0.1, w * 0.45, "#5f7fe6"), (w * 0.1, h * 1.0, w * 0.4, "#4cd97b"),
                             (w * 0.55, h * 0.6, w * 0.3, "#9b6bff")):
        rg = QRadialGradient(QPointF(cx, cy), r)
        c = QColor(color)
        c.setAlpha(70)
        rg.setColorAt(0, c)
        c.setAlpha(0)
        rg.setColorAt(1, c)
        p.fillRect(0, 0, w, h, rg)
    p.end()
    return img


def compose(panel: Overlay, name: str, pad: int = 70):
    panel.adjustSize()
    panel._relayout()
    QApplication.processEvents()
    panel._relayout()
    shot = panel.grab()
    w, h = shot.width() + pad * 2, shot.height() + pad * 2
    img = backdrop(w, h)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for i in range(18, 0, -2):  # soft drop shadow
        path = QPainterPath()
        path.addRoundedRect(QRectF(pad - i, pad - i + 10, shot.width() + 2 * i, shot.height() + 2 * i), 14 + i, 14 + i)
        p.fillPath(path, QColor(0, 0, 0, 7))
    p.drawPixmap(QPoint(pad, pad), shot)
    p.end()
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(str(OUT / f"{name}.png"))
    print("wrote", name)


def main():
    app = QApplication(sys.argv)  # noqa: F841
    cfg = config.load(ROOT / "config.example.yaml")
    ov = cfg["overlay"]
    ov.update(acrylic=False, hide_from_screen_share=False, tint_alpha=175)
    panel = Overlay(config.Cfg(ov))
    panel.move(-4000, -4000)  # off-screen: we only grab the widget itself
    panel.show()
    panel.set_base_title("Listening · claude")
    panel.set_levels(("speaking", ""), ("live", ""))
    panel.set_devices("Jabra Evolve2", "Jabra Evolve2 Mic")
    panel.set_voice_status("🗣 Voice set up ✓")
    fs = cfg.overlay.font_size

    # 1. start screen
    panel.set_mode("", "smart")
    panel.show_chooser()
    compose(panel, "1-start")

    # 2. work home: last week by workstream + what you owe from the last call
    panel.set_mode("work", "smart")
    panel.set_home("Last week · Sep 21 – 27", grouped_html([
        ("Payments API", ["Shipped idempotency keys for refunds — zero duplicate charges since Tuesday",
                          "Cut checkout p95 from 420 → 180 ms by caching tax lookups"]),
        ("Data pipeline", ["Backfilled 14 months of events after the schema migration",
                           "Moved the nightly job to spot instances; cost down 38%"]),
        ("Infra reliability", ["Alert dedup: pager noise down from 60 to 9 pings a week"]),
        ("From your last call (Sep 21) — you said you'd", ["Send Priya the rollout plan for the new queue"]),
    ], fs))
    compose(panel, "2-home")

    # 3. an answer the moment you're asked
    panel.show_answer("WORK · smart · 09:42:17",
                      "Q: Is the refund fix safe to roll out Friday?\n"
                      "> Yeah — it's been live in staging all week with zero duplicate refunds.\n"
                      "- Idempotency keys on every refund call since Tuesday\n"
                      "- One edge case left: partial refunds over $10k, test already written\n"
                      "- I'd flip it Friday morning, not end of day")
    compose(panel, "3-answer")

    # 4. "What?" — decode the speaker you couldn't understand
    panel.show_answer("WORK · explain · 09:47:03",
                      "Q: What they said\n"
                      "> Marco's asking whether the nightly backfill will finish before Monday's report, "
                      "since it ran long last week.\n"
                      "- For you: give an ETA for the backfill and say whether Monday's report is at risk")
    compose(panel, "4-explain")

    # 5. practice mode coaching
    panel.set_mode("work", "practice")
    panel.show_answer("PRACTICE · work · 2/12 · coaching",
                      "Q: What did you get done last week?\n"
                      "✓ Clear and specific — the latency number lands well.\n"
                      "+ Lead with the refunds fix; it's the one your lead asked about on Monday.\n"
                      "> Last week I shipped idempotency keys for refunds, so no more duplicate charges, and I cut "
                      "checkout p95 from 420 to 180 milliseconds by caching tax lookups.")
    panel.set_actions([("↻ Try again", lambda: None), ("▶ Next question", lambda: None),
                       ("✕ Done practicing", lambda: None)])
    compose(panel, "5-practice")
    panel.set_actions([])

    # 6. live transcript + notes pane
    panel.set_mode("work", "smart")
    panel.show_answer("WORK · manual · script · 10:02:44",
                      "Q: Can you walk us through the queue rollout?\n"
                      "Sure. We'll move ten percent of traffic to the new queue on Tuesday, watch error rates for a "
                      "day, then go to half on Wednesday. If anything looks off we flip the flag back — it takes about a minute.")
    rows = []
    for who, color, t, text in (("Priya", "#9fc0ff", "10:02:31", "Okay, Alex — can you walk us through the queue rollout?"),
                                ("ALEX", "#9be3b0", "10:02:40", "Sure. We'll move ten percent of traffic on Tuesday…"),
                                ("Speaker 2 ✎", "#9fc0ff", "10:02:58", "And what is the rollback if the error rate spikes?")):
        rows.append(f"<div style='margin:3px 0'><span style='color:{color}; font-weight:600'>{who}</span> "
                    f"<span style='color:rgba(210,220,235,0.45)'>{t}</span><br><span>{text}</span></div>")
    panel.set_transcript("".join(rows))
    panel.set_notes(md_html("## Key points\n- Queue rollout: 10% Tuesday, 50% Wednesday\n"
                            "## Action items\n- [ ] Alex: send Priya the rollout plan\n- [ ] Marco: confirm backfill ETA\n"
                            "## Asked of me / follow up\n- Rollback plan if error rate spikes"))
    panel.toggle_expanded()
    compose(panel, "6-transcript")
    panel._set_tab("notes")
    compose(panel, "7-notes")


if __name__ == "__main__":
    main()
