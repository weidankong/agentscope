# Sandbox Architecture Design Doc

## 背景

当前 Agent 的 tool/MCP/skill 全部在本地进程中加载和执行。Agent 通过 `Toolkit` 直接持有 tool 实例，调用时直接执行 Python 函数或 MCP session。

问题：
- 工具执行与 Agent 进程强耦合，无法隔离
- 无法将工具执行分发到远程环境
- MCP server 的生命周期管理分散在 Agent 侧

## 目标

将 tools / MCPs / skills 装进 sandbox，Agent 仅通过 client 与 sandbox 交互：
- Agent 侧：轻量 client，只做请求发送
- Sandbox 侧：实际持有工具、执行调用、返回结果
- Manager 侧：管理多个 sandbox 的生命周期

## 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                         Application                              │
│                                                                  │
│  ┌──────────┐    ┌──────────────────────────────────────────┐   │
│  │  Agent   │    │    SandboxManager                         │   │
│  │          │───▶│  CRUD of sandbox instances                │   │
│  │          │    │  sandbox_id → Sandbox mapping              │   │
│  │          │    │  get_sandbox(id) → Sandbox (cached)        │   │
│  └────┬─────┘    └──────────────┬───────────────────────────┘   │
│       │                         │                                │
│       │  holds                  │  creates & manages             │
│       ▼                         ▼                                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Sandbox (agent-side proxy)                               │   │
│  │  list_tools() / call_tool()                              │   │
│  │  add_mcp() / remove_mcp() / list_mcps()                  │   │
│  │  list_skills() / import_skills()                         │   │
│  │  run(str|dict)                                           │   │
│  │  file: FileAccessor                                      │   │
│  │                                                          │   │
│  │  holds ──────────┐                                       │   │
│  └──────────────────┼───────────────────────────────────────┘   │
│                     │                                            │
│                     ▼                                            │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ SandboxConnection (ABC)                                  │   │
│  │  create() / resume()    ← factory methods                │   │
│  │  exec(command)          ← shell execution                 │   │
│  │  read(path) / write()   ← filesystem access               │   │
│  │  destroy() / close()    ← lifecycle                       │   │
│  │                                                          │   │
│  │  Subclasses:                                             │   │
│  │    LocalSandboxConnection   → local container             │   │
│  │    RemoteSandboxConnection  → HTTP/gRPC to remote manager │   │
│  │    CloudSandboxConnection   → cloud SDK (e.g. AgentBay)   │   │
│  └──────────────┬───────────────────────────────────────────┘   │
│                 │ connects to                                     │
│                 ▼                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ SandboxBaseSession (inside sandbox)                      │   │
│  │ (holds internal Toolkit)                                 │   │
│  │  register_tools()                                        │   │
│  │  call_tool() ← 实际执行                                   │   │
│  │  add/remove_mcp_server()                                 │   │
│  │  register/list/get_skills()                              │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

## 核心类设计

### 0. SandboxConnection（ABC）

与远端 SandboxBaseSession 的实际连接。负责执行命令、读写文件、生命周期管理。local / remote / cloud 的差异封装在子类内，Sandbox 里不出现 `if remote` 分支。

```python
class SandboxConnection(ABC):
    """
    Handle to one running sandbox instance.

    Required: exec + read/write + destroy + close + running.
    Optional: PTY, ports, snapshot, resume (gate on ``capabilities()``).
    """

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """Identifier for this backend (e.g. ``'e2b'``)."""

    # ─── factory ──────────────────────────────────────────────

    @classmethod
    @abstractmethod
    async def create(cls, options: SandboxCreateOptions) -> SandboxConnection:
        """Provision a new sandbox and return a connected instance."""

    @classmethod
    async def resume(cls, state: SerializedSandboxState) -> SandboxConnection:
        """Reattach to an existing sandbox from serialized state (optional)."""
        raise UnsupportedOperation(f"resume not implemented for this backend")

    # ─── execution ────────────────────────────────────────────

    @abstractmethod
    async def exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        """Run a shell command string inside the sandbox."""

    # ─── filesystem ───────────────────────────────────────────

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """Read a sandbox-relative file path as bytes."""

    @abstractmethod
    async def write(self, path: str, data: bytes) -> None:
        """Write bytes to a sandbox-relative path."""

    # ─── lifecycle ────────────────────────────────────────────

    @abstractmethod
    async def destroy(self) -> None:
        """
        Hard cleanup: release **all** backend resources (kill VM, delete temp dir, etc.).

        Idempotent. After ``destroy()`` the connection is unusable.
        """

    async def close(self) -> None:
        """
        Soft cleanup: release local handles only.

        Default delegates to ``destroy()``; override if you want a lighter teardown
        that keeps the remote sandbox alive (e.g. for pool reuse).
        """
        await self.destroy()
```

