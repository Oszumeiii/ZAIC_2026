"""Conservative type conversion; ambiguity is reported, never guessed silently."""
from __future__ import annotations

import datetime as dt
import math
import re
from decimal import Decimal, InvalidOperation

import pandas as pd

from app.text import colnorm, norm
from .readers import RawTable, issue, json_text


def missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


def identifier_column(name):
    name = colnorm(re.sub(r"([a-z])([A-Z])", r"\1_\2", name))
    return bool(re.search(r"(^|_)(ma|id|code|sku|barcode|phone|tel|cccd|cmnd|mst|sdt)(_|$)", name)
                or name in ("hd", "so_hd", "so_kh")
                or any(x in name for x in ("dien_thoai", "tax_code", "so_tai_khoan", "so_hoa_don")))


def text_column(name, description):
    """Categorical meaning has priority over words such as money in examples."""
    name = colnorm(re.sub(r"([a-z])([A-Z])", r"\1_\2", name))
    description = colnorm(description)
    prefixes = ("ten", "nhom", "trang_thai", "phuong_thuc", "hinh_thuc", "cach", "kenh",
                "ghi_chu", "dia_chi", "email", "don_vi", "dvt", "unit", "name", "status", "category", "method", "tien_do")
    return any(value == p or value.startswith(p + "_") for value in (name, description) for p in prefixes)


def number_hint(name):
    name = colnorm(re.sub(r"([a-z])([A-Z])", r"\1_\2", name))
    return bool(re.search(r"(^|_)(gia|tien|luong|sluong|tong|no|ton|diem|qty|quantity|amount|price|total|stt|sl)(_|$)", name))


def decimal_style(value: str):
    """Evidence only: repeated thousands groups or both separators."""
    s = value.strip().lstrip("+-")
    if "." in s and "," in s:
        return "," if s.rfind(",") > s.rfind(".") else "."
    for thousands, decimal in ((",", "."), (".", ",")):
        if re.fullmatch(r"\d{1,3}(?:" + re.escape(thousands) + r"\d{3}){2,}", s):
            return decimal
    # One or two trailing digits are unambiguous fractional evidence.
    if re.fullmatch(r"\d+[.,]\d{1,2}", s):
        return s[-3] if s[-3] in ".," else s[-2]
    return None


def parse_number(value, decimal=None, suffix_multipliers=None):
    multiplier = Decimal(1)
    if isinstance(value, str) and suffix_multipliers:
        stripped = value.strip()
        for suffix in sorted(suffix_multipliers, key=len, reverse=True):
            if suffix and stripped.casefold().endswith(suffix.casefold()):
                multiplier = Decimal(str(suffix_multipliers[suffix]))
                value = stripped[:-len(suffix)].strip()
                break
    if isinstance(value, bool):
        raise ValueError("boolean_is_not_number")
    if isinstance(value, (int, float, Decimal)):
        result = Decimal(str(value))
    else:
        s = str(value).strip().replace("\u00a0", " ")
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        if not re.fullmatch(r"[+-]?\d+(?:[., ]\d+)*", s):
            raise ValueError("invalid_number")
        if " " in s:
            if not re.fullmatch(r"[+-]?\d{1,3}(?: \d{3})+(?:[.,]\d+)?", s):
                raise ValueError("invalid_grouping")
            s = s.replace(" ", "")
        style = decimal or decimal_style(s)
        if style:
            thousands = "," if style == "." else "."
            # Validate group sizes before removing thousands separators.
            integer = s.split(style)[0].lstrip("+-")
            if thousands in integer and not re.fullmatch(r"\d{1,3}(?:" + re.escape(thousands) + r"\d{3})+", integer):
                raise ValueError("invalid_grouping")
            if thousands in s.split(style)[-1] and style in s:
                raise ValueError("invalid_grouping")
            s = s.replace(thousands, "").replace(style, ".")
        elif "." in s or "," in s:
            raise ValueError("ambiguous_number")
        try:
            result = Decimal(s)
        except InvalidOperation:
            raise ValueError("invalid_number") from None
    if not result.is_finite():
        raise ValueError("non_finite_number")
    # Decimal's default arithmetic precision is 28: preserve up to the storage limit.
    from decimal import localcontext
    with localcontext() as context:
        context.prec = 80
        result *= multiplier
    if len(result.as_tuple().digits) > 38 or abs(result.adjusted()) > 37:
        raise ValueError("number_out_of_range")
    return result


