"""
ChEMBL REST API client for enriching PK/PD data.

ChEMBL provides:
- Enzyme interactions with potency (Ki, IC50) for strength classification
- Transporter data (P-gp, OATP, etc.)
- Pathway information
- Cross-validation of DrugBank enzyme data

API Documentation: https://www.ebi.ac.uk/chembl/documentation/web-services
"""
import os
import requests
import logging
from typing import Dict, List, Optional, Any
from functools import lru_cache

LOG = logging.getLogger(__name__)

# ChEMBL REST API base URL
CHEMBL_API_BASE = "https://www.ebi.ac.uk/chembl/api/data"

# Timeout for API requests
CHEMBL_TIMEOUT = int(os.getenv("CHEMBL_TIMEOUT", "10"))


@lru_cache(maxsize=1024)
def _get_compound_by_name(compound_name: str) -> Optional[Dict[str, Any]]:
    """
    Search ChEMBL for a compound by name.
    Returns the first matching molecule record or None.
    """
    try:
        url = f"{CHEMBL_API_BASE}/molecule.json"
        params = {
            "molecule_synonyms__synonyms__icontains": compound_name,
            "format": "json",
            "limit": 1,
        }
        r = requests.get(url, params=params, timeout=CHEMBL_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        molecules = data.get("molecules", [])
        if molecules:
            return molecules[0]
    except Exception as e:
        LOG.debug("ChEMBL compound search failed for %s: %s", compound_name, e)
    return None


def get_enzyme_interactions(
    compound_name: str, enzyme_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Get enzyme interactions from ChEMBL with potency data.
    
    Args:
        compound_name: Drug name to search
        enzyme_name: Optional filter for specific enzyme (e.g., "CYP3A4")
    
    Returns:
        List of dicts with keys: enzyme, action, potency_type (Ki/IC50), potency_value, potency_units
    """
    molecule = _get_compound_by_name(compound_name)
    if not molecule:
        return []
    
    molecule_chembl_id = molecule.get("molecule_chembl_id")
    if not molecule_chembl_id:
        return []
    
    try:
        # Get bioactivities for this molecule
        url = f"{CHEMBL_API_BASE}/activity.json"
        params = {
            "molecule_chembl_id": molecule_chembl_id,
            "target_type": "SINGLE PROTEIN",
            "standard_type__in": "Ki,IC50,EC50",
            "standard_relation": "=",
            "format": "json",
            "limit": 100,
        }
        
        # Filter by enzyme name if provided
        if enzyme_name:
            # Normalize enzyme name (e.g., "CYP3A4" -> "cyp3a4", "cytochrome p450 3a4")
            enzyme_filter = enzyme_name.lower().replace("cyp", "").replace("cytochrome p450", "").strip()
            params["target_pref_name__icontains"] = enzyme_filter
        
        r = requests.get(url, params=params, timeout=CHEMBL_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        
        activities = data.get("activities", [])
        interactions = []
        
        for act in activities:
            target_pref_name = act.get("target_pref_name", "")
            standard_type = act.get("standard_type", "")
            standard_value = act.get("standard_value")
            standard_units = act.get("standard_units", "")
            
            # Extract enzyme name from target
            enzyme_match = None
            if "cyp" in target_pref_name.lower() or "cytochrome" in target_pref_name.lower():
                import re
                m = re.search(r"(?i)(?:cyp|cytochrome\s*p450)\s*(\d+[a-z]?\d*)", target_pref_name)
                if m:
                    enzyme_match = f"cyp{m.group(1).lower()}"
            
            if enzyme_match or enzyme_name:
                # Infer action from activity type and value
                # Low Ki/IC50 suggests inhibition; high suggests substrate
                action = "unknown"
                if standard_value:
                    try:
                        val = float(standard_value)
                        if standard_type in ("Ki", "IC50"):
                            # < 1 μM = strong inhibitor, 1-10 μM = moderate, > 10 μM = weak
                            if val < 1.0:
                                action = "strong_inhibitor"
                            elif val < 10.0:
                                action = "moderate_inhibitor"
                            else:
                                action = "weak_inhibitor"
                        elif standard_type == "EC50":
                            action = "substrate"
                    except (ValueError, TypeError):
                        pass
                
                interactions.append({
                    "enzyme": enzyme_match or target_pref_name.lower(),
                    "action": action,
                    "potency_type": standard_type,
                    "potency_value": standard_value,
                    "potency_units": standard_units,
                    "target_name": target_pref_name,
                })
        
        return interactions
    except Exception as e:
        LOG.debug("ChEMBL enzyme interaction query failed for %s: %s", compound_name, e)
        return []


def get_transporter_data(compound_name: str) -> List[Dict[str, str]]:
    """
    Get transporter interactions from ChEMBL.
    
    Returns:
        List of dicts with keys: transporter, action (substrate/inhibitor)
    """
    molecule = _get_compound_by_name(compound_name)
    if not molecule:
        return []
    
    molecule_chembl_id = molecule.get("molecule_chembl_id")
    if not molecule_chembl_id:
        return []
    
    # Common transporter names
    transporters = ["P-glycoprotein", "P-gp", "ABCB1", "OATP", "OCT", "OAT", "MATE", "BCRP"]
    
    try:
        url = f"{CHEMBL_API_BASE}/activity.json"
        params = {
            "molecule_chembl_id": molecule_chembl_id,
            "target_type": "SINGLE PROTEIN",
            "format": "json",
            "limit": 100,
        }
        
        r = requests.get(url, params=params, timeout=CHEMBL_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        
        activities = data.get("activities", [])
        transporter_data = []
        
        for act in activities:
            target_pref_name = act.get("target_pref_name", "").lower()
            standard_type = act.get("standard_type", "")
            standard_value = act.get("standard_value")
            
            # Check if this is a transporter
            is_transporter = any(t.lower() in target_pref_name for t in transporters)
            if not is_transporter:
                continue
            
            # Infer action
            action = "substrate"  # Default
            if standard_type in ("Ki", "IC50") and standard_value:
                try:
                    val = float(standard_value)
                    if val < 10.0:  # Low Ki/IC50 suggests inhibition
                        action = "inhibitor"
                except (ValueError, TypeError):
                    pass
            
            transporter_data.append({
                "transporter": target_pref_name,
                "action": action,
            })
        
        return transporter_data
    except Exception as e:
        LOG.debug("ChEMBL transporter query failed for %s: %s", compound_name, e)
        return []


def get_pathway_data(compound_name: str) -> List[str]:
    """
    Get pathway associations from ChEMBL.
    
    Returns:
        List of pathway names
    """
    molecule = _get_compound_by_name(compound_name)
    if not molecule:
        return []
    
    molecule_chembl_id = molecule.get("molecule_chembl_id")
    if not molecule_chembl_id:
        return []
    
    try:
        # Get pathways via target associations
        url = f"{CHEMBL_API_BASE}/pathway.json"
        params = {
            "molecule_chembl_id": molecule_chembl_id,
            "format": "json",
            "limit": 50,
        }
        
        r = requests.get(url, params=params, timeout=CHEMBL_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        
        pathways = data.get("pathways", [])
        return [p.get("pathway", "") for p in pathways if p.get("pathway")]
    except Exception as e:
        LOG.debug("ChEMBL pathway query failed for %s: %s", compound_name, e)
        return []


def enrich_mechanistic_data(
    drug_name: str, enzymes: Dict[str, List[str]]
) -> Dict[str, Any]:
    """
    Enrich enzyme data with ChEMBL potency information.
    
    Args:
        drug_name: Drug name
        enzymes: Dict with 'substrate', 'inhibitor', 'inducer' lists
    
    Returns:
        Enriched dict with strength classifications and ChEMBL cross-validation
    """
    enriched = {
        "enzymes": enzymes.copy(),
        "enzyme_strength": {"strong": [], "moderate": [], "weak": []},
        "chembl_validation": {"found": False, "matches": [], "mismatches": []},
    }
    
    # Get ChEMBL enzyme interactions
    chembl_interactions = get_enzyme_interactions(drug_name)
    
    if chembl_interactions:
        enriched["chembl_validation"]["found"] = True
        
        # Map ChEMBL data to our enzyme lists
        for interaction in chembl_interactions:
            enzyme = interaction["enzyme"]
            action = interaction["action"]
            potency_value = interaction.get("potency_value")
            
            # Classify strength
            if "strong" in action:
                enriched["enzyme_strength"]["strong"].append(enzyme)
            elif "moderate" in action:
                enriched["enzyme_strength"]["moderate"].append(enzyme)
            elif "weak" in action:
                enriched["enzyme_strength"]["weak"].append(enzyme)
            
            # Cross-validate with DrugBank
            if enzyme in enzymes.get("inhibitor", []):
                enriched["chembl_validation"]["matches"].append(enzyme)
            elif enzyme not in sum(enzymes.values(), []):
                enriched["chembl_validation"]["mismatches"].append(enzyme)
    
    return enriched

