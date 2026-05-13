# CodeAct Agent Architecture

## Overview

```
+--------------------------------------------------------------------------+
|                            Host Machine                                  |
|                                                                          |
|  +--------------+        +-----------------------------------------+    |
|  |              |        |              Toolkit                     |    |
|  |  ReActAgent  |------->|                                         |    |
|  |  (Friday)    |<-------|  run_python_code   get_weather          |    |
|  |              |        |                     get_news             |    |
|  +--+-----------+        +-----+--------------+--------------------+    |
|     |                          |              |                         |
|     | direct call              |              | direct call             |
|     | (get_weather etc.)       |              | (get_weather etc.)      |
|     |                          v              |                         |
|     |                  +---------------+      |                         |
|     |                  |  CodeActEnv   |      |                         |
|     |                  |               |      |                         |
|     |                  |  +---------+  |      |                         |
|     |                  |  |ToolSvr  |  |      |                         |
|     |                  |  |(FastAPI)|  |      |                         |
|     |                  |  |/call/{} |  |      |                         |
|     |                  |  +---------+  |      |                         |
|     |                  |  get_weather <--------+ (same host function)   |
|     |                  |  get_news   --+--------------------------+     |
|     |                  +------+--------+                            |     |
|     |                         | HTTP (Docker bridge gateway)       |     |
|     |                         |                                    |     |
|     |                         v                                    |     |
|     |                  +-------------------+                       |     |
|     |                  | Docker Sandbox     |                       |     |
|     |                  | (BaseSandboxAsync) |                       |     |
|     |                  |                    |                       |     |
|     |                  |  +--------------+  |                       |     |
|     |                  |  |   IPython    |  |                       |     |
|     |                  |  |   Runtime    |  |                       |     |
|     |                  |  +--------------+  |                       |     |
|     |                  |  |  call_tool() |  |                       |     |
|     |                  |  |  (injected   |  |                       |     |
|     |                  |  |   proxy)     |  |                       |     |
|     |                  |  +--------------+  |                       |     |
|     |                  +-------------------+                       |     |
|     |                                                              |     |
|     +--------------------------------------------------------------+     |
|          (Agent direct call path via Toolkit)                            |
+--------------------------------------------------------------------------+
```

## Call Flow 1: Agent directly calls host tool

```
 Agent              Toolkit              Host Function
  |                   |                       |
  | get_weather(city) |                       |
  |------------------>|                       |
  |                   | get_weather(city)      |
  |                   |---------------------->|
  |                   |                       |--> wttr.in API
  |                   |      ToolResponse     |<--------------
  |  ToolResponse     |<----------------------|
  |<------------------|                       |
```

## Call Flow 2: Agent calls run_python_code, code calls call_tool (via CodeActEnv)

```
 Agent    Toolkit    CodeActEnv  ToolServer   Sandbox(IPython)   Host Function
  |          |          |          |              |                 |
  | run_     |          |          |              |                 |
  | python_  |          |          |              |                 |
  | code     |          |          |              |                 |
  |--------->|          |          |              |                 |
  |          | run_     |          |              |                 |
  |          | ipython_ |          |              |                 |
  |          | cell()   |          |              |                 |
  |          |--------->|          |              |                 |
  |          |          |------------------------------>|           |
  |          |          |          |              |                 |
  |          |          |          |              | call_tool(      |
  |          |          |          |              |  "get_weather", |
  |          |          |          |              |  city="BJ")     |
  |          |          |          |              |--------+        |
  |          |          |          |              |        | HTTP   |
  |          |          |          |              |<-------+ POST   |
  |          |          |          |<-------------|  /call/get_     |
  |          |          |          |              |  weather        |
  |          |          |          |              |                 |
  |          |          |          | get_weather(city)              |
  |          |          |          |------------------------------>|  |
  |          |          |          |              |        | wttr.in|
  |          |          |          |  ToolResponse|<-------+ API   |
  |          |          |          |<------------------------------|  |
  |          |          |          |              |                 |
  |          |          |          |  JSON result |                 |
  |          |          |          |------------->|                 |
  |          |          |  stdout  |              |                 |
  |          |          |<------------------------------|           |
  |  ToolResponse     |          |              |                 |
  |<---------|          |          |              |                 |
```
