# CodeAct Agent Example

## Architecture

```
┌─────────────────────────────────────────────────────── Host ─────────────────────────────────────────────────┐
│                                                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │  ReActAgent (Friday)                                                                                 │    │
│  │  ┌──────────────┐  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
│  │  │ LLM (qwen)   │  │ Toolkit                                                                  │      │    │
│  │  │              │  │  ├─ run_python_code  (from CodeActEnv)                                   │      │    │
│  │  │  sys_prompt: │  │  ├─ get_weather      (direct, host-side)                                 │      │    │
│  │  │  CODEACT_    │  │  └─ get_news         (direct, host-side)                                 │      │    │
│  │  │  SYSTEM_     │  │                                                                          │      │    │
│  │  │  PROMPT      │  │                                                                          │      │    │
│  │  └──────────────┘  └──────────────────────────────────────────────────────────────────────────┘      │    │
│  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘    │
│          │                                    │                          │                                   │
│          │  UserAgent ←→ ReActAgent loop      │                          │  Direct tool calls                │
│          │                                    │                          │  (no sandbox)                     │
│          │                                    ▼                          ▼                                   │
│  ┌───────────────────┐         ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │   UserAgent       │         │  ToolServer (FastAPI + Uvicorn)                                         │   │
│  │                   │         │  ┌─────────────────────────────────────────────────────────────────┐    │   │
│  └───────────────────┘         │  │  POST /call/{tool_name}                                         │    │   │
│                                │  │  └─ get_weather(**kwargs)  →  ToolResponse                      │    │   │
│                                │  └─────────────────────────────────────────────────────────────────┘    │   │
│                                └──────────────────────────┬──────────────────────────────────────────────┘   │
│                                                           │  HTTP (Docker bridge IP)                         │
│                                                           │                                                  │
└───────────────────────────────────────────────────────────┼──────────────────────────────────────────────────┘
                                                            │
                            ┌───────────────────────────────┼────────────────────────────────────────────────┐
                            │  Docker Sandbox               │                                                │
                            │                               ▼                                                │
                            │  ┌──────────────────────────────────────────────────────────────────────────┐  │
                            │  │  IPython Environment                                                     │  │
                            │  │                                                                          │  │
                            │  │  Pre-injected: call_tool(name, **kwargs)                                 │  │
                            │  │    │                                                                     │  │
                            │  │    │  HTTP POST → http://{host_ip}:{port}/call/{tool_name}               │  │
                            │  │    └─────────────────────────────────────────────────────────────────►  │──┘
                            │  │                                                                         │   ToolServer
                            │  │  User code:                                                             │
                            │  │    result = call_tool('get_weather', city='Beijing')                    │
                            │  │    print(result['temperature_c'])                                       │
                            │  │                                                                         │
                            │  └─────────────────────────────────────────────────────────────────────────┘
                            │                                                                           │
                            └───────────────────────────────────────────────────────────────────────────┘
```

## Request Flow

1. **User** sends a message via `UserAgent`
2. **ReActAgent** decides whether to call a tool directly (e.g. `get_weather`) or execute code via `run_python_code`
3. **Direct call**: Agent invokes the host-side function through `Toolkit` — no sandbox involved
4. **Code execution**: Agent calls `run_python_code(code)` → `CodeActEnv.run_python_code()` → `sandbox.run_ipython_cell(code)`
5. Inside the sandbox, code uses `call_tool(name, **kwargs)` which sends an HTTP request to the host `ToolServer`
6. **ToolServer** dispatches the call to the registered function and returns the result

## Example

> **Friday**: Hello! How can I assist you today?

> **user**: beijing sanya temperature diff

> **Friday**:
> ```json
> {
>     "type": "tool_use",
>     "id": "call_8f27c0cbd7124bf08e1a73",
>     "name": "run_python_code",
>     "input": {
>         "code": "beijing_weather = call_tool('get_weather', city='Beijing')\nsanya_weather = call_tool('get_weather', city='Sanya')\ntemperature_diff = abs(beijing_weather['temperature_c'] - sanya_weather['temperature_c'])\nprint(f\"The temperature difference between Beijing and Sanya is {temperature_diff}°C.\")"
>     },
>     "raw_input": "{\"code\": \"beijing_weather = call_tool('get_weather', city='Beijing')\\nsanya_weather = call_tool('get_weather', city='Sanya')\\ntemperature_diff = abs(beijing_weather['temperature_c'] - sanya_weather['temperature_c'])\\nprint(f\\\"The temperature difference between Beijing and Sanya is {temperature_diff}°C.\\\")\"}"
> }
> ```

> **system**:
> ```json
> {
>     "type": "tool_result",
>     "id": "call_8f27c0cbd7124bf08e1a73",
>     "name": "run_python_code",
>     "output": [
>         {
>             "type": "text",
>             "text": "The temperature difference between Beijing and Sanya is 4°C.\n"
>         }
>     ]
> }
> ```

> **Friday**: The temperature difference between Beijing and Sanya is 4°C.