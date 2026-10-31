"""The data package (config/package/package.yaml): one JSON record per decision, a table with one row per record, the
download candidates with the filter's verdict, examples and the record schema. The README, LICENSE and CITATION.cff of
the data are written by hand and added to the package folder; `checksums` then writes SHA256SUMS over all its files.
`agent_corpus` writes the agent corpus of stage 09 on its own: its records by split and year, and a table of them.

A record joins what the stages found in one decision: the document (text, paragraphs, sections), the filter's
selection, the case attributes (BVwG), the citations, the outcomes, the links to other decisions, and whether the
decision is in the agent corpus. The parts leave out what repeats the record (its ID, its date).

The record holds its text once, in full_text; citations, section titles and printed attribute values keep their own few
words. Paragraphs, dispositions and the evidence of links point into it by position (start, end) and keep no copy; each
copy is checked to be exactly its span before it is left out. So the structured data holds no name of the judge or a
representative: the judge becomes a pseudonymous ID, representatives keep their kind and their position, and where a
section title (a heading as the court printed it) or a value of the attributes as printed repeats the judge's or a
representative's name, the name is replaced by its role ("[judge]", "[lawyer]", ...). The texts stay as RIS publishes
them.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from .common import CONFIG, SCHEMAS, GzipLines, data_dir, file_sha256, read_csv, read_jsonl, require, year_files
from .persons import masker

JUDGE_ID = r"^J[0-9a-f]{10}$"
SALT = "ASYLUM_CORPUS_JUDGE_SALT"
DOCUMENTS = {"bvwg": "processed/fine_sections/bvwg", "vwgh": "processed/documents/vwgh",
             "vfgh": "processed/documents/vfgh"}
FILTER = "interim/filtered/filter_decisions"
DOCUMENT_KEYS = ["document_id", "ris_id", "court", "ecli", "case_number", "case_numbers", "document_numbers",
                 "decision_date", "pronouncement_date", "decision_type", "source_url"]
REPEATED = ("document_id", "source_document_id", "ris_id", "decision_date")
INDEX = ["ris_id", "court", "decision_date", "case_number", "decision_type", "group", "template", "characters",
         "agent_corpus", "judge_id", "judge_gender", "formation", "appellants", "minors", "nationalities",
         "represented", "representation_kinds", "appellant_gender", "hearing", "new_law", "outcome_units", "outcomes",
         "protections", "citations", "reviewed_by", "reviews"]
SELECTION = {
    "description": "Why the decision is in the corpus. BVwG: the filter's group, rule, evidence and flags. VwGH, VfGH: "
                   "connected (its text names the case number of a BVwG decision of the download) or pool (it names "
                   "the BVwG, but no decision of the download).",
    "type": "object", "additionalProperties": False,
    "required": ["group", "rule", "evidence", "flags", "flag_evidence"],
    "properties": {"group": {"enum": ["main", "subsequent_applications_avg68", "connected", "pool"]},
                   "rule": {"type": ["string", "null"]},
                   "evidence": {"type": "array", "items": {"type": "string"}},
                   "flags": {"type": "array", "items": {"type": "string"}},
                   "flag_evidence": {"type": "object", "additionalProperties": {"type": "array",
                                                                                "items": {"type": "string"}}}}}
UNRESOLVED = {
    "description": "A BVwG decision the higher court names but that could not be identified with certainty: the "
                   "candidates, and why. withheld_relation_id: the relation the weak match would give, not made.",
    "type": "object", "additionalProperties": False,
    "required": ["bvwg_case_number", "candidates", "how", "method", "reason"],
    "properties": {"bvwg_case_number": {"type": ["string", "null"]}, "vfgh_case_number": {"type": "string"},
                   "candidates": {"type": "array", "items": {"type": "string"}},
                   "how": {"type": "string"}, "method": {"type": "string"}, "reason": {"type": "string"},
                   "withheld_relation_id": {"type": "string"}}}


def judge_id(name: str | None, salt: str) -> str | None:
    if not name or not name.strip():
        return None
    return "J" + hashlib.sha256(f"{salt}:{' '.join(name.split()).upper()}".encode()).hexdigest()[:10]


def published_head(record: dict, salt: str) -> dict:
    r = copy.deepcopy(record)
    judge = r["judge"]
    judge["id"] = judge_id(judge.pop("name"), salt)
    judge.pop("span")
    for rep in r["representation"]["representatives"]:
        rep.pop("printed")
    return r


def published_head_schema(schema: dict) -> dict:
    s = copy.deepcopy(schema)
    judge = s["properties"]["judge"]
    judge["properties"].pop("name")
    judge["properties"].pop("span")
    judge["properties"]["id"] = {"type": ["string", "null"], "pattern": JUDGE_ID,
                                 "description": "Pseudonymous ID of the judge (salted SHA-256 of the name)."}
    judge["required"] = ["id" if k == "name" else k for k in judge["required"] if k != "span"]
    rep = s["properties"]["representation"]["properties"]["representatives"]["items"]
    rep["properties"].pop("printed")
    rep["required"] = [k for k in rep["required"] if k != "printed"]
    return s


# --- the parts of a record ------------------------------------------------------------------------------------------

def attributes(head: dict, salt: str, mask=lambda text: text) -> dict:
    """The published head without what the record has; its printed values masked (an authority as printed can run on
    into the representative's name)."""
    def masked(node, key: str = ""):
        if isinstance(node, dict):
            return {k: masked(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [masked(v, key) for v in node]
        return mask(node) if isinstance(node, str) and key.endswith("printed") else node
    return masked({k: v for k, v in published_head(head, salt).items() if k not in REPEATED})


def citation(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "source_document_id"}


def spans(text: str, items: list[dict], key: str, where: str) -> list[dict]:
    """The items without their copy of the text (key), after checking that each copy is text[start:end]."""
    out = []
    for it in items:
        if text[it["start"]:it["end"]] != it[key]:
            raise SystemExit(f"{where}: the {key} at {it['start']}-{it['end']} is not its span of full_text")
        out.append({k: v for k, v in it.items() if k != key})
    return out


def operative_parts_checked(annotation: dict, doc: dict) -> None:
    """A BVwG outcome unit names its operative parts by section ID: each is an operative part of the record, and each
    disposition lies inside its own."""
    v = annotation["value"]
    if v["court"] != "BVwG":
        return
    by_id = {s["id"]: s for s in doc["sections"]}
    named = [v["operative_part_id"], *v["operative_part_ids"], *(p["operative_part_id"] for p in v["parts"]),
             *(d["operative_part_id"] for d in v["dispositions"])]
    if any(by_id.get(i, {}).get("type") != "operative_part" for i in named) or not all(
            by_id[d["operative_part_id"]]["start"] <= d["start"] <= d["end"] <= by_id[d["operative_part_id"]]["end"]
            for d in v["dispositions"]):
        raise SystemExit(f"{annotation['annotation_id']}: its operative parts are not sections of the record")


def outcome(annotation: dict, doc: dict) -> dict:
    operative_parts_checked(annotation, doc)
    value = {k: v for k, v in annotation["value"].items() if k != "court"}
    value["dispositions"] = spans(doc["full_text"], value["dispositions"], "text", annotation["annotation_id"])
    return {"annotation_id": annotation["annotation_id"], **value, "evidence": annotation["evidence"]}


def link(relation: dict, doc: dict) -> dict:
    """The relation with its evidence as positions. A quote from this record's text is checked against its span; a
    quote from the other record is checked in that record."""
    spans(doc["full_text"], [e for e in relation["evidence"] if e["document_id"] == doc["document_id"]], "quote",
          relation["relation_id"])
    return {**relation, "evidence": [{k: v for k, v in e.items() if k != "quote"} for e in relation["evidence"]]}


def sections(doc: dict, mask) -> list[dict]:
    """The sections; a title that repeats a person's name has the name replaced by its role."""
    out = []
    for s in doc["sections"]:
        title = s.get("title")
        out.append({**s, "title": mask(title)} if title and mask(title) != title else s)
    return out


def record(doc: dict, selection: dict, agent: bool, head: dict | None, salt: str, citations: list[dict],
           outcomes: list[dict], links: list[dict], unresolved: list[dict]) -> dict:
    text, mask = doc["full_text"], masker(head)
    return {**{k: doc[k] for k in DOCUMENT_KEYS}, "selection": selection, "agent_corpus": agent,
            "attributes": attributes(head, salt, mask) if head else None, "full_text": text,
            "paragraphs": spans(text, doc["paragraphs"], "text", doc["ris_id"]),
            "sections": sections(doc, mask), "structure": doc.get("structure"),
            "citations": [citation(c) for c in citations], "outcomes": [outcome(a, doc) for a in outcomes],
            "links": [link(x, doc) for x in links],
            "unresolved_links": [{k: v for k, v in u.items() if k != "source_id"} for u in unresolved],
            "ris_metadata": doc["ris_metadata"]}


def index_row(r: dict) -> dict:
    """The row of decisions.csv: the record's main values; lists joined by " | ", one entry per outcome unit."""
    a = r["attributes"] or {}
    judge, appellants, representation = a.get("judge", {}), a.get("appellants", {}), a.get("representation", {})
    def value(key: str) -> str:
        return (a.get(key) or {}).get("value", "")

    def reviews(end: str) -> int:
        return sum(x["relation_type"] == "reviews" and x[end] == r["document_id"] for x in r["links"])
    join = " | ".join
    return {"ris_id": r["ris_id"], "court": r["court"], "decision_date": r["decision_date"],
            "case_number": r["case_number"], "decision_type": r["decision_type"], "group": r["selection"]["group"],
            "template": (r["structure"] or {}).get("status", ""), "characters": len(r["full_text"]),
            "agent_corpus": r["agent_corpus"], "judge_id": judge.get("id") or "",
            "judge_gender": judge.get("gender") or "",
            "formation": judge.get("formation") or "", "appellants": appellants.get("count", ""),
            "minors": appellants.get("minors", ""), "nationalities": join(a.get("nationality_set", [])),
            "represented": representation.get("represented", ""),
            "representation_kinds": join(representation.get("kinds", [])),
            "appellant_gender": value("appellant_gender"),
            "hearing": value("hearing"), "new_law": value("new_law"), "outcome_units": len(r["outcomes"]),
            "outcomes": join(u["outcome"] or "" for u in r["outcomes"]),
            "protections": join(u.get("protection") or "" for u in r["outcomes"]), "citations": len(r["citations"]),
            "reviewed_by": reviews("target_id"), "reviews": reviews("source_id")}


# --- the record schema ----------------------------------------------------------------------------------------------

def rebase(node, base: str):
    """A part of a schema with its local references ("#/...") made relative to the schema it comes from."""
    if isinstance(node, dict):
        return {k: base + v if k == "$ref" and v.startswith("#") else rebase(v, base) for k, v in node.items()}
    if isinstance(node, list):
        return [rebase(x, base) for x in node]
    return node


def without(schema: dict, keys) -> dict:
    s = copy.deepcopy(schema)
    for k in keys:
        s["properties"].pop(k, None)
    if "required" in s:
        s["required"] = [k for k in s["required"] if k not in keys]
    return s


def outcome_schema(schema: dict) -> dict:
    value = without(schema["properties"]["value"], ("court",))
    dispositions = value["properties"]["dispositions"]
    dispositions["items"] = without(dispositions["items"], ["text"])
    props = {"annotation_id": schema["properties"]["annotation_id"], **value["properties"],
             "evidence": schema["properties"]["evidence"]}
    return {**{k: schema[k] for k in ("$id", "title", "description") if k in schema}, "type": "object",
            "additionalProperties": False, "required": ["annotation_id", *value["required"], "evidence"],
            "properties": props}


def record_schema() -> dict:
    def load(name: str) -> dict:
        return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))

    defs = {}
    for key, name in (("bvwg_document", "fine_sections.schema.json"), ("higher_document", "document.schema.json")):
        s = load(name)
        s.pop("$schema", None)
        s["$defs"]["paragraph"] = without(s["$defs"]["paragraph"], ["text"])
        defs[key] = s
    defs["attributes"] = without(published_head_schema(load("head.schema.json")), REPEATED)
    defs["citation"] = without(load("citation.schema.json"), ["source_document_id"])
    defs["bvwg_outcome"] = outcome_schema(load("outcome.schema.json"))
    defs["higher_outcome"] = outcome_schema(load("higher_court_outcome.schema.json"))
    defs["relation"] = load("relation.schema.json")
    defs["relation"]["$defs"]["evidence"] = without(defs["relation"]["$defs"]["evidence"], ["quote"])
    defs["relation"]["properties"]["evidence"]["description"] = \
        "Where the relation is stated: positions in the full_text of the evidence document (a record of the corpus)."
    for s in defs.values():
        s.pop("$schema", None)
    defs["selection"], defs["unresolved_link"] = SELECTION, UNRESOLVED

    def variant(doc: dict, attrs: dict, structure: dict, out: str) -> dict:
        def lift(key: str) -> dict:
            return rebase(copy.deepcopy(doc["properties"][key]), doc["$id"])

        def array(ref: str) -> dict:
            return {"type": "array", "items": {"$ref": f"#/$defs/{ref}"}}

        props = {**{k: lift(k) for k in DOCUMENT_KEYS}, "selection": {"$ref": "#/$defs/selection"},
                 "agent_corpus": {"type": "boolean"}, "attributes": attrs, "full_text": lift("full_text"),
                 "paragraphs": lift("paragraphs"), "sections": lift("sections"), "structure": structure,
                 "citations": array("citation"), "outcomes": array(out), "links": array("relation"),
                 "unresolved_links": array("unresolved_link"), "ris_metadata": lift("ris_metadata")}
        return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}

    bvwg = defs["bvwg_document"]
    structure = rebase(copy.deepcopy(bvwg["properties"]["structure"]), bvwg["$id"])
    defs["bvwg_record"] = variant(bvwg, {"$ref": "#/$defs/attributes"}, structure, "bvwg_outcome")
    defs["higher_record"] = variant(defs["higher_document"], {"type": "null"}, {"type": "null"}, "higher_outcome")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "record.schema.json",
            "title": "A decision of the Austrian Asylum Corpus",
            "description": "One record per decision (README.md). BVwG decisions have case attributes and a structure; "
                           "VwGH and VfGH decisions have null there.",
            "type": "object", "required": ["court"],
            "if": {"properties": {"court": {"const": "BVwG"}}}, "then": {"$ref": "#/$defs/bvwg_record"},
            "else": {"$ref": "#/$defs/higher_record"}, "$defs": defs}


