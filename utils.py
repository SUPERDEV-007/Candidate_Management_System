import json
import re
import threading
from datetime import date
from functools import lru_cache
from typing import Optional

import PyPDF2

offline_llm_enabled = False
_tokenizer = None
_model = None
_llm_load_attempted = False
_llm_lock = threading.Lock()

SKILL_PATTERNS: list[tuple[str, str]] = [
    (r"\bpython\b", "Python"),
    (r"\bjava\b", "Java"),
    (r"\bc\+\+\b", "C++"),
    (r"\bc#\b", "C#"),
    (r"\bjavascript\b", "JavaScript"),
    (r"\btypescript\b", "TypeScript"),
    (r"\breact\b", "React"),
    (r"\bfastapi\b", "FastAPI"),
    (r"\bdjango\b", "Django"),
    (r"\bflask\b", "Flask"),
    (r"\bsql\b", "SQL"),
    (r"\bpostgres(?:ql)?\b", "PostgreSQL"),
    (r"\bmysql\b", "MySQL"),
    (r"\bmongodb\b", "MongoDB"),
    (r"\baws\b", "AWS"),
    (r"\bazure\b", "Azure"),
    (r"\bgcp\b", "GCP"),
    (r"\bdocker\b", "Docker"),
    (r"\bkubernetes\b", "Kubernetes"),
    (r"\bmachine learning\b", "Machine Learning"),
    (r"\bdata science\b", "Data Science"),
    (r"\bpandas\b", "Pandas"),
    (r"\bnumpy\b", "NumPy"),
    (r"\bhtml\b", "HTML"),
    (r"\bcss\b", "CSS"),
    (r"\bvue\b", "Vue"),
    (r"\bangular\b", "Angular"),
]

ROLE_REQUIREMENTS: dict[str, set[str]] = {
    "backend": {"python", "fastapi", "django", "flask", "sql", "postgresql", "mongodb", "aws", "docker"},
    "frontend": {"javascript", "typescript", "react", "html", "css", "vue", "angular"},
    "data": {"python", "machine learning", "data science", "sql", "pandas", "numpy"},
}

MONTH_TO_NUMBER: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
MONTH_NAME_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
WORK_CONTEXT_PATTERN = re.compile(
    r"\b(intern|internship|work|employment|experience|engineer|developer|analyst|consultant|associate|trainee|job)\b",
    re.IGNORECASE,
)
EDUCATION_CONTEXT_PATTERN = re.compile(
    r"\b(education|b\.?tech|bachelor|master|university|college|school|cgpa|gpa)\b",
    re.IGNORECASE,
)


def _try_load_offline_llm() -> None:
    global offline_llm_enabled, _tokenizer, _model
    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        _tokenizer = AutoTokenizer.from_pretrained("google/flan-t5-small", local_files_only=True)
        _model = AutoModelForSeq2SeqLM.from_pretrained("google/flan-t5-small", local_files_only=True)
        offline_llm_enabled = True
        print("[AI] Offline LLM enabled (google/flan-t5-small).")
    except Exception as exc:
        offline_llm_enabled = False
        _tokenizer = None
        _model = None
        print(f"[AI] Offline LLM disabled. Reason: {exc}")


def ensure_offline_llm_loaded() -> bool:
    global _llm_load_attempted
    if offline_llm_enabled and _tokenizer is not None and _model is not None:
        return True
    if _llm_load_attempted:
        return False

    with _llm_lock:
        if offline_llm_enabled and _tokenizer is not None and _model is not None:
            return True
        if _llm_load_attempted:
            return False
        _llm_load_attempted = True
        _try_load_offline_llm()
        return offline_llm_enabled and _tokenizer is not None and _model is not None


def generate_llm_response(prompt: str, max_tokens: int = 100) -> str:
    if not ensure_offline_llm_loaded():
        return ""
    inputs = _tokenizer(prompt, return_tensors="pt", truncation=True, max_length=512)
    outputs = _model.generate(**inputs, max_new_tokens=max_tokens)
    return _tokenizer.decode(outputs[0], skip_special_tokens=True).strip()


