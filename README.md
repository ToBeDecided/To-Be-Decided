# Internship Matcher

Upload your resume and get three things back:

1. **A resume score** (0–100, with a letter grade) from a transparent rubric, plus a prioritized list of specific fixes and the bullets worth rewriting.
2. **A profile rating**: how competitive you are, what level recruiters will read you as, and which role types (Software, AI/ML/Data, Hardware, Quant, Product) your resume fits best.
3. **A shortlist of internships you're likely to get interviews for**, pulled from thousands of live postings. Each role is rated **Likely**, **Target** or **Reach**, with an explanation and an apply link.

An optional **AI review** (Claude) adds a recruiter-style critique, line-by-line bullet rewrites and a second opinion on your top matches.

![Recommended internships](docs/screenshot-matches.png)

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/ToBeDecided/To-Be-Decided.git
cd To-Be-Decided
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
internmatch serve                  # opens http://127.0.0.1:8000
```

Drop in your resume (PDF, DOCX or TXT), set your graduation year, term and preferred locations, and click **Rate my resume & find internships**.

### Command line

The same engine works in the terminal:

```bash
internmatch analyze resume.pdf --term "Summer 2027" --location NYC --location "Bay Area" --top 25
internmatch analyze resume.pdf --work-auth needs_sponsorship --category Software --csv matches.csv
internmatch analyze resume.pdf --board greenhouse:stripe --board lever:palantir   # add company boards
internmatch analyze resume.pdf --ai                                               # add the Claude review
internmatch refresh                                                               # re-download listings now
```

Run `internmatch analyze --help` for every option.

### Optional: AI review with Claude

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # from https://console.anthropic.com
internmatch serve
```

Then open the **AI review** tab. It makes one API call (model `claude-opus-5-5` by default; override with `INTERNMATCH_MODEL`). That call sends your resume text and your top 20 recommended postings to Anthropic. Nothing else in the app needs a key or sends your resume anywhere.

## Where the internships come from

| Source | What it gives | Setup |
|---|---|---|
| [SimplifyJobs internship list](https://github.com/SimplifyJobs/Summer2027-Internships) | ~4,000+ active tech internships (software, AI/ML/data, hardware, quant, product), updated many times a day | Automatic. Cached for 6 hours in `~/.cache/internmatch` |
| Greenhouse / Lever / Ashby job boards | Every internship at a specific company, **with full job descriptions** | Add `greenhouse:stripe`, `lever:palantir`, `ashby:ramp` (or board URLs) under *Advanced options* or with `--board` |
| Job-description lookup | For top matches that link to Greenhouse, Lever or Ashby, the real description is fetched and the role is re-scored against it | On by default. Turn off with *Fetch full job descriptions* / `--no-enrich` |

## How the scoring works

Everything except the optional AI review is deterministic and runs locally, so you can see why you got each number.

### Resume score

| Part | Weight | What it measures |
|---|---|---|
| Impact & writing | 25% | Share of bullets that open with an action verb, include a number (users, %, $, scale) and are 6–35 words. Penalizes "Responsible for…"/"Worked on…" and first-person pronouns |
| Experience | 25% | Internships, other roles, projects, research, leadership, awards and open source, **compared with what's typical for your year** (a freshman isn't expected to have two internships) |
| Skills | 20% | Breadth of recognized technical skills (~150 in the taxonomy, with aliases) and depth in your best-fit role type |
| Academics | 10% | GPA (from the resume or your override) and whether you list relevant coursework |
| Format & completeness | 20% | Education, experience/projects and skills sections, contact info (email, phone, LinkedIn, GitHub/portfolio) and length (one full page) |

### Interview odds

Every posting you're eligible for gets two numbers:

- **Resume fit (0–100%)**: how well your skills cover the role. The inputs are the full job description when one is available, the skills implied by the title (e.g. "Embedded" → C/C++ + embedded systems), your affinity for the posting's category and whether the role's keywords appear in your resume.
- **Interview odds (0–100)**: fit × competitiveness × timing × seniority.
  - *Competitiveness* compares your candidate strength with the company's selectivity. There are three tiers: standard, highly competitive (e.g. Microsoft, Amazon, Bloomberg) and extremely selective (e.g. Jane Street, Citadel, Google, OpenAI). Extremely selective companies are capped, so they're never marked "Likely" from a cold application.
  - *Timing*: postings from the last few days rank higher, since early applicants get most interviews, and month-old postings are flagged.
  - *Seniority*: freshmen and sophomores get a boost on programs aimed at them and a small haircut elsewhere.

Tiers: **Likely** ≥ 70, **Target** 50–69, **Reach** < 50. The **Recommended** list takes the highest-odds roles, fills it with Likely/Target before any Reach, and allows at most two roles per company.

Before scoring, postings you can't apply to are hidden and counted: wrong term, a degree requirement you don't meet (e.g. PhD-only), roles that don't sponsor visas when you need sponsorship (from the listing or detected in the description), programs reserved for underclassmen when you're a junior or senior, and duplicates.

> **Treat the odds as a ranking, not a promise.** They are heuristics built from the signals recruiters are known to weigh. The app can't see referrals, your school's recruiting pipeline or how many people applied. Use the tiers the way you'd use a college list: apply to plenty of Likely and Target roles and a handful of Reaches.

![Resume report](docs/screenshot-report.png)

## Privacy

- Your resume is parsed on your machine by the local server. The only network calls are downloads of public job listings.
- The AI review is opt-in, per click, and sends your resume text to the Anthropic API.
- "Applied" checkmarks and form preferences are stored in your browser's local storage.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables the AI review (`ant auth login` also works) |
| `INTERNMATCH_MODEL` | `claude-opus-5-5` | Claude model used for the AI review |
| `INTERNMATCH_CACHE_DIR` | `~/.cache/internmatch` | Where listings and fetched job descriptions are cached |
| `INTERNMATCH_LISTINGS_URL` | next summer's SimplifyJobs feed | Point at a different `listings.json` in the same format |

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests run offline. HTTP calls are served by `httpx.MockTransport`, and the Claude client is faked.

```
internmatch/
  resume.py     text extraction (PDF/DOCX/TXT), parsing and the resume rubric
  skills.py     skill taxonomy, extraction, category signatures, title → requirement rules
  matcher.py    eligibility filters, fit, interview odds, tiers, shortlist
  companies.py  company selectivity tiers
  sources.py    SimplifyJobs feed, Greenhouse/Lever/Ashby boards, description lookup, caching
  engine.py     end-to-end pipeline used by the web app and CLI
  ai.py         optional Claude review (structured output)
  server.py     FastAPI JSON API + static UI
  static/       single-page web UI (no build step)
tests/          pytest suite with fixture resumes
```