三种子类各自只管自己的事：

```python
class LocalSandboxConnection(SandboxConnection):
    """本地模式：直接操作容器编排层。"""
    def __init__(self, container_manager, config):
        self._mgr = container_manager  # 复用现有 SandboxManager 的容器编排能力

    @classmethod
    async def create(cls, options: SandboxCreateOptions) -> LocalSandboxConnection:
        conn = cls(options.container_manager, options.config)
        conn._container_id = await conn._mgr.create_from_pool(
            sandbox_type=options.backend, **options.extra,
        )
        return conn

    async def exec(self, command, *, timeout=None, cwd=None, env=None):
        return await self._mgr.exec_in_container(self._container_id, command, ...)

    async def read(self, path): ...
    async def write(self, path, data): ...

    async def destroy(self):
        await self._mgr.release(self._container_id)

class RemoteSandboxConnection(SandboxConnection):
    """远程模式：通过 HTTP 转发到远端 Manager 服务。"""
    def __init__(self, base_url: str, bearer_token: str | None = None):
        self._http = httpx.AsyncClient(base_url=base_url)
        if bearer_token:
            self._http.headers["Authorization"] = f"Bearer {bearer_token}"
        self._sandbox_id: str | None = None

    @classmethod
    async def create(cls, options: SandboxCreateOptions) -> RemoteSandboxConnection:
        conn = cls(options.extra.get("endpoint", ""), options.extra.get("token"))
        resp = await conn._http.post("/create", json={...})
        conn._sandbox_id = resp.json()["sandbox_id"]
        return conn

    async def exec(self, command, *, timeout=None, cwd=None, env=None):
        resp = await self._http.post(f"/sandbox/{self._sandbox_id}/exec", json={...})
        return ExecResult(...)

    async def read(self, path): ...
    async def write(self, path, data): ...

    async def destroy(self):
        await self._http.post(f"/sandbox/{self._sandbox_id}/destroy")
        await self._http.aclose()

class CloudSandboxConnection(SandboxConnection):
    """云沙箱模式：封装 AgentBay 等云 SDK。"""
    def __init__(self, cloud_client, config):
        self._cloud = cloud_client
        self._instance_id: str | None = None

    @classmethod
    async def create(cls, options: SandboxCreateOptions) -> CloudSandboxConnection:
        conn = cls(options.extra.get("cloud_client"), options.config)
        instance = await conn._cloud.create_instance(type=options.backend)
        conn._instance_id = instance.id
        return conn

    async def exec(self, command, *, timeout=None, cwd=None, env=None):
        return await self._cloud.exec(self._instance_id, command, ...)

    async def read(self, path): ...
    async def write(self, path, data): ...

    async def destroy(self):
        await self._cloud.delete_instance(self._instance_id)
```

**关键**：Sandbox 不再有 `http_session` / `base_url` 属性。所有通信细节封装在 Connection 子类内。Sandbox 通过 `self.connection.exec()` / `self.connection.read()` 等方法与远端交互，不感知底层传输。

### 1. SandboxManager

管理多个 Sandbox 实例的生命周期。一个 application 对应一个 manager。具体实现类（非 ABC）。

Manager 的核心职责：
1. **Sandbox CRUD**：创建/释放 Sandbox 实例
2. **sandbox_id → Sandbox mapping**：维护 sandbox_id 到 Sandbox 的映射，`get_sandbox` 从缓存中取，不再重复建连
3. **Sandbox 生命周期**：release 时关闭对应 Sandbox 的 connection

```python
class SandboxManager:
    """Sandbox 生命周期管理器。"""

    def __init__(self, **config):
        self._sandboxes: dict[str, Sandbox] = {}  # sandbox_id → Sandbox
        self._config = config

    async def create(self, config: SandboxConfig) -> Sandbox:
        """创建一个 Sandbox 实例并启动。"""
        sandbox = Sandbox(config)
        await sandbox.start()
        self._sandboxes[sandbox.sandbox_id] = sandbox
        return sandbox

    def get_sandbox(self, sandbox_id: str) -> Sandbox:
        """获取对应的 Sandbox 实例。"""
        return self._sandboxes[sandbox_id]

    async def release(self, sandbox_id: str) -> None:
        """释放 Sandbox，关闭 connection。"""
        sandbox = self._sandboxes.pop(sandbox_id, None)
        if sandbox:
            await sandbox.close()

    def list_sandboxes(self) -> list[str]:
        """列出所有 sandbox_id。"""
        return list(self._sandboxes.keys())
```

