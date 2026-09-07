import os
from openai import AsyncOpenAI
from dotenv import load_dotenv
from contracts.proxy import ProxyRequest

load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)

MODEL = os.getenv("OPENAI_MODEL", "google/gemma-4-31b-it:free")

async def call_provider(request: ProxyRequest) -> dict:
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    response = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )

    return response.model_dump()