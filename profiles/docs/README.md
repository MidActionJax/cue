# Your documents

PDFs, Word files (.docx) and text/markdown that Cue can answer from: a solicitation, a paper,
your capability statement, a project plan. When someone asks about something in them, the
talking points use the document's actual figures and wording.

| Folder | Used on |
|---|---|
| `docs/` | every call |
| `docs/work/`, `docs/interview/`, `docs/sbir/` | only calls in that mode |

Add them from the tray (**Add documents…**), by dragging files onto the panel, or with
`cue-cli docs add <files> [--scope work|interview|sbir]`. Remove them from the tray's
**Documents** menu or `cue-cli docs remove <name>`. Files dropped straight into these folders
are picked up too.

- Documents up to `docs.whole_chars` of text in total are given to the model whole. Bigger ones
  are searched, and the passages that match what was just asked go along with each question.
- Scanned PDFs (pictures of text), password-protected PDFs and old `.doc` files can't be read;
  Cue says so and tells you how to convert them.
- Documents are sent to Claude with your questions. Only add material you're allowed to share
  with an AI tool.
