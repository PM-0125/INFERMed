# Expert Feedback Improvements - Implementation Summary

## Overview
Based on expert feedback, we've implemented three key improvements to better exploit the LLM's medical prior while maintaining strong grounding in retrieved data.

## Changes Implemented

### 1. ✅ Surface PubChem PK Data Explicitly in Prompt

**What**: Added PK metadata (logP, molecular weight, H-bonding) from PubChem REST API to the prompt context.

**Implementation**:
- Created `_format_pk_meta()` function in `src/llm/llm_interface.py`
- Extracts `pk_data_a` and `pk_data_b` from context
- Formats qualitative PK properties (e.g., "high logP (3.5) → lipophilic, MW 307")
- Added `PK_META` to context blocks in `_summarize_context()`
- Included in DOCTOR template as: "Additional PK metadata (from PubChem; qualitative only): {{PK_META}}"

**Result**: Model now has access to qualitative PK properties that can inform PK reasoning without changing overlap logic.

### 2. ✅ Make PK Overlap Sentence Ultra-Salient

**What**: Prefixed PK overlap statements with "DATA-DRIVEN PK OVERLAP:" to make it impossible for the model to miss.

**Implementation**:
- Updated `_format_pk()` in `src/llm/llm_interface.py`:
  - Added "DATA-DRIVEN PK OVERLAP:" prefix to both overlap and no-overlap messages
  - Handles both precomputed `pk_summary` and dynamically generated summaries
- Updated DOCTOR template with explicit instruction:
  - "CRITICAL: The PK summary line starting with 'DATA-DRIVEN PK OVERLAP:' tells you whether the retrieved data actually shows a PK interaction."

**Result**: The model cannot miss whether evidence actually shows a PK interaction - it's explicitly marked.

### 3. ✅ Mandatory PK Overlap Restatement

**What**: Added explicit instruction requiring the model to restate the PK overlap status verbatim in the Assessment section.

**Implementation**:
- Updated DOCTOR template Assessment section:
  - Added: "**MANDATORY**: You MUST explicitly restate the PK overlap status from the PK summary line."
  - Clear instruction: "If it says 'DATA-DRIVEN PK OVERLAP: No strong PK overlap detected', you must state that the retrieved data do not show a PK overlap."
- Updated Mechanism & Rationale section:
  - "**First, explicitly restate the DATA-DRIVEN PK OVERLAP status from the PK summary verbatim.**"
  - Provides example format for handling no-overlap cases with general knowledge

**Result**: Model is forced to explicitly state the data-driven PK status before adding general knowledge, preventing "compensation" with hallucination.

## Files Modified

1. **`src/llm/llm_interface.py`**:
   - Added `_format_pk_meta()` function
   - Updated `_format_pk()` to add "DATA-DRIVEN PK OVERLAP:" prefix
   - Updated `_summarize_context()` to include `PK_META` in context blocks
   - Updated `_fill()` to handle empty `PK_META` gracefully

2. **`src/llm/prompt_templates.txt`**:
   - Added `PK_META` placeholder in CONTEXT section
   - Added CRITICAL instruction about DATA-DRIVEN PK OVERLAP
   - Added MANDATORY restatement requirement in Assessment section
   - Enhanced Mechanism & Rationale section with explicit restatement instruction

## Expected Behavior

### Before Improvements:
- Model might "compensate" for missing PK overlap by hallucinating mechanisms
- PK metadata from PubChem was fetched but not shown to model
- PK overlap status was not explicitly marked, making it easy to miss

### After Improvements:
- Model **must** explicitly state: "DATA-DRIVEN PK OVERLAP: No strong PK overlap detected"
- Model can then add: "However, general pharmacology knowledge indicates..."
- PK metadata (logP, MW, H-bonds) is available for qualitative PK reasoning
- Clear separation between data-driven evidence and general knowledge

## Testing

All improvements verified:
- ✅ `_format_pk_meta()` correctly formats PK properties
- ✅ `_format_pk()` adds "DATA-DRIVEN PK OVERLAP:" prefix
- ✅ `PK_META` is included in context blocks
- ✅ Template includes all new instructions
- ✅ Prompt building works correctly

## Impact

These improvements ensure:
1. **Better grounding**: Model cannot miss the data-driven PK status
2. **Clearer separation**: Explicit distinction between retrieved data and general knowledge
3. **Richer context**: PK metadata available for qualitative reasoning
4. **No hallucination compensation**: Model must state data status before adding general knowledge

The system now better fulfills the "smartest pharmacology resident" role - one who has read all the tables, doesn't bluff, and clearly marks what comes from data vs. general knowledge.

