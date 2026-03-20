import os
import re
import sys
import threading
import traceback
from contextlib import contextmanager
from uuid import uuid4

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import crud
import database
import models
import schemas
import utils

APP_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(APP_DIR, "uploads"))
PLACEHOLDER_EMAIL_DOMAIN = "autofill.example.com"
ROLES = ["Backend Engineer (Python)", "Frontend Developer", "Data Scientist", "General"]

# Ensure schema exists and backfill normalized skills
models.Base.metadata.create_all(bind=database.engine)
with database.SessionLocal() as bootstrap_db:
    crud.bootstrap_normalized_skills(bootstrap_db)


def normalize_filename(filename: str) -> str:
    base = os.path.basename(filename or "resume.pdf")
    normalized = re.sub(r"[^A-Za-z0-9._\-]", "_", base)
    return normalized or "resume.pdf"


def is_placeholder_email(email: str) -> bool:
    lower = email.lower()
    return (
        lower.endswith(f"@{PLACEHOLDER_EMAIL_DOMAIN}")
        or lower.startswith("unknown")
        or lower.startswith("temp")
    )


def build_placeholder_email(name_hint: str, db) -> str:
    local_base = re.sub(r"[^a-z0-9]+", ".", (name_hint or "candidate").lower()).strip(".")
    if not local_base:
        local_base = "candidate"

    index = 1
    while True:
        email = f"{local_base}.{index}@{PLACEHOLDER_EMAIL_DOMAIN}"
        if crud.get_candidate_by_email(db, email=email) is None:
            return email
        index += 1


def copy_resume_to_uploads(source_path: str, prefix: str) -> str:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = normalize_filename(os.path.basename(source_path))
    destination = os.path.join(UPLOAD_DIR, f"{prefix}_{filename}")

    with open(source_path, "rb") as src, open(destination, "wb") as dst:
        dst.write(src.read())

    return destination


@contextmanager
def db_session():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def evaluate_and_summarize(candidate, role: str, resume_text: str, skills: str | None, experience: str | None):
    eval_result = utils.evaluate_candidate(role, resume_text, skills)
    summary = utils.generate_candidate_summary(
        name=candidate.name,
        role=role,
        skills=skills or candidate.skills,
        experience=experience or candidate.experience,
        status=eval_result["status"],
        match_score=eval_result["score"],
    )
    return eval_result, summary


def candidate_strengths(candidate) -> list[str]:
    evaluation = utils.evaluate_candidate(candidate.role_applied, "", candidate.skills)
    effective_score = candidate.match_score if candidate.match_score is not None else evaluation["score"]
    strengths = [
        f"Match score: {effective_score}%",
        f"AI rank score: {utils.candidate_priority_score(candidate)}",
        f"Status: {candidate.status}",
    ]
    if candidate.experience:
        strengths.append(f"Experience: {candidate.experience}")
    if evaluation.get("matched_skills"):
        strengths.append(f"Role-aligned skills: {evaluation['matched_skills']}")
    elif candidate.skills:
        strengths.append(f"Skills: {candidate.skills}")
    return strengths


def compare_two_candidates(candidate_a, candidate_b) -> dict:
    same_role = candidate_a.role_applied.strip().lower() == candidate_b.role_applied.strip().lower()
    role_context = candidate_a.role_applied if same_role else f"{candidate_a.role_applied} vs {candidate_b.role_applied}"

    score_a = candidate_a.match_score if candidate_a.match_score is not None else 0
    score_b = candidate_b.match_score if candidate_b.match_score is not None else 0
    priority_a = utils.candidate_priority_score(candidate_a)
    priority_b = utils.candidate_priority_score(candidate_b)

    years_a = utils.extract_experience_years(candidate_a.experience)
    years_b = utils.extract_experience_years(candidate_b.experience)

    if priority_a > priority_b + 2:
        better_fit = "candidate_a"
        recommendation = f"{candidate_a.name} is ranked higher by the AI sorting score."
    elif priority_b > priority_a + 2:
        better_fit = "candidate_b"
        recommendation = f"{candidate_b.name} is ranked higher by the AI sorting score."
    elif score_a > score_b + 5:
        better_fit = "candidate_a"
        recommendation = f"AI rank is close, but {candidate_a.name} has the stronger role-match score."
    elif score_b > score_a + 5:
        better_fit = "candidate_b"
        recommendation = f"AI rank is close, but {candidate_b.name} has the stronger role-match score."
    elif years_a > years_b + 0.5:
        better_fit = "candidate_a"
        recommendation = f"Scores are close, but {candidate_a.name} shows stronger experience depth."
    elif years_b > years_a + 0.5:
        better_fit = "candidate_b"
        recommendation = f"Scores are close, but {candidate_b.name} shows stronger experience depth."
    else:
        better_fit = "tie"
        recommendation = "Both candidates appear similarly competitive."

    return {
        "same_role": same_role,
        "role_context": role_context,
        "better_fit": better_fit,
        "recommendation": recommendation,
        "candidate_a_strengths": candidate_strengths(candidate_a),
        "candidate_b_strengths": candidate_strengths(candidate_b),
    }


class CandidateDesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Aimploy Candidate Management Desktop")
        self.geometry("1350x860")
        self.minsize(1150, 760)
        self._set_app_icon()

        self.candidates_cache = []
        self.candidate_map = {}
        self.compare_option_map = {}

        self._configure_style()
        self._build_ui()
        self.refresh_candidates()
        self.after(300, self._warm_up_ai_ranking_async)

    def _set_app_icon(self):
        ico_path = os.path.join(RESOURCE_DIR, "assets", "aimploy_icon.ico")
        png_path = os.path.join(RESOURCE_DIR, "assets", "aimploy_icon.png")

        try:
            if os.path.exists(ico_path):
                self.iconbitmap(ico_path)
                return
        except tk.TclError:
            pass

        try:
            if os.path.exists(png_path):
                self._app_icon_photo = tk.PhotoImage(file=png_path)
                self.iconphoto(True, self._app_icon_photo)
        except tk.TclError:
            self._app_icon_photo = None

    def _configure_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

    def _warm_up_ai_ranking_async(self):
        # Initialize optional local LLM in the background to keep app startup snappy.
        worker = threading.Thread(target=self._warm_up_ai_ranking_worker, daemon=True)
        worker.start()

    def _warm_up_ai_ranking_worker(self):
        try:
            if utils.ensure_offline_llm_loaded():
                try:
                    utils._cached_profile_rank.cache_clear()
                except Exception:
                    pass
                self.after(0, self.refresh_candidates)
        except Exception:
            # Keep startup resilient even if local model files are unavailable.
            pass

    def _build_ui(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        title = ttk.Label(
            root,
            text="Candidate Management Desktop (Offline)",
            font=("Segoe UI", 16, "bold"),
        )
        title.pack(anchor="w", pady=(0, 10))

        subtitle = ttk.Label(
            root,
            text="No server needed. Data is stored locally in SQLite and processed with offline parser/AI logic.",
        )
        subtitle.pack(anchor="w", pady=(0, 12))

        top_row = ttk.Frame(root)
        top_row.pack(fill="x", pady=(0, 12))

        self._build_manual_create_card(top_row)
        self._build_auto_create_card(top_row)

        self._build_directory_card(root)
        self._build_compare_card(root)

    def _build_manual_create_card(self, parent):
        card = ttk.LabelFrame(parent, text="Create Candidate", padding=12)
        card.pack(side="left", fill="both", expand=True, padx=(0, 6))

        ttk.Label(card, text="Full Name").grid(row=0, column=0, sticky="w")
        ttk.Label(card, text="Email").grid(row=1, column=0, sticky="w")
        ttk.Label(card, text="Role Applied").grid(row=2, column=0, sticky="w")

        self.name_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.role_var = tk.StringVar(value=ROLES[0])

        ttk.Entry(card, textvariable=self.name_var, width=40).grid(row=0, column=1, sticky="we", padx=(8, 0), pady=2)
        ttk.Entry(card, textvariable=self.email_var, width=40).grid(row=1, column=1, sticky="we", padx=(8, 0), pady=2)
        ttk.Combobox(card, textvariable=self.role_var, values=ROLES, state="readonly", width=37).grid(
            row=2, column=1, sticky="we", padx=(8, 0), pady=2
        )

        ttk.Button(card, text="Create Candidate", command=self.create_candidate).grid(
            row=3, column=1, sticky="e", pady=(10, 0)
        )

        card.columnconfigure(1, weight=1)

    def _build_auto_create_card(self, parent):
        card = ttk.LabelFrame(parent, text="Auto-create from Resume", padding=12)
        card.pack(side="left", fill="both", expand=True, padx=(6, 0))

        ttk.Label(card, text="Role Applied").grid(row=0, column=0, sticky="w")
        self.auto_role_var = tk.StringVar(value=ROLES[0])
        ttk.Combobox(card, textvariable=self.auto_role_var, values=ROLES, state="readonly", width=37).grid(
            row=0, column=1, sticky="we", padx=(8, 0), pady=2
        )

        ttk.Button(card, text="Select Resume & Auto-create", command=self.auto_create_from_resume).grid(
            row=1, column=1, sticky="e", pady=(12, 0)
        )
        ttk.Label(card, text="Extracts name, email, skills, experience, score, and summary.").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

        card.columnconfigure(1, weight=1)

    def _build_directory_card(self, parent):
        card = ttk.LabelFrame(parent, text="Candidate Directory", padding=12)
        card.pack(fill="both", expand=True, pady=(0, 12))

        columns = ("id", "name", "email", "role", "status", "score", "experience")
        self.tree = ttk.Treeview(card, columns=columns, show="headings", height=13)
        for col, heading, width in [
            ("id", "ID", 60),
            ("name", "Name", 200),
            ("email", "Email", 240),
            ("role", "Role", 200),
            ("status", "Status", 100),
            ("score", "Score", 80),
            ("experience", "Experience", 120),
        ]:
            self.tree.heading(col, text=heading)
            self.tree.column(col, width=width, anchor="w")

        scroll = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<<TreeviewSelect>>", lambda _: self.populate_detail_panel())

        button_row = ttk.Frame(card)
        button_row.grid(row=1, column=0, columnspan=2, sticky="we", pady=(10, 8))

        ttk.Button(button_row, text="Refresh", command=self.refresh_candidates).pack(side="left", padx=(0, 6))
        ttk.Button(button_row, text="Upload Resume", command=self.upload_resume_for_selected).pack(side="left", padx=6)
        ttk.Button(button_row, text="Generate Summary", command=self.generate_summary_for_selected).pack(side="left", padx=6)
        ttk.Button(button_row, text="Re-evaluate", command=self.re_evaluate_selected).pack(side="left", padx=6)
        ttk.Button(button_row, text="Re-evaluate All", command=self.re_evaluate_all).pack(side="left", padx=6)
        ttk.Button(button_row, text="Delete", command=self.delete_selected).pack(side="right")

        detail_row = ttk.Frame(card)
        detail_row.grid(row=2, column=0, columnspan=2, sticky="nsew")

        skills_box = ttk.LabelFrame(detail_row, text="Skills", padding=8)
        skills_box.pack(side="left", fill="both", expand=True, padx=(0, 6))
        summary_box = ttk.LabelFrame(detail_row, text="AI Summary", padding=8)
        summary_box.pack(side="left", fill="both", expand=True, padx=(6, 0))

        self.skills_text = tk.Text(skills_box, height=5, wrap="word")
        self.skills_text.pack(fill="both", expand=True)
        self.summary_text = tk.Text(summary_box, height=5, wrap="word")
        self.summary_text.pack(fill="both", expand=True)

        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)

    def _build_compare_card(self, parent):
        card = ttk.LabelFrame(parent, text="Comparison View", padding=12)
        card.pack(fill="both", expand=False)

        row = ttk.Frame(card)
        row.pack(fill="x")

        ttk.Label(row, text="Candidate A").grid(row=0, column=0, sticky="w")
        ttk.Label(row, text="Candidate B").grid(row=0, column=2, sticky="w")

        self.compare_a_var = tk.StringVar()
        self.compare_b_var = tk.StringVar()

        self.compare_a_combo = ttk.Combobox(row, textvariable=self.compare_a_var, state="readonly", width=45)
        self.compare_b_combo = ttk.Combobox(row, textvariable=self.compare_b_var, state="readonly", width=45)
        self.compare_a_combo.grid(row=1, column=0, sticky="we", padx=(0, 8), pady=(4, 8))
        self.compare_b_combo.grid(row=1, column=2, sticky="we", padx=(8, 0), pady=(4, 8))

        ttk.Button(row, text="Compare", command=self.compare_selected_candidates).grid(row=1, column=1, padx=6)

        row.columnconfigure(0, weight=1)
        row.columnconfigure(2, weight=1)

        self.compare_text = tk.Text(card, height=8, wrap="word")
        self.compare_text.pack(fill="both", expand=True)

    def selected_candidate_id(self) -> int | None:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return int(self.tree.item(selection[0], "values")[0])
        except Exception:
            return None

    def selected_candidate(self):
        cid = self.selected_candidate_id()
        if cid is None:
            return None
        return self.candidate_map.get(cid)

    def populate_detail_panel(self):
        candidate = self.selected_candidate()
        self.skills_text.delete("1.0", tk.END)
        self.summary_text.delete("1.0", tk.END)

        if not candidate:
            return

        self.skills_text.insert(tk.END, candidate.skills or "N/A")
        self.summary_text.insert(tk.END, candidate.ai_summary or "N/A")

    def refresh_candidates(self):
        try:
            with db_session() as db:
                candidates = crud.get_candidates(db, skip=0, limit=None)
            sorted_candidates = utils.sort_candidates_for_display(candidates)
            self.candidates_cache = sorted_candidates
            self.candidate_map = {candidate.id: candidate for candidate in sorted_candidates}

            existing_selection = self.selected_candidate_id()

            for item in self.tree.get_children():
                self.tree.delete(item)

            for candidate in sorted_candidates:
                score = f"{candidate.match_score}%" if candidate.match_score is not None else "N/A"
                self.tree.insert(
                    "",
                    "end",
                    values=(
                        candidate.id,
                        candidate.name,
                        candidate.email,
                        candidate.role_applied,
                        candidate.status,
                        score,
                        candidate.experience or "N/A",
                    ),
                )

            if existing_selection and existing_selection in self.candidate_map:
                for item in self.tree.get_children():
                    if int(self.tree.item(item, "values")[0]) == existing_selection:
                        self.tree.selection_set(item)
                        self.tree.focus(item)
                        break

            self.populate_detail_panel()
            self.refresh_compare_options()
        except Exception as exc:
            self.show_error("Failed to refresh candidates", exc)

    def refresh_compare_options(self):
        self.compare_option_map = {}
        options = []
        for candidate in self.candidates_cache:
            label = f"{candidate.id} - {candidate.name} ({candidate.role_applied})"
            options.append(label)
            self.compare_option_map[label] = candidate.id

        self.compare_a_combo["values"] = options
        self.compare_b_combo["values"] = options

        if len(options) >= 2:
            if not self.compare_a_var.get():
                self.compare_a_var.set(options[0])
            if not self.compare_b_var.get():
                self.compare_b_var.set(options[1])

    def create_candidate(self):
        name = self.name_var.get().strip()
        email = self.email_var.get().strip()
        role = self.role_var.get().strip() or "General"

        if not name or not email:
            messagebox.showwarning("Missing fields", "Please provide name and email.")
            return

        try:
            with db_session() as db:
                if crud.get_candidate_by_email(db, email=email):
                    messagebox.showerror("Duplicate email", "Email already registered.")
                    return

                payload = schemas.CandidateCreate(
                    name=name,
                    email=email,
                    role_applied=role,
                )
                crud.create_candidate(db, payload)

            self.name_var.set("")
            self.email_var.set("")
            self.refresh_candidates()
            messagebox.showinfo("Success", "Candidate created.")
        except Exception as exc:
            self.show_error("Failed to create candidate", exc)

    def auto_create_from_resume(self):
        file_path = filedialog.askopenfilename(title="Select Resume PDF", filetypes=[("PDF files", "*.pdf")])
        if not file_path:
            return

        role = self.auto_role_var.get().strip() or "General"

        try:
            with db_session() as db:
                saved_resume = copy_resume_to_uploads(file_path, f"auto_{uuid4().hex[:8]}")
                text = utils.extract_text_from_pdf(saved_resume)
                extracted = utils.parse_candidate_details(text)

                candidate_name = extracted.get("name") or "Unknown Candidate"
                if candidate_name.lower() == "unknown":
                    candidate_name = "Unknown Candidate"

                parsed_email = extracted.get("email")
                if parsed_email and crud.get_candidate_by_email(db, parsed_email) is None:
                    candidate_email = parsed_email
                else:
                    candidate_email = build_placeholder_email(candidate_name, db)

                created = crud.create_candidate(
                    db,
                    schemas.CandidateCreate(
                        name=candidate_name,
                        email=candidate_email,
                        role_applied=role,
                        skills=extracted.get("skills") or None,
                    ),
                )

                evaluation, summary = evaluate_and_summarize(
                    created,
                    role=role,
                    resume_text=text,
                    skills=extracted.get("skills"),
                    experience=extracted.get("experience"),
                )

                crud.update_candidate(
                    db,
                    created.id,
                    schemas.CandidateUpdate(
                        resume_filename=saved_resume,
                        skills=extracted.get("skills"),
                        skills_update_mode="replace",
                        status=evaluation["status"],
                        experience=extracted.get("experience"),
                        match_score=evaluation["score"],
                        ai_summary=summary,
                    ),
                )

            self.refresh_candidates()
            messagebox.showinfo("Success", "Candidate auto-created from resume.")
        except Exception as exc:
            self.show_error("Failed to auto-create candidate", exc)

    def upload_resume_for_selected(self):
        candidate = self.selected_candidate()
        if not candidate:
            messagebox.showwarning("No selection", "Select a candidate first.")
            return

        file_path = filedialog.askopenfilename(title="Select Resume PDF", filetypes=[("PDF files", "*.pdf")])
        if not file_path:
            return

        try:
            with db_session() as db:
                db_candidate = crud.get_candidate(db, candidate.id)
                if not db_candidate:
                    messagebox.showerror("Not found", "Candidate no longer exists.")
                    return

                saved_resume = copy_resume_to_uploads(file_path, f"{db_candidate.id}_{uuid4().hex[:8]}")
                text = utils.extract_text_from_pdf(saved_resume)
                extracted = utils.parse_candidate_details(text)

                evaluation, summary = evaluate_and_summarize(
                    db_candidate,
                    role=db_candidate.role_applied,
                    resume_text=text,
                    skills=extracted.get("skills"),
                    experience=extracted.get("experience"),
                )

                update_values = {
                    "resume_filename": saved_resume,
                    "skills": extracted.get("skills"),
                    "skills_update_mode": "replace",
                    "status": evaluation["status"],
                    "experience": extracted.get("experience"),
                    "match_score": evaluation["score"],
                }

                parsed_name = extracted.get("name")
                if parsed_name and parsed_name.lower() != "unknown":
                    if not db_candidate.name or db_candidate.name.strip().lower() in {"unknown", "unknown candidate", "n/a"}:
                        update_values["name"] = parsed_name

                parsed_email = extracted.get("email")
                if parsed_email and parsed_email != db_candidate.email and is_placeholder_email(db_candidate.email):
                    existing = crud.get_candidate_by_email(db, parsed_email)
                    if existing is None or existing.id == db_candidate.id:
                        update_values["email"] = parsed_email

                update_values["ai_summary"] = summary
                crud.update_candidate(db, db_candidate.id, schemas.CandidateUpdate(**update_values))

            self.refresh_candidates()
            messagebox.showinfo("Success", "Resume uploaded and candidate updated.")
        except Exception as exc:
            self.show_error("Failed to upload resume", exc)

    def generate_summary_for_selected(self):
        candidate = self.selected_candidate()
        if not candidate:
            messagebox.showwarning("No selection", "Select a candidate first.")
            return

        try:
            with db_session() as db:
                db_candidate = crud.get_candidate(db, candidate.id)
                if not db_candidate:
                    messagebox.showerror("Not found", "Candidate no longer exists.")
                    return

                summary = utils.generate_candidate_summary(
                    name=db_candidate.name,
                    role=db_candidate.role_applied,
                    skills=db_candidate.skills,
                    experience=db_candidate.experience,
                    status=db_candidate.status,
                    match_score=db_candidate.match_score,
                )
                crud.update_candidate(db, db_candidate.id, schemas.CandidateUpdate(ai_summary=summary))

            self.refresh_candidates()
            messagebox.showinfo("Success", "Summary generated.")
        except Exception as exc:
            self.show_error("Failed to generate summary", exc)

    def re_evaluate_selected(self):
        candidate = self.selected_candidate()
        if not candidate:
            messagebox.showwarning("No selection", "Select a candidate first.")
            return

        try:
            with db_session() as db:
                db_candidate = crud.get_candidate(db, candidate.id)
                if not db_candidate:
                    messagebox.showerror("Not found", "Candidate no longer exists.")
                    return

                evaluation = utils.evaluate_candidate(db_candidate.role_applied, "", db_candidate.skills)
                summary = utils.generate_candidate_summary(
                    name=db_candidate.name,
                    role=db_candidate.role_applied,
                    skills=db_candidate.skills,
                    experience=db_candidate.experience,
                    status=evaluation["status"],
                    match_score=evaluation["score"],
                )
                crud.update_candidate(
                    db,
                    db_candidate.id,
                    schemas.CandidateUpdate(
                        status=evaluation["status"],
                        match_score=evaluation["score"],
                        ai_summary=summary,
                    ),
                )

            self.refresh_candidates()
            messagebox.showinfo("Success", "Candidate re-evaluated.")
        except Exception as exc:
            self.show_error("Failed to re-evaluate candidate", exc)

    def re_evaluate_all(self):
        try:
            updated = 0
            with db_session() as db:
                candidates = crud.get_candidates(db, skip=0, limit=None)
                for candidate in candidates:
                    evaluation = utils.evaluate_candidate(candidate.role_applied, "", candidate.skills)
                    summary = utils.generate_candidate_summary(
                        name=candidate.name,
                        role=candidate.role_applied,
                        skills=candidate.skills,
                        experience=candidate.experience,
                        status=evaluation["status"],
                        match_score=evaluation["score"],
                    )
                    crud.update_candidate(
                        db,
                        candidate.id,
                        schemas.CandidateUpdate(
                            status=evaluation["status"],
                            match_score=evaluation["score"],
                            ai_summary=summary,
                        ),
                    )
                    updated += 1

            self.refresh_candidates()
            messagebox.showinfo("Success", f"Re-evaluated {updated} candidate(s).")
        except Exception as exc:
            self.show_error("Failed to re-evaluate all candidates", exc)

    def delete_selected(self):
        candidate = self.selected_candidate()
        if not candidate:
            messagebox.showwarning("No selection", "Select a candidate first.")
            return

        if not messagebox.askyesno("Confirm delete", f"Delete candidate '{candidate.name}' (ID {candidate.id})?"):
            return

        try:
            with db_session() as db:
                db_candidate = crud.get_candidate(db, candidate.id)
                if not db_candidate:
                    messagebox.showerror("Not found", "Candidate no longer exists.")
                    return
                db.delete(db_candidate)
                db.commit()

            self.refresh_candidates()
            messagebox.showinfo("Success", "Candidate deleted.")
        except Exception as exc:
            self.show_error("Failed to delete candidate", exc)

    def compare_selected_candidates(self):
        left_label = self.compare_a_var.get().strip()
        right_label = self.compare_b_var.get().strip()
        left_id = self.compare_option_map.get(left_label)
        right_id = self.compare_option_map.get(right_label)

        if left_id is None or right_id is None:
            messagebox.showwarning("Missing selection", "Select both candidates for comparison.")
            return
        if left_id == right_id:
            messagebox.showwarning("Invalid selection", "Select two different candidates.")
            return

        candidate_a = self.candidate_map.get(left_id)
        candidate_b = self.candidate_map.get(right_id)
        if not candidate_a or not candidate_b:
            messagebox.showerror("Not found", "Please refresh and try again.")
            return

        result = compare_two_candidates(candidate_a, candidate_b)

        lines = [
            f"Role Context: {result['role_context']}",
            f"Better Fit: {result['better_fit']}",
            f"Recommendation: {result['recommendation']}",
            "",
            f"{candidate_a.name} (ID: {candidate_a.id}) strengths:",
        ]
        lines.extend([f"- {item}" for item in result["candidate_a_strengths"]])
        lines.append("")
        lines.append(f"{candidate_b.name} (ID: {candidate_b.id}) strengths:")
        lines.extend([f"- {item}" for item in result["candidate_b_strengths"]])

        self.compare_text.delete("1.0", tk.END)
        self.compare_text.insert(tk.END, "\n".join(lines))

    @staticmethod
    def show_error(title: str, exc: Exception):
        debug = traceback.format_exc(limit=2)
        messagebox.showerror(title, f"{exc}\n\n{debug}")


def main():
    app = CandidateDesktopApp()
    app.mainloop()


if __name__ == "__main__":
    main()
