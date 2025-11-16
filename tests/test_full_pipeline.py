#!/usr/bin/env python3
"""
Comprehensive test script for the full RAG pipeline.
Tests 5 drug combinations through DuckDB + OpenFDA + QLever (CORE, BIO, DISEASE).
Enhanced with UniProt, KEGG, and Reactome API integrations.
"""

import os
import sys
from typing import Dict, Any

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Set environment variables
os.environ["CORE_ENDPOINT"] = "http://localhost:7010/"
os.environ["DISEASE_ENDPOINT"] = "http://localhost:7011/"
os.environ["BIO_ENDPOINT"] = "http://localhost:7012/"
os.environ["QLEVER_TIMEOUT_CORE"] = "90"
os.environ["QLEVER_TIMEOUT_DISEASE"] = "90"
os.environ["QLEVER_TIMEOUT_BIO"] = "90"

from src.llm.rag_pipeline import retrieve_and_normalize
import logging

logging.basicConfig(level=logging.WARNING)

# Test drug pairs - diverse combinations
TEST_PAIRS = [
    ("warfarin", "fluconazole"),
    ("aspirin", "ibuprofen"),
    ("metformin", "insulin"),
    ("atorvastatin", "amiodarone"),
    ("digoxin", "furosemide"),
]

def test_pair(drugA: str, drugB: str) -> Dict[str, Any]:
    """Test a single drug pair and return summary."""
    print(f"\n{'='*80}")
    print(f"Testing: {drugA.upper()} + {drugB.upper()}")
    print(f"{'='*80}")
    
    try:
        ctx = retrieve_and_normalize(
            drugA, drugB,
            parquet_dir='data/duckdb',
            openfda_cache='data/openfda',
            topk_targets=32,
            topk_side_effects=25,
            topk_faers=10
        )
        
        # Extract key information
        drugs = ctx.get('drugs', {})
        mech = ctx.get('signals', {}).get('mechanistic', {})
        tabular = ctx.get('signals', {}).get('tabular', {})
        faers = ctx.get('signals', {}).get('faers', {})
        pkpd = ctx.get('pkpd', {})
        caveats = ctx.get('caveats', [])
        
        result = {
            "success": True,
            "drugs": {
                "a": {
                    "name": drugs.get("a", {}).get("name", ""),
                    "cid": drugs.get("a", {}).get("ids", {}).get("pubchem_cid", "N/A"),
                    "synonyms": len(drugs.get("a", {}).get("synonyms", [])),
                },
                "b": {
                    "name": drugs.get("b", {}).get("name", ""),
                    "cid": drugs.get("b", {}).get("ids", {}).get("pubchem_cid", "N/A"),
                    "synonyms": len(drugs.get("b", {}).get("synonyms", [])),
                },
            },
            "qlever": {
                "targets_a": len(mech.get("targets_a", [])),
                "targets_b": len(mech.get("targets_b", [])),
                "diseases_a": len(mech.get("diseases_a", [])),
                "diseases_b": len(mech.get("diseases_b", [])),
                "enzymes_a": {
                    "substrate": len(mech.get("enzymes", {}).get("a", {}).get("substrate", [])),
                    "inhibitor": len(mech.get("enzymes", {}).get("a", {}).get("inhibitor", [])),
                    "inducer": len(mech.get("enzymes", {}).get("a", {}).get("inducer", [])),
                },
                "enzymes_b": {
                    "substrate": len(mech.get("enzymes", {}).get("b", {}).get("substrate", [])),
                    "inhibitor": len(mech.get("enzymes", {}).get("b", {}).get("inhibitor", [])),
                    "inducer": len(mech.get("enzymes", {}).get("b", {}).get("inducer", [])),
                },
            },
            "duckdb": {
                "prr": tabular.get("prr"),
                "side_effects_a": len(tabular.get("side_effects_a", [])),
                "side_effects_b": len(tabular.get("side_effects_b", [])),
                "dili_a": tabular.get("dili_a", "unknown"),
                "dili_b": tabular.get("dili_b", "unknown"),
                "dict_a": tabular.get("dict_a", "unknown"),
                "dict_b": tabular.get("dict_b", "unknown"),
            },
            "openfda": {
                "faers_a": len(faers.get("top_reactions_a", [])),
                "faers_b": len(faers.get("top_reactions_b", [])),
                "faers_combo": len(faers.get("combo_reactions", [])),
            },
            "api_enhancements": {
                "kegg_pathways_a": len(mech.get("pathways_a", [])),
                "kegg_pathways_b": len(mech.get("pathways_b", [])),
                "common_pathways": len(mech.get("common_pathways", [])),
                "uniprot_enriched_targets": any("(" in str(t) and ")" in str(t) for t in (mech.get("targets_a", []) + mech.get("targets_b", []))),
                "pk_data_a": bool(mech.get("pk_data_a", {})),
                "pk_data_b": bool(mech.get("pk_data_b", {})),
            },
            "pkpd": {
                "pk_summary": pkpd.get("pk_summary", ""),
                "pd_summary": pkpd.get("pd_summary", ""),
                "has_enhanced_pathways": "Enhanced pathways" in pkpd.get("pd_summary", ""),
            },
            "caveats": len(caveats),
            "sources": ctx.get('sources', {}),
        }
        
        # Print summary
        print(f"\n✅ SUCCESS")
        print(f"\n📊 Drug Information:")
        print(f"  {drugA}: CID {result['drugs']['a']['cid']}, {result['drugs']['a']['synonyms']} synonyms")
        print(f"  {drugB}: CID {result['drugs']['b']['cid']}, {result['drugs']['b']['synonyms']} synonyms")
        
        print(f"\n🧬 QLever Data (CORE/BIO/DISEASE):")
        print(f"  Targets: {drugA}={result['qlever']['targets_a']}, {drugB}={result['qlever']['targets_b']}")
        print(f"  Diseases: {drugA}={result['qlever']['diseases_a']}, {drugB}={result['qlever']['diseases_b']}")
        print(f"  Enzymes {drugA}: S={result['qlever']['enzymes_a']['substrate']}, I={result['qlever']['enzymes_a']['inhibitor']}, Ind={result['qlever']['enzymes_a']['inducer']}")
        print(f"  Enzymes {drugB}: S={result['qlever']['enzymes_b']['substrate']}, I={result['qlever']['enzymes_b']['inhibitor']}, Ind={result['qlever']['enzymes_b']['inducer']}")
        
        print(f"\n💾 DuckDB Data:")
        print(f"  PRR: {result['duckdb']['prr']}")
        print(f"  Side Effects: {drugA}={result['duckdb']['side_effects_a']}, {drugB}={result['duckdb']['side_effects_b']}")
        print(f"  DILI: {drugA}={result['duckdb']['dili_a']}, {drugB}={result['duckdb']['dili_b']}")
        print(f"  DICT: {drugA}={result['duckdb']['dict_a']}, {drugB}={result['duckdb']['dict_b']}")
        
        print(f"\n🏥 OpenFDA/FAERS Data:")
        print(f"  Reactions: {drugA}={result['openfda']['faers_a']}, {drugB}={result['openfda']['faers_b']}, Combo={result['openfda']['faers_combo']}")
        
        print(f"\n✨ API Enhancements (UniProt, KEGG, Reactome):")
        api = result['api_enhancements']
        print(f"  KEGG Pathways: {drugA}={api['kegg_pathways_a']}, {drugB}={api['kegg_pathways_b']}, Common={api['common_pathways']}")
        print(f"  UniProt Enrichment: {'✅' if api['uniprot_enriched_targets'] else '❌'}")
        print(f"  PubChem PK Data: {drugA}={api['pk_data_a']}, {drugB}={api['pk_data_b']}")
        
        print(f"\n💊 PK/PD Summary:")
        pkpd_summary = result['pkpd']
        print(f"  PK: {pkpd_summary['pk_summary'][:100]}...")
        print(f"  PD: {pkpd_summary['pd_summary'][:100]}...")
        if pkpd_summary['has_enhanced_pathways']:
            print(f"  ✅ Enhanced pathways included in PD summary")
        
        if result['caveats'] > 0:
            print(f"\n⚠️  Caveats ({result['caveats']}):")
            for c in caveats[:3]:
                print(f"    - {c}")
        
        print(f"\n📚 Sources:")
        for source_type, sources in result['sources'].items():
            print(f"  {source_type}: {', '.join(sources)}")
        
        return result
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
        }

