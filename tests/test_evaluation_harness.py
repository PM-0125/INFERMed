from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


def test_evaluate_testcases_mock_mode_checks_all_pairs(tmp_path: Path):
    case_file = tmp_path / "cases.csv"
    with case_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Case_ID",
                "Drug_1",
                "Drug_2",
                "Drug_3",
                "Drug_4",
                "Drug_5",
                "Expected_Risk_Clusters",
                "Expected_Severity_Band",
                "Expected_System_Behavior",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Case_ID": "DDI5-test",
                "Drug_1": "warfarin",
                "Drug_2": "fluconazole",
                "Drug_3": "aspirin",
                "Drug_4": "ibuprofen",
                "Drug_5": "amiodarone",
                "Expected_Risk_Clusters": "bleeding; CYP",
                "Expected_Severity_Band": "moderate to major",
                "Expected_System_Behavior": "Detect all pair combinations.",
            }
        )

    completed = subprocess.run(
        [sys.executable, "scripts/evaluate_testcases.py", "--cases", str(case_file), "--limit", "1"],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["ok"] is True
    assert payload["mode"] == "mock_context"
    assert payload["results"][0]["expected_pair_count"] == 10
    assert payload["results"][0]["executed_pair_count"] == 10
    assert payload["results"][0]["checks"]["medication_set_scope"] is True
