import json
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from app.llm.gemini import GeminiLLM
from app.llm.prompts import BUSINESS_MANIFEST_SYSTEM_PROMPT

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "business_manifest.schema.json"
RULEBOOK_EVIDENCE = {
    "rulebook.8": "## 8. Cách ghi trong file",
    "rulebook.10": "## 10. Từ ngữ",
}


def _read_markdown_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = []
    current_heading = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("#"):
            sections.append((current_heading, current_lines))
            current_heading = line
            current_lines = [line]
        else:
            current_lines.append(line)
    sections.append((current_heading, current_lines))

    return [
        (heading, "\n".join(lines).strip())
        for heading, lines in sections
        if "\n".join(lines).strip()
    ]


def _slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold().replace("đ", "d"))
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def _readme_evidence_id(heading: str, seen_ids: Counter[str]) -> str:
    if heading == "" or heading.startswith("# Dữ liệu"):
        base_id = "readme.overview"
    elif re.match(r"^##\s+File\s*$", heading, flags=re.IGNORECASE):
        base_id = "readme.overview"
    elif re.match(r"^##\s+Quy\s+ước\s*$", heading, flags=re.IGNORECASE):
        base_id = "readme.conventions"
    else:
        filename_match = re.search(r"`([^`]+)`", heading)
        if filename_match:
            stem = Path(filename_match.group(1)).stem
            normalized = _slug(stem)
            if "sobanhang" in normalized or "ban_hang" in normalized:
                match = re.search(r"(?:sobanhang|ban_hang)_?(.+)?", normalized)
                suffix = (match.group(1) or "").strip("_") if match else ""
                base_id = f"readme.sales_{suffix}" if suffix else "readme.sales"
            elif any(term in normalized for term in ("danhmuc", "quydoi", "donvi")):
                base_id = "readme.conversions"
            elif any(term in normalized for term in ("tonkho", "baocaoton")):
                base_id = "readme.stock"
            elif any(term in normalized for term in ("nhaphang", "phieunhap")):
                base_id = "readme.purchases"
            elif any(term in normalized for term in ("banggia", "lichsu_gia")):
                base_id = "readme.prices"
            else:
                base_id = f"readme.{normalized}"
            sheet_match = re.search(r"\bsheet\s+`([^`]+)`", heading, flags=re.IGNORECASE)
            if sheet_match:
                base_id = f"{base_id}.sheet_{_slug(sheet_match.group(1))}"
        else:
            base_id = f"readme.{_slug(heading.lstrip('# ')) or 'section'}"

    seen_ids[base_id] += 1
    if seen_ids[base_id] == 1:
        return base_id
    return f"{base_id}_{seen_ids[base_id]}"


def _make_readme_sections(readme_text: str) -> list[dict[str, str]]:
    sections = _read_markdown_sections(readme_text)
    evidence: list[dict[str, str]] = []
    seen_ids: Counter[str] = Counter()
    overview_fragments: list[str] = []

    for heading, text in sections:
        if heading == "" or heading.startswith("# Dữ liệu") or re.match(
            r"^##\s+File\s*$", heading, flags=re.IGNORECASE
        ):
            overview_fragments.append(text)
            continue
        evidence.append(
            {
                "evidence_id": _readme_evidence_id(heading, seen_ids),
                "text": text,
            }
        )

    if overview_fragments:
        evidence.insert(
            0,
            {
                "evidence_id": "readme.overview",
                "text": "\n\n".join(overview_fragments),
            },
        )
    return evidence


def _make_rulebook_sections(rulebook_text: str) -> list[dict[str, str]]:
    sections = _read_markdown_sections(rulebook_text)
    by_heading = {heading: text for heading, text in sections}
    evidence = []
    for evidence_id, heading in RULEBOOK_EVIDENCE.items():
        if heading not in by_heading:
            raise ValueError(f"Rulebook section not found: {heading}")
        evidence.append({"evidence_id": evidence_id, "text": by_heading[heading]})
    return evidence


def _cell_values(row: tuple[Any, ...], *, redact_customer: bool = False) -> list[dict[str, str]]:
    cells: list[dict[str, str]] = []
    for index, value in enumerate(row):
        if value is None:
            continue
        if redact_customer and index == 9:
            continue
        if isinstance(value, (datetime, date)):
            rendered = value.isoformat()
        else:
            rendered = str(value)
        cells.append({"cell": get_column_letter(index + 1), "value": rendered})
    return cells


