"""Documented model-specific reasoning controls, shared by setup and runtime."""

from .const import (
    REASONING_EFFORT_NONE,
    REASONING_MODEL_DEFAULT,
    REASONING_SETTINGS,
    MistralModel,
    ReasoningSetting,
)

# The SDK's enum is not a per-model support list. Model discovery only exposes
# a reasoning boolean. Use exact documented IDs and discovered aliases, never
# broad name prefixes that could accidentally constrain custom/fine-tuned IDs.
# https://docs.mistral.ai/studio/conversations/reasoning
# https://docs.mistral.ai/resources/changelogs
_ADJUSTABLE_MODELS = (
    "mistral-small-latest",
    "mistral-small-2603",
    "mistral-medium-3-5",
)
# https://docs.mistral.ai/resources/deprecated/native-reasoning
_NATIVE_MODELS = (
    "magistral-small-latest",
    "magistral-small-2506",
    "magistral-small-2509",
    "magistral-medium-latest",
    "magistral-medium-2506",
    "magistral-medium-2509",
)


def reasoning_options(model: MistralModel, known: bool) -> tuple[ReasoningSetting, ...]:
    """Return supported settings when documented, or SDK choices when unknown."""
    if not known:
        return REASONING_SETTINGS
    if not model.reasoning:
        return (REASONING_MODEL_DEFAULT, REASONING_EFFORT_NONE)
    # A redirected legacy alias must not override a model's canonical ID.
    # In particular, Small 4 can carry Magistral aliases while remaining hybrid.
    for model_id in (
        model.id.casefold(),
        *(alias.casefold() for alias in model.aliases),
    ):
        if model_id in _ADJUSTABLE_MODELS:
            return (REASONING_MODEL_DEFAULT, REASONING_EFFORT_NONE, "high")
        if model_id in _NATIVE_MODELS:
            return (REASONING_MODEL_DEFAULT,)
    return REASONING_SETTINGS


def reasoning_error(model: MistralModel, known: bool, effort: str) -> str | None:
    """Reject unsupported efforts without changing a user's saved selection."""
    if effort in reasoning_options(model, known):
        return None
    if known and not model.reasoning:
        return "model_no_reasoning"
    return "model_reasoning_effort"


def fixed_reasoning_setting(
    model: MistralModel, known: bool
) -> tuple[str, ReasoningSetting] | None:
    """Return a translated fixed state and its setting, when no control exists."""
    if known and not model.reasoning:
        return ("reasoning_unavailable", REASONING_EFFORT_NONE)
    if reasoning_options(model, known) == (REASONING_MODEL_DEFAULT,):
        return ("reasoning_always_on", REASONING_MODEL_DEFAULT)
    return None