def inline(schema: dict) -> dict:
    """The same schema with every reference replaced by what it points to: it checks the same, much faster."""
    resources = {}

    def collect(node) -> None:
        if isinstance(node, dict):
            if "$id" in node:
                resources[node["$id"]] = node
            for v in node.values():
                collect(v)
        elif isinstance(node, list):
            for v in node:
                collect(v)

    def at(doc: dict, pointer: str):
        for part in filter(None, pointer.split("/")):
            doc = doc[part]
        return doc

    def walk(node, base: str):
        if isinstance(node, list):
            return [walk(v, base) for v in node]
        if not isinstance(node, dict):
            return node
        base = node.get("$id", base)
        rest = {k: walk(v, base) for k, v in node.items() if k not in ("$ref", "$id", "$defs", "$schema")}
        if "$ref" not in node:
            return rest
        resource, _, pointer = node["$ref"].partition("#")
        return {**walk(at(resources[resource or base], pointer), resource or base), **rest}

    collect(schema)
    return walk(schema, schema["$id"])


# --- building the record files (one task per file, in worker processes) -------------------------------------------

STATE: dict = {}


def _init(data: str, salt: str) -> None:
    d = Path(data)
    links, unresolved = defaultdict(list), defaultdict(list)
    for r in read_jsonl(d / "processed/links/relations.jsonl"):
        for end in dict.fromkeys((r["source_id"], r["target_id"])):
            links[end].append(r)
    for u in read_jsonl(d / "processed/links/unresolved.jsonl"):
        unresolved[u["source_id"]].append(u)
    groups = {}
    for court in ("vwgh", "vfgh"):
        groups.update({row["ris_id"]: row["group"] for row in read_csv(d / DOCUMENTS[court] / "index.csv")})
    agent = {row["ris_id"] for row in read_csv(d / "processed/agent_corpus/agent_corpus.csv")}
    STATE.update(data=d, salt=salt, links=links, unresolved=unresolved, groups=groups, agent=agent,
                 filter={r["ris_id"]: r for r in read_jsonl(d / f"{FILTER}.jsonl")},
                 validator=Draft202012Validator(inline(record_schema())))


