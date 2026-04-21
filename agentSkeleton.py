import anthropic
import json

client = anthropic.Anthropic()

# 1. TOOLS — what the agent can do
tools = [
    {
        "name": "do_something",
        "description": "Does something specific",
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "string", "description": "what to do it with"}
            },
            "required": ["value"]
        }
    }
]

# 2. TOOL LOGIC — what actually runs when tool is called
def do_something(value: str) -> str:
    return f"did it with {value}"

def run_tool(name, inputs):
    if name == "do_something":
        return do_something(**inputs)
    return "unknown tool"

# 3. THE LOOP — this is the whole agent
def run_agent(user_message: str):
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1000,
            tools=tools,
            messages=messages
        )

        # --- did it stop? ---
        if response.stop_reason == "end_turn":
            final = next(b.text for b in response.content if hasattr(b, "text"))
            print("DONE:", final)
            return final

        # --- does it want to use a tool? ---
        if response.stop_reason == "tool_use":
            # add assistant response to history
            messages.append({"role": "assistant", "content": response.content})

            # run each tool it asked for
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  calling tool: {block.name}({block.input})")
                    result = run_tool(block.name, block.input)
                    print(f"  result: {result}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result)
                    })

            # add tool results to history so agent can continue
            messages.append({"role": "user", "content": tool_results})

        # safety: if neither, just break
        else:
            break

run_agent("do something with hello")