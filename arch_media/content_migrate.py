"""Pure helpers for the site-settings migration. No model imports."""

from urllib.parse import urlparse

DEFAULT_ENQUIRY_RECIPIENT = "anvi@createch.co.ke"
SEEDED_CATEGORIES = (
    ("Architecture", "architecture", "discipline", 0),
    ("Interior Design", "interior-design", "discipline", 1),
    ("Commercial", "commercial", "segment", 0),
    ("Residential", "residential", "segment", 1),
)
CATEGORY_SLOT_KEYS = ("discipline", "segment")


def add_project_category_slots(data):
    """Add null discipline and segment keys to chrome projects.

    Every existing key, including typology and underCreatech, is copied through.
    Returns the original object when nothing changed.
    """
    if not isinstance(data, dict):
        return data, False
    portfolio = data.get("portfolio")
    if not isinstance(portfolio, dict):
        return data, False
    projects = portfolio.get("projects")
    if not isinstance(projects, list):
        return data, False

    changed = False
    updated_projects = []
    for project in projects:
        if not isinstance(project, dict) or all(key in project for key in CATEGORY_SLOT_KEYS):
            updated_projects.append(project)
            continue
        project = dict(project)
        for key in CATEGORY_SLOT_KEYS:
            if key not in project:
                project[key] = None
                changed = True
        updated_projects.append(project)
    if not changed:
        return data, False

    portfolio = dict(portfolio)
    portfolio["projects"] = updated_projects
    data = dict(data)
    data["portfolio"] = portfolio
    return data, True


def instagram_url_from_chrome(data):
    """The http(s) URL already stored on chrome contact, or '' when it is blank or unusable."""
    if not isinstance(data, dict):
        return ""
    contact = data.get("contact")
    if not isinstance(contact, dict):
        return ""
    raw = contact.get("instagram")
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text or len(text) > 300 or any(ch.isspace() for ch in text):
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return text