def main():
    """Run tests for all drug pairs."""
    print("\n" + "="*80)
    print("FULL PIPELINE TEST - 5 Drug Combinations")
    print("="*80)
    print("\nTesting: DuckDB + OpenFDA + QLever (CORE, BIO, DISEASE)")
    print("Enhanced with: UniProt + KEGG + Reactome APIs")
    print(f"Endpoints:")
    print(f"  CORE: {os.getenv('CORE_ENDPOINT')}")
    print(f"  BIO: {os.getenv('BIO_ENDPOINT')}")
    print(f"  DISEASE: {os.getenv('DISEASE_ENDPOINT')}")
    
    results = []
    for drugA, drugB in TEST_PAIRS:
        result = test_pair(drugA, drugB)
        results.append((drugA, drugB, result))
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    successful = sum(1 for _, _, r in results if r.get("success", False))
    print(f"\n✅ Successful: {successful}/{len(TEST_PAIRS)}")
    
    print(f"\n📊 QLever Data Coverage:")
    total_targets = sum(r.get("qlever", {}).get("targets_a", 0) + r.get("qlever", {}).get("targets_b", 0) 
                       for _, _, r in results if r.get("success"))
    total_diseases = sum(r.get("qlever", {}).get("diseases_a", 0) + r.get("qlever", {}).get("diseases_b", 0)
                        for _, _, r in results if r.get("success"))
    print(f"  Total targets retrieved: {total_targets}")
    print(f"  Total diseases retrieved: {total_diseases}")
    
    print(f"\n💾 DuckDB Data Coverage:")
    pairs_with_prr = sum(1 for _, _, r in results if r.get("success") and r.get("duckdb", {}).get("prr") is not None)
    print(f"  Pairs with PRR data: {pairs_with_prr}/{successful}")
    
    print(f"\n🏥 OpenFDA Data Coverage:")
    pairs_with_faers = sum(1 for _, _, r in results 
                          if r.get("success") and (r.get("openfda", {}).get("faers_a", 0) > 0 or 
                                                   r.get("openfda", {}).get("faers_b", 0) > 0))
    print(f"  Pairs with FAERS data: {pairs_with_faers}/{successful}")
    
    print(f"\n✨ API Enhancement Coverage:")
    pairs_with_kegg = sum(1 for _, _, r in results 
                         if r.get("success") and (r.get("api_enhancements", {}).get("kegg_pathways_a", 0) > 0 or 
                                                  r.get("api_enhancements", {}).get("kegg_pathways_b", 0) > 0))
    pairs_with_uniprot = sum(1 for _, _, r in results 
                            if r.get("success") and r.get("api_enhancements", {}).get("uniprot_enriched_targets", False))
    pairs_with_enhanced_pd = sum(1 for _, _, r in results 
                                 if r.get("success") and r.get("pkpd", {}).get("has_enhanced_pathways", False))
    print(f"  Pairs with KEGG pathways: {pairs_with_kegg}/{successful}")
    print(f"  Pairs with UniProt enrichment: {pairs_with_uniprot}/{successful}")
    print(f"  Pairs with enhanced PD pathways: {pairs_with_enhanced_pd}/{successful}")
    
    # Detailed results
    print(f"\n📋 Detailed Results:")
    for drugA, drugB, result in results:
        status = "✅" if result.get("success") else "❌"
        print(f"  {status} {drugA} + {drugB}")
        if result.get("success"):
            ql = result.get("qlever", {})
            print(f"      QLever: T={ql.get('targets_a', 0)}+{ql.get('targets_b', 0)}, "
                  f"D={ql.get('diseases_a', 0)}+{ql.get('diseases_b', 0)}")
            db = result.get("duckdb", {})
            print(f"      DuckDB: PRR={db.get('prr', 'N/A')}, SE={db.get('side_effects_a', 0)}+{db.get('side_effects_b', 0)}")
            of = result.get("openfda", {})
            print(f"      OpenFDA: {of.get('faers_a', 0)}+{of.get('faers_b', 0)}+{of.get('faers_combo', 0)}")
            api = result.get("api_enhancements", {})
            print(f"      API Enhancements: KEGG={api.get('kegg_pathways_a', 0)}+{api.get('kegg_pathways_b', 0)}, "
                  f"UniProt={'✅' if api.get('uniprot_enriched_targets') else '❌'}, "
                  f"Enhanced PD={'✅' if result.get('pkpd', {}).get('has_enhanced_pathways') else '❌'}")
    
    print("\n" + "="*80)
    print("TEST COMPLETE")
    print("="*80 + "\n")

if __name__ == "__main__":
    main()

