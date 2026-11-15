"""Validated JSON and question rewriting using one local model instance."""
from collections import Counter
from copy import deepcopy
import json
import re

from jsonschema import Draft202012Validator, ValidationError

from app.llm.base import GenerationError
from app.llm.prompts import QUESTION_REWRITE_SYSTEM_PROMPT
from app.text import norm


class LocalLLMService:
    supports_batch = False
    VERSION = "local-json-v2"

    def __init__(self, backend, cfg, *, manifest_tokens=6144, rewrite_tokens=512):
        self.backend, self.cfg = backend, cfg
        self.manifest_tokens, self.rewrite_tokens = manifest_tokens, rewrite_tokens

    def tag(self):
        return f"{self.backend.tag()}|{self.VERSION}|sql={self.cfg.max_new_tokens}|manifest={self.manifest_tokens}"

    def count_tokens(self, text):
        return self.backend.count_tokens(text)

    @staticmethod
    def json_system(system, schema):
        return system + "\nReturn ONLY one JSON object, without Markdown. JSON schema:\n" + json.dumps(schema, ensure_ascii=False)

    def fits(self, system, prompt, output_tokens):
        return self.backend.prompt_tokens(system, prompt) + output_tokens <= self.cfg.max_model_len

    def json(self, system, prompt, schema, *, task="sql"):
        budget = {"manifest": self.manifest_tokens, "rewrite": self.rewrite_tokens,
                  "sql": self.cfg.max_new_tokens, "review": self.cfg.max_new_tokens}[task]
        temperature = self.cfg.temperature if task == "sql" else 0.0
        system = self.json_system(system, schema)
        validator = Draft202012Validator(schema)
        repair = ""
        attempts = []
        for attempt in range(self.cfg.max_retry + 1):
            request = prompt + repair
            if not self.fits(system, request, budget):
                raise GenerationError(f"{task}: prompt exceeds local context budget ({self.cfg.max_model_len}); "
                                      "increase context or reduce the evidence batch")
            # A small, seeded variation helps a failed greedy manifest escape the
            # same malformed output while keeping the first attempt deterministic.
            retry_temperature = 0.2 if task == "manifest" and attempt else temperature
            raw = self.backend.generate_with_options(system, request, max_new_tokens=budget,
                                                     temperature=retry_temperature)[0]
            record = {"attempt": attempt + 1, "raw": raw}
            attempts.append(record)
            try:
                text = raw.strip()
                if text.startswith("```") and text.endswith("```"):
                    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)[:-3].strip()
                result = json.loads(text)
                validator.validate(result)
                return result
            except (ValueError, TypeError, IndexError) as error:
                failure = str(error)[:600]
            except ValidationError as error:
                problems = sorted(validator.iter_errors(result),
                                  key=lambda e: (list(map(str, e.absolute_path)), e.validator or ""))
                def describe(problem):
                    path = "/".join(map(str, problem.absolute_path)) or "$"
                    if problem.validator in ("maxItems", "minItems", "maxLength", "minLength"):
                        message = f"violates {problem.validator}={problem.validator_value}"
                    else:
                        message = problem.message[:180]
                    return f"{path}: {message}"
                failure = "; ".join(describe(problem) for problem in problems[:3])[:600]
            record["error"] = failure
            repair = "\nPrevious response failed validation: " + failure + ". Return the complete corrected JSON."
        error = GenerationError(f"{task}: invalid JSON/schema after {self.cfg.max_retry + 1} attempts: {failure}")
        error.attempts = attempts
        raise error

    def generate_json(self, system, prompt, schema):
        return json.dumps(self.json(system, prompt, schema), ensure_ascii=False)

    def rewrite_question(self, question, *, protected_terms=()):
        if not isinstance(question, str):
            raise TypeError("question must be a string")
        if not question.strip():
            raise ValueError("question must not be empty")
        if isinstance(protected_terms, str) or any(
            not isinstance(term, str) or not term.strip() for term in protected_terms
        ):
            raise ValueError("protected_terms must be a sequence of non-empty strings")
        schema = {"type": "object", "properties": {"question": {"type": "string", "minLength": 1}},
                  "required": ["question"], "additionalProperties": False}
        # Explicit caller terms, quoted names and alphanumeric identifiers must survive rewriting.
        terms = set(protected_terms) | set(re.findall(r"\b(?=\w*[A-Za-z])(?=\w*\d)[\w-]+\b", question))
        terms.update(re.findall(r'["“]([^"”]+)["”]', question))
        prompt = json.dumps({"question": question, "protected_terms": sorted(terms)}, ensure_ascii=False)
        result = self.json(QUESTION_REWRITE_SYSTEM_PROMPT, prompt, schema, task="rewrite")["question"].strip()
        pattern = r"\d+(?:[.,:/-]\d+)*%?"
        if not result or Counter(re.findall(pattern, question)) != Counter(re.findall(pattern, result)):
            raise GenerationError("Rewrite changed numbers/dates or returned empty text")
        if any(question.count(term) != result.count(term) for term in terms):
            raise GenerationError("Rewrite changed a protected identifier/name")
        return result

    def fit_sql_context(self, context, system, schema, required_tables=()):
        """Keep rules and complete selected table schemas; explicitly disclose omitted tables."""
        result = deepcopy(context)
        system = self.json_system(system, schema)
        # Reserve room for the SQL error feedback used by the solver on retries.
        def fits():
            return self.fits(system, json.dumps(result, ensure_ascii=False, default=str),
                             self.cfg.max_new_tokens + 768)
        if fits():
            return result
        for table in result["tables"]:
            table["sample_rows"] = []
            for column in table["columns"]:
                column.pop("profile", None)  # completeness/parse counts remain available
        if fits():
            return result
        # Keep explicit dependencies and connected tables together, including bridge tables.
        words = set(norm(result["original_question"]).split())
        result["tables"].sort(key=lambda t: len(words & set(norm(json.dumps(t, ensure_ascii=False)).split())), reverse=True)
        required = set(required_tables)
        known = {t["name"] for t in result["tables"]}
        if not required <= known:
            raise GenerationError(f"Unknown required tables: {sorted(required - known)}")
        if not required:
            required.add(result["tables"][0]["name"])
        relations = result.get("relationships", []) + result.get("organization", {}).get("relationships", [])
        # Match both catalog physical names and source object identities.
        aliases = {t["name"]: {t["name"], *t.get("source_objects", [])} for t in result["tables"]}
        from pathlib import Path
        for table in result['tables']:
            aliases[table['name']].update(
                f"{Path(source).stem}::{obj}" for source in table.get('sources', [])
                for obj in table.get('source_objects', []))
        edges = []
        for relation in relations:
            tokens = {str(relation[k]) for k in ('from_table', 'to_table', 'source_table', 'target_table')
                      if relation.get(k) is not None}
            edge = {name for name, names in aliases.items() if names & tokens}
            if len(edge) > 1:
                edges.append(edge)
        changed = True
        while changed:
            previous = set(required)
            for edge in edges:
                if edge & required:
                    required.update(edge)
            changed = required != previous
        result["table_directory"] = [{"name": t["name"], "role": t.get("role"),
                                      "sources": t.get("sources", [])} for t in result["tables"]]
        result["omitted_tables"] = []
        result["context_notice"] = "Some tables were omitted for context size. Omission is not evidence that data does not exist."
        while len(result["tables"]) > 1 and not fits():
            removable = [t for t in reversed(result["tables"]) if t["name"] not in required]
            if not removable:
                break
            removed = removable[0]
            result["tables"].remove(removed)
            result["omitted_tables"].append(removed["name"])
        if not fits():
            raise GenerationError("SQL rulebook/README and one table exceed context budget; increase max_model_len")
        return result
