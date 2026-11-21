"""The owner's own browser tab, through the JARVIS Connector extension: read the page, read and fill a form, click,
scroll. And "my details" - what JARVIS fills forms with.

Rules (enforced here and again in the extension):
- Only the active tab of the browser the owner is using. Nothing runs in the background on other tabs.
- Reading is READ (the content is third-party: untrusted). Filling and clicking change a page: CONFIRM, with a preview
  of every field and value; never trust-listed. JARVIS never submits a form on its own - clicking Submit is a separate,
  always-asked click.
- Never filled, never stored: passwords, OTPs, card numbers/CVV/expiry, bank account/IFSC/UPI PIN, Aadhaar, PAN,
  passport, SSN. Those stay with the owner or their password manager.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..security import Level, SecurityError
from .registry import Tool, ToolContext, ToolRegistry

SENSITIVE = re.compile(r"pass\s*(?:word|code)|\botp\b|one[\s-]?time|\bcvv\b|\bcvc\b|card\s*(?:number|no)|credit\s*card|debit\s*card|expir|"
                       r"aadha+r|\bpan\b(?!\s*(?:cake|india))|bank\s*(?:account|a/c)|account\s*(?:number|no)|\bifsc\b|\bupi\b|\bpin\b(?!\s*code)|"
                       r"\bssn\b|social\s*security|passport|security\s*(?:code|question)|\bmpin\b|login\s*id|user\s*name|username", re.I)

# what the owner can store, and the words forms use for it (longest match wins: "first name" beats "name")
DETAILS: dict[str, list[str]] = {
    "full_name": ["full name", "name", "your name", "student name", "candidate name", "applicant name", "name of the student", "name of the candidate"],
    "first_name": ["first name", "given name", "fname"],
    "last_name": ["last name", "surname", "family name", "lname"],
    "email": ["email", "e-mail", "email address", "email id", "mail id", "e mail"],
    "phone": ["phone", "mobile", "phone number", "mobile number", "contact number", "contact no", "mobile no", "whatsapp number", "whatsapp", "telephone"],
    "date_of_birth": ["date of birth", "dob", "birth date", "birthday", "d.o.b"],
    "age": ["age"],
    "gender": ["gender", "sex"],
    "address": ["address", "street address", "residential address", "permanent address", "address line 1", "street", "postal address", "communication address"],
    "city": ["city", "town", "city / town"],
    "district": ["district"],
    "state": ["state", "province"],
    "pincode": ["pincode", "pin code", "zip", "zip code", "postal code", "postcode"],
    "country": ["country"],
    "nationality": ["nationality"],
    "school": ["school", "school name", "name of the school"],
    "college": ["college", "college name", "institution", "institute", "university", "name of the institution", "name of the college"],
    "course": ["course", "degree", "programme", "program", "branch", "department", "stream", "class", "standard", "grade"],
    "year_of_study": ["year of study", "current year", "semester", "year / semester"],
    "roll_number": ["roll number", "roll no", "register number", "registration number", "reg no", "reg. no", "student id", "enrollment number", "enrolment number"],
    "father_name": ["father's name", "father name", "fathers name", "name of the father"],
    "mother_name": ["mother's name", "mother name", "mothers name", "name of the mother"],
    "occupation": ["occupation", "profession", "job title", "designation"],
    "company": ["company", "organisation", "organization", "employer", "company name"],
    "website": ["website", "portfolio"],
    "linkedin": ["linkedin", "linkedin profile", "linkedin url"],
    "github": ["github", "github profile", "github url"],
}
AUTOCOMPLETE = {"name": "full_name", "given-name": "first_name", "family-name": "last_name", "email": "email", "tel": "phone",
                "tel-national": "phone", "bday": "date_of_birth", "sex": "gender", "street-address": "address", "address-line1": "address",
                "address-level2": "city", "address-level1": "state", "postal-code": "pincode", "country-name": "country", "country": "country",
                "organization": "company", "organization-title": "occupation", "url": "website"}
SAY = {k: k.replace("_", " ") for k in DETAILS}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*:?()\[\]_]+|\brequired\b|\(required\)", " ", (text or "").lower())).strip()


def detail_key(name: str) -> str | None:
    """'my mobile number' / 'Phone' -> 'phone'; None when it is nothing JARVIS stores (or something it must not)."""
    n = norm(re.sub(r"^\s*(?:my|the)\s+", "", name or ""))
    if not n or SENSITIVE.search(n):
        return None
    if n.replace(" ", "_") in DETAILS:
        return n.replace(" ", "_")
    best, length = None, 0
    for key, words in DETAILS.items():
        for w in words:
            if (n == w or re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", n)) and len(w) > length:
                best, length = key, len(w)
    return best


def iso_date(text: str) -> str | None:
    """'15/08/2007', '15-08-2007', '2007-08-15', '15 Aug 2007' -> '2007-08-15' (day first, as in India)."""
    t = text.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def details(app) -> dict:
    profile = app.db.get_setting("profile", {})
    return {k: v for k, v in profile.items() if k in DETAILS and v}


def with_derived(d: dict) -> dict:
    """First/last name from the full name and back; age from the date of birth."""
    d = dict(d)
    if d.get("full_name") and not d.get("first_name"):
        parts = d["full_name"].split()
        d["first_name"] = parts[0]
        if len(parts) > 1 and not d.get("last_name"):
            d["last_name"] = " ".join(parts[1:])
    if not d.get("full_name") and d.get("first_name"):
        d["full_name"] = (d["first_name"] + " " + d.get("last_name", "")).strip()
    if d.get("date_of_birth") and not d.get("age") and (iso := iso_date(d["date_of_birth"])):
        born, today = datetime.strptime(iso, "%Y-%m-%d"), datetime.now()
        d["age"] = str(today.year - born.year - ((today.month, today.day) < (born.month, born.day)))
    return d


def pick_option(value: str, options: list[str]) -> str | None:
    """The option that means `value` ("Male" for "male", "M"; "Tamil Nadu" for "tamil nadu")."""
    v = value.strip().lower()
    for o in options:
        if o.strip().lower() == v:
            return o
    for o in options:
        ol = o.strip().lower()
        if ol and (ol.startswith(v) or v.startswith(ol)) and min(len(ol), len(v)) >= 1:
            return o
    for o in options:
        if v and v in o.lower():
            return o
    return None


def propose(fields: list[dict], known: dict) -> tuple[list[dict], list[str]]:
    """For each form field, the stored detail that answers it -> (fill proposal, labels JARVIS does not know)."""
    known = with_derived(known)
    proposal, missing = [], []
    for f in fields:
        label = f.get("label") or f.get("name") or ""
        if f.get("sensitive") or SENSITIVE.search(f"{label} {f.get('name', '')} {f.get('autocomplete', '')}"):
            continue
        if f.get("value") and f.get("kind") not in ("radio", "aria-radio", "checkbox", "aria-checkbox"):
            continue  # already filled in: leave it
        key = AUTOCOMPLETE.get((f.get("autocomplete") or "").split()[-1] if f.get("autocomplete") else "", None) or detail_key(label) or detail_key(f.get("name", ""))
        if f.get("kind") == "date" and key not in ("date_of_birth", None):
            key = key if "birth" in norm(label) else None
        value = known.get(key) if key else None
        if not value:
            if f.get("required") and label:
                missing.append(label)
            continue
        if f.get("kind") == "date":
            value = iso_date(value) or value
        if f.get("options"):
            value = pick_option(value, f["options"])
            if value is None:
                missing.append(label)
                continue
        proposal.append({"id": f["id"], "label": label[:80], "value": str(value)[:300], "detail": key})
    return proposal, missing


# -- tools ---------------------------------------------------------------------------------------------------------------
async def tab_read(ctx: ToolContext) -> dict:
    page = await ctx.app.connector.call("read_page")
    return {"title": page.get("title", ""), "url": page.get("url", ""), "text": (page.get("text") or "")[:6000]}


async def tab_form(ctx: ToolContext) -> dict:
    """Read the form on the active tab and propose values from the owner's details (nothing is filled yet)."""
    form = await ctx.app.connector.call("read_form")
    fields = form.get("fields", [])
    proposal, missing = propose(fields, details(ctx.app))
    return {"title": form.get("title", ""), "url": form.get("url", ""), "fields": len(fields),
            "field_labels": [f.get("label", "")[:60] for f in fields][:40], "proposal": proposal, "missing": missing[:20],
            "sensitive_skipped": [f.get("label", "")[:40] for f in fields if f.get("sensitive")][:10]}


