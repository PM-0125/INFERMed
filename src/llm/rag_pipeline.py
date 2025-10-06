from src.llm.llm_interface import generate_response, build_prompt
def run_llm(context, mode="Patient", **kwargs):
    return generate_response(context, mode, **kwargs)
