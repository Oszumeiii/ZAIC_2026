"""Summarize all audit issues, including those beyond report.json's sample limit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_review(directory):
    import duckdb
    directory = Path(directory)
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    results = []
    for entry in summary["businesses"]:
        business_id = entry["business_id"]
        if entry["status"] == "failed":
            results.append(entry)
            continue
        groups = {}
        with duckdb.connect(str(directory / business_id / "data.duckdb"), read_only=True) as con:
            cursor = con.execute("SELECT logical_name, code, details_json FROM meta.issues")
            while batch := cursor.fetchmany(2000):
                for table, code, details in batch:
                    item = json.loads(details)
                    if code in ("header_detected", "blank_row"):
                        continue
                    key = (code, item.get("column"), item.get("reason"))
                    group = groups.setdefault(key, {"code": code, "column": key[1], "reason": key[2], "count": 0, "samples": []})
                    group["count"] += 1
                    if len(group["samples"]) < 3:
                        group["samples"].append({"table": table, **item})
        results.append({**entry, "review_groups": sorted(groups.values(), key=lambda g: -g["count"])})
    totals = {"businesses": len(results), "failed": sum(r["status"] == "failed" for r in results),
              "tables": sum(r.get("tables", 0) for r in results), "rows": sum(r.get("rows", 0) for r in results)}
    report = {"totals": totals, "businesses": results}
    (directory / "review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Rà soát ingestion thực tế", "",
             f"{totals['businesses']} doanh nghiệp; {totals['tables']} bảng; {totals['rows']:,} bản ghi; {totals['failed']} thất bại.", "",
             "`complete` nghĩa là đọc/xuất được dữ liệu, KHÔNG xác nhận mọi giá trị Typed đã hợp lệ.",
             "Cảnh báo có thể là cùng một bản ghi ở nhiều cột, không phải số bản ghi bị mất.",
             "Toàn bộ dòng vẫn còn trong Raw. Giá trị Typed parse lỗi là NULL.", "",
             "| Business | Bảng | Dòng | Parse lỗi | Tách số/đơn vị | Nghi trùng | Trạng thái |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for result in results:
        counts = result.get("issue_counts", {})
        lines.append(f"| [{result['business_id']}]({result['business_id']}/REPORT.md) | {result.get('tables', 0)} | {result.get('rows', 0)} | {counts.get('parse_failed', 0)} | {counts.get('quantity_split', 0)} | {counts.get('possible_duplicate', 0)} | {result['status']} |")
    lines.extend(["", "## Những điểm cần bạn quyết định", "",
                  "- `ambiguous_number`: xác nhận dấu thập phân qua override; chưa tự coi dấu đơn là phân cách nghìn.",
                  "- `ambiguous_date` / `date_policy_unresolved`: xác nhận dmy/mdy hoặc format cụ thể.",
                  "- `excel_serial_requires_explicit_epoch`: xác nhận epoch 1900/1904 trước khi đổi số serial thành ngày.",
                  "- `quantity_split`: số đầu và đơn vị được tách; chưa quy đổi đơn vị, cần rà chuỗi phức hợp.",
                  "- `formula_cache_missing`: cần nguồn được tính/lưu lại hoặc quy tắc tính có kiểm chứng.",
                  "- `possible_duplicate`, `possible_total`, `repeated_header`: xem nguồn trước khi quyết định loại khỏi view.", "",
                  "## Ví dụ theo doanh nghiệp", ""])
    for result in results:
        groups = result.get("review_groups", [])
        if not groups and result["status"] != "failed":
            continue
        lines.extend([f"### {result['business_id']}", ""])
        if result["status"] == "failed":
            lines.extend([result["error"], ""])
            continue
        for group in groups[:6]:
            sample = group["samples"][0]
            value = str(sample.get("raw", sample.get("value", ""))).replace("\n", " ")[:80]
            lines.append(f"- `{group['code']}`: {group['count']} sự kiện; cột `{group['column']}`; lý do `{group['reason']}`; ví dụ `{value}` tại `{sample['table']}` / `{sample.get('position', sample.get('row', ''))}`.")
        lines.append("")
    lines.extend(["Chi tiết mọi nhóm lỗi và tối đa 3 ví dụ/nhóm: review.json. Toàn bộ vị trí lỗi: meta.issues trong từng database.",
                  "Lịch dùng chung được xuất riêng ở _shared/, không tính vào thống kê doanh nghiệp ở trên."])
    (directory / "REVIEW.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    report = build_review(parser.parse_args().out_dir)
    print(report["totals"])


if __name__ == "__main__":
    main()
