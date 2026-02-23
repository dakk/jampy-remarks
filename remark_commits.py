import http.client
import json
import subprocess
import time
from datetime import datetime, timezone

# Subscan API config
API_HOST = "polkadot.api.subscan.io"
HEADERS = {
    # 'x-api-key': "API_KEY",  # from https://pro.subscan.io/ for higher rate limit
    'Content-Type': 'application/json'
}

ADDRESS = "12iqwZGB2sguEhjFi2ZRuWWixU8mHJnSiP1pwDefqGsBy4rV"
ROW_PER_PAGE = 100
MAX_RETRIES = 3
RETRY_DELAY = 5

# Only remarks from August 2024 onwards
CUTOFF_TIMESTAMP = int(datetime(2024, 8, 1, tzinfo=timezone.utc).timestamp())

# Local repo path
JAMPY_REPO = "/home/dakk/Repositories/MyRepos/jampy"

JSON_FILENAME = "remark_commits.json"


def api_request(endpoint, payload):
    """Make a POST request to Subscan API with retries."""
    for attempt in range(MAX_RETRIES):
        conn = None
        try:
            conn = http.client.HTTPSConnection(API_HOST)
            conn.request("POST", endpoint, json.dumps(payload), HEADERS)
            response = conn.getresponse()

            if response.status == 429:
                print(f"Rate limited, waiting {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)
                continue

            if response.status != 200:
                raise Exception(f"HTTP {response.status}: {response.reason}")

            data = json.loads(response.read().decode())
            if data.get('code') != 0:
                raise Exception(f"API error: {data.get('message', 'unknown')}")

            return data.get('data', {})

        except Exception as e:
            print(f"Attempt {attempt + 1}/{MAX_RETRIES} failed: {e}")
            if attempt == MAX_RETRIES - 1:
                return None
            time.sleep(RETRY_DELAY)
        finally:
            if conn:
                conn.close()
    return None


def fetch_extrinsics_list(after_id=0, page=0):
    """Fetch list of extrinsics (without params)."""
    payload = {
        "row": ROW_PER_PAGE,
        "signed": "signed",
        "address": ADDRESS,
        "module": "system",
        "call": "remark",
    }
    if after_id > 0:
        payload["after_id"] = after_id
    payload["page"] = page

    data = api_request("/api/v2/scan/extrinsics", payload)
    if data is None:
        return None
    return data.get('extrinsics', [])


def fetch_extrinsic_detail(extrinsic_index):
    """Fetch single extrinsic detail including params."""
    data = api_request("/api/scan/extrinsic", {"extrinsic_index": extrinsic_index})
    return data


def extract_remark_value(params):
    """Extract the remark value from extrinsic params."""
    if isinstance(params, list):
        for p in params:
            if isinstance(p, dict) and p.get("name") == "remark":
                return p.get("value", "")
    return None


def git_commit_info(commit_hash):
    """Get commit message and date from local repo."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s%n%aI", commit_hash],
            cwd=JAMPY_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None, None
        lines = result.stdout.strip().split("\n", 1)
        if len(lines) == 2:
            return lines[0], lines[1]
        return None, None
    except Exception:
        return None, None


def git_commits_between(older_hash, newer_hash):
    """Get commits between two hashes, excluding both endpoints."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H%n%aI%n%s", f"{older_hash}..{newer_hash}"],
            cwd=JAMPY_REPO,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []
        lines = result.stdout.strip().split("\n")
        commits = []
        # Each commit is 3 lines: hash, date, message
        for i in range(0, len(lines) - 2, 3):
            h = lines[i].strip()
            d = lines[i + 1].strip()
            m = lines[i + 2].strip()
            if not h:
                continue
            # Skip the newer_hash itself (it's the first entry from git log)
            if h == newer_hash:
                continue
            commits.append({
                "commit_hash": h,
                "commit_message": m,
                "commit_date": d,
                "github_commit_url": f"https://github.com/dakk/jampy/commit/{h}",
            })
        return commits
    except Exception:
        return []


