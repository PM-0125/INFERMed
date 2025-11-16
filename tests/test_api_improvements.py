#!/usr/bin/env python3
"""
Test API improvements independently - shows what KEGG, UniProt, Reactome add
even when QLever is not available.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.retrieval import kegg_client as kg
from src.retrieval import uniprot_client as uc
from src.retrieval import reactome_client as rc

print("="*80)
print("TESTING API IMPROVEMENTS - Independent API Testing")
print("="*80)

# Test combinations
combinations = [
    ("warfarin", "fluconazole"),
    ("simvastatin", "clarithromycin"),
    ("omeprazole", "clopidogrel"),
]

for drug_a, drug_b in combinations:
    print(f"\n{'='*80}")
    print(f"Testing: {drug_a.upper()} + {drug_b.upper()}")
    print(f"{'='*80}\n")
    
    # 1. KEGG Pathways
    print("📊 KEGG PATHWAYS:")
    print("-" * 80)
    try:
        pathways_a = kg.get_drug_pathways(drug_a)
        pathways_b = kg.get_drug_pathways(drug_b)
        common_pathways = kg.get_common_pathways(drug_a, drug_b)
        
        print(f"{drug_a}: {len(pathways_a)} pathways")
        for p in pathways_a[:3]:
            print(f"  • {p.get('pathway_name', 'N/A')}")
        
        print(f"\n{drug_b}: {len(pathways_b)} pathways")
        for p in pathways_b[:3]:
            print(f"  • {p.get('pathway_name', 'N/A')}")
        
        print(f"\nCommon pathways: {len(common_pathways)}")
        for p in common_pathways[:3]:
            print(f"  • {p.get('pathway_name', 'N/A')}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # 2. KEGG Enzymes
    print(f"\n🔬 KEGG ENZYMES:")
    print("-" * 80)
    try:
        enzymes_a = kg.get_drug_enzymes(drug_a)
        enzymes_b = kg.get_drug_enzymes(drug_b)
        metabolism_a = kg.get_metabolism_pathway(drug_a)
        metabolism_b = kg.get_metabolism_pathway(drug_b)
        
        print(f"{drug_a}: {len(enzymes_a)} enzymes")
        for e in enzymes_a[:3]:
            enzyme_name = e.get('enzyme_name', 'N/A')
            print(f"  • {enzyme_name}")
            # Check if CYP
            if 'cyp' in enzyme_name.lower() or 'cytochrome' in enzyme_name.lower():
                print(f"    ✅ CYP enzyme detected!")
        
        print(f"\n{drug_b}: {len(enzymes_b)} enzymes")
        for e in enzymes_b[:3]:
            enzyme_name = e.get('enzyme_name', 'N/A')
            print(f"  • {enzyme_name}")
            if 'cyp' in enzyme_name.lower() or 'cytochrome' in enzyme_name.lower():
                print(f"    ✅ CYP enzyme detected!")
        
        if metabolism_a and metabolism_a.get('enzymes'):
            print(f"\n{drug_a} metabolism enzymes: {len(metabolism_a['enzymes'])}")
        if metabolism_b and metabolism_b.get('enzymes'):
            print(f"{drug_b} metabolism enzymes: {len(metabolism_b['enzymes'])}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # 3. UniProt (test with known CYPs)
    print(f"\n🧬 UNIPROT ENZYME INFO:")
    print("-" * 80)
    try:
        # Known CYP UniProt IDs
        cyps = {
            "CYP3A4": "P08684",
            "CYP2C9": "P11712",
            "CYP2D6": "P10632",
            "CYP2C19": "P33261",
        }
        
        for cyp_name, uniprot_id in cyps.items():
            info = uc.get_protein_info(uniprot_id)
            if info:
                enzyme_info = uc.get_enzyme_info(uniprot_id)
                print(f"{cyp_name} ({uniprot_id}):")
                print(f"  Name: {info.get('name', 'N/A')[:60]}")
                if enzyme_info.get('is_cyp'):
                    print(f"  ✅ Identified as CYP: {enzyme_info.get('cyp_family', 'N/A')}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # 4. Reactome (test with known protein)
    print(f"\n🔄 REACTOME PATHWAYS:")
    print("-" * 80)
    try:
        # Test with CYP3A4
        pathways = rc.get_pathways_for_protein("P08684")
        print(f"Pathways for CYP3A4 (P08684): {len(pathways)}")
        for p in pathways[:3]:
            print(f"  • {p.get('pathway_name', 'N/A')}")
    except Exception as e:
        print(f"❌ Error: {e}")

print(f"\n\n{'='*80}")
print("✅ API TESTING COMPLETE")
print("="*80)
print("\nSummary:")
print("• KEGG provides: Drug pathways, metabolism enzymes, common pathways")
print("• UniProt provides: Protein names, CYP identification, enzyme classification")
print("• Reactome provides: Mechanistic pathways for proteins")
print("\nThese APIs enhance the system even when QLever is not available!")

