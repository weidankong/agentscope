# test_as_mcp Architecture

## Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Host Machine                                   │
│                                                                         │
│  ┌─────────────┐    ┌──────────────────────────────────────────┐       │
│  │             │    │              Toolkit                      │       │
│  │  ReActAgent │───▶│                                          │       │
│  │  (Friday)   │    │  ┌──────────────────┐ ┌───────────────┐  │       │
│  │             │◀───│  │execute_python_code│ │execute_shell  │  │       │
│  └─────────────┘    │  └──────────────────┘ └───────────────┘  │       │
│       │             │  ┌──────────────────┐ ┌───────────────┐  │       │
│       │             │  │get_news (proxy)  │ │get_weather    │  │       │
│       │             │  │                  │ │  (proxy)      │  │       │
│       │             │  └──────────────────┘ └───────────────┘  │       │
│       │             └──────────────┬───────────┬──────────────┘       │
│       │                            │           │                      │
│       │               ┌────────────▼───────────▼────────────┐         │
│       │               │       HostToolServer                │         │
│       │               │       FastAPI :port                 │         │
│       │               │       POST /call/{tool_name}        │         │
│       │               │                                     │         │
│       │               │  ┌─────────────┐  ┌─────────────┐  │         │
│       │               │  │ host_tools  │  │  mcp_tools   │  │         │
│       │               │  │ get_weather │  │  get_news    │  │         │
│       │               │  └──────┬──────┘  └──────┬──────┘  │         │
│       │               └────────┼─────────────────┼─────────┘         │
│       │                        │                 │                    │
└───────┼────────────────────────┼─────────────────┼────────────────────┘
        │                        │                 │
        │              ┌─────────▼──────┐  ┌───────▼──────────────────┐
        │              │  Docker Bridge │  │     Docker Bridge        │
        │              │  Gateway IP    │  │     Gateway IP           │
        │              └─────────┬──────┘  └───────┬──────────────────┘
        │                        │                 │
        │                        ▼                 ▼
        │          ┌─────────────────────┐  ┌──────────────────────────┐
        │          │ BaseSandbox Container│  │ McpSandbox Container     │
        │          │                     │  │                          │
        │          │  ┌───────────────┐  │  │  ┌────────────────────┐  │
        │          │  │  IPython      │  │  │  │  FastAPI           │  │
        │          │  │  Runtime      │  │  │  │  /mcp/call_tool    │  │
        │          │  │               │  │  │  │  /mcp/list_tools   │  │
        │          │  ├───────────────┤  │  │  └────────┬───────────┘  │
        │          │  │  proxy funcs  │  │  │           │              │
        │          │  │ ┌───────────┐ │  │  │  ┌────────▼───────────┐  │
        │          │  │ │get_weather│ │  │  │  │  MCP Servers       │  │
        │          │  │ │  → HTTP   │ │  │  │  │ ┌───────────────┐ │  │
        │          │  │ │  → host   │ │  │  │  │ │ news          │ │  │
        │          │  │ ├───────────┤ │  │  │  │ │  get_news(q)  │ │  │
        │          │  │ │get_news   │ │  │  │  │ ├───────────────┤ │  │
        │          │  │ │  → HTTP   │ │  │  │  │ │ weather       │ │  │
        │          │  │ │  → host   │ │  │  │  │ │  get_weather  │ │  │
        │          │  │ └───────────┘ │  │  │  │ └───────────────┘ │  │
        │          │  └───────────────┘  │  │  └────────────────────┘  │
        │          └─────────────────────┘  └──────────────────────────┘
        │
        └────────────────────────────────────────────────────────────────
                         (Agent direct call path)
```

## Call Flow 1: Agent directly calls get_news

```
 Agent                Toolkit              McpSandbox Container
  │                     │                          │
  │  get_news(query)    │                          │
  │────────────────────▶│                          │
  │                     │  call_tool_async         │
  │                     │  ("get_news", {query})   │
  │                     │─────────────────────────▶│
  │                     │                          │──▶ MCP news server
  │                     │                          │    get_news(query)
  │                     │      result              │◀──────────────────
  │                     │◀─────────────────────────│
  │    ToolResponse     │                          │
  │◀────────────────────│                          │
```

## Call Flow 2: Agent calls execute_python_code, code calls get_news

```
 Agent     Toolkit    CodeSandbox    HostToolServer    McpSandbox
  │          │            │               │               │
  │ execute  │            │               │               │
  │ _python  │            │               │               │
  │ _code    │            │               │               │
  │─────────▶│            │               │               │
  │          │ run_ipython│               │               │
  │          │ _cell(code)│               │               │
  │          │───────────▶│               │               │
  │          │            │               │               │
  │          │            │ proxy get_news│               │
  │          │            │ HTTP POST     │               │
  │          │            │ /call/get_news│               │
  │          │            │──────────────▶│               │
  │          │            │               │ call_tool_async│
  │          │            │               │ ("get_news")  │
  │          │            │               │──────────────▶│
  │          │            │               │               │──▶ MCP server
  │          │            │               │               │◀── result
  │          │            │               │    result     │
  │          │            │               │◀──────────────│
  │          │            │   {"result":} │               │
  │          │            │◀──────────────│               │
  │          │            │               │               │
  │          │  stdout    │               │               │
  │          │◀───────────│               │               │
  │ result   │            │               │               │
  │◀─────────│            │               │               │
```

## Call Flow 3: Agent calls execute_python_code, code calls host tool get_weather

```
 Agent     Toolkit    CodeSandbox    HostToolServer
  │          │            │               │
  │ execute  │            │               │
  │ _python  │            │               │
  │ _code    │            │               │
  │─────────▶│            │               │
  │          │ run_ipython│               │
  │          │───────────▶│               │
  │          │            │               │
  │          │            │ proxy get_    │
  │          │            │ weather       │
  │          │            │ HTTP POST     │
  │          │            │ /call/get_    │
  │          │            │ weather       │
  │          │            │──────────────▶│
  │          │            │               │──▶ host func
  │          │            │               │    get_weather(city)
  │          │            │   {"result":} │◀──────────────
  │          │            │◀──────────────│
  │          │            │               │
  │          │  stdout    │               │
  │          │◀───────────│               │
  │ result   │            │               │
  │◀─────────│            │               │
```
