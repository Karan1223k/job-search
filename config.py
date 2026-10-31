"""Tunable settings for the daily job search."""
import os
import re

SHEET_ID = os.environ.get("SHEET_ID", "1AT3bCFWoiCIYgY1IZxJCWzm58F3aJO0WAbW3tND8JB4")
EMAIL_TO = os.environ.get("EMAIL_TO", "karannaveensood@gmail.com")

MAX_AGE_DAYS = 5          # only jobs posted within this many days
AGE_PENALTY_PER_DAY = 0.04  # score lost per day of age after the first, so fresher jobs rank higher
MAX_MIN_EXPERIENCE_YEARS = 1  # reject jobs whose minimum experience is above this ("2-5 yrs" and "2+ yrs" fail; "0-2", "1-3" pass)
TOP_N_EMAIL = 7

# A location must match this to count as India.
INDIA_RE = re.compile(
    r"\bindia\b|bengaluru|bangalore|hyderabad|pune|mumbai|chennai|gurgaon|gurugram|noida|"
    r"new delhi|\bdelhi\b|\bncr\b|kolkata|ahmedabad|kochi|jaipur|chandigarh|coimbatore|thiruvananthapuram|trivandrum|indore|mohali",
    re.I,
)
# Remote roles count only if they are open to India / APAC / anywhere.
REMOTE_RE = re.compile(r"remote|work from home|anywhere", re.I)
REMOTE_OK_RE = re.compile(r"india|apac|asia|anywhere|worldwide|global", re.I)

# Title must be a data science / AI / ML / data engineering / software engineering role.
ROLE_RE = re.compile(
    r"machine learning|\bml\b|\bai\b|artificial intelligence|gen ?ai|\bllm|nlp|deep learning|computer vision|"
    r"data scien|applied scien|research (engineer|scientist)|mlops|\bdata\b.{0,20}(engineer|analyst)|analytics engineer|"
    r"software (engineer|developer|development engineer)|software engineering|\bsde\b|\bswe\b|"
    r"(python|backend|back-end|full[- ]?stack|java|application|web) (developer|engineer)|programmer|\bdeveloper\b|"
    r"\bamts\b|\bmts\b|member of technical staff",
    re.I,
)
# Titles that signal too much seniority or the wrong kind of job.
EXCLUDE_TITLE_RE = re.compile(
    r"senior|\bsr\.?\b|\blead\b|principal|staff|manager|director|\bhead\b|\bvp\b|vice president|architect|chief|"
    r"distinguished|fellow|\bII\b|\bIII\b|\bIV\b|\bAVP\b|assistant vice|,\s*AS\b|\bL3\b|technical specialist|\bL[5-9]\b|intern\b|internship|sales|account executive|marketing|recruit|"
    r"counsel|legal|designer|frontend|front-end|\bios\b|android|embedded|firmware|hardware|asic|verification|"
    r"analog|rf |mechanical|electrical|technician|network|\btest\b|\bqa\b|quality|security|cyber|support|"
    r"devops|site reliability|\bsre\b|mainframe|sap\b|salesforce developer|servicenow",
    re.I,
)

# Description wording that marks a role as senior when no years are stated.
SENIOR_PHRASE_RE = re.compile(
    r"(leading|lead) or mentoring|mentor(ing)? (junior|other|a team of)|people management|manage a team|"
    r"deep experience|extensive experience|seasoned|proven track record of leading",
    re.I,
)
# Description wording that rules a job out regardless of stated years: senior roles behind a
# plain title ("seeking an experienced Senior ...") and internships.
REJECT_PHRASE_RE = re.compile(
    r"(seeking|looking for|hiring|hire) an? (highly )?(experienced|senior|seasoned)|\bsenior [a-z]+ (engineer|developer|specialist)|"
    r"\bas an intern\b|this internship|internship (program|duration|period)|\binterns? (will|in this)",
    re.I,
)

ENTRY_LEVEL_RE = re.compile(
    r"new grad|graduate|entry[- ]level|early career|fresher|0\s*[-–to]+\s*[12]\s*(years|yrs)|junior|\bassociate\b|\bamts\b|associate member|\bengineer I\b|\bdeveloper 1\b",
    re.I,
)

# Each resume is scored separately against every job; a job's rank uses whichever resume fits it
# better. Skill weights reflect how central each skill is to that resume.
RESUMES = {
    "AI": {
        "file": "resume_ai.txt",
        "skills": {
            "llm": 3, "large language model": 3, "generative ai": 3, "genai": 3, "gen ai": 3, "rag": 3,
            "retrieval": 2, "langgraph": 3, "langchain": 3, "agent": 2, "agentic": 3, "multi-agent": 3,
            "prompt": 2, "vector database": 2, "embedding": 2, "hugging face": 2, "transformer": 2, "nlp": 2,
            "machine learning": 3, "deep learning": 2, "scikit-learn": 2, "pytorch": 1, "tensorflow": 1,
            "model evaluation": 2, "evaluation": 1, "fine-tuning": 1, "function calling": 2, "tool calling": 2,
            "time series": 1, "forecasting": 1, "feature engineering": 2,
            "python": 3, "sql": 2, "pyspark": 1, "pandas": 1, "rest api": 1, "distributed systems": 1,
            "aws": 2, "azure": 1, "docker": 2, "linux": 1, "ci/cd": 1, "streamlit": 1,
        },
    },
    "Data Science": {
        "file": "resume_ds.txt",
        "skills": {
            "data science": 3, "statistics": 3, "statistical": 3, "hypothesis testing": 2, "a/b test": 2,
            "time series": 3, "forecasting": 3, "arima": 2, "prophet": 2, "machine learning": 2,
            "feature engineering": 2, "regression": 2, "eda": 2, "exploratory data analysis": 2,
            "python": 3, "sql": 3, "mysql": 1, "pyspark": 2, "spark": 2, "kafka": 2, "pandas": 2, "numpy": 1,
            "etl": 2, "data pipeline": 2, "data engineering": 2, "data modeling": 3, "data modelling": 3,
            "star schema": 2, "schema design": 2, "data warehouse": 2, "data quality": 2, "reconciliation": 1,
            "parquet": 1, "avro": 1, "power bi": 2, "dax": 2, "tableau": 2, "dashboard": 1, "visualization": 1,
            "matplotlib": 1, "azure": 1, "git": 1, "rag": 1, "langgraph": 1,
        },
    },
}
