# Enhanced Follow-Up Question Handling and Completeness Fix

## Issues Identified

1. **Follow-up questions not directly answered**: The system was still giving general interaction assessments instead of directly addressing the specific clinical scenario (e.g., high blood pressure).

2. **Missing details in initial assessment**: The first answer was missing important information like QT prolongation and hepatotoxicity that appeared in the follow-up answer.

## Root Causes

1. **Follow-up instructions not strong enough**: The instructions were present but not emphatic enough to override the template's default structure.

2. **Template not emphasizing all available information**: The template didn't explicitly instruct the LLM to include ALL risk flags (DIQT, DILI) and ALL FAERS signals in the initial assessment.

## Solutions Implemented

### 1. Enhanced Follow-Up Question Instructions

**Before:**
- Generic instruction to "answer directly"
- No specific structure for clinical scenarios

**After:**
- **CRITICAL** section header (more emphatic)
- **MANDATORY INSTRUCTIONS** with numbered steps
- Automatic detection of clinical conditions (high BP, diabetes, renal/hepatic impairment, etc.)
- Specific instructions for each detected condition
- Required answer structure:
  - Start with direct answer
  - List specific problems/risks
  - Explain condition-drug interaction
  - Provide condition-specific monitoring

**Example:**
```
## CRITICAL: ANSWER THIS SPECIFIC QUESTION DIRECTLY
USER QUESTION: Can you tell me if this combination of medicine is given to an adult patient with high Blood pressure what problems could happen?

MANDATORY INSTRUCTIONS:
1. DO NOT repeat the general interaction assessment...
2. You MUST directly address the question asked. If the question mentions 'high blood pressure', you MUST explain:
   - How high blood pressure affects or is affected by this drug combination
   - Specific problems that could occur in patients with high blood pressure
   - Additional risks beyond the general interaction
   - How monitoring and management differs for hypertensive patients
3. The question specifically mentions: high blood pressure (hypertension). You MUST address this in your answer.
4. Structure your answer to directly respond to the question...
5. Use ALL available evidence from CONTEXT...
```

### 2. Enhanced Template for Initial Assessment

**Before:**
- Generic "Expected Clinical Effects" section
- No emphasis on including all risk flags

**After:**
- **Comprehensive list** requirement
- Explicit mention of ALL risk types:
  * PK effects
  * PD effects
  * QT prolongation risk (if DIQT scores elevated)
  * Hepatotoxicity risk (if DILI scores elevated)
  * All FAERS signals
- Added explanation of risk flag meanings in CONTEXT section
- Explicit instruction: "If RISK_FLAGS show elevated DIQT or DILI scores, you MUST mention QT prolongation and hepatotoxicity risks"

**Updated Sections:**

1. **Mechanism & Rationale:**
   - Now includes: "Include ALL relevant mechanisms from CONTEXT"
   - Lists: PK, PD, QT prolongation, Hepatotoxicity mechanisms
   - Emphasizes: "Tie back explicitly to CONTEXT terms you were given, including RISK_FLAGS"

2. **Expected Clinical Effects:**
   - Changed from "Concise list" to "Comprehensive list of ALL plausible clinical outcomes"
   - Explicitly lists all risk types to include
   - Added: "IMPORTANT: Include ALL risks mentioned in RISK_FLAGS and FAERS_SUMMARY"

3. **CONTEXT Section:**
   - Added explanations of risk flag meanings:
     * PRR: Pairwise Reporting Ratio
     * DILI: Drug-Induced Liver Injury score
     * DICT: Drug-Induced Cardiotoxicity score
     * DIQT: Drug-Induced QT Prolongation score
   - Added instruction: "Use ALL information from CONTEXT. If RISK_FLAGS show elevated DIQT or DILI scores, you MUST mention QT prolongation and hepatotoxicity risks."

## Expected Behavior

### Initial Assessment (Before Fix)
- Missing QT prolongation risk (even though DIQT(B)=4.0)
- Missing hepatotoxicity details (even though DILI(B)=8.0)
- Generic clinical effects list

### Initial Assessment (After Fix)
- ✅ Includes QT prolongation risk when DIQT scores are elevated
- ✅ Includes hepatotoxicity risk when DILI scores are elevated
- ✅ Comprehensive list of ALL clinical effects from CONTEXT
- ✅ All FAERS signals included

### Follow-Up Question (Before Fix)
**User asks:** "Can you tell me if this combination of medicine is given to an adult patient with high Blood pressure what problems could happen?"

**System responds:** (Repeats general interaction assessment without addressing high BP)

### Follow-Up Question (After Fix)
**User asks:** "Can you tell me if this combination of medicine is given to an adult patient with high Blood pressure what problems could happen?"

**System responds:**
- ✅ Starts with direct answer: "In a patient with high blood pressure, this combination could cause..."
- ✅ Lists specific problems for hypertensive patients
- ✅ Explains how high BP interacts with the drug combination
- ✅ Provides condition-specific monitoring recommendations
- ✅ Addresses cardiovascular risks specific to hypertension

## Files Modified

1. **`src/llm/llm_interface.py`**:
   - Enhanced follow-up question detection and instructions
   - Added clinical condition extraction (high BP, diabetes, renal/hepatic impairment)
   - More emphatic and structured instructions

2. **`src/llm/prompt_templates.txt`**:
   - Enhanced CONTEXT section with risk flag explanations
   - Updated "Mechanism & Rationale" to include all mechanisms
   - Updated "Expected Clinical Effects" to be comprehensive
   - Added explicit instructions to use ALL information

## Testing

The enhancements have been tested to ensure:
- ✅ Follow-up questions are detected and handled with enhanced instructions
- ✅ Clinical conditions are automatically extracted
- ✅ All risk flags (DIQT, DILI) are emphasized in the template
- ✅ Initial assessments will include all available information

## Notes

- The system now has **two levels of emphasis**:
  1. Template-level: Ensures all information is included in initial assessments
  2. Follow-up-level: Forces direct answers to specific clinical scenarios

- The follow-up instructions are **much more emphatic** (CRITICAL, MANDATORY) to override default behavior

- Clinical condition detection helps the system understand what specific aspects to address