**设计思路**：Manager 本身不需要抽象——它的职责就是 CRUD + sandbox_id → Sandbox mapping + 生命周期。local vs remote vs cloud 的差异通过 `SandboxConfig.backend.type` 指定，由 `create_connection()` 工厂函数分发到对应的 `SandboxConnection` 子类，而不是继承 Manager 类。

**注意**：Manager **不管 sandbox 内部的工具/MCP/skill**。工具的注册、分组、查询、执行全部由 `SandboxBaseSession` 负责，`Sandbox` 做对应的前端代理调用。`create()` 方法接受 `SandboxConfig`，Sandbox 在 `start()` 时根据 config 注册 tools/MCPs/skills，实际注册工作委托给远端 session 完成。

**与现有代码的关系**：现有 `SandboxManager`（`sandbox/manager/sandbox_manager.py`）已经做了容器 CRUD。新 Manager 通过 `LocalSandboxConnection` 组合现有 Manager 作为实现细节，对外只暴露 sandbox 粒度的操作。

### 2. Sandbox（agent-side proxy）

Agent 侧的代理，通过它与 sandbox 交互。持有 SandboxConnection，管理 tools/MCPs/skills 的注册信息。具体实现类（非 ABC）。

```python
class Sandbox:
    """
    Agent-side proxy to one running sandbox.

    Lifecycle: ``start()`` → use → ``close()``.  Use as async context manager.
    """

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._conn: SandboxConnection | None = None
        self._id: str = uuid.uuid4().hex[:12]
        self._started = False

        self._tools: dict[str, _ToolEntry] = {}
        self._mcp_servers: dict[str, _McpServerHandle] = {}
        self._skills: dict[str, _SkillEntry] = {}
        self._mcp_client: Any = None
        self.file: FileAccessor | None = None

    @property
    def sandbox_id(self) -> str: ...
    @property
    def connection(self) -> SandboxConnection: ...

    # ─── lifecycle ─────────────────────────────────────────────
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> Sandbox: ...
    async def __aexit__(self, *exc) -> None: ...

    # ─── tool surface ─────────────────────────────────────────
    async def list_tools(self) -> list[Any]: ...
    async def call_tool(self, name: str, args: dict | None = None) -> Any: ...

    # ─── skill surface ────────────────────────────────────────
    async def list_skills(self) -> list[dict[str, Any]]: ...
    async def import_skills(self, spec: str | list[str]) -> None: ...

    # ─── MCP surface ──────────────────────────────────────────
    async def list_mcps(self) -> list[dict[str, Any]]: ...
    async def add_mcp(self, name: str, command: str, ...) -> None: ...
    async def remove_mcp(self, name: str) -> None: ...

    # ─── general dispatch ─────────────────────────────────────
    async def run(self, request: str | dict[str, Any]) -> Any: ...
    def as_mcp_client(self) -> Any: ...
```

**通信方式**：Sandbox 通过 `self.connection`（SandboxConnection 实例）与远端交互：
- Local sandbox：`LocalSandboxConnection` 进程内调用或 Unix socket
- Remote sandbox：`RemoteSandboxConnection` HTTP/gRPC
- Cloud sandbox：`CloudSandboxConnection` 云 SDK

Sandbox 本身不关心底层传输，由 Connection 子类实现。

### 3. SandboxBaseSession

实际执行工具的 sandbox wrapper 层。对应当前 `SandboxBase`（runtime 侧）的升级版。

