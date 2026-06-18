import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from domain_policy import is_excluded_domain


SOURCE_FILES = [
    "courier_config_clean.json",
    "discovered_sites.json",
    "review_sites.json",
]
REJECTED_FILE = "rejected_sites.json"

SUBDOMAIN_ALLOWLIST = {
    item.strip().lower().lstrip(".")
    for item in os.getenv("CLEANUP_SUBDOMAIN_ALLOWLIST", "").split(",")
    if item.strip()
}
PROTECTED_PARENT_DOMAINS = {
    "az",
    "com.az",
    "edu.az",
    "gov.az",
    "net.az",
    "org.az",
    "info.az",
    "biz.az",
    "co.az",
    "ac.az",
}


def clean_text(value):
    return " ".join(str(value or "").split()).strip()


def clean_domain(value):
    value = clean_text(value).lower()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    domain = (parsed.netloc or parsed.path.split("/")[0]).split("@")[-1].split(":")[0].strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return default
    except Exception as exc:
        print(f"JSON oxunmadı: {path} | {exc}", flush=True)
        return default


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def site_url(site):
    return clean_text(site.get("url") or site.get("base_url") or site.get("latest_url"))


def site_domain(site):
    return clean_domain(site_url(site))


def is_subdomain_of(domain, parent_domain):
    domain = clean_domain(domain)
    parent_domain = clean_domain(parent_domain)
    return bool(domain and parent_domain and domain != parent_domain and domain.endswith("." + parent_domain))


def find_parent_domain(domain, domains):
    domain = clean_domain(domain)
    if not domain or domain in SUBDOMAIN_ALLOWLIST:
        return None
    for parent in sorted(domains, key=len, reverse=True):
        if parent in PROTECTED_PARENT_DOMAINS:
            continue
        if is_subdomain_of(domain, parent):
            return parent
    return None


def backup_file(path):
    source = Path(path)
    if not source.exists():
        return
    backup = source.with_name(f"{source.name}.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.backup")
    shutil.copyfile(source, backup)
    print(f"Backup yaradıldı: {backup}", flush=True)


def collect_domains():
    domains = set()
    for filename in SOURCE_FILES:
        data = read_json(filename, {"sites": []})
        if not isinstance(data, dict):
            continue
        for site in data.get("sites", []):
            domain = site_domain(site)
            if domain:
                domains.add(domain)
    return domains


def rejected_record(site, parent_domain, source_file):
    domain = site_domain(site)
    if parent_domain == "excluded_domain":
        reason = f"excluded_domain_cleanup: domain={domain}; source_file={source_file}"
    else:
        reason = f"subdomain_cleanup: parent_domain_exists={parent_domain}; source_file={source_file}"
    record = dict(site)
    record.update(
        {
            "enabled": False,
            "status": "rejected",
            "reason": reason,
            "source_type": "subdomain_rejected",
            "monitor_method": "none",
            "cleanup_checked_at": datetime.utcnow().isoformat(),
        }
    )
    analysis = record.get("analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    reasons = analysis.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    if reason not in reasons:
        reasons.append(reason)
    analysis["reasons"] = reasons
    record["analysis"] = analysis
    record["domain"] = domain
    return record


def append_rejected(records):
    if not records:
        return 0

    data = read_json(REJECTED_FILE, {"sites": []})
    if not isinstance(data, dict):
        data = {"sites": []}
    if not isinstance(data.get("sites"), list):
        data["sites"] = []

    existing = {site_domain(site) for site in data["sites"]}
    added = 0
    for record in records:
        domain = site_domain(record)
        if not domain or domain in existing:
            continue
        data["sites"].append(record)
        existing.add(domain)
        added += 1

    if added:
        backup_file(REJECTED_FILE)
        write_json(REJECTED_FILE, data)
    return added


def cleanup_file(filename, all_domains):
    data = read_json(filename, {"sites": []})
    if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
        print(f"Format keçildi: {filename}", flush=True)
        return [], 0

    kept = []
    rejected = []

    for site in data["sites"]:
        domain = site_domain(site)
        if is_excluded_domain(domain):
            rejected.append(rejected_record(site, "excluded_domain", filename))
            continue
        parent = find_parent_domain(domain, all_domains)
        if parent:
            rejected.append(rejected_record(site, parent, filename))
            continue
        kept.append(site)

    removed = len(data["sites"]) - len(kept)
    if removed:
        backup_file(filename)
        data["sites"] = kept
        write_json(filename, data)

    print(f"{filename}: removed={removed} kept={len(kept)}", flush=True)
    return rejected, removed


def main():
    all_domains = collect_domains()
    print(f"Toplam domain: {len(all_domains)}", flush=True)

    all_rejected = []
    total_removed = 0
    for filename in SOURCE_FILES:
        rejected, removed = cleanup_file(filename, all_domains)
        all_rejected.extend(rejected)
        total_removed += removed

    rejected_added = append_rejected(all_rejected)

    print("=" * 50, flush=True)
    print(f"Subdomain removed: {total_removed}", flush=True)
    print(f"Rejected əlavə edildi: {rejected_added}", flush=True)
    print("=" * 50, flush=True)


if __name__ == "__main__":
    main()
