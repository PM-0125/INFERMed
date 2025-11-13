# Model Selection Fix - Dynamic Ollama Model Fetching

## Issue
The model selection sidebar was using a hardcoded list of models, so deleted models still appeared in the dropdown. The system should dynamically fetch available models from Ollama.

## Root Cause
In `src/frontend/app.py`, the `_sidebar()` function had a hardcoded list:
```python
available_models = [
    "gpt-oss",
    "gpt-oss:latest",
    "mistral-nemo:12b",
    "merlinvn/MedicalQA-Llama-3.2-3B-Instruct",
    "Elixpo/LlamaMedicine",
]
```

## Solution
Implemented dynamic model fetching from Ollama API with caching and fallback:

### 1. New Function: `_fetch_ollama_models()`
- Fetches models from Ollama API endpoint: `{OLLAMA_HOST}/api/tags`
- Uses Streamlit session state to cache results (avoids repeated API calls on reruns)
- Returns empty list if Ollama is unavailable
- Logs success/failure for debugging

### 2. Updated `_sidebar()` Function
- Calls `_fetch_ollama_models()` to get current models
- Falls back to hardcoded list only if Ollama is unavailable or returns no models
- Added refresh button (🔄) to manually reload models from Ollama
- Updated help text to indicate models are dynamically fetched

### 3. Features
- **Dynamic Fetching**: Models are fetched from Ollama on first load
- **Caching**: Results cached in session state to avoid repeated API calls
- **Fallback**: Uses hardcoded list if Ollama is unavailable
- **Manual Refresh**: Button to force reload models (clears cache and reruns)
- **Error Handling**: Gracefully handles connection errors, timeouts, etc.

## Testing
Tested with actual Ollama instance:
```
✅ Successfully fetched 3 models:
   - alibayram/medgemma:latest
   - gpt-oss:latest
   - mistral-nemo:12b
```

These match the user's current models, confirming the fix works correctly.

## Files Modified
- `src/frontend/app.py`:
  - Added `_fetch_ollama_models()` function
  - Updated `_sidebar()` to use dynamic fetching
  - Added refresh button for manual model list reload
  - Updated imports to include `OLLAMA_HOST`

## Usage
1. **Automatic**: Models are fetched automatically when the sidebar loads
2. **Manual Refresh**: Click the 🔄 button next to the model dropdown to reload
3. **Custom Models**: Still supports entering custom model names via text input
4. **Fallback**: If Ollama is unavailable, falls back to default list

## Benefits
- ✅ Models list always reflects actual Ollama models
- ✅ Deleted models no longer appear in dropdown
- ✅ New models automatically appear (after refresh)
- ✅ Works even if Ollama is temporarily unavailable (fallback)
- ✅ Efficient (cached, no repeated API calls)

