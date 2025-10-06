# scripts/run_llm_demo.py
from src.llm.llm_interface import generate_response

ctx = {
    "drugs": {"a":{"name":"warfarin","ids":{}}, "b":{"name":"fluconazole","ids":{}}},
    "signals": {"mechanistic":{"enzymes":{
        "a":{"substrate":["CYP2C9"],"inhibitor":[],"inducer":[]},
        "b":{"substrate":[],"inhibitor":["CYP2C9"],"inducer":[]}
    }}},
    "sources": {"duckdb":["TwoSides"], "qlever":["PubChem RDF subset"], "openfda":["FAERS"]},
}

out = generate_response(ctx, "Doctor", seed=42)
print(out["text"])
print("\n--- meta:", out["meta"])