```python
class SandboxBaseSession(ABC):
    """Sandbox 执行环境。持有 tool/MCP/skill 实例，执行 tool_call。"""

    @property
    @abstractmethod
    def session_id(self) -> str: ...

    @abstractmethod
    def setup(self) -> None:
        """初始化 sandbox 环境。"""

    @abstractmethod
    def teardown(self) -> None:
        """清理 sandbox 环境。"""

    @abstractmethod
    def list_tools(self) -> list[ToolSchema]:
        """返回所有已注册 tool 的 schema。"""

    @abstractmethod
    def call_tool(self, tool_call: ToolCallBlock) -> ToolResponse:
        """执行 tool_call 并返回结果。"""

    @abstractmethod
    def register_tools(self, tools: list[ToolBase]) -> None:
        """注册工具到 sandbox。"""

    @abstractmethod
    def register_mcp_server(self, config: dict) -> list[ToolSchema]:
        """在 sandbox 内启动 MCP client 并注册其 tools。"""

    @abstractmethod
    def unregister_mcp_server(self, name: str) -> None:
        """关闭 MCP client 并移除其 tools。"""

    @abstractmethod
    def register_skills(self, skills: list[SkillLoaderBase]) -> None:
        """注册 skill loader 到 sandbox。"""

    @abstractmethod
    def list_skills(self) -> list[SkillInfo]: ...

    @abstractmethod
    def get_skill(self, name: str) -> SkillDetail: ...
```

**关键**：Session 是 tool/MCP/skill 真正生活的地方。MCP server 的连接在这里建立，tool 的 Python 函数在这里执行。

## 权限模型

权限检查需要分两层：

1. **Agent 侧（前置检查）**：在发出请求前，基于 schema + input 做快速判断
   - `ALLOW` → 直接发送到 sandbox
   - `ASK` → yield `RequireUserConfirmEvent`
   - `DENY` → 直接拒绝，不发请求

2. **Sandbox 侧（执行时检查）**：session 内部可做额外的安全检查
   - 资源限制（CPU/内存/文件系统）
   - 沙箱级权限策略

Agent 侧的 `PermissionEngine` 保留，但输入从 `ToolBase` 实例变为 `ToolSchema`（因为不再持有实例）。

## 数据流：Agent 发起 tool call

```
Agent._reply()
  │
  ├─ _reasoning() → LLM 返回 ToolCallBlock
  │
  ├─ _execute_tool_call(tool_call)
  │     │
  │     ├─ PermissionEngine.check(schema, input)
  │     │     ├─ DENY → 返回 error
  │     │     └─ ASK  → yield RequireUserConfirmEvent
  │     │
  │     ├─ ALLOW → sandbox.call_tool(tool_call)
  │     │              │
  │     │              └─ [HTTP/local] → sandbox_session.call_tool(tool_call)
  │     │                    │
  │     │                    └─ [HTTP/local] → sandbox_session.call_tool(tool_call)
  │     │                                        │
  │     │                                        ├─ middleware chain
  │     │                                        ├─ tool(**kwargs) or MCPTool()
  │     │                                        └─ return ToolResponse
  │     │
  │     └─ yield ToolResultEndEvent
  │
  └─ 继续循环
```

## Session 类型与工具绑定

不同类型的 sandbox session 绑定不同的工具集：

```
SandboxBaseSession
  ├── CodeSandboxSession     → Bash, Read, Write, Edit, Glob, Grep, IPython
  ├── BrowserSandboxSession  → Browser automation tools
  └── CustomSandboxSession   → User-defined tools
```

每种 session 类型在 `setup()` 时自动注册对应的 builtin tools。用户可通过 `register_tools()` 和 `register_mcp_server()` 动态扩展。

## MCP Server 的生命周期

当前：Agent 侧创建 MCP client → 连接 MCP server → 注册到 Toolkit
新架构：Sandbox session 内创建 MCP client → 连接 MCP server → 注册到 session 内 toolkit

```
Before:
  Agent ── MCPClient ──▶ MCP Server (remote)

After:
  Agent ── Sandbox ── SandboxConnection ──▶ SandboxSession ── MCPClient ──▶ MCP Server (remote)
```

好处：
- MCP client 的资源（连接、进程）在 sandbox 内管理，Agent 侧无负担
- 多个 agent 共享同一 sandbox 时，MCP 连接可复用
- Sandbox 销毁时，MCP 连接自动清理

## Skill 的处理

Skill 比较特殊——它是"指令"而非"可执行工具"。

**决定**：Skill 也装进 Sandbox（方案 B），与原 Toolkit 一致迁移到 `SandboxBaseSession`。

- Skill 可能关联 sandbox 内特定 tool（如 "如何使用 Bash 做某事"），与 session 绑定更自然
- `SkillViewer` builtin tool 在 sandbox 内执行，需要访问 skill 数据
- 支持远程 skill 仓库，不局限于本地文件系统
- Agent 侧通过 `Sandbox.list_skills()` / `get_skill()` 查询，用于构建系统提示