def extract_text_from_pdf(file_path: str) -> str:
    text = ""
    with open(file_path, "rb") as file:
        reader = PyPDF2.PdfReader(file)
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"
    return text


def _normalize_year(year_text: str) -> int:
    year = int(year_text)
    if year < 100:
        return 2000 + year
    return year


def _parse_month_token(month_text: str) -> Optional[int]:
    if not month_text:
        return None
    return MONTH_TO_NUMBER.get(month_text.strip().lower())


def _extract_name(lines: list[str], email: Optional[str]) -> str:
    noise_tokens = {
        "resume",
        "curriculum",
        "vitae",
        "email",
        "phone",
        "linkedin",
        "github",
        "address",
        "profile",
        "summary",
        "objective",
        "experience",
        "skills",
        "projects",
        "education",
    }

    for line in lines[:15]:
        line_lower = line.lower()
        if "@" in line_lower:
            continue
        if any(token in line_lower for token in noise_tokens):
            continue
        if re.search(r"\d{3,}", line):
            continue
        if len(line.split()) < 2 or len(line.split()) > 5:
            continue
        if len(line) < 4 or len(line) > 50:
            continue
        if not re.fullmatch(r"[A-Za-z .'\-]+", line):
            continue
        return line.strip()

    if email:
        local_part = email.split("@")[0]
        guess = re.sub(r"[._\-]+", " ", local_part).strip()
        if guess and any(ch.isalpha() for ch in guess):
            guess = " ".join([token.capitalize() for token in guess.split() if token])
            if len(guess.split()) >= 2:
                return guess
    return "Unknown"


def _extract_years_from_date_ranges(text_lower: str) -> Optional[float]:
    range_pattern = re.compile(
        rf"(?P<start_month>{MONTH_NAME_PATTERN})\s*[./,\-]?\s*(?P<start_year>\d{{2,4}})"
        r"\s*(?:-|to)\s*"
        rf"(?P<end_month>{MONTH_NAME_PATTERN}|present|current|now)\s*[./,\-]?\s*(?P<end_year>\d{{2,4}})?",
        re.IGNORECASE,
    )

    today = date.today()
    total_months = 0
    seen_ranges: set[tuple[int, int, int, int]] = set()

    for match in range_pattern.finditer(text_lower):
        context_start = max(0, match.start() - 140)
        context_end = min(len(text_lower), match.end() + 140)
        context = text_lower[context_start:context_end]

        has_work_context = WORK_CONTEXT_PATTERN.search(context) is not None
        has_education_context = EDUCATION_CONTEXT_PATTERN.search(context) is not None
        if not has_work_context and has_education_context:
            continue
        if not has_work_context and not has_education_context:
            continue

        start_month_text = match.group("start_month")
        start_year_text = match.group("start_year")
        end_month_text = match.group("end_month")
        end_year_text = match.group("end_year")

        start_month = _parse_month_token(start_month_text)
        if start_month is None:
            continue
        start_year = _normalize_year(start_year_text)

        if end_month_text in {"present", "current", "now"}:
            end_month = today.month
            end_year = today.year
        else:
            end_month = _parse_month_token(end_month_text)
            if end_month is None:
                continue
            if end_year_text:
                end_year = _normalize_year(end_year_text)
            else:
                end_year = start_year
                if end_month < start_month:
                    end_year += 1

        if end_year < start_year:
            continue
        if end_year == start_year and end_month < start_month:
            continue

        signature = (start_year, start_month, end_year, end_month)
        if signature in seen_ranges:
            continue
        seen_ranges.add(signature)

        months = (end_year - start_year) * 12 + (end_month - start_month) + 1
        if months <= 0 or months > 240:
            continue
        total_months += months

    if total_months > 0:
        return total_months / 12.0
    return None


