import os
from urllib.parse import urlparse


EXCLUDED_DOMAIN_SUFFIXES = {
    item.strip().lower().lstrip(".")
    for item in os.getenv("EXCLUDED_DOMAIN_SUFFIXES", "gov.az").split(",")
    if item.strip()
}


def clean_domain(value):
    value = str(value or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    domain = (parsed.netloc or parsed.path.split("/")[0]).split("@")[-1].split(":")[0].strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def is_excluded_domain(value):
    domain = clean_domain(value)
    return any(domain == suffix or domain.endswith("." + suffix) for suffix in EXCLUDED_DOMAIN_SUFFIXES)
