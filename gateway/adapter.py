from openai import AsyncOpenAI

from contracts.proxy import ProxyRequest


async def call_provider(
    request: ProxyRequest,
    client: AsyncOpenAI,
) -> dict:
    response = await client.chat.completions.create(
        model=request.model,
        messages=[
            message.model_dump()
            for message in request.messages
        ],
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )

    return response.model_dump(mode="json")