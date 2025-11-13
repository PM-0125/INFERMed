# Follow-Up Question Handling Fix

## Problem

When users ask follow-up questions with clinical context (e.g., "Can you tell me if this combination of medicine is given to an adult patient with high Blood pressure what problems could happen?"), the system was not directly answering the question. Instead, it was repeating the general interaction assessment without addressing the specific clinical scenario.

## Root Cause

1. The `USER_QUESTION` placeholder was extracted from history but not prominently used in the prompt
2. The prompt templates didn't explicitly instruct the LLM to answer the specific follow-up question
3. The system didn't emphasize integrating clinical context from the question with the drug interaction information

## Solution

### 1. Updated Prompt Templates

All templates now use `{{USER_QUESTION}}` instead of hardcoded default questions:

- **DOCTOR template**: Uses `{{USER_QUESTION}}` instead of "Evaluate potential interactions..."
- **PATIENT template**: Uses `{{USER_QUESTION}}` instead of "Can I take..."
- **PHARMA template**: Uses `{{USER_QUESTION}}` instead of "Prepare a risk brief..."

### 2. Enhanced Prompt Building Logic

Added explicit follow-up question handling in `build_prompt()`:

```python
# If this is a follow-up question, add explicit instruction to answer it directly
if user_question and user_question.strip():
    is_followup = (
        not user_question.startswith("Evaluate potential") and
        not user_question.startswith("Can I take") and
        not user_question.startswith("Prepare a risk brief")
    )
    
    if is_followup:
        prompt += "## IMPORTANT: ANSWER THIS SPECIFIC QUESTION\n"
        prompt += f"The user is asking: {user_question}\n\n"
        prompt += "INSTRUCTIONS:\n"
        prompt += "- Answer this question DIRECTLY. Do not just repeat the general interaction assessment.\n"
        prompt += "- Integrate any clinical context mentioned in the question "
        prompt += "(e.g., patient conditions like high blood pressure, age, comorbidities, specific symptoms) "
        prompt += "with the drug interaction information.\n"
        prompt += "- Explain how the drug interaction might be affected by or affect the patient's specific condition.\n"
        prompt += "- If the question asks about specific problems or risks, list them clearly.\n"
        prompt += "- Use the evidence from CONTEXT to support your answer.\n\n"
```

### 3. Default Questions by Mode

When no user question is found, default questions are set based on mode:
- **DOCTOR**: "Evaluate potential interactions between {drugA} and {drugB}..."
- **PATIENT**: "Can I take {drugA} and {drugB} together?"
- **PHARMA**: "Prepare a risk brief for {drugA} + {drugB}."

## Expected Behavior

### Before Fix
**User asks**: "Can you tell me if this combination of medicine is given to an adult patient with high Blood pressure what problems could happen?"

**System responds**: (Repeats general interaction assessment without addressing high blood pressure)

### After Fix
**User asks**: "Can you tell me if this combination of medicine is given to an adult patient with high Blood pressure what problems could happen?"

**System responds**: 
- Directly addresses the question about high blood pressure
- Integrates the clinical context (high BP) with the drug interaction
- Explains specific problems/risks for patients with hypertension
- Uses evidence from CONTEXT to support the answer

## Example Response (Expected)

For warfarin + fluconazole in a patient with high blood pressure:

1. **Assessment**: Addresses the specific scenario (warfarin + fluconazole + hypertension)
2. **Mechanism & Rationale**: Explains how hypertension affects the interaction risk
3. **Expected Clinical Effects**: 
   - Increased bleeding risk (warfarin + fluconazole interaction)
   - Additional cardiovascular risks due to hypertension
   - Potential for hypertensive crisis or complications
4. **Monitoring / Actions**: 
   - Blood pressure monitoring
   - INR monitoring
   - Signs of bleeding
5. **Evidence & Uncertainty**: Cites sources and limitations

## Testing

The fix has been tested to ensure:
- ✅ Follow-up questions are detected correctly
- ✅ Explicit instructions are added to the prompt
- ✅ All modes (DOCTOR, PATIENT, PHARMA) work correctly
- ✅ Default questions are set when no user question is found

## Files Modified

1. `src/llm/prompt_templates.txt` - Updated all templates to use `{{USER_QUESTION}}`
2. `src/llm/llm_interface.py` - Added follow-up question detection and explicit instructions

## Notes

- The system now explicitly instructs the LLM to answer follow-up questions directly
- Clinical context from questions (e.g., high blood pressure, age, comorbidities) is integrated with drug interaction information
- The fix maintains backward compatibility with initial questions

