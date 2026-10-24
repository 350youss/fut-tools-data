"""Small GitHub Actions helper, run from the PC (uses the credential stored by Git).

    py scripts/gh.py run [auto|force|probe]   # starts the workflow
    py scripts/gh.py status                   # last runs
    py scripts/gh.py log <run id>             # end of the run's log
"""

import io
import subprocess
import sys
import zipfile

import requests

REPO = "350youss/fut-tools-data"
API = f"https://api.github.com/repos/{REPO}/actions"


def headers():
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, timeout=60).stdout
    cred = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {"Authorization": f"token {cred['password']}", "Accept": "application/vnd.github+json"}


def main():
    h, cmd = headers(), sys.argv[1]
    if cmd == "run":
        r = requests.post(f"{API}/workflows/evolutions.yml/dispatches", headers=h,
                          json={"ref": "main", "inputs": {"mode": sys.argv[2] if len(sys.argv) > 2 else "auto"}}, timeout=60)
        print("lancé" if r.status_code == 204 else f"erreur {r.status_code} {r.text[:200]}")
    elif cmd == "status":
        for x in requests.get(f"{API}/runs?per_page=5", headers=h, timeout=60).json().get("workflow_runs", []):
            print(x["id"], x["event"], x["status"], x["conclusion"], x["created_at"], x["html_url"])
    elif cmd == "log":
        r = requests.get(f"{API}/runs/{sys.argv[2]}/logs", headers=h, timeout=120)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        for name in sorted(z.namelist()):
            if "/" in name:
                continue
            text = z.read(name).decode("utf-8", "replace").splitlines()
            print(f"===== {name}")
            print("\n".join(text[-40:]))


if __name__ == "__main__":
    main()
