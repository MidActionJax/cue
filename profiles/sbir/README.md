# SBIR mode files

For calls about SBIR/STTR consulting: referral partners (state SBIR programs, SBDC advisors,
university SBIR offices, I-Corps hubs, larger firms) and founders/PIs. Everything in this folder
is loaded as context in SBIR mode, except `rules.md`, which is added **last** so nothing pushes
it out of the model's attention.

| File | What it holds | Who writes it |
|---|---|---|
| `intro.md` | how you introduce yourself on these calls | you |
| `offer.md` | services, deliverables, how pricing works, the free-review offer | you |
| `proof.md` | credentials and claims you're allowed to make, word for word | you |
| `rules.md` | hard guardrails: claims, naming, pricing, style for emails | you |
| `faq.md` | likely questions with approved answers | you |
| `contacts.md` | every partner/prospect from your tracker spreadsheets | `cue-cli sbir-sync` |
| `contacts_manual.md` | your own notes per contact; post-call promises are appended here | you + Cue |
| `prep.md` | one-page prep for the next call | `cue-cli prep-sbir "<name or org>"` |
| `threads/<name>.md` | paste an email thread here and the prep reads it | you |
| `glossary.txt` | names from your trackers, so Whisper hears them right | `cue-cli sbir-sync` |

The `*.example.md` templates are copied to real names on first run. A file still containing
`<!-- template -->` is ignored, so delete that line once you've filled it in.
