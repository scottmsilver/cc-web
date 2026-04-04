// Simple proxy to cchost's OpenAI-compatible endpoint
// The useChat hook from @ai-sdk/react can consume raw OpenAI SSE

export async function POST(req: Request) {
  const body = await req.json();

  // Forward to cchost
  const response = await fetch(
    process.env.CCHOST_URL
      ? `${process.env.CCHOST_URL}/chat/completions`
      : "http://localhost:8420/v1/chat/completions",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "claude-code",
        messages: body.messages,
        stream: true,
      }),
    }
  );

  // Pass through the SSE stream
  return new Response(response.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
