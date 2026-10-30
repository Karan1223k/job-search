"""Filter jobs by location / title / experience, and rank them against the resume."""
from __future__ import annotations

import re

import config

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
NUM = r"(\d{1,2}|" + "|".join(WORD_NUM) + r")"
YEARS = r"\s*\+?\s*(?:years?|yrs?)(?!\s*(?:old|of age))"
RANGE_RE = re.compile(NUM + r"\s*\+?\s*(?:-|–|—|to)\s*" + NUM + YEARS, re.I)        # "2-5 years", "1 to 3 yrs"
EXPLICIT_RE = re.compile(r"(?:(?:minimum|min\.?|at least|over|more than)\s*(?:of\s*)?" + NUM + YEARS + r")|(?:"
                         + NUM + r"\s*\+\s*(?:years?|yrs?))", re.I)                    # "at least 3 years", "3+ years"
PLAIN_RE = re.compile(NUM + YEARS, re.I)                                                  # "3 years" (needs context)
EXPERIENCE_CONTEXT = re.compile(r"experience|exp\b|background|track record|working|\bin\s+(?:a|the)?\s*\w+", re.I)


def _num(s: str | None) -> int | None:
    if s is None:
        return None
    return int(s) if s.isdigit() else WORD_NUM[s.lower()]


def required_years(text: str) -> int | None:
    """Highest minimum years any experience requirement in the text asks for, or None if none stated.

    Ranges count by their lower bound, so "2-5 years" -> 2 and "0-2 years" -> 0.
    """
    found, taken = [], []

    def add(m, years, need_context):
        # A later, looser pattern must not re-read part of an already-matched phrase.
        if any(s <= m.start() < e for s, e in taken):
            return
        taken.append(m.span())
        if need_context and not EXPERIENCE_CONTEXT.search(text[max(0, m.start() - 80): m.end() + 60]):
            return
        if years <= 15:  # ignore "20 years of company history" / "35 yrs and above" style noise
            found.append(years)

    for m in RANGE_RE.finditer(text):
        add(m, _num(m.group(1)), need_context=False)
    for m in EXPLICIT_RE.finditer(text):
        add(m, _num(m.group(1)) if m.group(1) else _num(m.group(2)), need_context=False)
    for m in PLAIN_RE.finditer(text):
        add(m, _num(m.group(1)), need_context=True)
    return max(found) if found else None


def location_ok(location: str) -> bool:
    if config.INDIA_RE.search(location):
        return True
    return bool(config.REMOTE_RE.search(location) and config.REMOTE_OK_RE.search(location))


def title_ok(title: str) -> bool:
    return bool(config.ROLE_RE.search(title)) and not config.EXCLUDE_TITLE_RE.search(title)


def experience_ok(job) -> bool:
    if config.REJECT_PHRASE_RE.search(job.description):
        job.required_years = None
        return False
    years = required_years(job.title + "\n" + job.description)
    job.required_years = years
    if years is not None:
        return years <= config.MAX_MIN_EXPERIENCE_YEARS
    # No years stated: fall back to wording that signals a senior role.
    return not config.SENIOR_PHRASE_RE.search(job.description)


# --- ranking ----------------------------------------------------------------------

def _skill_matchers(skills: dict) -> dict:
    return {s: (re.compile(r"(?<![a-z])" + re.escape(s) + r"(?![a-z])", re.I), w) for s, w in skills.items()}


def keyword_score(text: str, matchers: dict) -> float:
    hit = sum(w for rx, w in matchers.values() if rx.search(text))
    total = sum(w for _, w in matchers.values())
    return min(1.0, hit / (total * 0.35))  # ~35% of the resume's skills present counts as a full match


class Ranker:
    """Scores each job against every resume in config.RESUMES and keeps the best fit."""

    def __init__(self, root):
        from fastembed import TextEmbedding
        self.model = TextEmbedding("BAAI/bge-small-en-v1.5")
        self.resumes = []
        for label, r in config.RESUMES.items():
            vec = next(iter(self.model.embed([(root / r["file"]).read_text()])))
            self.resumes.append((label, vec, _skill_matchers(r["skills"])))

    def score(self, jobs) -> None:
        if not jobs:
            return
        texts = [f"{j.title}\n{j.description[:3000]}" for j in jobs]
        for job, vec in zip(jobs, self.model.embed(texts)):
            full = f"{job.title}\n{job.description}"
            # Entry-level wording in the title is a strong signal; in the description, a weaker one.
            entry = 0.12 if config.ENTRY_LEVEL_RE.search(job.title) else 0.04 if config.ENTRY_LEVEL_RE.search(job.description[:1500]) else 0.0
            stated = 0.05 if getattr(job, "required_years", None) is not None else 0.0  # explicitly asks for 0-1 yrs minimum
            shared = 0.15 * title_fit(job.title) + entry + stated
            per_resume = {
                label: round(0.5 * float(vec @ rvec) + 0.35 * keyword_score(full, matchers) + shared, 4)
                for label, rvec, matchers in self.resumes  # vectors are normalised, so dot = cosine
            }
            job.best_resume = max(per_resume, key=per_resume.get)
            job.score = per_resume[job.best_resume]
            job.resume_scores = per_resume


CORE_TITLE_RE = re.compile(r"machine learning|\bml\b|\bai\b|gen ?ai|\bllm|nlp|data scien|applied scien|data engineer|data analyst|"
                           r"analytics engineer|mlops|research engineer", re.I)


def title_fit(title: str) -> float:
    """1.0 for the resumes' core roles (AI/ML/data), 0.5 for general software roles."""
    return 1.0 if CORE_TITLE_RE.search(title) else 0.5
