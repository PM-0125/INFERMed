"""
UniProt REST API client for protein-level data enrichment.

UniProt provides:
- Protein names, functions, and biological roles
- Enzyme classifications (CYPs, transporters)
- Target information for drug-protein interactions
- Protein families and domains

API Documentation: https://www.uniprot.org/help/api
"""
import os
import requests
import logging
from typing import Dict, List, Optional, Any
from functools import lru_cache
import time

LOG = logging.getLogger(__name__)

# UniProt REST API base URL
UNIPROT_API_BASE = "https://rest.uniprot.org"

# Timeout for API requests
UNIPROT_TIMEOUT = int(os.getenv("UNIPROT_TIMEOUT", "15"))

# Rate limiting: UniProt recommends max 3 requests per second
UNIPROT_RATE_LIMIT = 0.35  # 350ms between requests = ~3 req/sec


def _rate_limit():
    """Simple rate limiting to avoid overwhelming UniProt API."""
    time.sleep(UNIPROT_RATE_LIMIT)


@lru_cache(maxsize=2048)
def get_protein_info(uniprot_id: str) -> Dict[str, Any]:
    """
    Get comprehensive protein information from UniProt.
    
    Args:
        uniprot_id: UniProt accession (e.g., "P08684", "P11712")
    
    Returns:
        Dictionary with protein information:
        - name: Recommended protein name
        - gene_names: List of gene names
        - function: Protein function description
        - subcellular_location: Subcellular location
        - enzyme_classification: EC number if enzyme
        - pathways: Associated pathways
        - keywords: Protein keywords
    """
    if not uniprot_id or not uniprot_id.strip():
        return {}
    
    # Clean UniProt ID (remove version suffix if present)
    clean_id = uniprot_id.strip().split(".")[0]
    
    # UniProt IDs typically start with P, Q, O, A, or are 6+ alphanumeric
    if not (clean_id[0].isupper() and len(clean_id) >= 6):
        return {}
    
    try:
        _rate_limit()
        url = f"{UNIPROT_API_BASE}/uniprotkb/{clean_id}.json"
        r = requests.get(url, timeout=UNIPROT_TIMEOUT)
        
        if r.status_code == 200:
            data = r.json()
            
            # Extract recommended name
            protein_description = data.get("proteinDescription", {})
            recommended_name = protein_description.get("recommendedName", {})
            name = recommended_name.get("fullName", {}).get("value", "")
            
            # Extract gene names
            genes = data.get("genes", [])
            gene_names = []
            for gene in genes:
                gene_name_obj = gene.get("geneName", {})
                if gene_name_obj:
                    gene_names.append(gene_name_obj.get("value", ""))
            
            # Extract function
            comments = data.get("comments", [])
            function = ""
            for comment in comments:
                if comment.get("commentType") == "FUNCTION":
                    texts = comment.get("texts", [])
                    if texts:
                        function = texts[0].get("value", "")
                        break
            
            # Extract subcellular location
            subcellular_location = []
            for comment in comments:
                if comment.get("commentType") == "SUBCELLULAR LOCATION":
                    locations = comment.get("subcellularLocations", [])
                    for loc in locations:
                        location_obj = loc.get("location", {})
                        if location_obj:
                            subcellular_location.append(location_obj.get("value", ""))
            
            # Extract EC number (enzyme classification)
            ec_number = None
            for comment in comments:
                if comment.get("commentType") == "CATALYTIC ACTIVITY":
                    reaction = comment.get("reaction", {})
                    if reaction:
                        ec_number = reaction.get("ecNumber", "")
                        break
            
            # Extract pathways
            pathways = []
            for comment in comments:
                if comment.get("commentType") == "PATHWAY":
                    texts = comment.get("texts", [])
                    for text in texts:
                        pathways.append(text.get("value", ""))
            
            # Extract keywords
            keywords = []
            for keyword in data.get("keywords", []):
                keywords.append(keyword.get("name", ""))
            
            return {
                "uniprot_id": clean_id,
                "name": name,
                "gene_names": gene_names,
                "function": function,
                "subcellular_location": subcellular_location,
                "ec_number": ec_number,
                "pathways": pathways,
                "keywords": keywords,
            }
        elif r.status_code == 404:
            LOG.debug("UniProt entry not found: %s", clean_id)
        else:
            LOG.debug("UniProt API error for %s: status %d", clean_id, r.status_code)
    except Exception as e:
        LOG.debug("UniProt query failed for %s: %s", clean_id, e)
    
    return {}


