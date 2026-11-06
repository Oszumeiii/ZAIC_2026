"""Protect class ownership and the supported command-line entry points."""
import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class TestArchitecture:
    def test_python_definitions_belong_to_classes(self):
        for directory in (ROOT / "app", ROOT / "tests"):
            for path in directory.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                parents = {
                    child: node
                    for node in ast.walk(tree)
                    for child in ast.iter_child_nodes(node)
                }
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        assert isinstance(parents[node], ast.ClassDef), (
                            f"{path.relative_to(ROOT)}:{node.lineno}: "
                            f"{node.name} must be a class method"
                        )

    def test_all_application_modules_import(self):
        for path in (ROOT / "app").rglob("*.py"):
            module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            importlib.import_module(module)

    @pytest.mark.parametrize(
        "module",
        ["build_manifests", "audit_manifests", "upgrade_manifests", "pipeline",
         "cli.build_manifests", "cli.audit_manifests", "cli.upgrade_manifests", "cli.ingest"],
    )
    def test_existing_cli_entry_points(self, module):
        result = subprocess.run(
            [sys.executable, "-m", f"app.ingestion.{module}", "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "--help" in result.stdout

    @pytest.mark.parametrize("cli_prefix", ["", "cli."])
    def test_offline_cli_migration_audit_and_ingestion(self, tmp_path, cli_prefix):
        dataset = tmp_path / "data"
        business = dataset / "biz_cli"
        business.mkdir(parents=True)
        (business / "README.md").write_text(
            "# Dữ liệu\n## Quy ước\n**Amount** là thành tiền dòng.\n",
            encoding="utf-8",
        )
        (business / "sales.csv").write_text("ID,Amount\n001,10\n002,20\n")
        (tmp_path / "rulebook.md").write_text("# Rules\n## 1. Sales\nRules\n")
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        (legacy / "biz_cli.json").write_text(json.dumps({
            "schema_version": "1.0",
            "business_id": "biz_cli",
            "tables": [{
                "table_role": "sales_records",
                "source_files": ["sales.csv"],
                "columns": {"Amount": "thành tiền dòng"},
                "source_refs": ["readme.conventions"],
            }],
            "facts": [],
            "unresolved": [],
        }))
        generated = tmp_path / "generated"
        output = tmp_path / "catalog"
        commands = [
            ["upgrade_manifests", "--dataset-dir", str(dataset),
             "--manifest-dir", str(legacy), "--output-dir", str(generated)],
            ["audit_manifests", "--dataset-dir", str(dataset),
             "--manifest-dir", str(generated), "--output-dir", str(tmp_path / "audit")],
            ["pipeline", "--business-dir", str(business),
             "--manifest", str(generated / "biz_cli.json"), "--output-dir", str(output)],
        ]
        for module, *arguments in commands:
            if cli_prefix:
                module = cli_prefix + ("ingest" if module == "pipeline" else module)
            result = subprocess.run(
                [sys.executable, "-m", f"app.ingestion.{module}", *arguments],
                cwd=ROOT, text=True, capture_output=True, timeout=30,
            )
            assert result.returncode == 0, result.stderr
        catalog = json.loads((output / "catalog.json").read_text())
        assert catalog["tables"][0]["row_count"] == 2
        assert catalog["quality"]["coverage"]["documented_columns"] == 1