def _fill_preview(args: dict) -> str:
    values = args.get("values") or []
    if not values:
        raise ValueError("nothing to fill")
    for v in values:
        if SENSITIVE.search(f"{v.get('label', '')}"):
            raise SecurityError(f"JARVIS never fills '{v.get('label')}' - passwords, OTPs, card and ID numbers stay with you")
    lines = [f"- {v.get('label') or v.get('id')}: {v.get('value')}" for v in values[:25]]
    return f"Fill {len(values)} field(s) in your browser tab (it will not submit):\n" + "\n".join(lines)


async def tab_fill(ctx: ToolContext, values: list) -> dict:
    _fill_preview({"values": values})
    result = await ctx.app.connector.call("fill", {"values": [{"id": str(v["id"]), "value": str(v["value"])} for v in values]})
    return {"filled": result.get("filled", []), "skipped": result.get("skipped", []), "submitted": False}


async def tab_click(ctx: ToolContext, text: str) -> dict:
    return await ctx.app.connector.call("click", {"text": text})


async def tab_scroll(ctx: ToolContext, direction: str = "down") -> dict:
    return await ctx.app.connector.call("scroll", {"direction": direction})


def set_my_details(ctx: ToolContext, details: dict) -> dict:
    """Remember details for filling forms (this computer only)."""
    profile = dict(ctx.app.db.get_setting("profile", {}))
    saved, refused = {}, []
    for name, value in (details or {}).items():
        key = detail_key(str(name))
        if key is None or SENSITIVE.search(f"{name} {value}"):
            refused.append(str(name))
            continue
        v = str(value).strip()
        if key == "date_of_birth":
            v = iso_date(v) or v
        if key == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError(f"'{v}' does not look like an email address")
        saved[key] = v[:200]
    if not saved and refused:
        raise SecurityError("JARVIS does not store passwords, OTPs, card, bank or ID numbers" if any(SENSITIVE.search(r) for r in refused)
                            else f"I don't know where to keep '{', '.join(refused)}'")
    profile.update(saved)
    ctx.app.db.set_setting("profile", profile)
    return {"saved": {SAY[k]: v for k, v in saved.items()}, "refused": refused}