@lru_cache(maxsize=1024)
def search_proteins_by_name(protein_name: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search UniProt by protein name or gene name.
    
    Args:
        protein_name: Protein or gene name to search
        limit: Maximum number of results
    
    Returns:
        List of protein info dicts
    """
    if not protein_name or not protein_name.strip():
        return []
    
    try:
        _rate_limit()
        # UniProt search API
        query = f"name:{protein_name} OR gene:{protein_name}"
        url = f"{UNIPROT_API_BASE}/uniprotkb/search"
        params = {
            "query": query,
            "format": "json",
            "size": limit,
            "fields": "accession,id,protein_name,gene_names,function,ec"
        }
        
        r = requests.get(url, params=params, timeout=UNIPROT_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            results = []
            for entry in data.get("results", [])[:limit]:
                results.append({
                    "uniprot_id": entry.get("primaryAccession", ""),
                    "name": entry.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {}).get("value", ""),
                    "gene_names": [g.get("value", "") for g in entry.get("genes", [{}])[0].get("geneName", {}).get("value", [])] if entry.get("genes") else [],
                })
            return results
    except Exception as e:
        LOG.debug("UniProt search failed for %s: %s", protein_name, e)
    
    return []


@lru_cache(maxsize=512)
def get_enzyme_info(uniprot_id: str) -> Dict[str, Any]:
    """
    Get enzyme-specific information (CYPs, transporters, etc.).
    
    Args:
        uniprot_id: UniProt accession
    
    Returns:
        Dictionary with enzyme information:
        - is_cyp: Whether this is a cytochrome P450 enzyme
        - cyp_family: CYP family (e.g., "CYP3A4")
        - is_transporter: Whether this is a transporter
        - transporter_type: Type of transporter
    """
    info = get_protein_info(uniprot_id)
    if not info:
        return {}
    
    result = {
        "is_cyp": False,
        "cyp_family": None,
        "is_transporter": False,
        "transporter_type": None,
    }
    
    # Check if CYP enzyme
    name_lower = (info.get("name", "") or "").lower()
    gene_names_lower = [g.lower() for g in info.get("gene_names", [])]
    keywords_lower = [k.lower() for k in info.get("keywords", [])]
    
    all_text = " ".join([name_lower] + gene_names_lower + keywords_lower)
    
    if "cytochrome p450" in all_text or "cyp" in all_text:
        result["is_cyp"] = True
        # Try to extract CYP family (e.g., CYP3A4)
        import re
        cyp_match = re.search(r"cyp\s*(\d+[a-z]?\d*)", all_text, re.IGNORECASE)
        if cyp_match:
            result["cyp_family"] = f"CYP{cyp_match.group(1).upper()}"
    
    # Check if transporter
    if any(t in all_text for t in ["transporter", "solute carrier", "slc", "abc transporter"]):
        result["is_transporter"] = True
        if "abc" in all_text:
            result["transporter_type"] = "ABC"
        elif "slc" in all_text or "solute carrier" in all_text:
            result["transporter_type"] = "SLC"
    
    return result


def enrich_protein_list(protein_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Enrich a list of protein IDs with UniProt information.
    
    Args:
        protein_ids: List of protein IDs (UniProt IDs, gene names, etc.)
    
    Returns:
        List of enriched protein info dicts
    """
    enriched = []
    for pid in protein_ids:
        if not pid or not pid.strip():
            continue
        
        # Try as UniProt ID first
        info = get_protein_info(pid)
        if not info:
            # Try searching by name
            search_results = search_proteins_by_name(pid, limit=1)
            if search_results:
                info = get_protein_info(search_results[0]["uniprot_id"])
        
        if info:
            enriched.append({
                "original_id": pid,
                **info
            })
    
    return enriched