def _extract_years_of_experience(text_lower: str) -> Optional[float]:
    patterns = [
        r"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?|yr)\b",
        r"(?:experience|exp)\s*[:\-]?\s*(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?|yr)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None

    months_match = re.search(r"(\d+(?:\.\d+)?)\+?\s*(?:months?|mos?)\b", text_lower)
    if months_match:
        try:
            months = float(months_match.group(1))
            if months > 0:
                return months / 12.0
        except ValueError:
            return None

    range_years = _extract_years_from_date_ranges(text_lower)
    if range_years is not None:
        return range_years

    if re.search(r"\bintern(?:ship)?\b", text_lower):
        return 0.25

    return None


def _extract_skills(text_lower: str) -> str:
    found_skills: list[str] = []
    for pattern, display_name in SKILL_PATTERNS:
        if re.search(pattern, text_lower):
            found_skills.append(display_name)
    return ", ".join(found_skills)


def _normalize_skill_token(skill: str) -> str:
    token = skill.strip().lower()
    replacements = {
        "postgre sql": "postgresql",
        "postgres": "postgresql",
        "js": "javascript",
        "ts": "typescript",
        "ml": "machine learning",
    }
    return replacements.get(token, token)


def _split_skills(skills_csv: Optional[str]) -> list[str]:
    if not skills_csv:
        return []
    return [_normalize_skill_token(skill) for skill in skills_csv.split(",") if skill.strip()]


def _role_key(role: Optional[str]) -> Optional[str]:
    if not role:
        return None
    role_lower = role.lower()
    if "backend" in role_lower or "python" in role_lower:
        return "backend"
    if "frontend" in role_lower or "react" in role_lower:
        return "frontend"
    if "data" in role_lower or "machine learning" in role_lower:
        return "data"
    return None


def _safe_float(value, min_value: float = 0.0, max_value: float = 100.0) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(min(parsed, max_value), min_value)


def _extract_first_json_object(text: str) -> Optional[str]:
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escape_next:
                escape_next = False
            elif char == "\\":
                escape_next = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


def _parse_structured_json(text: str) -> Optional[dict]:
    blob = _extract_first_json_object(text)
    if not blob:
        return None

    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        return None

    if isinstance(payload, dict):
        return payload
    return None


def _coerce_skill_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_skills = [str(item) for item in value]
    else:
        raw_skills = [part.strip() for part in re.split(r"[,;\n]", str(value))]

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in raw_skills:
        skill = raw.strip()
        if not skill:
            continue
        norm = _normalize_skill_token(skill)
        if norm in seen:
            continue
        seen.add(norm)
        if len(skill) <= 3:
            cleaned.append(skill.upper())
        elif skill.lower() == "fastapi":
            cleaned.append("FastAPI")
        elif skill.lower() == "javascript":
            cleaned.append("JavaScript")
        elif skill.lower() == "typescript":
            cleaned.append("TypeScript")
        elif skill.lower() == "numpy":
            cleaned.append("NumPy")
        else:
            cleaned.append(skill.title())
    return cleaned


def _format_experience_text(years: Optional[float]) -> Optional[str]:
    if years is None:
        return None
    years = max(0.0, years)
    if years >= 1:
        if float(years).is_integer():
            return f"{int(years)} Years"
        return f"{years:.1f} Years"
    months = max(1, int(round(years * 12)))
    return f"{months} Months"


def _heuristic_extraction_confidence(name: str, email: Optional[str], skills_csv: str, experience: Optional[str]) -> float:
    confidence = 0.20
    if name and name.lower() != "unknown":
        confidence += 0.20
    if email:
        confidence += 0.25
    if skills_csv:
        confidence += min(0.25, 0.04 * len([s for s in skills_csv.split(",") if s.strip()]))
    if experience:
        confidence += 0.15
    return round(min(confidence, 0.90), 2)


def _llm_extract_candidate_fields(text: str) -> Optional[dict]:
    if not ensure_offline_llm_loaded():
        return None

    truncated = text[:2600].replace("\n", " ")
    prompt = (
        "Extract resume fields and return only valid JSON. "
        "Schema: {\"name\": string|null, \"email\": string|null, "
        "\"skills\": [string], \"experience_years\": number|null, "
        "\"confidence\": number, \"rationale\": string}. "
        "confidence must be between 0 and 1.\n"
        f"Resume Text: {truncated}"
    )

    raw = generate_llm_response(prompt, max_tokens=220)
    parsed = _parse_structured_json(raw)
    if not parsed:
        return None

    confidence = _safe_float(parsed.get("confidence"), 0.0, 1.0)
    if confidence is None:
        confidence = 0.5

    skills = _coerce_skill_list(parsed.get("skills"))
    years = _safe_float(parsed.get("experience_years"), 0.0, 40.0)

    name = parsed.get("name")
    email = parsed.get("email")
    rationale = str(parsed.get("rationale") or "")

    return {
        "name": str(name).strip() if isinstance(name, str) and name.strip() else None,
        "email": str(email).strip().lower() if isinstance(email, str) and "@" in email else None,
        "skills": skills,
        "experience_years": years,
        "confidence": round(confidence, 2),
        "rationale": rationale[:240],
    }


def parse_candidate_details(text: str) -> dict:
    details = {
        "name": "Unknown",
        "email": None,
        "skills": "",
        "experience": None,
        "extraction_confidence": 0.0,
        "extraction_source": "heuristic",
    }
    if not text:
        return details

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text_lower = text.lower()

    email_match = re.search(r"[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9.\-]+", text)
    if email_match:
        details["email"] = email_match.group(0).lower()

    details["name"] = _extract_name(lines, details["email"])
    details["skills"] = _extract_skills(text_lower)

    heuristic_years = _extract_years_of_experience(text_lower)
    details["experience"] = _format_experience_text(heuristic_years)

    heuristic_conf = _heuristic_extraction_confidence(
        details["name"], details["email"], details["skills"], details["experience"]
    )

    llm_result = _llm_extract_candidate_fields(text)
    if not llm_result:
        details["extraction_confidence"] = heuristic_conf
        return details

    llm_conf = llm_result["confidence"]

    if llm_result["name"] and (details["name"].lower() == "unknown" or llm_conf >= 0.55):
        details["name"] = llm_result["name"]

    if llm_result["email"] and (not details["email"] or llm_conf >= 0.55):
        details["email"] = llm_result["email"]

    heuristic_skills = _coerce_skill_list(details["skills"])
    llm_skills = llm_result["skills"]
    merged_skills = _coerce_skill_list(", ".join(heuristic_skills + llm_skills if llm_conf >= 0.45 else heuristic_skills))
    details["skills"] = ", ".join(merged_skills)

    if llm_result["experience_years"] is not None and (details["experience"] is None or llm_conf >= 0.50):
        details["experience"] = _format_experience_text(llm_result["experience_years"])

    details["extraction_confidence"] = round(max(heuristic_conf, llm_conf), 2)
    details["extraction_source"] = "llm+heuristic"
    if llm_result["rationale"]:
        details["extraction_rationale"] = llm_result["rationale"]

    return details


def _heuristic_evaluation(role: str, extracted_skills: Optional[str]) -> dict:
    role_bucket = _role_key(role)
    skills_list = set(_split_skills(extracted_skills))

    if role_bucket and role_bucket in ROLE_REQUIREMENTS:
        required = ROLE_REQUIREMENTS[role_bucket]
        matched_skills = sorted(required.intersection(skills_list))
        missing_skills = sorted(required.difference(skills_list))
        max_score = len(required)
        score = len(matched_skills)
    else:
        matched_skills = sorted(skills_list)
        missing_skills = []
        max_score = 5
        score = min(len(skills_list), 5)

    match_percentage = int((score / max_score) * 100) if max_score > 0 else 0
    match_percentage = min(max(match_percentage, 0), 100)
    status = "Shortlisted" if (score >= 2 or match_percentage >= 40) else "Rejected"

    return {
        "status": status,
        "score": match_percentage,
        "matched_skills": ", ".join(matched_skills),
        "missing_skills": ", ".join(missing_skills),
        "confidence": 0.62,
        "rationale": "Rule-based role-skill matching",
    }


def _llm_evaluate_candidate(
    role: str,
    resume_text: str,
    skills: Optional[str],
    experience: Optional[str],
    heuristic_score: int,
    heuristic_status: str,
) -> Optional[dict]:
    if not ensure_offline_llm_loaded():
        return None

    context = resume_text[:1800].replace("\n", " ") if resume_text else "Not provided"
    prompt = (
        "Evaluate candidate fit and return only JSON. "
        "Schema: {\"score\": number, \"status\": \"Shortlisted\"|\"Rejected\", "
        "\"confidence\": number, \"rationale\": string}. confidence must be 0..1.\n"
        f"Role: {role}\n"
        f"Skills: {skills or 'Not provided'}\n"
        f"Experience: {experience or 'Not provided'}\n"
        f"Heuristic score: {heuristic_score} ({heuristic_status})\n"
        f"Resume: {context}"
    )

    raw = generate_llm_response(prompt, max_tokens=140)
    parsed = _parse_structured_json(raw)
    if not parsed:
        return None

    score = _safe_float(parsed.get("score"), 0.0, 100.0)
    confidence = _safe_float(parsed.get("confidence"), 0.0, 1.0)
    status = str(parsed.get("status") or "").strip().title()
    rationale = str(parsed.get("rationale") or "").strip()

    if score is None or confidence is None or status not in {"Shortlisted", "Rejected"}:
        return None

    return {
        "score": int(round(score)),
        "status": status,
        "confidence": round(confidence, 2),
        "rationale": rationale[:260],
    }


def evaluate_candidate(role: str, resume_text: str, extracted_skills: Optional[str]) -> dict:
    heuristic = _heuristic_evaluation(role, extracted_skills)
    heuristic_score = heuristic["score"]
    heuristic_status = heuristic["status"]

    llm_eval = _llm_evaluate_candidate(
        role=role,
        resume_text=resume_text,
        skills=extracted_skills,
        experience=None,
        heuristic_score=heuristic_score,
        heuristic_status=heuristic_status,
    )

    if not llm_eval:
        return {**heuristic, "evaluation_source": "heuristic"}

    llm_conf = llm_eval["confidence"]
    llm_weight = 0.25 + (0.50 * llm_conf)
    blended_score = int(round((1.0 - llm_weight) * heuristic_score + llm_weight * llm_eval["score"]))

    if llm_conf >= 0.75:
        final_status = llm_eval["status"]
    else:
        final_status = "Shortlisted" if blended_score >= 40 else "Rejected"

    return {
        "status": final_status,
        "score": blended_score,
        "matched_skills": heuristic["matched_skills"],
        "missing_skills": heuristic["missing_skills"],
        "confidence": round(max(heuristic["confidence"], llm_conf), 2),
        "rationale": llm_eval["rationale"] or heuristic["rationale"],
        "evaluation_source": "llm+heuristic",
    }


def _deterministic_summary(
    name: str,
    role: str,
    skills: Optional[str],
    experience: Optional[str],
    status: str,
    match_score: Optional[int],
) -> str:
    skills_list = [skill.strip() for skill in (skills or "").split(",") if skill.strip()]
    top_skills = ", ".join(skills_list[:4]) if skills_list else "general engineering foundations"

    if experience:
        experience_phrase = f"{experience.lower()} of hands-on experience"
    else:
        experience_phrase = "an unspecified amount of practical experience"

    sentence_one = f"{name} is a candidate for {role} with {experience_phrase}."

    if match_score is not None:
        sentence_two = (
            f"Key strengths include {top_skills}, and the profile is currently marked as "
            f"{status} with an estimated match score of {match_score}%."
        )
    else:
        sentence_two = f"Key strengths include {top_skills}, and the profile is currently marked as {status}."

    return f"{sentence_one} {sentence_two}"


def _normalize_to_two_sentences(text: str, fallback: str) -> str:
    if not text.strip():
        return fallback

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]
    if len(sentences) < 2:
        return fallback

    first = sentences[0].rstrip(".!?") + "."
    second = sentences[1].rstrip(".!?") + "."
    return f"{first} {second}"


