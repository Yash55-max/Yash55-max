#!/usr/bin/env python3
"""
Repository Activity Scanner Bot
- Scans repositories for the given GitHub user/owner
- Compares state with previous scan to detect pushes, new repos, or changes
- Updates data/repo-activity.json and data/activity.log
- Generates an appropriate commit message
- Guarantees updates on every run even if no repo changes are detected
"""

import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

def get_env_var(name, default=""):
    return os.environ.get(name, default).strip()

def fetch_repos(owner, token=None, pat_token=None):
    """
    Fetch repositories. If pat_token is available, can fetch user's authenticated repos (including private).
    Otherwise, fetches owner's public repos.
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GitHub-Repo-Scanner-Bot"
    }
    
    auth_token = pat_token or token
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    repos = []
    page = 1
    
    # If using PAT, can query /user/repos to include private repos
    if pat_token:
        base_url = "https://api.github.com/user/repos?per_page=100&affiliation=owner&sort=pushed"
    else:
        base_url = f"https://api.github.com/users/{owner}/repos?per_page=100&sort=pushed"

    while True:
        url = f"{base_url}&page={page}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data or not isinstance(data, list):
                    break
                repos.extend(data)
                if len(data) < 100:
                    break
                page += 1
        except urllib.error.HTTPError as e:
            print(f"HTTPError while fetching page {page}: {e.code} {e.reason}", file=sys.stderr)
            break
        except Exception as e:
            print(f"Error while fetching repos page {page}: {e}", file=sys.stderr)
            break

    return repos

def main():
    repo_owner = get_env_var("REPO_OWNER") or get_env_var("GITHUB_REPOSITORY_OWNER") or "Yash55-max"
    github_token = get_env_var("GITHUB_TOKEN")
    pat_token = get_env_var("PAT_TOKEN") # Optional for private repos
    
    current_repo_name = (get_env_var("GITHUB_REPOSITORY", "").split("/")[-1] or "Yash55-max").lower()
    
    now_utc = datetime.now(timezone.utc)
    timestamp_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    timestamp_human = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(data_dir, exist_ok=True)
    
    activity_file = os.path.join(data_dir, "repo-activity.json")
    log_file = os.path.join(data_dir, "activity.log")
    commit_msg_file = os.path.join(data_dir, ".commit_msg")
    
    # Load previous activity
    prev_data = {}
    is_initial_run = not os.path.exists(activity_file)
    if not is_initial_run:
        try:
            with open(activity_file, "r", encoding="utf-8") as f:
                prev_data = json.load(f)
        except Exception as e:
            print(f"Notice: Could not load previous activity file: {e}")
            prev_data = {}

    prev_repos_map = {r["name"]: r for r in prev_data.get("repos", []) if "name" in r}

    # Fetch repos
    print(f"Scanning repositories for owner: {repo_owner}...")
    repos = fetch_repos(repo_owner, token=github_token, pat_token=pat_token)
    print(f"Total repositories found: {len(repos)}")

    changed_repos = []
    new_repos = []
    scanned_repos_data = []

    for r in repos:
        name = r.get("name", "")
        pushed_at = r.get("pushed_at", "")
        updated_at = r.get("updated_at", "")
        stars = r.get("stargazers_count", 0)
        forks = r.get("forks_count", 0)
        language = r.get("language")
        description = r.get("description")
        is_fork = r.get("fork", False)

        repo_info = {
            "name": name,
            "html_url": r.get("html_url", ""),
            "description": description,
            "pushed_at": pushed_at,
            "updated_at": updated_at,
            "stars": stars,
            "forks": forks,
            "language": language,
            "is_fork": is_fork,
        }
        scanned_repos_data.append(repo_info)

        # Ignore changes to the bot's own hosting repository to prevent false change loops
        if name.lower() == current_repo_name:
            continue

        if not is_initial_run:
            if name not in prev_repos_map:
                new_repos.append(name)
            else:
                prev_repo = prev_repos_map[name]
                # Check if pushed_at changed (new commit/push detected)
                if pushed_at and prev_repo.get("pushed_at") and pushed_at > prev_repo.get("pushed_at"):
                    changed_repos.append(name)

    # Determine status & commit message
    if is_initial_run:
        status = "initial_scan"
        commit_msg = f"chore(bot): initialize repository activity scan [{now_utc.strftime('%Y-%m-%d %H:%M UTC')}]"
        log_entry = f"[{timestamp_human}] Initial scan complete. Baseline established for {len(repos)} repositories."
    elif new_repos or changed_repos:
        status = "changes_detected"
        parts = []
        if changed_repos:
            parts.append(f"updates in {', '.join(changed_repos[:3])}" + (f" (+{len(changed_repos)-3} more)" if len(changed_repos) > 3 else ""))
        if new_repos:
            parts.append(f"new repo(s): {', '.join(new_repos[:2])}")
        details = "; ".join(parts)
        commit_msg = f"chore(bot): sync repo scan ({details})"
        log_entry = f"[{timestamp_human}] Scan detected changes: {details}. Total repos: {len(repos)}."
    else:
        status = "no_changes"
        commit_msg = f"chore(bot): 6-hour heartbeat scan - no changes [{now_utc.strftime('%Y-%m-%d %H:%M UTC')}]"
        log_entry = f"[{timestamp_human}] Heartbeat scan complete. 0 repo changes detected. Total repos: {len(repos)}."

    # Prepare output data
    output_data = {
        "last_scan_utc": timestamp_iso,
        "status": status,
        "total_repos": len(repos),
        "changed_repos": changed_repos,
        "new_repos": new_repos,
        "heartbeat": timestamp_iso,
        "repos": scanned_repos_data
    }

    # Write data/repo-activity.json
    with open(activity_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    # Append to data/activity.log (keep last 100 lines)
    existing_logs = []
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                existing_logs = f.readlines()
        except Exception:
            existing_logs = []
    
    existing_logs.append(log_entry + "\n")
    if len(existing_logs) > 100:
        existing_logs = existing_logs[-100:]
        
    with open(log_file, "w", encoding="utf-8") as f:
        f.writelines(existing_logs)

    # Write commit message for fallback
    with open(commit_msg_file, "w", encoding="utf-8") as f:
        f.write(commit_msg)

    # Export to GITHUB_OUTPUT if available
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        try:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write(f"commit_msg={commit_msg}\n")
                f.write(f"status={status}\n")
        except Exception as e:
            print(f"Notice: Could not write to GITHUB_OUTPUT: {e}")

    print(f"Scan finished. Status: {status}.")
    print(f"Commit message: {commit_msg}")

if __name__ == "__main__":
    main()
