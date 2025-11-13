# PK/PD Data Assessment & System Readiness

## Executive Summary

✅ **System is FUNCTIONAL for testing** with current data sources, but has **LIMITATIONS** for complete PK/PD analysis.

---

## Current Data Coverage

### ✅ What We Have (Working Well)

1. **Pharmacodynamics (PD) - EXCELLENT**
   - ✅ **Protein Targets**: 32 targets per drug from BIO index (e.g., warfarin=32, fluconazole=32)
   - ✅ **Target Overlap Detection**: Working (common targets identified)
   - ✅ **DrugBank Targets**: Fallback from DuckDB when QLever unavailable
   - **Status**: Sufficient for PD analysis

2. **Real-World Signals - EXCELLENT**
   - ✅ **TwoSides PRR**: Pair interaction scores (e.g., warfarin+fluconazole=120.0)
   - ✅ **Side Effects**: 25 per drug from TwoSides
   - ✅ **Risk Scores**: DILI, DICT, DIQT from DuckDB
   - ✅ **FAERS Data**: 10 reactions per drug + combo from OpenFDA
   - **Status**: Comprehensive real-world evidence

3. **Drug Identification - GOOD**
   - ✅ **PubChem CIDs**: Retrieved from CORE index
   - ✅ **Synonyms**: Available
   - **Status**: Sufficient

### ⚠️ What's Missing (Critical Gaps)

1. **Pharmacokinetics (PK) - CRITICAL GAP**
   - ❌ **Enzyme Roles**: Empty (substrate/inhibitor/inducer not found)
   - ❌ **CYP Enzymes**: Found in BIO index but can't extract standard names (CYP3A4, CYP2C9, etc.)
   - ❌ **Transporter Data**: Not queried
   - **Impact**: **Cannot detect PK interactions** (inhibition, induction, competition)
   - **Reason**: 
     - PubChem CORE index doesn't have enzyme role attributes
     - BIO index has CYP proteins but:
       - Protein IDs are non-standard (e.g., `ACCYP_401673` vs `CYP3A4`)
       - No role information (substrate/inhibitor/inducer)
       - Need external mapping or different data source

2. **Disease Associations - MINOR GAP**
   - ❌ **Disease Links**: Empty (0 diseases found)
   - **Impact**: Low - diseases are nice-to-have but not critical for DDI analysis
   - **Reason**: Disease index may not have direct compound-disease links in expected format

---

## Why Enzymes Are Empty

### Root Cause Analysis

1. **CORE Index**: 
   - ✅ Has compound attributes (23 found for fluconazole)
   - ❌ **No CYP enzyme attributes** in expected format
   - ❌ No `sio:SIO_000008` attributes with CYP information

2. **BIO Index**:
   - ✅ **Has CYP proteins** (10 found for fluconazole: `ACCYP_401673`, `ACCYP_003708382`, etc.)
   - ❌ **Non-standard naming**: IDs like `ACCYP_401673` don't map to `CYP3A4`
   - ❌ **No role information**: Can't tell if substrate/inhibitor/inducer
   - ❌ **No labels/identifiers**: Most proteins have no `rdfs:label` or `dcterms:identifier`

3. **Data Structure Mismatch**:
   - PubChem RDF stores enzymes as **proteins in bioactivity data**
   - But doesn't encode **metabolic roles** (substrate/inhibitor/inducer)
   - Need **external knowledge base** (e.g., DrugBank, ChEMBL) for role information

---

## PK/PD Requirements vs Current State

### For Complete PK/PD Analysis, We Need:

| Component | Required | Current Status | Gap |
|-----------|----------|----------------|-----|
| **PK: Enzyme Substrates** | ✅ Critical | ❌ Empty | **HIGH** |
| **PK: Enzyme Inhibitors** | ✅ Critical | ❌ Empty | **HIGH** |
| **PK: Enzyme Inducers** | ✅ Critical | ❌ Empty | **HIGH** |
| **PK: Transporters** | ⚠️ Important | ❌ Not queried | **MEDIUM** |
| **PD: Protein Targets** | ✅ Critical | ✅ 32 per drug | **NONE** |
| **PD: Pathways** | ⚠️ Nice-to-have | ❌ Empty (reserved) | **LOW** |
| **Real-World: PRR** | ✅ Critical | ✅ Working | **NONE** |
| **Real-World: FAERS** | ✅ Critical | ✅ Working | **NONE** |
| **Risk Scores: DILI/DICT/DIQT** | ✅ Important | ✅ Working | **NONE** |