def generate_candidate_summary(
    name: str,
    role: str,
    skills: Optional[str],
    experience: Optional[str],
    status: str,
    match_score: Optional[int] = None,
) -> str:
    fallback = _deterministic_summary(name, role, skills, experience, status, match_score)

    if not ensure_offline_llm_loaded():
        return fallback

    try:
        prompt = (
            "Write exactly two professional recruiter sentences and return plain text only. "
            f"Candidate: {name}. Role: {role}. Experience: {experience or 'Not specified'}. "
            f"Skills: {skills or 'Not specified'}. Status: {status}. Match Score: {match_score}."
        )
        summary = generate_llm_response(prompt, max_tokens=100)
        return _normalize_to_two_sentences(summary, fallback)
    except Exception as exc:
        print(f"[AI] Summary generation failed, using deterministic summary. Reason: {exc}")
        return fallback


def extract_experience_years(experience: Optional[str]) -> float:
    if not experience:
        return 0.0
    match = re.search(r"(\d+(?:\.\d+)?)", experience)
    if not match:
        return 0.0
    try:
        value = float(match.group(1))
        exp_lower = experience.lower()
        if "month" in exp_lower or "mo" in exp_lower:
            return value / 12.0
        return value
    except ValueError:
        return 0.0


def _extract_numeric_score(text: str) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


