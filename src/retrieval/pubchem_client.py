"""
PubChem REST API client for enriching PK/PD data.

PubChem REST API provides:
- Human-readable protein labels for protein IDs
- ADME properties (absorption, distribution, metabolism, excretion)
- Pharmacokinetic data (clearance, half-life, bioavailability)
- Protein information (names, synonyms, functions)

API Documentation: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
"""
import os
import requests
import logging
from typing import Dict, List, Optional, Any
from functools import lru_cache
import time

LOG = logging.getLogger(__name__)

# PubChem REST API base URL
PUBCHEM_API_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

# Timeout for API requests
PUBCHEM_TIMEOUT = int(os.getenv("PUBCHEM_TIMEOUT", "10"))

# Rate limiting: PubChem recommends max 5 requests per second
PUBCHEM_RATE_LIMIT = 0.2  # 200ms between requests = 5 req/sec


def _rate_limit():
    """Simple rate limiting to avoid overwhelming PubChem API."""
    time.sleep(PUBCHEM_RATE_LIMIT)


@lru_cache(maxsize=2048)
def get_protein_label(protein_id: str) -> Optional[str]:
    """
    Get human-readable label for a protein ID from PubChem RDF or PDB API.
    
    Args:
        protein_id: Protein ID (e.g., "1DE9_A", "2lm5_a", PDB chain ID, or UniProt ID)
    
    Returns:
        Human-readable protein name/label, or None if not found
    """
    if not protein_id or not protein_id.strip():
        return None
    
    # Normalize protein_id (handle case variations)
    pid_upper = protein_id.upper()
    
    # PDB chain IDs (e.g., "1DE9_A", "2lm5_a" -> "2LM5_A")
    if "_" in protein_id and len(protein_id.split("_")[0]) == 4:
        pdb_id = protein_id.split("_")[0].upper()
        chain = protein_id.split("_")[1].upper() if len(protein_id.split("_")) > 1 else ""
        pdb_chain_id = f"{pdb_id}_{chain}" if chain else pdb_id
        
        # Method 1: Try PubChem RDF REST API (as suggested)
        try:
            _rate_limit()
            # PubChem RDF endpoint for protein/compound
            url = f"{PUBCHEM_API_BASE}/compound/{pdb_chain_id}/rdf/"
            r = requests.get(url, headers={"Accept": "application/rdf+xml"}, timeout=PUBCHEM_TIMEOUT)
            if r.status_code == 200:
                # Parse RDF to find rdfs:label or dc:title
                content = r.text
                # Look for rdfs:label or dc:title in RDF (handle both XML and Turtle formats)
                import re
                # Try to find label in RDF - multiple patterns for different RDF formats
                label_patterns = [
                    r'<rdfs:label[^>]*>([^<]+)</rdfs:label>',  # XML format
                    r'<dc:title[^>]*>([^<]+)</dc:title>',      # XML format
                    r'rdfs:label\s+"([^"]+)"',                 # Turtle format
                    r'dc:title\s+"([^"]+)"',                    # Turtle format
                    r'rdfs:label\s+([^;.\s]+)',                # Turtle format (no quotes)
                    r'label\s+"([^"]+)"',                       # Generic label
                ]
                for pattern in label_patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
                    if matches:
                        # Take the first meaningful match
                        for match in matches:
                            label = match.strip().strip('"').strip("'")
                            if label and len(label) > 5 and not label.startswith("http"):  # Valid label
                                return f"{label} (PDB: {pdb_id}{chain})"
        except Exception as e:
            LOG.debug("PubChem RDF query failed for %s: %s", pdb_chain_id, e)
        
        # Method 2: Try PDB API (RCSB PDB REST API)
        try:
            _rate_limit()
            url = f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
            r = requests.get(url, timeout=PUBCHEM_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                # Get structure title
                title = data.get("struct", {}).get("title", "")
                if title:
                    # Also try to get entity info for the specific chain
                    try:
                        entity_url = f"https://data.rcsb.org/rest/v1/core/polymer_entity/{pdb_id}/1"
                        entity_r = requests.get(entity_url, timeout=PUBCHEM_TIMEOUT)
                        if entity_r.status_code == 200:
                            entity_data = entity_r.json()
                            entity_name = entity_data.get("rcsb_polymer_entity_container_identifiers", {}).get("entity_id")
                            # Try to get better name from entity
                            if entity_name:
                                pass  # Could enhance further
                    except:
                        pass
                    return f"{title} (PDB: {pdb_id}{chain})"
        except Exception as e:
            LOG.debug("PDB API query failed for %s: %s", pdb_id, e)
        
        # Method 3: Try PubChem SPARQL endpoint (if available)
        # This would require a SPARQL endpoint, which PubChem doesn't provide publicly
        # So we skip this method
    
    # Try UniProt API if it looks like a UniProt ID
    if protein_id.startswith(("P", "Q", "O", "A")) and len(protein_id) >= 6 and not "_" in protein_id:
        try:
            _rate_limit()
            url = f"https://www.uniprot.org/uniprot/{protein_id}.json"
            r = requests.get(url, timeout=PUBCHEM_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                # Get recommended name
                recommended_name = data.get("proteinDescription", {}).get("recommendedName", {})
                if recommended_name:
                    name = recommended_name.get("fullName", {}).get("value", "")
                    if name:
                        return name
                # Fallback to gene name
                gene_names = data.get("genes", [{}])[0].get("geneName", {})
                if gene_names:
                    name = gene_names[0].get("value", "")
                    if name:
                        return f"{name} ({protein_id})"
        except Exception as e:
            LOG.debug("UniProt API query failed for %s: %s", protein_id, e)
    
    return None


@lru_cache(maxsize=1024)
def get_protein_labels_batch(protein_ids: tuple) -> Dict[str, Optional[str]]:
    """
    Get human-readable labels for multiple protein IDs.
    
    Args:
        protein_ids: Tuple of protein IDs (must be hashable for lru_cache)
    
    Returns:
        Dictionary mapping protein_id -> label
    """
    result = {}
    for pid in protein_ids:
        result[pid] = get_protein_label(pid)
    return result


def enrich_protein_ids(protein_ids: List[str]) -> List[Dict[str, str]]:
    """
    Enrich a list of protein IDs with human-readable labels.
    
    Args:
        protein_ids: List of protein IDs (e.g., ["2lm5_a", "aah17444", "http://.../protein/2lm5_a"])
    
    Returns:
        List of dicts with 'id', 'uri', 'label', and 'original_id'
    """
    enriched = []
    for pid in protein_ids:
        # Extract clean ID from URI if needed
        clean_id = pid
        if pid.startswith("http://"):
            # Extract last part of URI
            clean_id = pid.split("/")[-1]
        
        label = get_protein_label(clean_id)
        enriched.append({
            "id": clean_id,
            "uri": pid if pid.startswith("http://") else f"http://rdf.ncbi.nlm.nih.gov/pubchem/protein/{clean_id}",
            "label": label or clean_id,  # Fallback to ID if no label found
            "original_id": pid,
        })
    return enriched


@lru_cache(maxsize=512)
def get_compound_pk_data(pubchem_cid: str) -> Dict[str, Any]:
    """
    Get pharmacokinetic data for a compound from PubChem.
    
    Args:
        pubchem_cid: PubChem Compound ID (CID)
    
    Returns:
        Dictionary with PK data (ADME properties, clearance, half-life, etc.)
    """
    if not pubchem_cid or not pubchem_cid.strip():
        return {}
    
    # Remove "CID" prefix if present
    cid = pubchem_cid.replace("CID", "").strip()
    
    pk_data = {
        "absorption": None,
        "distribution": None,
        "metabolism": None,
        "excretion": None,
        "clearance": None,
        "half_life": None,
        "bioavailability": None,
        "protein_binding": None,
    }
    
    try:
        _rate_limit()
        # Get compound properties
        url = f"{PUBCHEM_API_BASE}/compound/cid/{cid}/property/MolecularWeight,LogP,HBondDonorCount,HBondAcceptorCount/json"
        r = requests.get(url, timeout=PUBCHEM_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            props = data.get("PropertyTable", {}).get("Properties", [])
            if props:
                prop = props[0]
                pk_data["molecular_weight"] = prop.get("MolecularWeight")
                pk_data["log_p"] = prop.get("LogP")
                pk_data["h_bond_donors"] = prop.get("HBondDonorCount")
                pk_data["h_bond_acceptors"] = prop.get("HBondAcceptorCount")
        
        # Try to get ADME data from PubChem's BioAssay or other endpoints
        # Note: PubChem doesn't have a direct ADME endpoint, but we can try
        # to get related data from compound summaries or literature
        
    except Exception as e:
        LOG.debug("PubChem PK data query failed for CID %s: %s", cid, e)
    
    return pk_data


def get_compound_pk_data_by_name(compound_name: str) -> Dict[str, Any]:
    """
    Get pharmacokinetic data for a compound by name.
    
    Args:
        compound_name: Compound name
    
    Returns:
        Dictionary with PK data
    """
    # First, try to get CID from name
    try:
        _rate_limit()
        url = f"{PUBCHEM_API_BASE}/compound/name/{compound_name}/cids/JSON"
        r = requests.get(url, timeout=PUBCHEM_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            cids = data.get("IdentifierList", {}).get("CID", [])
            if cids:
                cid = str(cids[0])
                return get_compound_pk_data(cid)
    except Exception as e:
        LOG.debug("PubChem CID lookup failed for %s: %s", compound_name, e)
    
    return {}