QUANTITY = re.compile(r"^([+-]?\d+(?:[.,]\d+)*)\s+([^\d\s].*)$")


def date_policy(values, readme, override):
    if override:
        return override, "override"
    text = norm(readme)
    dmy = bool(re.search(r"dd[/.-]mm[/.-]yyyy|dd[/.-]mm[/.-]yy\b", text))
    mdy = bool(re.search(r"mm[/.-]dd[/.-]yyyy|mm[/.-]dd[/.-]yy\b", text))
    evidence = set()
    for value in values:
        m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-]\d{4}(?:\s|$)", str(value).strip())
        if m:
            a, b = map(int, m.groups())
            if a > 12:
                evidence.add("dmy")
            if b > 12:
                evidence.add("mdy")
    if dmy:
        evidence.add("dmy")
    if mdy:
        evidence.add("mdy")
    if len(evidence) == 1:
        return evidence.pop(), "README/column"
    return None, "conflict" if evidence else "ambiguous"


def parse_date(value, policy, excel_epoch=None):
    if isinstance(value, (dt.datetime, dt.date, pd.Timestamp)):
        return pd.Timestamp(value)
    s = str(value).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        if not excel_epoch:
            raise ValueError("excel_serial_requires_explicit_epoch")
        from openpyxl.utils.datetime import from_excel, CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900
        return pd.Timestamp(from_excel(float(s), epoch=CALENDAR_MAC_1904 if excel_epoch == "1904" else CALENDAR_WINDOWS_1900))
    vietnamese = re.fullmatch(r"(\d{1,2})\s+(?:thg|thang)\s+(\d{1,2})\s+(\d{4})", norm(s))
    if vietnamese:
        day, month, year = map(int, vietnamese.groups())
        return pd.Timestamp(dt.datetime(year, month, day))
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:[ T]|$)", s):
        return pd.Timestamp(dt.datetime.fromisoformat(s))
    if policy and policy not in ("dmy", "mdy"):
        return pd.Timestamp(dt.datetime.strptime(s, policy))
    if not policy:
        raise ValueError("ambiguous_date")
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(.*)", s)
    if not m:
        raise ValueError("invalid_date")
    a, b, year, tail = m.groups()
    day, month = (a, b) if policy == "dmy" else (b, a)
    return pd.Timestamp(dt.datetime.fromisoformat(f"{year}-{int(month):02}-{int(day):02}{tail}"))


def row_flags(raw: RawTable):
    flags, seen = [], set()
    header = [norm(c) for c in raw.source.get("original_headers", raw.headers)]
    for row in raw.rows:
        current = []
        strings = [norm(v) for v in row]
        if all(missing(v) for v in row):
            current.append("blank_row")
        if strings == header:
            current.append("repeated_header")
        if any(re.fullmatch(r"(?:tong(?: cong)?|tong ket|subtotal|grand total|total)(?:\s*:)?", s) for s in strings):
            current.append("possible_total")
        signature = json_text(row)
        if signature in seen and "blank_row" not in current:
            current.append("possible_duplicate")
        seen.add(signature)
        flags.append(current)
    return flags