def _profile_biz_105(business_dir: Path) -> list[dict[str, str]]:
    sales_files = ("SoBanHang_1.xlsx", "SoBanHang_2026.xlsx")
    layout: list[str] = []
    sample_values: list[str] = []
    return_rows: list[str] = []
    duplicate_rows: list[str] = []
    auxiliary_layouts: list[str] = []
    duplicate_values: list[tuple[Any, ...]] = []

    for filename in sales_files:
        path = business_dir / filename
        workbook = load_workbook(path, data_only=True, read_only=False)
        first_sample_added = False
        for sheet in workbook.worksheets:
            first_nonempty: tuple[int, tuple[Any, ...]] | None = None
            last_nonempty: tuple[int, tuple[Any, ...]] | None = None
            for row_number, row_cells in enumerate(sheet.iter_rows(), start=1):
                values = tuple(cell.value for cell in row_cells)
                if not any(value is not None for value in values):
                    continue
                if first_nonempty is None:
                    first_nonempty = (row_number, values)
                last_nonempty = (row_number, values)

                if not first_sample_added and row_number > 1:
                    sample_values.append(
                        f"{filename} / {sheet.title} row {row_number}: "
                        f"{json.dumps(_cell_values(values, redact_customer=True), ensure_ascii=False)}"
                    )
                    first_sample_added = True

                if "ORD059865" in values:
                    return_rows.append(
                        f"{filename} / {sheet.title} row {row_number}: "
                        f"{json.dumps(_cell_values(values, redact_customer=True), ensure_ascii=False)}"
                    )
                if "ORD081719" in values:
                    duplicate_rows.append(
                        f"{filename} / {sheet.title} row {row_number}: "
                        f"{json.dumps(_cell_values(values, redact_customer=True), ensure_ascii=False)}"
                    )
                    duplicate_values.append(values)

            if first_nonempty is not None and last_nonempty is not None:
                layout.append(
                    f"{filename} / {sheet.title}: first non-empty row "
                    f"{first_nonempty[0]}="
                    f"{json.dumps(_cell_values(first_nonempty[1], redact_customer=True), ensure_ascii=False)}; "
                    f"last non-empty row {last_nonempty[0]}="
                    f"{json.dumps(_cell_values(last_nonempty[1], redact_customer=True), ensure_ascii=False)}"
                )
        workbook.close()

    auxiliary_files = (
        "DanhMuc.xlsx",
        "TonKho.xlsx",
        "SoNhapHang.xlsx",
        "BangGia.xlsx",
    )
    for filename in auxiliary_files:
        workbook = load_workbook(business_dir / filename, data_only=True, read_only=False)
        for sheet in workbook.worksheets:
            first_nonempty = next(
                (
                    (row_number, tuple(cell.value for cell in row_cells))
                    for row_number, row_cells in enumerate(sheet.iter_rows(), start=1)
                    if any(cell.value is not None for cell in row_cells)
                ),
                None,
            )
            if first_nonempty is not None:
                auxiliary_layouts.append(
                    f"{filename} / {sheet.title}, first non-empty row "
                    f"{first_nonempty[0]}: "
                    f"{json.dumps(_cell_values(first_nonempty[1]), ensure_ascii=False)}"
                )
        workbook.close()

    if len(return_rows) != 2:
        raise ValueError(
            "Expected exactly two surveyed rows for ORD059865, "
            f"found {len(return_rows)}."
        )
    if len(duplicate_rows) != 2 or duplicate_values[0] != duplicate_values[1]:
        raise ValueError(
            "Expected exactly two identical surveyed rows for ORD081719."
        )

    return [
        {
            "evidence_id": "profile.sales_layout",
            "text": "\n".join(layout),
        },
        {
            "evidence_id": "profile.sales_values",
            "text": "\n".join(sample_values),
        },
        {
            "evidence_id": "profile.auxiliary_layouts",
            "text": "\n".join(auxiliary_layouts),
        },
        {
            "evidence_id": "profile.return_pair",
            "text": "Hai dòng có cùng mã đơn ORD059865 được khảo sát:\n"
            + "\n".join(return_rows),
        },
        {
            "evidence_id": "profile.duplicate_pair",
            "text": "Hai dòng của ORD081719 được xác nhận trùng toàn bộ giá trị ô; "
            "cột tên khách đã được lược khỏi nội dung chia sẻ:\n"
            + "\n".join(duplicate_rows),
        },
    ]


