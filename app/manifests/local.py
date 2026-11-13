"""Generate source-sized manifest batches with Qwen and validate against source evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from app.llm.prompts import BUSINESS_MANIFEST_SYSTEM_PROMPT
from app.manifests.service import BusinessManifestService, SCHEMA_FILE
from app.manifests.validation import ManifestValidator
from app.storage.json_store import JsonStore
from app.text import norm


class LocalManifestGenerator:
    VERSION = "source-batches-v2"
    COLUMNS_PER_BATCH = 12

    def __init__(self, llm):
        self.llm = llm
        self.schema = json.loads(SCHEMA_FILE.with_name("business_manifest.v2.schema.json").read_text())

    @staticmethod
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def fit_evidence(self, prompt, schema):
        """Select complete evidence sections for a column batch, never truncate source/schema."""
        system = self.llm.json_system(BUSINESS_MANIFEST_SYSTEM_PROMPT, schema)
        def fits():
            return self.llm.fits(system, json.dumps(prompt, ensure_ascii=False), self.llm.manifest_tokens + 512)
        if fits():
            return prompt
        source = prompt["target_source"]
        words = set(norm(source["source_file"] + " " + source["source_object"] + " " +
                         " ".join(c["source_name"] for c in source["columns"])).split())
        prompt["omitted_evidence"] = []
        prompt["instruction"] += " Evidence was selected for this batch. Unprovided meanings must stay unknown."
        candidates = [(field, section) for field in ("readme_sections", "rulebook_sections") for section in prompt[field]]
        candidates.sort(key=lambda item: len(words & set(norm(item[1]["text"]).split())))
        while candidates and not fits():
            field, section = candidates.pop(0)
            prompt[field].remove(section)
            prompt["omitted_evidence"].append(section["evidence_id"])
            prompt["evidence_ids"] = [ref for ref in prompt["evidence_ids"] if ref != section["evidence_id"]]
        # Large source indexes are optional for this batch's column mapping.
        if not fits():
            prompt["source_index"] = [s for s in prompt["source_index"] if
                                      (s["source_file"], s["source_object"]) == (source["source_file"], source["source_object"])]
            prompt["evidence_ids"] = [f"profile:{source['source_file']}::{source['source_object']}"]
        return prompt  # LocalLLMService rejects any remaining overflow before generation.

    def generate(self, business_dir, rulebook, output_dir):
        directory, output = Path(business_dir), Path(output_dir)
        data = BusinessManifestService.build_input(directory, rulebook, self.schema)
        evidence = BusinessManifestService.build_evidence(data)
        JsonStore.write(evidence, output / "evidence" / f"{directory.name}.json")
        signature = self.digest([self.VERSION, BUSINESS_MANIFEST_SYSTEM_PROMPT, self.llm.tag(), self.schema, evidence])
        path = output / f"{directory.name}.json"
        metadata = output / "provenance" / f"{directory.name}.json"
        if path.exists() and metadata.exists() and json.loads(metadata.read_text()).get("signature") == signature:
            manifest = json.loads(path.read_text())
            self.validate(manifest, data)
            if json.loads(metadata.read_text()).get("manifest_sha256") == self.digest(manifest):
                return path

        parts, batches = [], []
        for source in data["source_inventory"]["objects"]:
            columns = [c for c in source["columns"] if c["source_name"]]
            for start in range(0, max(1, len(columns)), self.COLUMNS_PER_BATCH):
                batches.append({**source, "columns": columns[start:start + self.COLUMNS_PER_BATCH]})
        merged_tables = {}
        for batch_number, source in enumerate(batches, 1):
            print(f"[manifest] {directory.name} batch {batch_number}/{len(batches)}: "
                  f"{source['source_file']} / {source['source_object']}", flush=True)
            schema = deepcopy(self.schema)
            schema["properties"]["business_id"]["const"] = directory.name
            table = schema["properties"]["tables"]
            table.update(minItems=1, maxItems=1)
            for key in ("source_file", "source_object", "header_row_number"):
                table["items"]["properties"][key]["const"] = source[key]
            identity = (source["source_file"], source["source_object"])
            if identity in merged_tables:
                for key in ("table_role", "grain"):
                    table["items"]["properties"][key]["const"] = merged_tables[identity][key]
            # Keep all object identities/column names for documented cross-table relations.
            inventory = [{"source_file": s["source_file"], "source_object": s["source_object"],
                          "columns": [c["source_name"] for c in s["columns"]]}
                         for s in data["source_inventory"]["objects"]]
            prompt = {"business_id": directory.name, "target_source": source, "source_index": inventory,
                      "readme_sections": data["readme_sections"], "rulebook_sections": data["rulebook_sections"],
                      "evidence_ids": data["_evidence_ids"],
                      "instruction": "Map exactly the supplied column batch of this source object. Include ALL supplied columns, no others. "
                                     "Keep undocumented semantics unknown. "
                                     "Include only facts/relationships supported by supplied evidence. "
                                     "The source profile evidence id is profile:<source_file>::<source_object>."}
            prompt = self.fit_evidence(prompt, schema)
            key = self.digest([signature, prompt, schema])
            cache = output / "batches" / directory.name / f"{key}.json"
            part = json.loads(cache.read_text()) if cache.exists() else None
            error = ""
            for attempt in range(3):
                if part is None:
                    request = json.dumps(prompt, ensure_ascii=False) + error
                    part = self.llm.json(BUSINESS_MANIFEST_SYSTEM_PROMPT, request, schema, task="manifest")
                try:
                    Draft202012Validator(schema).validate(part)
                    self.validate(part, data, complete=False)
                    names = {c["source_name"] for c in part["tables"][0]["columns"]}
                    if names != {c["source_name"] for c in source["columns"] if c["source_name"]}:
                        raise ValueError("Target source must include every observed column")
                    break
                except (ValueError, ValidationError) as exc:
                    if attempt == 2:
                        raise ValueError(f"Invalid manifest for {directory.name}/{source['source_object']}: {exc}") from exc
                    error = "\nCorrect the previous source mapping: " + str(exc)[:1000]
                    part = None
            JsonStore.write(part, cache)
            if prompt.get("omitted_evidence"):
                part["unresolved"] = ManifestValidator.merge_issues(part["unresolved"], [ManifestValidator.issue(
                    f"context:{source['source_file']}:{source['source_object']}",
                    "Evidence sections omitted to fit local context: " + ", ".join(prompt["omitted_evidence"]),
                    "Review omitted sections before promoting unknown mappings to documented semantics.")])
            parts.append(part)
            mapped = part["tables"][0]
            if identity not in merged_tables:
                merged_tables[identity] = deepcopy(mapped)
            else:
                merged_tables[identity]["columns"].extend(mapped["columns"])
                merged_tables[identity]["source_refs"] = sorted(set(merged_tables[identity]["source_refs"]) | set(mapped["source_refs"]))

        if not parts:
            raise ValueError(f"No source objects in {directory}")
        manifest = {"schema_version": "2.0", "business_id": directory.name,
                    "tables": list(merged_tables.values()),
                    "facts": [], "relationships": [], "rule_refs": sorted({r for p in parts for r in p["rule_refs"]}),
                    "unresolved": ManifestValidator.merge_issues(*(p["unresolved"] for p in parts))}
        for field, id_key in (("facts", "fact_id"), ("relationships", "relationship_id")):
            entries = {}
            for part in parts:
                for entry in part[field]:
                    content = {k: v for k, v in entry.items() if k != id_key}
                    key = self.digest(content)[:24]
                    entries[key] = {**content, id_key: f"local_{key}"}
            manifest[field] = list(entries.values())
        report = self.validate(manifest, data)
        manifest["unresolved"] = ManifestValidator.merge_issues(manifest["unresolved"], report["unresolved"])
        JsonStore.write(report, output / "reports" / f"{directory.name}.json")
        JsonStore.write(manifest, path)
        JsonStore.write({"signature": signature, "model": self.llm.tag(),
                         "manifest_sha256": self.digest(manifest)}, metadata)
        return path

    def validate(self, manifest, data, *, complete=True):
        Draft202012Validator(self.schema).validate(manifest)
        if manifest["business_id"] != data["business_id"]:
            raise ValueError("Wrong manifest business_id")
        report = ManifestValidator.validate(manifest, data["source_inventory"], data["_evidence_ids"])
        for table in manifest["tables"]:
            for column in table["columns"]:
                report["errors"].extend(ManifestValidator.validate_column_semantics(column))
        if complete and len(manifest["tables"]) != len(data["source_inventory"]["objects"]):
            report["errors"].append("Manifest must cover every source object")
        if report["errors"]:
            raise ValueError("; ".join(report["errors"]))
        return report
