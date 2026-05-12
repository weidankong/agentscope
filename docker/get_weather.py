# -*- coding: utf-8 -*-
"""The get_weather tool — fetches weather from wttr.in."""

import json
import urllib.parse
import urllib.request
from typing import Any

from agentscope.tool._base import ToolBase
from agentscope.tool._response import ToolChunk
from agentscope.message import TextBlock, ToolResultState


class GetWeather(ToolBase):
    """Get current weather for a city from wttr.in."""

    name: str = "get_weather"
    description: str = "Get current weather for a city from wttr.in"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "City name, e.g. beijing, london",
            },
            "format": {
                "type": "string",
                "description": "Output format: 'text' (default) or 'json'",
                "enum": ["text", "json"],
            },
        },
        "required": ["city"],
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    async def __call__(  # type: ignore[override]
        self,
        city: str,
        format: str = "text",
    ) -> ToolChunk:
        if not city:
            return ToolChunk(
                content=[TextBlock(text="Missing required argument: city")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        try:
            if format == "json":
                url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
            else:
                url = f"https://wttr.in/{urllib.parse.quote(city)}?format=3"

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "curl/7.68.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")

            if format == "json":
                data = json.loads(body)
                current = data.get("current_condition", [{}])[0]
                text = (
                    f"Weather for {city}:\n"
                    f"  Temperature: {current.get('temp_C', '?')}°C "
                    f"({current.get('temp_F', '?')}°F)\n"
                    f"  Feels like: {current.get('FeelsLikeC', '?')}°C\n"
                    f"  Humidity: {current.get('humidity', '?')}%\n"
                    f"  Wind: {current.get('windspeedKmph', '?')} km/h\n"
                    f"  Description: "
                    f"{current.get('weatherDesc', [{}])[0].get('value', '?')}"
                )
            else:
                text = body.strip()

            return ToolChunk(
                content=[TextBlock(text=text)],
                state=ToolResultState.RUNNING,
                is_last=True,
            )
        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Error fetching weather: {e}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
