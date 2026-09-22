"""Tool-capable model adapters; endpoints and credentials stay server-side."""

import os

SUPPORTED = {
    "openai",
    "openrouter",
    "dashscope",
    "google",
    "ollama",
    "azure",
    "bedrock",
}


def select_model(provider: str = "", model: str = "") -> tuple[str, str]:
    from api.config import configs

    provider = (
        provider
        or os.getenv("AGENT_PROVIDER")
        or configs.get("default_provider", "openai")
    )
    cfg = configs.get("providers", {}).get(provider)
    if provider not in SUPPORTED or cfg is None:
        raise ValueError("This model provider is not available for Ask Agent.")
    model = model or os.getenv("AGENT_MODEL") or cfg.get("default_model", "")
    if not model or len(model) > 200:
        raise ValueError("Choose a tool-calling model.")
    if model not in cfg.get("models", {}) and not cfg.get("supportsCustomModel", False):
        raise ValueError("Custom models are not enabled for this provider.")
    return provider, model


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Configure {name} on the server.")
    return value


def build_model(provider: str, model: str):
    from langchain_openai import AzureChatOpenAI, ChatOpenAI

    common = {"model": model, "timeout": 90, "max_retries": 1, "streaming": True}
    if provider == "openai":
        return ChatOpenAI(
            **common,
            api_key=required("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            use_responses_api=False,
        )
    if provider == "openrouter":
        return ChatOpenAI(
            **common,
            api_key=required("OPENROUTER_API_KEY"),
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            use_responses_api=False,
        )
    if provider == "dashscope":
        return ChatOpenAI(
            **common,
            api_key=required("DASHSCOPE_API_KEY"),
            base_url=os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            use_responses_api=False,
        )
    if provider == "azure":
        return AzureChatOpenAI(
            **common,
            azure_deployment=model,
            api_key=required("AZURE_OPENAI_API_KEY"),
            azure_endpoint=required("AZURE_OPENAI_ENDPOINT"),
            api_version=required("AZURE_OPENAI_VERSION"),
        )
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=required("GOOGLE_API_KEY"),
            timeout=90,
            max_retries=1,
        )
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        from api.config import configs

        options = (
            configs.get("providers", {})
            .get(provider, {})
            .get("models", {})
            .get(model, {})
            .get("options", {})
        )
        context_tokens = int(
            options.get("num_ctx", os.getenv("AGENT_CONTEXT_TOKENS", "32000"))
        )

        return ChatOllama(
            model=model,
            base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            client_kwargs={"timeout": 90},
            num_ctx=context_tokens,
            profile={"max_input_tokens": context_tokens},
        )
    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse

        return ChatBedrockConverse(
            model=model, region_name=os.getenv("AWS_REGION", "us-east-1")
        )
    raise ValueError("Unsupported agent provider.")