def build_business_input(
    business_dir: Path,
    rulebook_text: str,
    output_schema: dict[str, Any],
) -> dict[str, Any]:
    readme_paths = sorted(
        path
        for path in business_dir.iterdir()
        if path.is_file() and path.name.casefold().startswith("readme")
    )
    if len(readme_paths) != 1:
        raise ValueError(
            f"Expected exactly one README in {business_dir}, found {len(readme_paths)}."
        )

    file_inventory = sorted(
        path.relative_to(business_dir).as_posix()
        for path in business_dir.rglob("*")
        if path.is_file()
        and path.name != ".DS_Store"
        and not path.name.casefold().startswith("readme")
    )
    readme_sections = _make_readme_sections(
        readme_paths[0].read_text(encoding="utf-8")
    )
    survey_results = (
        _profile_biz_105(business_dir)
        if business_dir.name == "biz_105"
        else []
    )

    evidence_ids = {
        section["evidence_id"]
        for section in readme_sections + survey_results
    }
    rulebook_sections = _make_rulebook_sections(rulebook_text)
    evidence_ids.update(section["evidence_id"] for section in rulebook_sections)

    return {
        "business_id": business_dir.name,
        "file_inventory": file_inventory,
        "readme_sections": readme_sections,
        "survey_results": survey_results,
        "rulebook_sections": rulebook_sections,
        "output_schema": output_schema,
        "_evidence_ids": sorted(evidence_ids),
    }


def build_evidence_bundle(business_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "business_id": business_input["business_id"],
        "file_inventory": business_input["file_inventory"],
        "readme_sections": business_input["readme_sections"],
        "survey_results": business_input["survey_results"],
        "rulebook_sections": business_input["rulebook_sections"],
    }


def generate_business_manifest(
    llm: GeminiLLM,
    business_input: dict[str, Any],
    output_schema: dict[str, Any],
) -> dict[str, Any]:
    evidence_ids = set(business_input["_evidence_ids"])
    prompt_input = {
        key: value for key, value in business_input.items() if not key.startswith("_")
    }
    prompt = json.dumps(prompt_input, ensure_ascii=False, indent=2)
    validator = Draft202012Validator(output_schema)
    last_error = "No response was generated."

    for attempt in range(3):
        repair_instruction = (
            ""
            if attempt == 0
            else (
                "\n\nYour previous response failed local validation. "
                "Regenerate the complete JSON object from the supplied evidence "
                f"and schema. Validation error: {last_error}"
            )
        )
        response_text = llm.generate(
            prompt + repair_instruction,
            system_instruction=BUSINESS_MANIFEST_SYSTEM_PROMPT,
            response_json_schema=output_schema,
        )
        try:
            manifest = json.loads(response_text)
            validator.validate(manifest)
            if manifest["business_id"] != business_input["business_id"]:
                raise ValueError(
                    "Gemini returned a manifest for a different business_id."
                )

            inventory = set(business_input["file_inventory"])
            for table in manifest["tables"]:
                unknown_files = set(table["source_files"]) - inventory
                if unknown_files:
                    raise ValueError(
                        "Manifest references files absent from inventory: "
                        f"{sorted(unknown_files)}"
                    )
            references = {
                reference
                for table in manifest["tables"]
                for reference in table["source_refs"]
            }
            references.update(
                reference
                for fact in manifest["facts"]
                for reference in fact["source_refs"]
            )
            unknown_references = references - evidence_ids
            if unknown_references:
                raise ValueError(
                    "Manifest references unknown evidence IDs: "
                    f"{sorted(unknown_references)}"
                )
            return manifest
        except json.JSONDecodeError as error:
            last_error = (
                f"Response was invalid JSON at character {error.pos}: {error.msg}"
            )
        except ValidationError as error:
            path = "/".join(str(part) for part in error.absolute_path) or "(root)"
            last_error = f"Schema validation failed at {path}: {error.message}"
        except ValueError as error:
            last_error = str(error)

    raise RuntimeError(
        f"Gemini failed to generate a valid manifest for "
        f"{business_input['business_id']} after 3 attempts: {last_error}"
    )