def _by_document(files, key: str) -> dict[str, list[dict]]:
    out = defaultdict(list)
    for f in files:
        for r in read_jsonl(f):
            out[r[key]].append(r)
    return out


def _selection(court: str, ris_id: str) -> dict:
    if court != "bvwg":
        return {"group": STATE["groups"][ris_id], "rule": None, "evidence": [], "flags": [], "flag_evidence": {}}
    f = STATE["filter"][ris_id]
    if f["verdict"] != "include":
        raise SystemExit(f"{ris_id}: parsed, but the filter excluded it")
    return {"group": f["group"], "rule": f["rule"], "evidence": f["evidence"], "flags": f["flags"],
            "flag_evidence": f.get("flag_evidence") or {}}


def _task(court: str, names: list[str], out: str) -> dict:
    d, salt = STATE["data"], STATE["salt"]
    citations = _by_document([d / "processed/citations" / court / n for n in names], "source_document_id")
    if court == "bvwg":
        outcomes = _by_document([d / "processed/outcomes/bvwg" / n for n in names], "document_id")
        heads = {h["document_id"]: h for n in names for h in read_jsonl(d / "processed/head/bvwg" / n)}
    else:
        outcomes, heads = _by_document([d / "processed/outcomes" / court / "outcomes.jsonl"], "document_id"), {}
    rows = []
    writer = GzipLines(Path(out))
    for n in names:
        for doc in read_jsonl(d / DOCUMENTS[court] / n):
            did = doc["document_id"]
            selection = _selection(court, doc["ris_id"])
            head = heads.get(did)
            if court == "bvwg" and head is None:
                raise SystemExit(f"{doc['ris_id']}: no case attributes")
            r = record(doc, selection, doc["ris_id"] in STATE["agent"], head, salt, citations.get(did, []),
                       outcomes.get(did, []), STATE["links"].get(did, []), STATE["unresolved"].get(did, []))
            error = next(STATE["validator"].iter_errors(r), None)
            if error:
                raise SystemExit(f"{doc['ris_id']}: {error.message} (at {'/'.join(map(str, error.absolute_path))})")
            writer.write(json.dumps(r, ensure_ascii=False) + "\n")
            rows.append(index_row(r))
    writer.close()
    return {"file": Path(out).name, "rows": rows}