@lru_cache(maxsize=512)
def _cached_profile_rank(
    role: str,
    name: str,
    skills: str,
    experience: str,
    status: str,
    match_score: int,
) -> tuple[float, float, str]:
    base_score = float(match_score)
    if not offline_llm_enabled:
        return base_score, 0.0, "heuristic"

    prompt = (
        "Return only JSON for candidate ranking. "
        "Schema: {\"score\": number, \"confidence\": number}. confidence 0..1.\n"
        f"Role: {role}\n"
        f"Candidate: {name}\n"
        f"Skills: {skills or 'Not specified'}\n"
        f"Experience: {experience or 'Not specified'}\n"
        f"Status: {status}\n"
        f"Heuristic score: {match_score}"
    )

    raw = generate_llm_response(prompt, max_tokens=60)
    parsed = _parse_structured_json(raw)
    if not parsed:
        return base_score, 0.0, "heuristic"

    score = _safe_float(parsed.get("score"), 0.0, 100.0)
    confidence = _safe_float(parsed.get("confidence"), 0.0, 1.0)
    if score is None or confidence is None:
        fallback_score = _extract_numeric_score(raw)
        if fallback_score is None:
            return base_score, 0.0, "heuristic"
        return max(0.0, min(100.0, fallback_score)), 0.35, "llm-fallback"

    return score, round(confidence, 2), "llm"


