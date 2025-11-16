"""
KEGG REST API client for drug pathways and metabolism maps.

KEGG provides:
- Drug pathways and metabolism networks
- Enzyme interactions (CYPs, metabolism)
- Drug-drug interaction pathways
- Metabolic pathway maps

API Documentation: https://www.kegg.jp/kegg/rest/keggapi.html
"""
import os
import requests
import logging
from typing import Dict, List, Optional, Any
from functools import lru_cache
import time

LOG = logging.getLogger(__name__)

# KEGG REST API base URL
KEGG_API_BASE = "https://rest.kegg.jp"

# Timeout for API requests
KEGG_TIMEOUT = int(os.getenv("KEGG_TIMEOUT", "15"))

# Rate limiting: KEGG recommends reasonable use (no strict limit, but be polite)
KEGG_RATE_LIMIT = 0.2  # 200ms between requests


def _rate_limit():
    """Simple rate limiting to avoid overwhelming KEGG API."""
    time.sleep(KEGG_RATE_LIMIT)


@lru_cache(maxsize=1024)
def get_drug_pathways(drug_name: str) -> List[Dict[str, Any]]:
    """
    Get KEGG pathways associated with a drug.
    
    Args:
        drug_name: Drug name (e.g., "warfarin", "fluconazole")
    
    Returns:
        List of pathway dictionaries with:
        - pathway_id: KEGG pathway ID (e.g., "hsa00980")
        - pathway_name: Pathway name
        - drug_id: KEGG drug ID (e.g., "D01412")
    """
    if not drug_name or not drug_name.strip():
        return []
    
    pathways = []
    
    try:
        _rate_limit()
        # Step 1: Find drug ID by name
        url = f"{KEGG_API_BASE}/find/drug/{drug_name}"
        r = requests.get(url, timeout=KEGG_TIMEOUT)
        
        if r.status_code == 200:
            lines = r.text.strip().split("\n")
            drug_ids = []
            for line in lines:
                if "\t" in line:
                    drug_id, name = line.split("\t", 1)
                    drug_ids.append(drug_id.strip())
            
            # Step 2: For each drug ID, get pathways
            for drug_id in drug_ids[:3]:  # Limit to first 3 matches
                try:
                    _rate_limit()
                    url2 = f"{KEGG_API_BASE}/link/pathway/{drug_id}"
                    r2 = requests.get(url2, timeout=KEGG_TIMEOUT)
                    
                    if r2.status_code == 200:
                        lines2 = r2.text.strip().split("\n")
                        for line2 in lines2:
                            if "\t" in line2:
                                pathway_id, _ = line2.split("\t", 1)
                                pathway_name = get_pathway_name(pathway_id)
                                pathways.append({
                                    "drug_id": drug_id,
                                    "pathway_id": pathway_id.strip(),
                                    "pathway_name": pathway_name,
                                })
                except Exception as e:
                    LOG.debug("KEGG pathway query failed for %s: %s", drug_id, e)
    except Exception as e:
        LOG.debug("KEGG drug search failed for %s: %s", drug_name, e)
    
    return pathways


@lru_cache(maxsize=512)
def get_pathway_name(pathway_id: str) -> str:
    """
    Get human-readable pathway name from KEGG pathway ID.
    
    Args:
        pathway_id: KEGG pathway ID (e.g., "hsa00980")
    
    Returns:
        Pathway name
    """
    if not pathway_id or not pathway_id.strip():
        return ""
    
    try:
        _rate_limit()
        url = f"{KEGG_API_BASE}/get/{pathway_id}"
        r = requests.get(url, timeout=KEGG_TIMEOUT)
        
        if r.status_code == 200:
            lines = r.text.split("\n")
            for line in lines:
                if line.startswith("NAME"):
                    # Extract name after "NAME"
                    name = line.replace("NAME", "").strip()
                    return name
    except Exception as e:
        LOG.debug("KEGG pathway name query failed for %s: %s", pathway_id, e)
    
    return pathway_id


