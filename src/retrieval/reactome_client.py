"""
Reactome Pathway Database REST API client for mechanistic pathway analysis.

Reactome provides:
- Mechanistic biological pathways involving drug targets
- Pathway hierarchies and relationships
- Protein-protein interactions
- Disease-pathway associations

API Documentation: https://reactome.org/ContentService/
"""
import os
import requests
import logging
from typing import Dict, List, Optional, Any
from functools import lru_cache
import time

LOG = logging.getLogger(__name__)

# Reactome REST API base URL
REACTOME_API_BASE = "https://reactome.org/ContentService"

# Timeout for API requests
REACTOME_TIMEOUT = int(os.getenv("REACTOME_TIMEOUT", "15"))

# Rate limiting: Reactome recommends reasonable use
REACTOME_RATE_LIMIT = 0.2  # 200ms between requests


def _rate_limit():
    """Simple rate limiting to avoid overwhelming Reactome API."""
    time.sleep(REACTOME_RATE_LIMIT)


@lru_cache(maxsize=1024)
def get_pathways_for_protein(uniprot_id: str) -> List[Dict[str, Any]]:
    """
    Get Reactome pathways associated with a protein (UniProt ID).
    
    Args:
        uniprot_id: UniProt accession (e.g., "P08684")
    
    Returns:
        List of pathway dictionaries with:
        - pathway_id: Reactome pathway ID (e.g., "R-HSA-1234567")
        - pathway_name: Pathway name
        - pathway_species: Species (usually "Homo sapiens")
    """
    if not uniprot_id or not uniprot_id.strip():
        return []
    
    # Clean UniProt ID
    clean_id = uniprot_id.strip().split(".")[0]
    
    pathways = []
    
    try:
        _rate_limit()
        # Query pathways for protein
        url = f"{REACTOME_API_BASE}/query/mapping/uniprot/{clean_id}"
        params = {"species": "9606"}  # Homo sapiens
        
        r = requests.get(url, params=params, timeout=REACTOME_TIMEOUT)
        
        if r.status_code == 200:
            data = r.json()
            pathway_ids = []
            
            # Extract pathway IDs from response
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        pathways_list = item.get("pathways", [])
                        for pathway in pathways_list:
                            pathway_id = pathway.get("stId") or pathway.get("dbId")
                            if pathway_id:
                                pathway_ids.append(str(pathway_id))
            
            # Get pathway details
            for pathway_id in pathway_ids[:10]:  # Limit to top 10
                try:
                    _rate_limit()
                    pathway_info = get_pathway_info(pathway_id)
                    if pathway_info:
                        pathways.append(pathway_info)
                except Exception as e:
                    LOG.debug("Reactome pathway info query failed for %s: %s", pathway_id, e)
    except Exception as e:
        LOG.debug("Reactome pathway query failed for %s: %s", clean_id, e)
    
    return pathways


@lru_cache(maxsize=512)
def get_pathway_info(pathway_id: str) -> Optional[Dict[str, Any]]:
    """
    Get detailed information about a Reactome pathway.
    
    Args:
        pathway_id: Reactome pathway ID (e.g., "R-HSA-1234567")
    
    Returns:
        Dictionary with pathway information:
        - pathway_id: Pathway ID
        - pathway_name: Pathway name
        - pathway_species: Species
        - pathway_summary: Pathway summary/description
    """
    if not pathway_id or not pathway_id.strip():
        return None
    
    try:
        _rate_limit()
        url = f"{REACTOME_API_BASE}/data/query/{pathway_id}"
        params = {"species": "9606"}
        
        r = requests.get(url, params=params, timeout=REACTOME_TIMEOUT)
        
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                return {
                    "pathway_id": pathway_id,
                    "pathway_name": data.get("displayName", ""),
                    "pathway_species": "Homo sapiens",
                    "pathway_summary": data.get("summation", [{}])[0].get("text", "") if data.get("summation") else "",
                }
    except Exception as e:
        LOG.debug("Reactome pathway info query failed for %s: %s", pathway_id, e)
    
    return None


@lru_cache(maxsize=256)
def search_pathways_by_name(pathway_name: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search Reactome pathways by name.
    
    Args:
        pathway_name: Pathway name to search
        limit: Maximum number of results
    
    Returns:
        List of pathway info dicts
    """
    if not pathway_name or not pathway_name.strip():
        return []
    
    try:
        _rate_limit()
        url = f"{REACTOME_API_BASE}/search/query"
        params = {
            "query": pathway_name,
            "species": "9606",
            "types": "Pathway",
            "cluster": "true",
        }
        
        r = requests.get(url, params=params, timeout=REACTOME_TIMEOUT)
        
        if r.status_code == 200:
            data = r.json()
            results = []
            
            if isinstance(data, dict):
                results_list = data.get("results", [])
                for result in results_list[:limit]:
                    entries = result.get("entries", [])
                    for entry in entries[:limit]:
                        pathway_id = entry.get("stId") or entry.get("dbId")
                        if pathway_id:
                            pathway_info = get_pathway_info(str(pathway_id))
                            if pathway_info:
                                results.append(pathway_info)
            
            return results
    except Exception as e:
        LOG.debug("Reactome pathway search failed for %s: %s", pathway_name, e)
    
    return []


def get_common_pathways_for_proteins(uniprot_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Find common Reactome pathways for a list of proteins.
    
    Args:
        uniprot_ids: List of UniProt accessions
    
    Returns:
        List of common pathway dictionaries
    """
    if not uniprot_ids:
        return []
    
    # Get pathways for each protein
    all_pathways = {}
    for uniprot_id in uniprot_ids:
        pathways = get_pathways_for_protein(uniprot_id)
        for pathway in pathways:
            pathway_id = pathway.get("pathway_id")
            if pathway_id:
                if pathway_id not in all_pathways:
                    all_pathways[pathway_id] = {
                        "pathway": pathway,
                        "proteins": [],
                    }
                all_pathways[pathway_id]["proteins"].append(uniprot_id)
    
    # Find pathways shared by multiple proteins
    common_pathways = [
        {
            **all_pathways[pid]["pathway"],
            "shared_by_proteins": all_pathways[pid]["proteins"],
        }
        for pid in all_pathways
        if len(all_pathways[pid]["proteins"]) > 1
    ]
    
    return common_pathways


@lru_cache(maxsize=256)
def get_drug_target_pathways(drug_name: str, target_uniprot_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Get Reactome pathways for drug targets.
    
    Args:
        drug_name: Drug name (for logging)
        target_uniprot_ids: List of UniProt IDs for drug targets
    
    Returns:
        List of pathway dictionaries
    """
    if not target_uniprot_ids:
        return []
    
    # Get common pathways for all targets
    common_pathways = get_common_pathways_for_proteins(target_uniprot_ids)
    
    # Also get individual pathways (top pathways per target)
    all_pathways = {}
    for uniprot_id in target_uniprot_ids[:10]:  # Limit to avoid too many queries
        pathways = get_pathways_for_protein(uniprot_id)
        for pathway in pathways[:5]:  # Top 5 per target
            pathway_id = pathway.get("pathway_id")
            if pathway_id and pathway_id not in all_pathways:
                all_pathways[pathway_id] = pathway
    
    # Combine common and individual pathways
    result = common_pathways.copy()
    for pathway_id, pathway in all_pathways.items():
        if pathway not in result:
            result.append(pathway)
    
    return result[:20]  # Limit total results