### Current PK/PD Capabilities

**What We CAN Do:**
- ✅ Detect **PD interactions** (target overlap)
- ✅ Provide **real-world signals** (PRR, FAERS)
- ✅ Assess **risk scores** (DILI, DICT, DIQT)
- ✅ Identify **drugs** and **synonyms**

**What We CANNOT Do:**
- ❌ Detect **PK interactions** (inhibition, induction, competition)
- ❌ Predict **exposure changes** (↑ or ↓ drug levels)
- ❌ Identify **metabolic pathways**
- ❌ Warn about **CYP-mediated interactions**

---

## Recommendations

### Option 1: Use External Enzyme Data (RECOMMENDED)
**Integrate DrugBank or ChEMBL for enzyme roles:**
- DrugBank XML (already in DuckDB) may have enzyme data
- Query DrugBank for CYP substrate/inhibitor/inducer information
- Merge with QLever target data

### Option 2: Improve BIO Index Extraction
**Better CYP name extraction from protein IDs:**
- Map `ACCYP_401673` → `CYP3A4` using lookup table
- Extract from protein identifiers more intelligently
- Still won't get roles (substrate/inhibitor/inducer)

### Option 3: Accept Limitation
**Document that PK analysis is limited:**
- System works well for **PD interactions** and **real-world signals**
- PK interactions require manual review or external sources
- Still valuable for clinical decision support

---

## System Readiness Assessment

### ✅ GREEN LIGHT FOR TESTING - With Caveats

**The system is READY for testing IF:**

1. **Primary Use Case**: PD interactions + real-world signals
   - ✅ System is **excellent** for this
   - ✅ Targets, PRR, FAERS all working

2. **Secondary Use Case**: Complete PK/PD analysis
   - ⚠️ System is **limited** for this
   - ❌ PK interactions won't be detected
   - ⚠️ Need to supplement with external enzyme data

3. **Clinical Use**: 
   - ⚠️ **Not ready** for unsupervised clinical use
   - ✅ **Ready** for research/testing with clear limitations documented

### Recommendations for Production

1. **Short Term** (Current State):
   - ✅ Test system with focus on **PD interactions** and **real-world signals**
   - ⚠️ Document PK limitations clearly in UI
   - ✅ Use for **research/validation** purposes

2. **Medium Term** (Improvements Needed):
   - 🔧 Integrate DrugBank enzyme data from DuckDB
   - 🔧 Add transporter queries to BIO index
   - 🔧 Improve CYP name extraction from protein IDs

3. **Long Term** (Full PK/PD):
   - 🔧 Integrate ChEMBL or other enzyme knowledge bases
   - 🔧 Add pathway queries to DISEASE or CORE index
   - 🔧 Build enzyme role inference from bioactivity data

---

## Test Results Summary

### 5 Drug Pairs Tested: ✅ 5/5 Successful

| Pair | Targets | Enzymes | PRR | FAERS | Status |
|------|---------|---------|-----|-------|--------|
| warfarin + fluconazole | 32+32 | 0+0 | 120.0 | ✅ | ✅ |
| aspirin + ibuprofen | 32+32 | 0+0 | 160.0 | ✅ | ✅ |
| metformin + insulin | 32+0 | 0+0 | None | ✅ | ✅ |
| atorvastatin + amiodarone | 32+32 | 0+0 | 90.0 | ✅ | ✅ |
| digoxin + furosemide | 32+0 | 0+0 | 252.7 | ✅ | ✅ |

**Total Targets Retrieved**: 256  
**Total Enzymes Retrieved**: 0  
**Pairs with PRR**: 4/5  
**Pairs with FAERS**: 5/5  

---

## Conclusion

**The system is FUNCTIONAL and READY for testing**, but with **clear limitations**:

✅ **Strengths**: PD analysis, real-world signals, risk assessment  
❌ **Weaknesses**: PK analysis (enzyme roles missing)  
⚠️ **Recommendation**: Proceed with testing, document limitations, plan enzyme data integration

**For PK/PD completeness**: Need to integrate external enzyme knowledge base (DrugBank/ChEMBL).

