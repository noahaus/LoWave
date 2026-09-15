"""Emit identical synthetic cases through baseline and contributed generators."""
import json
import subprocess
import sys

repo = sys.argv[1]
sys.path.insert(0, repo)
from qa_pipeline import generate

baseline = {}
exec(compile(subprocess.check_output(["git", "show", "00c8918a8f544b3993ebb28005b328ec0034ece0:qa_pipeline/generate.py"], cwd=repo, text=True), "baseline_generate.py", "exec"), baseline)
cases = [
    ("field value", {"field_value": "Antigua"}, 'get_by_label("City", exact=True)', '<label>City<input value="Antigua"></label>', '<label>City<input value="Wrong"></label>'),
    ("empty field", {"field_value": ""}, 'get_by_label("City", exact=True)', '<label>City<input value=""></label>', '<label>City<input value="Wrong"></label>'),
    ("checkbox state", {"checked": True}, 'get_by_label("Consent", exact=True)', '<label>Consent<input type="checkbox" checked></label>', '<label>Consent<input type="checkbox"></label>'),
    ("authored text", {"visible_text": "Email Campaigns", "assert_values": ["Sign in"]}, 'get_by_role("button", name="Sign in", exact=True)', '<button aria-label="Sign in">Email Campaigns</button>', '<button>Sign in</button>'),
]
results = []
for name, outcome, locator, good, bad in cases:
    step = {"step": 1, "action": "assert", "description": "Verify the requested outcome.", "target": {"playwright_locator": locator}, "expected_outcome": outcome}
    versions = {}
    for version, compiler in [("baseline", baseline["emit_assert"]), ("contribution", generate.emit_assert)]:
        try:
            versions[version] = {"code": compiler(step, {"grounded": True})}
        except Exception as error:
            versions[version] = {"error": str(error)}
    results.append({"name": name, "good": good, "bad": bad, "versions": versions})
print(json.dumps(results))
