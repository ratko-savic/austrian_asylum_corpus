# Austrian Asylum Corpus

Code and rules that build the Austrian Asylum Corpus from the open data of the Legal Information System of the
Republic of Austria (RIS). The corpus holds the asylum decisions of the Federal Administrative Court (BVwG) since 2014,
with the decisions of the Administrative Court (VwGH) and the Constitutional Court (VfGH) whose text mentions the BVwG;
those that review or cite a corpus decision are marked connected.

Every step is a deterministic rule, written down with its reason in `config/` or in the code that applies it; the same
input and code give the same bytes (with the same zlib, and for the judge IDs the same salt; a new download gets the
state of RIS on its day). The data itself, with its documentation, is published separately:
https://doi.org/10.5281/zenodo.23809754, where its README describes the record.

## Stages

Each stage is one script. Its rules are in `config/`, its output formats in `schemas/`, and its output goes to the data
folder.

| Stage | Script | Rules | Output |
|---|---|---|---|
| 01 download | `scripts/01_download.py` | `config/ris/` | `raw/`, `interim/candidates/`, `interim/connected/` |
| 02 filter | `scripts/02_filter.py` | `config/filtering/` | `interim/filtered/` |
| 03 parse | `scripts/03_parse.py` | – (the parser, `src/asylum_corpus/parsing/`) | `processed/documents/<court>/` |
| 04 head | `scripts/04_head.py` | `config/head/` | `processed/head/bvwg/` |
| 05 segment | `scripts/05_segment.py segment`, `fine` | `config/segmentation/` | `processed/sections/bvwg/`, `processed/fine_sections/bvwg/` |
| 06 citations | `scripts/06_citations.py` | `config/citations/` | `processed/citations/<court>/` |
| 07 links | `scripts/07_links.py` | `config/links/` | `processed/links/` |
| 08 outcomes | `scripts/08_outcomes.py` | `config/outcomes/` | `processed/outcomes/<court>/` |
| 09 agent corpus | `scripts/09_agent_corpus.py` | `config/agent_corpus.yaml` | `processed/agent_corpus/` |
| 10 package | `scripts/10_package.py` | `config/package/` | `package/` |

## Running it

Python 3.12 or later on Linux or macOS. Install the code in place (`-e`): the stages find `config/` and `schemas/`
next to `src/`.

```
python -m venv .venv && . .venv/bin/activate && pip install -e ".[test]"
export ASYLUM_CORPUS_DATA=/path/to/data          # default: data/ next to the code
python -m pytest
```

The runs, in this order:

```
python scripts/01_download.py inventory
python scripts/01_download.py download
python scripts/01_download.py connected          # after the complete BVwG download
python scripts/02_filter.py
python scripts/03_parse.py
python scripts/03_parse.py --court vwgh
python scripts/03_parse.py --court vfgh
python scripts/05_segment.py segment
python scripts/05_segment.py fine
python scripts/04_head.py                        # reads the coarse sections
python scripts/06_citations.py
python scripts/06_citations.py --court vwgh
python scripts/06_citations.py --court vfgh
python scripts/07_links.py
python scripts/08_outcomes.py
python scripts/08_outcomes.py --court vwgh
python scripts/08_outcomes.py --court vfgh
python scripts/07_links.py --table               # needs the outcomes of the three courts
python scripts/09_agent_corpus.py
python scripts/10_package.py --out /path/to/package
# add the data's README.md, LICENSE and CITATION.cff, written by hand, to the package folder
python scripts/10_package.py --checksums /path/to/package
```

Stage 01 sends requests to RIS and follows its usage rules (`config/ris/client.yaml`): at most one request every two
seconds, no parallel connections, and bulk downloads only outside office hours, announced in advance. It refuses a bulk
run until `contact` and `bulk_announced_on` are set there. Every run resumes where the last one stopped. At that pace
the full download takes days: about 170,000 texts took ten days inside the allowed hours. Stages 02 to 09 then take
about two hours with four workers; the data folder needs about 60 GB, the package another 7 GB.

Stage 10 needs `ASYLUM_CORPUS_JUDGE_SALT`, a secret string of your choice from which the pseudonymous judge IDs are
computed. Keep it, and keep it secret: another salt gives other IDs, and with the salt the IDs can be traced back to
the names.

## The rules

- **Filter:** RIS metadata only. Ordered rules over the RIS norms, keywords and decision types; the first match sets
  the verdict and the group.
- **Structure:** headings matched by their whole title and their outline label. Fine sections follow the court's
  numbering, its "Zu …" headings and its point headings. Quoted material (country reports, provisions, hearing
  records) gives no sections.
- **Citations:** a provision chain followed by an act named in `config/citations/acts.yaml`, an EU act number or
  "leg. cit."; decisions by their case number, ECLI, VfSlg, CJEU or ECtHR number; law-gazette and EU act numbers.
  Besides the acts listed first, an act is admitted under a name when that name follows a provision in at least 100
  decisions, or when it is needed to read another act correctly.
- **Outcomes:** the operative part read sentence by sentence into dispositions with their types and issues. Numbered
  points are resolved from the reasoning. One appellant's judgment and order form one unit.
- **Agent corpus:** the decisions in which facts, legal assessment, outcome and cited norms can be told apart most
  reliably. The criteria are in `config/agent_corpus.yaml`, in the order they are applied.
- **Package:** one JSON record per decision, joining what the stages found in it, each checked against the record
  schema, with a table of all records, the download candidates, examples and the record schema; then SHA256SUMS over
  the package with its README, LICENSE and CITATION.cff, which are written by hand. The structured data holds no name
  of the judge or a representative: the judge becomes a pseudonymous ID, and representatives keep their kind and their
  position in the text.

## License

The code is under the MIT License (`LICENSE`); cite it as in `CITATION.cff`. The data is under CC BY 4.0, like its source, the RIS.