@lru_cache(maxsize=512)
def get_drug_enzymes(drug_name: str) -> List[Dict[str, Any]]:
    """
    Get enzymes (CYPs, etc.) associated with a drug in KEGG.
    
    Args:
        drug_name: Drug name
    
    Returns:
        List of enzyme dictionaries with:
        - enzyme_id: KEGG enzyme ID (e.g., "1.14.14.1")
        - enzyme_name: Enzyme name
        - drug_id: KEGG drug ID
    """
    if not drug_name or not drug_name.strip():
        return []
    
    enzymes = []
    
    try:
        _rate_limit()
        # Find drug ID
        url = f"{KEGG_API_BASE}/find/drug/{drug_name}"
        r = requests.get(url, timeout=KEGG_TIMEOUT)
        
        if r.status_code == 200:
            lines = r.text.strip().split("\n")
            drug_ids = []
            for line in lines:
                if "\t" in line:
                    drug_id, _ = line.split("\t", 1)
                    drug_ids.append(drug_id.strip())
            
            # Get enzymes for each drug
            for drug_id in drug_ids[:3]:
                try:
                    _rate_limit()
                    url2 = f"{KEGG_API_BASE}/link/enzyme/{drug_id}"
                    r2 = requests.get(url2, timeout=KEGG_TIMEOUT)
                    
                    if r2.status_code == 200:
                        lines2 = r2.text.strip().split("\n")
                        for line2 in lines2:
                            if "\t" in line2:
                                enzyme_id, _ = line2.split("\t", 1)
                                enzyme_name = get_enzyme_name(enzyme_id.strip())
                                enzymes.append({
                                    "drug_id": drug_id,
                                    "enzyme_id": enzyme_id.strip(),
                                    "enzyme_name": enzyme_name,
                                })
                except Exception as e:
                    LOG.debug("KEGG enzyme query failed for %s: %s", drug_id, e)
    except Exception as e:
        LOG.debug("KEGG drug enzyme search failed for %s: %s", drug_name, e)
    
    return enzymes


@lru_cache(maxsize=512)
def get_enzyme_name(enzyme_id: str) -> str:
    """
    Get enzyme name from EC number.
    
    Args:
        enzyme_id: EC number (e.g., "1.14.14.1")
    
    Returns:
        Enzyme name
    """
    if not enzyme_id or not enzyme_id.strip():
        return ""
    
    try:
        _rate_limit()
        url = f"{KEGG_API_BASE}/get/ec:{enzyme_id}"
        r = requests.get(url, timeout=KEGG_TIMEOUT)
        
        if r.status_code == 200:
            lines = r.text.split("\n")
            for line in lines:
                if line.startswith("NAME"):
                    name = line.replace("NAME", "").strip()
                    return name
    except Exception as e:
        LOG.debug("KEGG enzyme name query failed for %s: %s", enzyme_id, e)
    
    return enzyme_id


@lru_cache(maxsize=256)
def get_metabolism_pathway(drug_name: str) -> Optional[Dict[str, Any]]:
    """
    Get drug metabolism pathway information.
    
    Args:
        drug_name: Drug name
    
    Returns:
        Dictionary with metabolism information:
        - pathways: List of metabolism pathways
        - enzymes: List of metabolizing enzymes
    """
    pathways = get_drug_pathways(drug_name)
    enzymes = get_drug_enzymes(drug_name)
    
    # Filter for metabolism-related pathways
    metabolism_pathways = [
        p for p in pathways
        if any(term in (p.get("pathway_name", "") or "").lower()
               for term in ["metabolism", "drug", "xenobiotic", "cyp", "metabolic"])
    ]
    
    return {
        "pathways": metabolism_pathways,
        "enzymes": enzymes,
    }


def get_common_pathways(drug_a: str, drug_b: str) -> List[Dict[str, Any]]:
    """
    Find common pathways between two drugs.
    
    Args:
        drug_a: First drug name
        drug_b: Second drug name
    
    Returns:
        List of common pathway dictionaries
    """
    pathways_a = get_drug_pathways(drug_a)
    pathways_b = get_drug_pathways(drug_b)
    
    # Find common pathways
    pathway_ids_a = {p["pathway_id"] for p in pathways_a}
    pathway_ids_b = {p["pathway_id"] for p in pathways_b}
    common_ids = pathway_ids_a & pathway_ids_b
    
    # Return common pathways with names
    common_pathways = []
    for p in pathways_a + pathways_b:
        if p["pathway_id"] in common_ids:
            if p not in common_pathways:
                common_pathways.append(p)
    
    return common_pathways