# --- the package ---------------------------------------------------------------------------------------------------

def size(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"


def examples(cfg: dict, rows: list[dict], files: dict[str, str], out: Path, relations: list[dict]) -> list[dict]:
    by_vwgh = {x["target_id"] for x in relations
               if x["relation_type"] == "reviews" and x["source_id"].startswith("ris:vwgh:")}
    chosen = []
    for k, (outcome_, protection) in enumerate(cfg["examples"]):
        pool = [r for r in rows if r["court"] == "BVwG" and r["template"] == "merits" and r["outcome_units"] == 1
                and r["outcomes"] == outcome_ and r["protections"] == protection and r["reviewed_by"]
                and (k > 0 or f"ris:bvwg:{r['ris_id']}" in by_vwgh)]
        if not pool:
            raise SystemExit(f"examples: no reviewed merits decision with {outcome_} and {protection}")
        chosen.append(min(pool, key=lambda r: (r["characters"], r["ris_id"]))["ris_id"])
    first, outcomes = f"ris:bvwg:{chosen[0]}", {r["ris_id"]: r["outcomes"] for r in rows}
    reviewers = [x["source_id"].split(":", 2)[2] for x in relations if x["relation_type"] == "reviews"
                 and x["target_id"] == first and x["source_id"].startswith("ris:vwgh:")]
    chosen.append(min(reviewers, key=lambda i: (outcomes[i] == "interim", i)))      # one that decides the revision
    found = {}
    for name in sorted({files[i] for i in chosen}):
        found.update({r["ris_id"]: r for r in read_jsonl(out / name) if r["ris_id"] in chosen})
    return [found[i] for i in chosen]


def inputs(data: Path) -> dict[str, list[str]]:
    """The year files of each court, after checking that every input of the package is there."""
    steps = {"bvwg": "stage 05 fine", "vwgh": "stage 03 --court vwgh", "vfgh": "stage 03 --court vfgh"}
    names = {court: [Path(f).name for f in year_files(data / folder, steps[court])]
             for court, folder in DOCUMENTS.items()}
    for n in names["bvwg"]:
        for folder, step in (("citations", "stage 06"), ("outcomes", "stage 08"), ("head", "stage 04")):
            require(data / "processed" / folder / "bvwg" / n, step)
    for court in ("vwgh", "vfgh"):
        require(data / DOCUMENTS[court] / "index.csv", f"stage 03 --court {court}")
        require(data / "processed/outcomes" / court / "outcomes.jsonl", f"stage 08 --court {court}")
        for n in names[court]:
            require(data / "processed/citations" / court / n, f"stage 06 --court {court}")
    for path, step in (("processed/links/relations.jsonl", "stage 07"),
                       ("processed/links/unresolved.jsonl", "stage 07"),
                       ("processed/agent_corpus/agent_corpus.csv", "stage 09"),
                       (f"{FILTER}.jsonl", "stage 02"), (f"{FILTER}.csv", "stage 02")):
        require(data / path, step)
    return names


def prepare(out: Path) -> None:
    """A new or empty folder: the build writes over nothing and deletes nothing."""
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty: build the package into a new or empty folder")
    out.mkdir(parents=True, exist_ok=True)


def build(out: Path | None = None, workers: int = 4) -> Path:
    cfg = yaml.safe_load((CONFIG / "package" / "package.yaml").read_text(encoding="utf-8"))
    salt = os.environ.get(SALT, "")
    if not salt:
        raise SystemExit(f"set {SALT}: the salt of the pseudonymous judge IDs")
    data = data_dir()
    out = Path(out) if out else data / cfg["out"]
    names = inputs(data)
    prepare(out)

    tasks = [("bvwg", [n], str(out / cfg["files"]["bvwg"].format(year=n[:4]))) for n in names["bvwg"]]
    tasks += [(court, names[court], str(out / cfg["files"][court])) for court in ("vwgh", "vfgh")]

    def size_of(i: int) -> int:
        return sum((data / DOCUMENTS[tasks[i][0]] / n).stat().st_size for n in tasks[i][1])

    with ProcessPoolExecutor(workers, initializer=_init, initargs=(str(data), salt)) as pool:
        done = {i: pool.submit(_task, *tasks[i]) for i in sorted(range(len(tasks)), key=size_of, reverse=True)}
        results = [done[i].result() for i in range(len(tasks))]                  # submitted largest first

    rows = [row for res in results for row in res["rows"]]
    record_file = {row["ris_id"]: res["file"] for res in results for row in res["rows"]}
    with (out / "decisions.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=INDEX)
        w.writeheader()
        w.writerows(rows)
    with (data / f"{FILTER}.csv").open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        candidates = [dict(c, in_corpus=str(c["ris_id"] in record_file)) for c in reader]
        columns = [*reader.fieldnames, "in_corpus"]
    with (out / "candidates.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(candidates)
    relations = list(read_jsonl(data / "processed/links/relations.jsonl"))
    (out / "examples.json").write_text(json.dumps(examples(cfg, rows, record_file, out, relations),
                                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "record.schema.json").write_text(json.dumps(record_schema(), ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    total = sum(p.stat().st_size for p in out.iterdir())
    print(f"{len(rows):,} records in {len(results)} record files; {size(total)} -> {out}")
    return out


# --- the agent corpus, published on its own -------------------------------------------------------------------------

AGENT_COLUMNS = ["ris_id", "decision_date", "split", "outcome", "protection", "norms"]


def _agent_year(path: str, split: dict[str, str], out: str) -> Counter:
    """The records of one year file of the agent corpus texts, each with its split, into <split>/<year>.jsonl.gz."""
    year, writers, counts = Path(path).name.split(".")[0], {}, Counter()
    for r in read_jsonl(Path(path)):
        s = split[r["ris_id"]]
        if s not in writers:
            writers[s] = GzipLines(Path(out) / s / f"{year}.jsonl.gz")
        record = {k: r[k] for k in ("ris_id", "decision_date", "case_number")} | {"split": s} | r
        writers[s].write(json.dumps(record, ensure_ascii=False) + "\n")
        counts[s] += 1
    for w in writers.values():
        w.close()
    return counts


def agent_corpus(out: Path, workers: int = 4) -> Path:
    """The agent corpus as published on its own: its records by split and year (<split>/<year>.jsonl.gz, the texts of
    stage 09 with the split) and decisions.csv, one row per record. The dataset card is written by hand."""
    folder = data_dir() / "processed" / "agent_corpus"
    rows = read_csv(require(folder / "agent_corpus.csv", "stage 09"))
    files = year_files(folder / "texts", "stage 09")
    prepare(out)
    split = {r["ris_id"]: r["split"] for r in rows}
    with ProcessPoolExecutor(workers) as pool:
        counts = sum(pool.map(_agent_year, files, [split] * len(files), [str(out)] * len(files)), Counter())
    if sum(counts.values()) != len(rows):
        raise SystemExit(f"agent corpus: {sum(counts.values())} records written of {len(rows)}")
    with (out / "decisions.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=AGENT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows):,} records ({', '.join(f'{s} {n:,}' for s, n in sorted(counts.items()))}) -> {out}")
    return out


def checksums(folder: Path) -> Path:
    """SHA256SUMS over every file of the package folder (the format of `sha256sum -c`), once the README, LICENSE and
    CITATION.cff of the data are in it."""
    files = sorted(p for p in Path(folder).iterdir() if p.is_file() and p.name != "SHA256SUMS")
    path = Path(folder) / "SHA256SUMS"
    path.write_text("".join(f"{file_sha256(p)}  {p.name}\n" for p in files), encoding="utf-8")
    print(f"{len(files)} files -> {path}")
    return path
