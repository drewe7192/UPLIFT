# 4. EVALS — does it actually work?
test_cases = [
    {
        "input": "do something with hello",
        "must_call_tool": "do_something",      # did it use the right tool?
        "output_contains": ["did it"],          # is the answer right?
    }
]

def run_evals():
    for case in test_cases:
        print(f"\nTesting: {case['input']}")
        
        # run the agent, but capture tool calls too
        messages = [{"role": "user", "content": case["input"]}]
        tools_called = []
        final_output = ""

        while True:
            response = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1000,
                tools=tools,
                messages=messages
            )

            if response.stop_reason == "end_turn":
                final_output = next(b.text for b in response.content if hasattr(b, "text"))
                break

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tools_called.append(block.name)
                        result = run_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result)
                        })
                messages.append({"role": "user", "content": tool_results})
            else:
                break

        # --- check results ---
        passed = True

        if "must_call_tool" in case:
            ok = case["must_call_tool"] in tools_called
            print(f"  tool called:    {'✅' if ok else '❌'} (got {tools_called})")
            if not ok: passed = False

        if "output_contains" in case:
            for keyword in case["output_contains"]:
                ok = keyword.lower() in final_output.lower()
                print(f"  has '{keyword}': {'✅' if ok else '❌'}")
                if not ok: passed = False

        print(f"  RESULT: {'PASS' if passed else 'FAIL'}")

run_evals()