def my_details(ctx: ToolContext) -> dict:
    return {"details": {SAY[k]: v for k, v in details(ctx.app).items()}}


def register(reg: ToolRegistry) -> None:
    S = {"type": "string"}
    reg.register(Tool("tab_read", "Read the page open in the user's own browser (the active tab): title, address and text.", {}, tab_read,
                      untrusted_output=True, timeout=30))
    reg.register(Tool("tab_form", "Read the form in the user's browser tab and propose values from the user's saved details "
                      "(nothing is filled). Then call tab_fill with the proposal.", {}, tab_form, untrusted_output=True, timeout=30))
    reg.register(Tool("tab_fill", "Fill fields of the form in the user's browser tab: values = [{id, label, value}] from tab_form. Never submits.",
                      {"values": {"type": "array", "items": {"type": "object", "properties": {"id": S, "label": S, "value": S}, "required": ["id", "value"]}}},
                      tab_fill, required=["values"], level=Level.CONFIRM, preview=_fill_preview, timeout=40))
    reg.register(Tool("tab_click", "Click a button or link by its visible text in the user's browser tab (e.g. 'Next', 'Submit').", {"text": S},
                      tab_click, required=["text"], level=Level.CONFIRM, grantable=False, timeout=20,
                      preview=lambda a: f"Click “{a['text']}” in your browser tab"))
    reg.register(Tool("tab_scroll", "Scroll the user's browser tab: up, down, top or bottom.",
                      {"direction": {"type": "string", "enum": ["up", "down", "top", "bottom"]}}, tab_scroll, timeout=15))
    reg.register(Tool("set_my_details", "Remember the user's details for filling forms, e.g. {\"email\": \"...\", \"phone\": \"...\"}. "
                      "Never passwords, OTPs, card, bank or ID numbers.", {"details": {"type": "object"}}, set_my_details, required=["details"]))
    reg.register(Tool("my_details", "The details JARVIS has saved for filling forms.", {}, my_details))