def llm_candidate_fit_score(
    role: str,
    name: str,
    skills: Optional[str],
    experience: Optional[str],
    status: str,
    match_score: Optional[int],
) -> dict:
    base = int(match_score if match_score is not None else 0)
    score, confidence, source = _cached_profile_rank(
        role=role or "General",
        name=name or "Unknown",
        skills=skills or "",
        experience=experience or "",
        status=status or "Pending",
        match_score=base,
    )
    return {
        "score": round(score, 2),
        "confidence": confidence,
        "source": source,
    }


def candidate_priority_score(candidate) -> float:
    base_score = (
        float(candidate.match_score)
        if candidate.match_score is not None
        else float(evaluate_candidate(candidate.role_applied, "", candidate.skills).get("score", 0))
    )
    exp_score = min(extract_experience_years(candidate.experience) * 10.0, 100.0)

    llm_rank = llm_candidate_fit_score(
        role=candidate.role_applied,
        name=candidate.name,
        skills=candidate.skills,
        experience=candidate.experience,
        status=candidate.status,
        match_score=int(base_score),
    )

    heuristic_priority = (0.85 * base_score) + (0.15 * exp_score)
    llm_conf = llm_rank["confidence"]

    if llm_rank["source"].startswith("llm") and llm_conf > 0:
        llm_weight = 0.20 + (0.35 * llm_conf)
        final_score = ((1.0 - llm_weight) * heuristic_priority) + (llm_weight * llm_rank["score"])
    else:
        final_score = heuristic_priority

    return round(final_score, 2)


def sort_candidates_for_display(candidates: list):
    return sorted(
        candidates,
        key=lambda candidate: (candidate_priority_score(candidate), candidate.id),
        reverse=True,
    )

