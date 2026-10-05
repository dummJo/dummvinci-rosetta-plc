# Vendored schema: PLCopen TC6 XML v2.01

`tc6_xml_v201.xsd` is the PLCopen TC6 XML schema, version 2.01
(target namespace `http://www.plcopen.org/xml/tc6_0201`), © PLCopen. It is
vendored unmodified so XSD validation is reproducible offline and in CI.

Provenance (two independent copies, identical after CRLF→LF normalisation):

| Source | Line endings | sha256 |
|---|---|---|
| `raw.githubusercontent.com/beremiz/beremiz/master/plcopen/tc6_xml_v201.xsd` (this file) | CRLF | `591b92ba65018a77c32ab9e606abf27bd810cb1f7761d972a85689531c51e20f` |
| PyPI `plcopen==1.0.1` (MIT), `src/plcopen/tc6_xml_v201.xsd` | LF | `db546a6d1847fee06268fa29baf9f643c8bdd75b3ad083eb9ebd4bfa93cb6c9b` |

`tests/test_codesys.py::test_vendored_xsd_is_the_official_tc6_v201` pins the
normalised hash.

## What validation proves — and what it does not

CODESYS-family IDEs write and read the v2.00 namespace (`tc6_0200`). For the
subset this tool emits (project header, typed POU interfaces, ST bodies,
`configuration/resource/globalVars`, `addData`), v2.00 and v2.01 are
structurally the same, so the tests validate against v2.01 with the namespace
aligned.

**Schema-valid is not import-proven.** The previous emitter was schema-valid
too, while putting `PROGRAM … VAR … END_VAR` inside the ST body, which an IDE
import would reject. Content under `addData` (the CODESYS GVL encoding) is
validated only as "well-formed, lax". Real import evidence is tracked in
`feature_list.json` as `codesys-ide-import-validation`, and that item stays
`false` until someone records an actual IDE import.
