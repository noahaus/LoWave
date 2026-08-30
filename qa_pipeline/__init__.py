"""qa_pipeline — turn numbered English workflow steps into a runnable Playwright test.

Three stages, each usable on its own from the command line:

    parse_steps   steps.txt          -> action_plan.json      (text LLM)
    refine_plan   action_plan.json   -> refined_action_plan   (grounded on live DOM)
    generate      (refined) plan     -> Playwright .spec.ts    (deterministic)

Shared helpers: config (env/CLI settings), llm (LangChain model factory).
"""

__version__ = "0.1.0"
