"""CustomResourceDefinitions -> JSON schemas for kubeconform, strict.

    python3 scripts/crd2schema.py OUT_DIR FILE... (YAML streams; non-CRD documents are ignored)

Writes OUT_DIR/<group>/<kind>_<version>.json, for kubeconform's
-schema-location 'OUT_DIR/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'.
Objects that list their properties get additionalProperties: false, so a misspelt field
(nodeSelecter, versoin) fails validation instead of being silently dropped by the API server.
Needs PyYAML.
"""

import json
import sys
from pathlib import Path

import yaml


def strict(s):
    if isinstance(s, dict):
        if (s.get("type") == "object" and "properties" in s and "additionalProperties" not in s
                and not s.get("x-kubernetes-preserve-unknown-fields")):
            s["additionalProperties"] = False
        for v in s.values():
            strict(v)
    elif isinstance(s, list):
        for v in s:
            strict(v)
    return s


def main(out: str, files: list[str]) -> int:
    n = 0
    for path in files:
        with open(path) as fh:
            for d in yaml.safe_load_all(fh):
                if not isinstance(d, dict) or d.get("kind") != "CustomResourceDefinition":
                    continue
                group, kind = d["spec"]["group"], d["spec"]["names"]["kind"].lower()
                for v in d["spec"]["versions"]:
                    schema = (v.get("schema") or {}).get("openAPIV3Schema")
                    if not schema:
                        continue
                    schema = strict(schema)
                    # apiVersion, kind and metadata are checked by the API server, not the CRD schema
                    for k in ("apiVersion", "kind", "metadata"):
                        schema.setdefault("properties", {}).setdefault(k, {"type": "string" if k != "metadata" else "object"})
                    target = Path(out) / group / f"{kind}_{v['name']}.json"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps(schema))
                    n += 1
    print(f"{n} schemas in {out}")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