def git_commits_after(newer_hash):
    """Get commits from HEAD down to newer_hash, excluding newer_hash itself."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H%n%aI%n%s", f"{newer_hash}..HEAD"],
            cwd=JAMPY_REPO,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []
        lines = result.stdout.strip().split("\n")
        commits = []
        for i in range(0, len(lines) - 2, 3):
            h = lines[i].strip()
            d = lines[i + 1].strip()
            m = lines[i + 2].strip()
            if not h:
                continue
            commits.append({
                "commit_hash": h,
                "commit_message": m,
                "commit_date": d,
                "github_commit_url": f"https://github.com/dakk/jampy/commit/{h}",
            })
        return commits
    except Exception:
        return []


def main():
    print("Fetching system.remark extrinsics...")

    # Step 1: get all extrinsic indices
    all_extrinsics = []
    after_id = 0
    page = 0
    stop = False

    while not stop:
        records = fetch_extrinsics_list(after_id, page)
        if not records:
            break

        for ext in records:
            block_timestamp = ext.get("block_timestamp", 0)
            if block_timestamp < CUTOFF_TIMESTAMP:
                print("Reached extrinsics before Aug 2024, stopping list fetch.")
                stop = True
                break
            all_extrinsics.append(ext)

        if len(records) < ROW_PER_PAGE:
            break

        if "id" in records[-1]:
            after_id = records[-1]["id"]
        else:
            page += 1

        time.sleep(0.5)

    print(f"Found {len(all_extrinsics)} remark extrinsics, fetching details...")

    # Step 2: fetch detail for each extrinsic to get params
    rows = []
    for i, ext in enumerate(all_extrinsics):
        extrinsic_index = ext.get("extrinsic_index", "")
        block_timestamp = ext.get("block_timestamp", 0)
        remark_date = (
            datetime.fromtimestamp(block_timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if block_timestamp else ""
        )
        remark_url = f"https://polkadot.subscan.io/extrinsic/{extrinsic_index}"

        # Fetch detail
        detail = fetch_extrinsic_detail(extrinsic_index)
        if not detail:
            print(f"  [{i+1}/{len(all_extrinsics)}] Failed to fetch detail for {extrinsic_index}")
            continue

        commit_hash = extract_remark_value(detail.get("params", []))
        if not commit_hash:
            print(f"  [{i+1}/{len(all_extrinsics)}] No remark value in {extrinsic_index}")
            continue

        commit_hash = commit_hash.strip()

        commit_message, commit_date = git_commit_info(commit_hash)
        if commit_message is None:
            print(f"  [{i+1}/{len(all_extrinsics)}] Skipping non-commit remark: {commit_hash}")
            continue

        github_url = f"https://github.com/dakk/jampy/commit/{commit_hash}"

        rows.append({
            "remark_url": remark_url,
            "commit_hash": commit_hash,
            "commit_message": commit_message,
            "remark_date": remark_date,
            "commit_date": commit_date,
            "github_commit_url": github_url,
        })

        print(f"  [{i+1}/{len(all_extrinsics)}] {commit_hash[:12]}... -> {commit_message[:50]}")
        time.sleep(0.5)

    print(f"\nCollected {len(rows)} remark rows, finding gap commits...")

    # Step 3: interleave gap commits between consecutive remarks
    # rows is sorted newest-first
    output = []

    # Gap from HEAD to the newest remark
    if rows:
        gap = git_commits_after(rows[0]["commit_hash"])
        if gap:
            output.append({"type": "gap", "commits": gap})
            print(f"  {len(gap)} commits after newest remark")

    for i, row in enumerate(rows):
        output.append({**row, "type": "remark"})

        if i + 1 < len(rows):
            gap = git_commits_between(rows[i + 1]["commit_hash"], row["commit_hash"])
            if gap:
                output.append({"type": "gap", "commits": gap})
                print(f"  {len(gap)} commits between {row['commit_hash'][:8]} and {rows[i+1]['commit_hash'][:8]}")

    # Write JSON
    with open(JSON_FILENAME, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    remark_count = sum(1 for e in output if e["type"] == "remark")
    gap_count = sum(len(e["commits"]) for e in output if e["type"] == "gap")
    print(f"\nWrote {len(output)} entries ({remark_count} remarks, {gap_count} gap commits) to {JSON_FILENAME}")


if __name__ == "__main__":
    main()
