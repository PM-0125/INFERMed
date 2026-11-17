"""Reviewed unnumbered SPL layout profiles.

A content fingerprint binds the hierarchy to the complete inspected XML body.
New or changed label content needs a fresh layout review, not a guessed split.
The profile establishes boundaries only; publication checks still apply.
"""

import hashlib
from copy import deepcopy
import xml.etree.ElementTree as ET

FLUCONAZOLE_INTERACTION_BODY = (
    "abbbbdc2e9325f0d57bfcc1db3cbcab6fe91782cb3041ca688fda7e6e2529df9"
)


def reviewed_hierarchy(body, drug):
    body = deepcopy(body)
    body.tail = None
    if (
        drug != "fluconazole"
        or hashlib.sha256(ET.tostring(body, encoding="utf-8")).hexdigest()
        != FLUCONAZOLE_INTERACTION_BODY
    ):
        return None
    names = [
        "Oral contraceptives",
        "Cimetidine",
        "Antacid",
        "Hydrochlorothiazide",
        "Rifampin",
        "Warfarin",
        "Phenytoin",
        "Cyclosporine",
        "Zidovudine",
        "Theophylline",
        "Quinidine",
        "Oral hypoglycemics",
        "Tolbutamide",
        "Glipizide",
        "Glyburide",
        "Rifabutin",
        "Tacrolimus",
        "Midazolam",
        "Azithromycin",
        "Voriconazole",
        "Tofacitinib",
        "Abrocitinib",
    ]
    return {
        name.casefold(): (
            ["oral hypoglycemics"]
            if name in {"Tolbutamide", "Glipizide", "Glyburide"}
            else []
        )
        for name in names
    }
