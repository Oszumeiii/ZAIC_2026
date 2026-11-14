from pathlib import Path
from unittest.mock import Mock
import json

import pytest

from app.manifests.service import BusinessManifestService

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "schemas" / "business_manifest.schema.json").read_text(encoding="utf-8")
)


class TestBusinessManifest:
    def test_readme_sections_use_stable_semantic_ids(self) -> None:
        sections = BusinessManifestService._make_readme_sections(
            "# Dữ liệu biz_105\nGiới thiệu\n\n"
            "## File\n- `SoBanHang_1.xlsx`\n\n"
            "## Cột trong `SoBanHang_1.xlsx`\n- **Hàng**: tên hàng\n\n"
            "## Quy ước\n- Ghi chú"
        )

        assert [section["evidence_id"] for section in sections] == [
            "readme.overview",
            "readme.sales_1",
            "readme.conventions",
        ]
        assert "SoBanHang_1.xlsx" in sections[0]["text"]
        assert "**Hàng**" in sections[1]["text"]

    def test_rulebook_sections_keep_content_with_requested_ids(self) -> None:
        sections = BusinessManifestService._make_rulebook_sections(
            "# Rulebook\n\n## 8. Cách ghi trong file\nQuy tắc 8\n\n"
            "## 9. Khác\nBỏ qua\n\n## 10. Từ ngữ\nQuy tắc 10"
        )

        assert sections == [
            {"evidence_id": "rulebook.preamble", "text": "# Rulebook"},
            {"evidence_id": "rulebook.8", "text": "## 8. Cách ghi trong file\nQuy tắc 8"},
            {"evidence_id": "rulebook.9", "text": "## 9. Khác\nBỏ qua"},
            {"evidence_id": "rulebook.10", "text": "## 10. Từ ngữ\nQuy tắc 10"},
        ]

    def test_evidence_bundle_persists_section_text_and_file_inventory(self) -> None:
        business_input = {
            "business_id": "biz_105",
            "file_inventory": ["SoBanHang_1.xlsx"],
            "readme_sections": [
                {"evidence_id": "readme.sales_1", "text": "Column mapping"}
            ],
            "survey_results": [
                {"evidence_id": "profile.sales_layout", "text": "Observed rows"}
            ],
            "rulebook_sections": [
                {"evidence_id": "rulebook.8", "text": "Duplicate rule"}
            ],
        }

        assert BusinessManifestService.build_evidence(business_input) == business_input

    def test_generate_business_manifest_requests_json_and_validates_references(self) -> None:
        manifest = {
            "schema_version": "1.0",
            "business_id": "biz_101",
            "tables": [
                {
                    "table_role": "sales_records",
                    "source_files": ["ban_hang.csv"],
                    "columns": {"Thanh tien": "thành tiền dòng"},
                    "source_refs": ["readme.sales"],
                }
            ],
            "facts": [],
            "unresolved": [],
        }
        llm = Mock()
        llm.json.return_value = manifest
        business_input = {
            "business_id": "biz_101",
            "file_inventory": ["README.md", "ban_hang.csv"],
            "readme_sections": [],
            "survey_results": [],
            "rulebook_sections": [],
            "output_schema": SCHEMA,
            "_evidence_ids": ["readme.sales"],
        }

        result = BusinessManifestService.generate(llm, business_input, SCHEMA)

        assert result == manifest
        args, kwargs = llm.json.call_args
        assert args[0]
        assert args[2] == SCHEMA
        assert kwargs == {"task": "manifest"}

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("source_files", ["missing.xlsx"], "absent from inventory"),
            ("source_refs", ["unknown.evidence"], "unknown evidence IDs"),
        ],
    )
    def test_generate_business_manifest_rejects_untrusted_references(self,
        field: str,
        value: list[str],
        message: str,
    ) -> None:
        manifest = {
            "schema_version": "1.0",
            "business_id": "biz_101",
            "tables": [
                {
                    "table_role": "sales_records",
                    "source_files": ["ban_hang.csv"],
                    "columns": {"Thanh tien": "thành tiền dòng"},
                    "source_refs": ["readme.sales"],
                }
            ],
            "facts": [],
            "unresolved": [],
        }
        manifest["tables"][0][field] = value
        llm = Mock()
        llm.json.return_value = manifest
        business_input = {
            "business_id": "biz_101",
            "file_inventory": ["ban_hang.csv"],
            "readme_sections": [],
            "survey_results": [],
            "rulebook_sections": [],
            "output_schema": SCHEMA,
            "_evidence_ids": ["readme.sales"],
        }

        with pytest.raises(RuntimeError, match=message):
            BusinessManifestService.generate(llm, business_input, SCHEMA)

    def test_generate_business_manifest_retries_schema_invalid_response(self) -> None:
        manifest = {
            "schema_version": "1.0",
            "business_id": "biz_101",
            "tables": [
                {
                    "table_role": "sales_records",
                    "source_files": ["ban_hang.csv"],
                    "columns": {"Thanh tien": "thành tiền dòng"},
                    "source_refs": ["readme.sales"],
                }
            ],
            "facts": [],
            "unresolved": [],
        }
        invalid_manifest = {
            **manifest,
            "tables": [{**manifest["tables"][0], "columns": {}}],
        }
        llm = Mock()
        llm.json.side_effect = [invalid_manifest, manifest]
        business_input = {
            "business_id": "biz_101",
            "file_inventory": ["ban_hang.csv"],
            "readme_sections": [],
            "survey_results": [],
            "rulebook_sections": [],
            "output_schema": SCHEMA,
            "_evidence_ids": ["readme.sales"],
        }

        result = BusinessManifestService.generate(llm, business_input, SCHEMA)

        assert result == manifest
        assert llm.json.call_count == 2
        assert "Schema validation failed at tables/0/columns" in llm.json.call_args.args[1]