def convert_table(raw: RawTable, readme: str, options: dict):
    unknown_columns = set(options.get("columns", {})) - set(raw.headers)
    if unknown_columns:
        raise ValueError(f"Unknown override columns: {sorted(unknown_columns)}")
    flags = row_flags(raw)
    issues = list(raw.issues)
    data, columns = {}, []
    descriptions = {}
    file_name = raw.source.get("file", "").replace("\\", "/").rsplit("/", 1)[-1]
    # Only use column descriptions under this exact file's Markdown section.
    section = re.search(r"^## Cột trong `" + re.escape(file_name) + r"`[^\n]*\n(.*?)(?=^## |\Z)", readme, re.M | re.S)
    if section:
        descriptions = dict(re.findall(r"^- \*\*(.*?)\*\*:\s*(.+)$", section.group(1), re.M))
    for j, name in enumerate(raw.headers):
        spec = options.get("columns", {}).get(name, {})
        values = [row[j] for row in raw.rows]
        valid = [v for i, v in enumerate(values) if not missing(v) and "repeated_header" not in flags[i]]
        kind = spec.get("type")
        description = spec.get("description", descriptions.get(name, ""))
        primary_description = re.split(r"[;:(,]", description)[0]
        identifier = identifier_column(name) or identifier_column(primary_description)
        date_like = bool(valid) and sum(isinstance(v, (dt.date, dt.datetime)) or bool(re.match(r"^\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}(?:\s|T|$)", str(v))) for v in valid) / len(valid) >= .9
        if kind is None:
            if identifier:
                kind = "string"
            elif text_column(name, primary_description):
                kind = "string"
            elif date_like or any(k in colnorm(name) for k in ("ngay", "thoi_gian", "date")):
                kind = "date"
            elif number_hint(name) or number_hint(primary_description) or (valid and all(isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) for v in valid)):
                numeric_evidence = not valid or any(isinstance(v, (int, float, Decimal)) or re.match(r"^[+\-(]?\d", str(v).strip()) for v in valid)
                if numeric_evidence:
                    kind = "quantity" if any(QUANTITY.fullmatch(str(v).strip()) for v in valid) else "number"
                else:
                    kind = "string"
                    issues.append(issue("numeric_hint_unconfirmed", column=name, review_required=True))
            elif valid and all(isinstance(v, bool) for v in valid):
                kind = "boolean"
            else:
                kind = "string"
        if kind not in ("string", "number", "integer", "date", "boolean", "quantity"):
            raise ValueError(f"Unsupported type {kind!r} for {name}")
        decimal = spec.get("decimal")
        if decimal not in (None, ".", ","):
            raise ValueError(f"Invalid decimal separator for {name}")
        excel_epoch = spec.get("excel_epoch")
        if excel_epoch not in (None, "1900", "1904"):
            raise ValueError(f"Invalid excel_epoch for {name}; use string 1900 or 1904")
        suffix_multipliers = spec.get("suffix_multipliers", {})
        for suffix, multiplier in suffix_multipliers.items():
            numeric_multiplier = Decimal(str(multiplier))
            if not suffix or not numeric_multiplier.is_finite() or numeric_multiplier <= 0:
                raise ValueError(f"Invalid suffix multiplier for {name}")
        styles = {decimal_style(QUANTITY.fullmatch(v.strip()).group(1) if kind == "quantity" and QUANTITY.fullmatch(v.strip()) else v)
                  for v in valid if isinstance(v, str)} - {None}
        if decimal is None and len(styles) == 1:
            decimal = next(iter(styles))
        # Conflicting per-value styles are allowed only when individually unambiguous.
        policy, policy_source = date_policy(valid, readme, spec.get("date_format")) if kind == "date" else (None, None)
        if kind == "date" and not policy:
            requires_policy = any(
                not isinstance(v, (dt.date, dt.datetime))
                and not re.match(r"^\d{4}-", str(v))
                and not re.fullmatch(r"\d{1,2}\s+(?:thg|thang)\s+\d{1,2}\s+\d{4}", norm(v))
                and not (excel_epoch and re.fullmatch(r"\d+(?:\.\d+)?", str(v)))
                for v in valid
            )
            if requires_policy:
                issues.append(issue("date_policy_unresolved", column=name, reason=policy_source, review_required=True))
        converted = []
        quantity_values, quantity_units = [], []
        for i, value in enumerate(values):
            if missing(value):
                converted.append(None)
                if kind == "quantity":
                    quantity_values.append(None)
                    quantity_units.append(None)
                continue
            try:
                if kind == "quantity":
                    text = str(value)
                    m = QUANTITY.fullmatch(text.strip())
                    numeric, unit = m.groups() if m else (value, None)
                    result = parse_number(numeric, decimal, suffix_multipliers)
                    quantity_values.append(result)
                    quantity_units.append(unit)
                    converted.append(text)
                    continue
                elif kind == "string":
                    result = str(value)
                    if identifier and isinstance(value, (int, float)) and not isinstance(value, bool):
                        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
                            result = str(int(value))
                        fmt = raw.cell_metadata.get(f"{raw.positions[i].get('row')}:{j+1}", {}).get("number_format", "")
                        if re.fullmatch(r"0+", fmt) and result.isdigit():
                            result = result.zfill(len(fmt))
                        elif isinstance(value, float) and abs(value) >= 2**53:
                            issues.append(issue("identifier_precision_risk", column=name, position=raw.positions[i], review_required=True))
                elif kind in ("number", "integer"):
                    result = parse_number(value, decimal, suffix_multipliers)
                    if kind == "integer" and result != result.to_integral_value():
                        raise ValueError("not_integer")
                elif kind == "date":
                    result = parse_date(value, policy, excel_epoch)
                else:
                    if value not in (True, False, "true", "false", "True", "False"):
                        raise ValueError("invalid_boolean")
                    result = value in (True, "true", "True")
                converted.append(result)
            except (ValueError, TypeError, OverflowError) as exc:
                if kind == "quantity":
                    converted.append(str(value))
                    quantity_values.append(None)
                    quantity_units.append(None)
                else:
                    converted.append(None)
                issues.append(issue("parse_failed", column=name, position=raw.positions[i], raw=str(value),
                                    target=kind, reason=str(exc), review_required=True))
        if kind == "integer":
            data[name] = pd.Series([int(x) if x is not None else None for x in converted], dtype=object)
        elif kind == "date":
            data[name] = pd.Series(converted, dtype="datetime64[ns]")
        else:
            data[name] = pd.Series(converted, dtype=object)
        columns.append({"name": name, "search_alias": colnorm(name), "type": "string" if kind == "quantity" else kind,
                        "identifier": identifier, "decimal": decimal, "date_format": policy,
                        "excel_epoch": excel_epoch,
                        "date_evidence": policy_source, "unit": spec.get("unit"),
                        "description": description or None, "override": spec,
                        "null_count": sum(missing(v) for v in converted)})
        if kind == "quantity":
            derived = []
            for suffix, series, dtype in (("__quantity", quantity_values, "number"), ("__unit", quantity_units, "string")):
                derived_name = name + suffix
                while derived_name.casefold() in {c.casefold() for c in list(raw.headers) + list(data)}:
                    derived_name += "_"
                data[derived_name] = pd.Series(series, dtype=object)
                columns.append({"name": derived_name, "search_alias": colnorm(derived_name), "type": dtype,
                                "identifier": False, "decimal": decimal, "date_format": None,
                                "date_evidence": None, "unit": None, "description": "Tách số/đơn vị, không quy đổi",
                                "override": {}, "derived_from": name, "null_count": sum(v is None for v in series)})
                derived.append(derived_name)
            issues.append(issue("quantity_split", column=name, derived_columns=derived, review_required=True))
    for i, row_flag in enumerate(flags):
        for flag in row_flag:
            issues.append(issue(flag, position=raw.positions[i], review_required=flag != "blank_row"))
    return pd.DataFrame(data), columns, flags, issues
